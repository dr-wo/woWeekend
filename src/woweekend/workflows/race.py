from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
import csv
import json
from pathlib import Path
from typing import Any, Callable

from wodata import (
    event_identity_from_metadata,
    find_event_operational_inputs_by_identity,
    get_data_root,
    write_event_operational_inputs,
)
from wodata.artifacts import weekend_model_root
from wodata.contracts import StrategySearchState
from wodata.events import resolve_race_distance
from wodata.interruption_history import load_history
from wodata.weekend_runs import WeekendRunStore, weekend_runs_root
from wostrategy.algorithm.exact_strategy_search import StrategyModel, StrategyRules, search_best_compound_sequences
from wostrategy.analysis.degradation_cutoff import calculate_degradation_cutoffs
from wostrategy.analysis.pre_race_tyre_prediction import load_pre_race_tyre_prediction_source
from wostrategy.analysis.pre_race_model_config import ensure_pre_race_model_config
from wostrategy.model.tyre_prediction import (
    COMMON_GAUGE_FRAME,
    PreRaceTyrePrediction,
    TyreCompoundPrediction,
    canonicalize_operational_tyre_prediction,
    select_source_performance_reference,
)

from woweekend.artifacts.models import ComponentResult
from woweekend.config.common import resolved_dict
from woweekend.config.race import RaceConfig
from woweekend.historical_vsc import query_historical_vsc_evidence
from woweekend.reports.bundle import create_report_bundle
from woweekend.reports.historical_traffic import historical_traffic_section
from .common import create_run, finish_run, resolve_workflow_event


class RacePreparationPrerequisiteError(RuntimeError):
    """Raised before run creation when a pre-race input is missing or unusable."""


def _strategy_dict(results):
    return [{"rank": item.rank, "compounds": list(item.plan.compounds), "pit_laps": list(item.plan.pit_laps), "cost": item.remaining_cost, "delta_to_best": item.delta_to_best, "formatted_strategy": item.formatted_strategy} for item in results]


def run_race(config: RaceConfig, *, event: str, input_config: dict[str, Any] | None = None, pit_loss_total: float | None = None, pit_in_s3: float | None = None, pit_out_s1: float | None = None, tyre_provider: Callable[..., Any] = load_pre_race_tyre_prediction_source, tyre_refresh: Callable[[], object] | None = None, model_config_ensurer: Callable[..., Any] = ensure_pre_race_model_config, event_resolver=None, historical_vsc_loader=None, prerequisites: dict[str, Any] | None = None):
    identity = resolve_workflow_event(
        event, config, include_race_distance=False,
        **({"resolver": event_resolver} if event_resolver is not None else {}),
    )
    tyre, automatic_tyre, tyre_load_warning = _resolve_operational_tyre_prediction(
        identity=identity,
        config=config,
        tyre_provider=tyre_provider,
        tyre_refresh=tyre_refresh,
    )
    metadata = resolve_workflow_event(
        event, config, include_race_distance=True,
        **({"resolver": event_resolver} if event_resolver is not None else {}),
    )
    resolved = config
    if pit_loss_total is not None and (pit_in_s3 is not None or pit_out_s1 is not None):
        raise ValueError("Explicit pit loss is ambiguous: use --pit-loss-total or the S3/S1 pair")
    if pit_loss_total is not None:
        green = replace(config.pit_loss.green, total=float(pit_loss_total), pit_in_s3=None, pit_out_s1=None)
        resolved = replace(config, pit_loss=replace(config.pit_loss, green=green))
    if pit_in_s3 is not None or pit_out_s1 is not None:
        green = replace(config.pit_loss.green, total=None, pit_in_s3=pit_in_s3 if pit_in_s3 is not None else config.pit_loss.green.pit_in_s3, pit_out_s1=pit_out_s1 if pit_out_s1 is not None else config.pit_loss.green.pit_out_s1)
        resolved = replace(config, pit_loss=replace(config.pit_loss, green=green))
    total_laps, total_laps_source = resolve_race_distance(
        metadata, manual_override=resolved.total_laps,
    )
    resolved_payload = resolved_dict(resolved)
    resolved_payload.update({"event_id": metadata.event_id, "event_name": metadata.event_name, "season": metadata.season, "round_number": metadata.round_number, "event_metadata": metadata.to_dict(), "resolved_total_laps": total_laps, "total_laps_source": total_laps_source})
    run = create_run(event=metadata.event_id, workflow="race_preparation", data_root=resolved.data_root, input_config=input_config or resolved_dict(config), resolved_config=resolved_payload, event_metadata=metadata)
    components: dict[str, ComponentResult] = {}
    figures = []
    if prerequisites is not None:
        run.save_json("pre_race_prerequisites", prerequisites)
        components["pre_race_prerequisites"] = ComponentResult("SUCCESS", prerequisites)
    if total_laps is None:
        components["race_distance"] = ComponentResult(
            "FAILED",
            error=(
                "Race distance is unavailable: canonical scheduled_race_laps and "
                "FastF1 session total laps are both missing; set race.total_laps "
                "as a manual override."
            ),
        )
        finish_run(run, components, status_override="FAILED")
        return run
    if total_laps_source == "event_metadata":
        source_detail = metadata.scheduled_race_laps_source
        source_provenance = metadata.scheduled_race_laps_provenance
    elif total_laps_source == "fastf1_session":
        source_detail = metadata.session_total_laps_source
        source_provenance = None
    else:
        source_detail = "race.total_laps"
        source_provenance = {"input": "manual_override"}
    race_distance_output = {
        "total_laps": total_laps,
        "source": total_laps_source,
        "source_detail": source_detail,
        "provenance": source_provenance,
    }
    run.save_json("race_distance", race_distance_output)
    components["race_distance"] = ComponentResult("SUCCESS", race_distance_output)
    try:
        ensured = model_config_ensurer(
            season=metadata.season,
            round_number=metadata.round_number,
            data_root=resolved.data_root,
            session_names=metadata.session_names,
        )
        model_config_output = (
            ensured.to_dict() if hasattr(ensured, "to_dict") else dict(ensured)
        )
        run.save_json("live_mc_model_config", model_config_output)
        components["live_mc_model_config"] = ComponentResult(
            "SUCCESS",
            model_config_output,
            provenance={
                "owner": "woStrategy",
                "status": model_config_output.get("status"),
                "producer_api": model_config_output.get("producer_api"),
            },
        )
    except Exception as exc:
        components["live_mc_model_config"] = ComponentResult(
            "FAILED",
            error=f"{type(exc).__name__}: {exc}",
            provenance={
                "owner": "woStrategy",
                "generation_attempted": True,
                "failure_isolated": True,
            },
        )
    if resolved.pit_loss.green.effective_total is None:
        components["pit_loss"] = ComponentResult("FAILED", error="Missing required green pit loss: provide either one total value or both pit_in_s3 and pit_out_s1.")
        finish_run(run, components, status_override="FAILED")
        return run
    pit_loss_output = {
        state: {**asdict(value), "effective_total": value.effective_total}
        for state, value in (
            ("green", resolved.pit_loss.green),
            ("sc", resolved.pit_loss.sc),
            ("vsc", resolved.pit_loss.vsc),
            ("sc_vsc", resolved.pit_loss.sc_vsc),
        )
    }
    pit_loss_output.update({
        "source": (
            "explicit_argument"
            if pit_loss_total is not None or pit_in_s3 is not None or pit_out_s1 is not None
            else "current_event_config"
        ),
        "legacy_sc_vsc_is_not_separate_sc_or_vsc": True,
    })
    components["pit_loss"] = ComponentResult("SUCCESS", pit_loss_output)
    history_values = {
        "season": metadata.season,
        "round_number": metadata.round_number,
        "total_laps": total_laps,
        "green_pit_loss": resolved.pit_loss.green.effective_total,
        "event_identity": event_identity_from_metadata(metadata),
        "source": "woWeekend race preparation resolved configuration",
        "updated_via": "woWeekend race-distance finalization",
        "data_root": resolved.data_root,
    }
    for key, value in (
        ("sc_pit_loss", resolved.pit_loss.sc.effective_total),
        ("vsc_pit_loss", resolved.pit_loss.vsc.effective_total),
        ("legacy_sc_vsc_pit_loss", resolved.pit_loss.sc_vsc.effective_total),
    ):
        if value is not None:
            history_values[key] = value
    try:
        history_path = write_event_operational_inputs(**history_values)
    except Exception as exc:
        components["event_operational_history"] = ComponentResult(
            "FAILED",
            error=f"{type(exc).__name__}: {exc}",
            provenance={"owner": "woWeekend race preparation"},
        )
        finish_run(run, components, status_override="FAILED")
        return run
    components["event_operational_history"] = ComponentResult(
        "SUCCESS",
        {
            "path": str(history_path),
            "total_laps": total_laps,
            "event_identity": event_identity_from_metadata(metadata),
        },
        provenance={
            "owner": "woWeekend race preparation",
            "history_resolved_race_distance": False,
        },
    )
    tyre_data = tyre.to_dict() if hasattr(tyre, "to_dict") else dict(tyre)
    run.save_json("tyre_prediction", tyre_data)
    if tyre_load_warning is not None:
        components["tyre_prediction"] = ComponentResult(
            "SUCCESS", tyre_data,
            warnings=[tyre_load_warning],
            provenance={"source": "complete_manual_fallback", "automatic_load_attempted": True},
        )
    else:
        components["tyre_prediction"] = ComponentResult(
            "SUCCESS", tyre_data,
            provenance={"owner": "woStrategy", "api": "get_pre_race_tyre_prediction"},
        )
    effective_tyre = _effective_tyre_prediction(
        tyre,
        resolved.tyre_prediction.manual_override,
        automatic_tyre=automatic_tyre,
    )
    run.save_json("tyre_prediction.effective", effective_tyre)
    components["effective_tyre_prediction"] = ComponentResult("SUCCESS", effective_tyre)
    fp_evidence = _report_fp_evidence(
        season=metadata.season,
        round_number=metadata.round_number,
        data_root=resolved.data_root,
        tyre=tyre,
    )
    run.save_json("practice_tyre_evidence", fp_evidence)
    components["practice_tyre_evidence"] = ComponentResult(
        "SUCCESS", fp_evidence,
        provenance={"source_family": "fp_diagnostic", "production_input": False},
    )
    fp_cutoff_markers = _fp_medium_cutoff_markers(fp_evidence)
    model = StrategyModel(
        {key: value["performance_delta_to_medium"]["effective_value"] for key, value in effective_tyre["compounds"].items()},
        {key: value["degradation_seconds_per_lap"]["effective_value"] for key, value in effective_tyre["compounds"].items()},
        tyre.artifact_id, tyre.model_version or "unknown",
    )
    state = StrategySearchState(0, total_laps, "MEDIUM", 0, None, resolved.max_stops, "pre_race_tyre_prediction")
    from woweekend.reports.modelled_undercut import save_modelled_undercut
    undercut, undercut_figure = save_modelled_undercut(run, model, total_laps)
    components["modelled_undercut_power"] = ComponentResult(
        "SUCCESS", undercut, provenance={"owner": "woStrategy", "input": "effective StrategyModel"},
    )
    figures.append(undercut_figure)
    for name, rules in (("strategy_unrestricted", StrategyRules()), ("strategy_rules_compliant", StrategyRules(minimum_distinct_dry_compounds=2))):
        try:
            results = search_best_compound_sequences(state, model, resolved.pit_loss.green.effective_total, max_stops=resolved.max_stops, k=resolved.result_count, allow_any_start=True, strategy_rules=rules)
            output = _strategy_dict(results)
            run.save_json(name, output)
            components[name] = ComponentResult("SUCCESS", output, provenance={"owner": "woStrategy", "rules": asdict(rules)})
        except Exception as exc:
            components[name] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    for name, rules in (("cutoff_unrestricted", StrategyRules()), ("cutoff_rules_compliant", StrategyRules(minimum_distinct_dry_compounds=2))):
        try:
            cutoff = calculate_degradation_cutoffs(model=model, state=state, pit_loss=resolved.pit_loss.green.effective_total, strategy_rules=rules, **asdict(resolved.degradation_cutoff))
            output = _cutoff_output_with_fp_markers(cutoff.to_dict(), fp_cutoff_markers)
            run.save_json(name, output)
            try:
                from wostrategy.plots.degradation_cutoff import save_degradation_cutoff_plot
                figure_path = save_degradation_cutoff_plot(
                    cutoff, run.path / "figures" / f"{name}.png",
                    fp_diagnostic_markers=fp_cutoff_markers,
                )
                figures.append(figure_path)
            except Exception as plot_error:
                cutoff_warnings = list(cutoff.warnings) + [
                    f"Degradation cutoff plot unavailable: {type(plot_error).__name__}: {plot_error}"
                ]
            else:
                cutoff_warnings = list(cutoff.warnings)
            components[name] = ComponentResult("SUCCESS", output, warnings=cutoff_warnings, provenance={"owner": "woStrategy"})
        except Exception as exc:
            components[name] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    if (_has_manual_override(resolved.tyre_prediction.manual_override)
            and automatic_tyre is not None):
        try:
            appendix = _automatic_baseline_appendix(
                tyre=automatic_tyre, state=state,
                pit_loss=resolved.pit_loss.green.effective_total,
                max_stops=resolved.max_stops, result_count=resolved.result_count,
                cutoff_config=resolved.degradation_cutoff,
                effective_tyre=effective_tyre,
                primary_strategy=(components.get("strategy_rules_compliant").output or [])
                if components.get("strategy_rules_compliant") else [],
                fp_cutoff_markers=fp_cutoff_markers,
            )
            run.save_json("automatic_model_without_manual_override", appendix)
            components["automatic_model_without_manual_override"] = ComponentResult(
                "SUCCESS", appendix,
                provenance={"source_family": _automatic_source_family(tyre), "counterfactual": True},
            )
        except Exception as exc:
            components["automatic_model_without_manual_override"] = ComponentResult(
                "FAILED", error=f"{type(exc).__name__}: {exc}",
            )
    interruption_history = load_history(metadata.season, metadata.round_number, resolved.data_root)
    run.save_json("historical_interruptions", interruption_history)
    components["historical_interruptions"] = ComponentResult(
        "SUCCESS", interruption_history,
        warnings=([interruption_history.get("reason", "Historical selection incomplete")]
                  if interruption_history["status"] != "available" else []),
        provenance={"owner": "woStrategy", "canonical_store": "woData/wostrategy/interruption_history/schema_v1",
                    "report_context_only": True, "current_calculation_input": False},
    )
    historical_vsc = query_historical_vsc_evidence(
        metadata=metadata, data_root=resolved.data_root,
        lookback_years=resolved.historical_vsc.lookback_years,
        missing_loader=historical_vsc_loader,
    )
    run.save_json("historical_empirical_vsc_context", historical_vsc)
    components["historical_empirical_vsc_context"] = ComponentResult(
        "SUCCESS",
        historical_vsc,
        provenance={"operational_input": False, "report_context_only": True},
    )
    historical_operational = _load_previous_season_operational_inputs(
        metadata=metadata, data_root=resolved.data_root,
    )
    run.save_json("historical_operational_inputs_context", historical_operational)
    components["historical_operational_inputs_context"] = ComponentResult(
        "SUCCESS",
        historical_operational,
        provenance={
            "semantic_role": "prior_event_operational_assumptions",
            "current_calculation_input": False,
            "report_context_only": True,
        },
    )
    historical_traffic, traffic_figures = historical_traffic_section(
        circuit=metadata.location or "unknown", target_season=metadata.season,
        data_root=resolved.data_root, run_path=run.path,
    )
    figures.extend(traffic_figures)
    # Optional descriptive context follows the existing history conventions:
    # its own available/partial/unavailable status never gates strategy inputs.
    components["historical_traffic_context"] = ComponentResult(
        "SUCCESS", historical_traffic,
        provenance={"operational_input": False, "report_context_only": True},
    )
    manifest = finish_run(run, components)
    context = {
        "warnings": manifest["warnings"],
        "tyre_uncertainty_retained": True,
        "strategy_central_values_only": True,
        "historical_interruptions": interruption_history,
        "historical_vsc_evidence": historical_vsc,
        "previous_season_operational_inputs": historical_operational,
        "historical_traffic": historical_traffic,
        "historical_empirical_values_affect_current_calculations": False,
        "historical_operational_values_affect_current_calculations": False,
    }
    run.save_json("report_context", context)
    create_report_bundle(run_path=run.path, workflow=run.workflow, event=metadata.event_id, context=context, results={name: item.to_dict() for name, item in components.items()}, figures=figures, language=resolved.report.language, template=resolved.report.template)
    return run


def _load_previous_season_operational_inputs(*, metadata, data_root) -> dict[str, Any]:
    """Load prior-season operational assumptions as report-only context."""
    identity = {
        "event_name": metadata.event_name,
        "official_event_name": metadata.official_event_name,
        "country": metadata.country,
        "location": metadata.location,
    }
    common = {
        "semantic_role": "prior_event_operational_assumptions",
        "current_calculation_input": False,
        "report_context_only": True,
        "empirical_observation": False,
    }
    if not (metadata.country and metadata.location):
        return {
            "status": "unavailable",
            "reason": "stable country/location identity unavailable",
            **common,
        }
    try:
        record = find_event_operational_inputs_by_identity(
            season=metadata.season - 1,
            event_identity=identity,
            data_root=data_root,
        )
    except ValueError as exc:
        return {"status": "unavailable", "reason": str(exc), **common}
    if record is None:
        return {
            "status": "unavailable",
            "reason": "no previous-season same-circuit operational history",
            **common,
        }
    return {
        "status": "available",
        "source_event": record.get("event_id"),
        "match": {
            "country": metadata.country,
            "location": metadata.location,
        },
        "operational": record.get("operational", {}),
        "source_provenance": record.get("provenance", {}),
        "source_updated_at": record.get("updated_at"),
        **common,
    }


def _load_previous_season_empirical_vsc(*, metadata, data_root) -> dict[str, Any]:
    """Load prior-season same-circuit evidence without feeding calculations."""
    identity = (metadata.country, metadata.location)
    if not all(identity):
        return {
            "status": "unavailable",
            "reason": "stable country/location identity unavailable",
            "operational_input": False,
        }
    root = weekend_runs_root(data_root)
    previous_prefix = f"event={metadata.season - 1:04d}-"
    if not root.exists():
        return {
            "status": "unavailable",
            "reason": "no previous-season post-race artifacts",
            "operational_input": False,
        }
    for event_directory in sorted(root.glob(f"{previous_prefix}*")):
        event_id = event_directory.name.removeprefix("event=")
        try:
            runs = WeekendRunStore.list_runs(
                event=event_id, workflow="post_race", data_root=data_root,
            )
        except Exception:
            continue
        for prior_run in reversed(runs):
            try:
                prior_metadata = prior_run.load_json("event.metadata")
                if (prior_metadata.get("country"), prior_metadata.get("location")) != identity:
                    continue
                empirical = prior_run.load_json("empirical_pit_loss")
                vsc = empirical.get("vsc", {})
                if not isinstance(vsc, dict) or vsc.get("status") != "available":
                    return {
                        "status": "unavailable",
                        "reason": "previous-season event has no valid VSC pit-stop sample",
                        "source_event": event_id,
                        "sample_count": 0,
                        "operational_input": False,
                    }
                return {
                    "status": "available",
                    "value": float(vsc["total"]),
                    "source_event": event_id,
                    "sample_count": int(vsc.get("sample_count", 0)),
                    "samples": list(vsc.get("samples", [])),
                    "source_run_id": prior_run.run_id,
                    "match": {"country": identity[0], "location": identity[1]},
                    "operational_input": False,
                    "report_context_only": True,
                }
            except (FileNotFoundError, KeyError, TypeError, ValueError):
                continue
    return {
        "status": "unavailable",
        "reason": "no previous-season same-circuit empirical VSC artifact",
        "operational_input": False,
    }



def _resolve_operational_tyre_prediction(
    *, identity, config, tyre_provider, tyre_refresh,
):
    automatic_tyre = None
    automatic_source = None
    source_error = None
    try:
        automatic_source = tyre_provider(
            season=identity.season,
            round_number=identity.round_number,
            data_root=config.data_root,
            refresh=tyre_refresh,
        )
        automatic_tyre = canonicalize_operational_tyre_prediction(
            automatic_source,
            expected_season=identity.season,
            expected_round_number=identity.round_number,
            expected_event_id=identity.event_id,
        )
    except Exception as exc:
        source_error = exc

    try:
        if automatic_tyre is None:
            candidate = _manual_only_tyre_prediction(
                config.tyre_prediction.manual_override,
                event=identity.event_id,
                season=identity.season,
                round_number=identity.round_number,
            )
            effective = canonicalize_operational_tyre_prediction(
                candidate,
                expected_season=identity.season,
                expected_round_number=identity.round_number,
                expected_event_id=identity.event_id,
            )
        elif not _has_manual_override(config.tyre_prediction.manual_override):
            effective = automatic_tyre
        else:
            candidate = _source_prediction_with_overrides(
                automatic_source,
                config.tyre_prediction.manual_override,
            )
            effective = canonicalize_operational_tyre_prediction(
                candidate,
                expected_season=identity.season,
                expected_round_number=identity.round_number,
                expected_event_id=identity.event_id,
            )
    except Exception as fallback_error:
        provider_detail = (
            f"{type(source_error).__name__}: {source_error}"
            if source_error is not None else "canonical artifact loaded"
        )
        raise RacePreparationPrerequisiteError(
            f"Cannot prepare {identity.event_name} ({identity.event_id}, round "
            f"{identity.round_number}): the canonical pre-race tyre prediction "
            "is missing or unusable. Complete the woStrategy tyre-prediction "
            "prerequisite first with `PYTHONPATH=../woData/src:src python -m "
            f"wostrategy.script.tyre_prediction_pipeline --year {identity.season}`. "
            "This workflow will not invoke image/API extraction automatically. "
            "Alternatively, the documented complete-manual fallback remains "
            "available: provide performance_delta_to_medium and "
            "degradation_seconds_per_lap for SOFT, MEDIUM, and HARD under "
            f"tyre_prediction.manual_override. Provider result: {provider_detail}. "
            f"Resolved input error: {type(fallback_error).__name__}: {fallback_error}"
        ) from (source_error or fallback_error)

    warning = None
    if automatic_tyre is None:
        warning = (
            "Automatic tyre prediction unavailable or unusable; complete manual "
            f"override used ({type(source_error).__name__}: {source_error})."
        )
    return effective, automatic_tyre, warning


def _source_prediction_with_overrides(source_tyre, overrides):
    """Merge fields in the source gauge; the caller normalizes exactly once."""
    provenance = dict(source_tyre.provenance)
    selection = provenance.get("source_reference_selection")
    if not isinstance(selection, dict):
        reference, rule, zero_candidates = select_source_performance_reference(
            source_tyre.compounds,
            declared_reference=(
                source_tyre.reference_compound
                if source_tyre.reference_declared is True else None
            ),
        )
        selection = {"rule": rule, "zero_candidates": list(zero_candidates)}
        provenance["source_reference_selection"] = selection
    else:
        reference = source_tyre.reference_compound
    compounds = {}
    for compound, source_value in source_tyre.compounds.items():
        override = overrides.get(compound)
        performance = getattr(override, "performance_delta_to_medium", None)
        degradation = getattr(override, "degradation_seconds_per_lap", None)
        compounds[compound] = replace(
            source_value,
            performance_delta_to_medium=(
                source_value.performance_delta_to_medium
                if performance is None else performance
            ),
            degradation_seconds_per_lap=(
                source_value.degradation_seconds_per_lap
                if degradation is None else degradation
            ),
        )
    provenance["partial_override"] = {
        "merge_frame": source_tyre.performance_frame,
        "source_reference_compound": source_tyre.reference_compound,
        "reference_reinferred_after_merge": False,
        "normalized_before_merge": False,
    }
    provenance["intermediate_state"] = {
        "kind": "partial_override_merged_in_source_gauge",
        "performance_frame": source_tyre.performance_frame,
        "reference_compound": reference,
        "formal_relative_output": False,
        "reference_zero_required": False,
        "normalization_applied": False,
    }
    return replace(
        source_tyre,
        reference_compound=reference,
        compounds=compounds,
        provenance=provenance,
        formal_relative_output=False,
    )


def _effective_tyre_prediction(tyre, overrides, *, automatic_tyre=None):
    automatic_available = automatic_tyre is not None
    compounds = {}
    for compound, effective in tyre.compounds.items():
        automatic = (
            automatic_tyre.compounds[compound] if automatic_available else None
        )
        override = overrides.get(compound)
        performance_override = getattr(override, "performance_delta_to_medium", None)
        degradation_override = getattr(override, "degradation_seconds_per_lap", None)
        compounds[compound] = {
            "performance_delta_to_medium": {
                "automatic_value": automatic.performance_delta_to_medium if automatic_available else None,
                "effective_value": effective.performance_delta_to_medium,
                "source": _automatic_source_family(tyre) if performance_override is None else "manual_override",
                "human_source": _human_source(tyre, performance_override is not None),
                "uncertainty": effective.performance_uncertainty,
            },
            "degradation_seconds_per_lap": {
                "automatic_value": automatic.degradation_seconds_per_lap if automatic_available else None,
                "effective_value": effective.degradation_seconds_per_lap,
                "source": _automatic_source_family(tyre) if degradation_override is None else "manual_override",
                "human_source": _human_source(tyre, degradation_override is not None),
                "uncertainty": effective.degradation_uncertainty,
            },
            "prediction_domain_status": (
                "inside_observed_descriptor_domain" if effective.identifiable
                else "descriptor_boundary_or_outside_observed_domain"
            ),
            "diagnostics": dict(effective.diagnostics),
        }
    return {
        "reference_compound": "MEDIUM",
        "performance_semantics": dict(
            tyre.provenance.get("performance_semantics", {})
        ),
        "degradation_semantics": dict(
            tyre.provenance.get("degradation_semantics", {})
        ),
        "automatic_artifact_id": automatic_tyre.artifact_id if automatic_available else None,
        "manual_fallback_artifact_id": None if automatic_available else tyre.artifact_id,
        "automatic_source_family": _automatic_source_family(automatic_tyre) if automatic_available else None,
        "automatic_model_version": automatic_tyre.model_version if automatic_available else None,
        "automatic_generated_at": automatic_tyre.generated_at if automatic_available else None,
        "automatic_provenance": dict(automatic_tyre.provenance) if automatic_available else {},
        "operational_provenance": dict(tyre.provenance),
        "compounds": compounds,
    }


def _automatic_source_family(tyre) -> str:
    families = {
        str(value.diagnostics.get("selected_default"))
        for value in tyre.compounds.values()
        if value.diagnostics.get("selected_default")
    }
    return next(iter(families)) if len(families) == 1 else "automatic_model"


def _human_source(tyre, overridden: bool) -> str:
    if overridden:
        return "manual override"
    family = _automatic_source_family(tyre)
    if family == "historical_baseline":
        return (
            "Historical Race-Retro cross-event P0/D0 prediction (default), mapped "
            "to the Pirelli-announced compound allocation; current-weekend FP does "
            "not modify the production input."
        )
    return family.replace("_", " ") + " (default)"


def _has_manual_override(overrides) -> bool:
    return any(
        getattr(value, field, None) is not None
        for value in overrides.values()
        for field in ("performance_delta_to_medium", "degradation_seconds_per_lap")
    )


def _automatic_baseline_appendix(*, tyre, state, pit_loss, max_stops, result_count,
                                  cutoff_config, effective_tyre, primary_strategy,
                                  fp_cutoff_markers=()):
    automatic = {
        compound: {
            "performance_delta_to_medium": value.performance_delta_to_medium,
            "degradation_seconds_per_lap": value.degradation_seconds_per_lap,
        }
        for compound, value in tyre.compounds.items()
    }
    model = StrategyModel(
        {key: value["performance_delta_to_medium"] for key, value in automatic.items()},
        {key: value["degradation_seconds_per_lap"] for key, value in automatic.items()},
        tyre.artifact_id, tyre.model_version or "unknown",
    )
    rules = StrategyRules(minimum_distinct_dry_compounds=2)
    strategies = _strategy_dict(search_best_compound_sequences(
        state, model, pit_loss, max_stops=max_stops, k=result_count,
        allow_any_start=True, strategy_rules=rules,
    ))
    cutoff = _cutoff_output_with_fp_markers(calculate_degradation_cutoffs(
        model=model, state=state, pit_loss=pit_loss, strategy_rules=rules,
        **asdict(cutoff_config),
    ).to_dict(), fp_cutoff_markers)
    return {
        "title": "Automatic model result without manual override",
        "source_family": _automatic_source_family(tyre),
        "tyre_values": automatic,
        "rules_compliant_strategy": strategies,
        "degradation_cutoffs": cutoff,
        "comparison": {
            "manual_effective_tyre_values": effective_tyre["compounds"],
            "primary_manual_strategy": list(primary_strategy),
            "automatic_best_strategy": strategies[0] if strategies else None,
            "manual_best_strategy": primary_strategy[0] if primary_strategy else None,
        },
    }


FP_CUTOFF_MARKER_LIMITATION = (
    "FP degradation estimates are diagnostic coordinates. Absolute degradation is not "
    "fully separable from fuel/load effects with the current practice-session model, "
    "and compound performance is not independently identifiable without stronger "
    "cross-compound constraints. Therefore FP values are shown only as reference "
    "markers against the production cutoff envelope and do not replace the production "
    "tyre inputs."
)


def _fp_medium_cutoff_markers(fp_evidence):
    markers = []
    for session in fp_evidence.get("sessions", ()):
        if session.get("status") != "available":
            continue
        quantity = session.get("quantities", {}).get("degradation:MEDIUM")
        if not quantity:
            continue
        support = quantity.get("support", {})
        coordinate = quantity.get("posterior_coordinate", {})
        value = coordinate.get("median")
        if (
            support.get("status") != "measured"
            or int(support.get("usable_laps", 0)) <= 0
            or int(support.get("usable_runs", 0)) <= 0
            or value is None
            or float(value) < 0
        ):
            continue
        markers.append({
            "session": session["session"],
            "medium_degradation": float(value),
            "unit": coordinate.get("unit", "s/lap"),
            "p10": coordinate.get("p10"),
            "p90": coordinate.get("p90"),
            "support": dict(support),
            "fp_structural_identifiability": quantity.get("fp_structural_identifiability"),
            "source_family": "fp_diagnostic",
            "production_input": False,
        })
    return markers


def _cutoff_output_with_fp_markers(output, markers):
    return {
        **dict(output),
        "fp_diagnostic_medium_markers": list(markers),
        "fp_marker_interpretation": (
            "Session-level FP MEDIUM posterior coordinates positioned against the "
            "unchanged production strategy cutoff envelope."
        ),
        "fp_marker_limitation": FP_CUTOFF_MARKER_LIMITATION,
        "fp_markers_modify_production_inputs": False,
    }


def _as_utc(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(timezone.utc)


def _report_fp_evidence(*, season: int, round_number: int, data_root, tyre):
    root = weekend_model_root(season, round_number, get_data_root(data_root))
    report_path = root / "fp_tyre_evidence_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    report_sessions = {
        str(row.get("session", "")).upper(): row
        for row in report.get("sessions", ())
    }
    sessions = []
    timestamps = []
    for session in ("FP1", "FP2", "FP3"):
        directory = root / "sessions" / session
        parameters_path = directory / "latest_parameters.csv"
        manifest_path = directory / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        temperature_context = _report_temperature_context(
            report_sessions.get(session),
            analysis_id=manifest.get("analysis_id"),
            model_created_at=manifest.get("created_at"),
            model_available=parameters_path.is_file(),
        )
        if not parameters_path.is_file():
            sessions.append({
                "session": session,
                "status": "unavailable",
                "quantities": {},
                "temperature_context": temperature_context,
            })
            continue
        created_at = manifest.get("created_at")
        if created_at:
            timestamps.append(str(created_at))
        with parameters_path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        quantities = {}
        for row in rows:
            parameter = row["parameter"]
            compound = row.get("compound") or None
            key = f"{parameter}:{compound}" if compound else parameter
            if parameter == "track_rate":
                structural = "conditionally_identifiable_in_current_model"
            elif parameter == "fuel_rate":
                structural = "not_identifiable_separately_from_absolute_degradation"
            elif parameter == "degradation":
                structural = "absolute_not_identifiable; combined_slope_or_contrasts_only"
            else:
                structural = (
                    "reference_definition" if compound == "MEDIUM"
                    else "not_identifiable_with_free_single_compound_run_intercepts"
                )
            quantities[key] = {
                "parameter": parameter, "compound": compound,
                "posterior_coordinate": {
                    "p10": float(row["p10"]), "median": float(row["median"]),
                    "p90": float(row["p90"]), "unit": row["unit"],
                },
                "fp_structural_identifiability": structural,
                "support": {
                    "usable_laps": int(float(row["usable_lap_count"])),
                    "usable_runs": int(float(row["usable_run_count"])),
                    "status": row["support_status"],
                },
            }
        sessions.append({
            "session": session, "status": "available", "analysis_id": manifest.get("analysis_id"),
            "calculated_at": created_at, "quantities": quantities,
            "temperature_context": temperature_context,
        })
    latest_fp = max(timestamps, key=lambda value: _as_utc(value)) if timestamps else None
    prediction_time = tyre.generated_at
    return {
        "source_family": "fp_diagnostic",
        "report_status": "diagnostic_only",
        "production_strategy_inputs_modified": bool(report.get("production_strategy_inputs_modified", False)),
        "temperature_context_contract": report.get("temperature_context_contract", {
            "version": 0,
            "status": "legacy_report_without_temperature_context",
            "reporting_only": True,
        }),
        "artifact": {
            "path": str(report_path) if report_path.is_file() else None,
            "artifact_id": report.get("report_fingerprint"),
            "calculated_at": latest_fp,
        },
        "production_tyre_prediction": {
            "artifact_id": tyre.artifact_id, "generated_at": prediction_time,
        },
        "freshness": {
            "fp_evidence_newer_than_prediction": bool(
                latest_fp and prediction_time and _as_utc(latest_fp) > _as_utc(prediction_time)
            ),
            "warning": (
                "FP evidence is newer than the production tyre prediction; FP remains diagnostic-only."
                if latest_fp and prediction_time and _as_utc(latest_fp) > _as_utc(prediction_time)
                else None
            ),
        },
        "sessions": sessions,
        "confounding": [
            "Absolute fuel and the common absolute degradation level are structurally confounded.",
            "A reported posterior coordinate is not automatically a physically identifiable measurement.",
            "Non-reference compound performance is absorbed by free single-compound run intercepts.",
        ],
        "limitations": report.get("limitations", []),
        "performance_note": report.get("performance_note"),
    }


def _report_temperature_context(
    report_session, *, analysis_id, model_available, model_created_at=None
):
    if not model_available:
        return {
            "status": "unavailable_no_tyre_evidence",
            "reason": "No current persisted tyre-evidence artifact exists for this session.",
            "analysis_id": analysis_id,
        }
    if not analysis_id or not model_created_at:
        return {
            "status": "unavailable_unbound_provenance",
            "reason": "The current model manifest lacks analysis_id or created_at.",
            "analysis_id": analysis_id,
        }
    if not report_session or "temperature_context" not in report_session:
        return {
            "status": "legacy_provenance_unavailable",
            "reason": (
                "The source report does not persist supporting-lap temperature provenance; "
                "current filters were not used to reconstruct it."
            ),
            "analysis_id": analysis_id,
        }
    context = dict(report_session["temperature_context"])
    context_analysis_id = context.get("analysis_id")
    context_created_at = context.get("model_created_at")
    if not context_analysis_id or not context_created_at:
        return {
            "status": "unavailable_unbound_provenance",
            "reason": (
                "Persisted temperature provenance lacks analysis_id or model_created_at; "
                "it was not attached to the current evidence."
            ),
            "analysis_id": analysis_id,
        }
    if context_analysis_id != analysis_id or context_created_at != model_created_at:
        return {
            "status": "stale_provenance_unavailable",
            "reason": (
                "Persisted temperature provenance belongs to a different model artifact instance; "
                "it was not attached to the current evidence."
            ),
            "analysis_id": analysis_id,
            "temperature_context_analysis_id": context_analysis_id,
            "model_created_at": model_created_at,
            "temperature_context_model_created_at": context_created_at,
        }
    return context


def _manual_only_tyre_prediction(overrides, *, event: str, season: int, round_number: int):
    compounds = {}
    for compound in ("SOFT", "MEDIUM", "HARD"):
        override = overrides.get(compound)
        performance = getattr(override, "performance_delta_to_medium", None)
        degradation = getattr(override, "degradation_seconds_per_lap", None)
        compounds[compound] = TyreCompoundPrediction(
            performance_delta_to_medium=performance,
            degradation_seconds_per_lap=degradation,
            diagnostics={"source": "manual_only_fallback"},
        )
    reference, selection_rule, zero_candidates = select_source_performance_reference(
        compounds
    )
    return PreRaceTyrePrediction(
        event=event,
        season=season,
        round_number=round_number,
        reference_compound=reference,
        compounds=compounds,
        generated_at="manual_input",
        provider="complete_manual_fallback",
        artifact_id=f"manual-{season}-{round_number:02d}",
        provenance={
            "automatic_load_attempted": True,
            "source_reference_selection": {
                "rule": selection_rule,
                "zero_candidates": list(zero_candidates),
            },
        },
        performance_frame=COMMON_GAUGE_FRAME,
        reference_declared=False,
    )
