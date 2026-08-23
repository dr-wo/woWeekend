from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any, Callable

from wodata.contracts import StrategySearchState
from wodata.events import resolve_race_distance
from wostrategy.algorithm.exact_strategy_search import StrategyModel, StrategyRules, search_best_compound_sequences
from wostrategy.analysis.degradation_cutoff import calculate_degradation_cutoffs
from wostrategy.analysis.pre_race_tyre_prediction import get_pre_race_tyre_prediction
from wostrategy.model.tyre_prediction import PreRaceTyrePrediction, TyreCompoundPrediction

from woweekend.artifacts.models import ComponentResult
from woweekend.config.common import resolved_dict
from woweekend.config.race import RaceConfig
from woweekend.reports.bundle import create_report_bundle
from .common import create_run, finish_run, resolve_workflow_event


def _strategy_dict(results):
    return [{"rank": item.rank, "compounds": list(item.plan.compounds), "pit_laps": list(item.plan.pit_laps), "cost": item.remaining_cost, "delta_to_best": item.delta_to_best, "formatted_strategy": item.formatted_strategy} for item in results]


def run_race(config: RaceConfig, *, event: str, input_config: dict[str, Any] | None = None, pit_loss_total: float | None = None, pit_in_s3: float | None = None, pit_out_s1: float | None = None, tyre_provider: Callable[..., Any] = get_pre_race_tyre_prediction, tyre_refresh: Callable[[], object] | None = None, event_resolver=None):
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
    if resolved.pit_loss.green.effective_total is None:
        components["pit_loss"] = ComponentResult("FAILED", error="Missing required green pit loss: provide either one total value or both pit_in_s3 and pit_out_s1.")
        finish_run(run, components, status_override="FAILED")
        return run
    components["pit_loss"] = ComponentResult("SUCCESS", {"green": {**asdict(resolved.pit_loss.green), "effective_total": resolved.pit_loss.green.effective_total}, "sc_vsc": {**asdict(resolved.pit_loss.sc_vsc), "effective_total": resolved.pit_loss.sc_vsc.effective_total}, "source": "explicit_argument" if pit_loss_total is not None or pit_in_s3 is not None or pit_out_s1 is not None else "config"})
    try:
        tyre = tyre_provider(season=metadata.season, round_number=metadata.round_number, data_root=resolved.data_root, refresh=tyre_refresh)
        tyre_data = tyre.to_dict() if hasattr(tyre, "to_dict") else dict(tyre)
        run.save_json("tyre_prediction", tyre_data)
        components["tyre_prediction"] = ComponentResult("SUCCESS", tyre_data, provenance={"owner": "woStrategy", "api": "get_pre_race_tyre_prediction"})
    except Exception as exc:
        try:
            tyre = _manual_only_tyre_prediction(
                resolved.tyre_prediction.manual_override,
                event=metadata.event_name,
                season=metadata.season,
                round_number=metadata.round_number,
            )
        except ValueError:
            components["tyre_prediction"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
            finish_run(run, components, status_override="FAILED")
            return run
        tyre_data = tyre.to_dict()
        run.save_json("tyre_prediction", tyre_data)
        components["tyre_prediction"] = ComponentResult(
            "SUCCESS", tyre_data,
            warnings=[f"Automatic tyre prediction unavailable; complete manual override used ({type(exc).__name__}: {exc})."],
            provenance={"source": "complete_manual_fallback", "automatic_load_attempted": True},
        )
    effective_tyre = _effective_tyre_prediction(tyre, resolved.tyre_prediction.manual_override)
    run.save_json("tyre_prediction.effective", effective_tyre)
    components["effective_tyre_prediction"] = ComponentResult("SUCCESS", effective_tyre)
    model = StrategyModel(
        {key: value["performance_delta_to_medium"]["effective_value"] for key, value in effective_tyre["compounds"].items()},
        {key: value["degradation_seconds_per_lap"]["effective_value"] for key, value in effective_tyre["compounds"].items()},
        tyre.artifact_id, tyre.model_version or "unknown",
    )
    state = StrategySearchState(0, total_laps, "MEDIUM", 0, None, resolved.max_stops, "pre_race_tyre_prediction")
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
            output = cutoff.to_dict()
            run.save_json(name, output)
            try:
                from wostrategy.plots.degradation_cutoff import save_degradation_cutoff_plot
                figure_path = save_degradation_cutoff_plot(
                    cutoff, run.path / "figures" / f"{name}.png"
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
    manifest = finish_run(run, components)
    context = {"warnings": manifest["warnings"], "tyre_uncertainty_retained": True, "strategy_central_values_only": True}
    run.save_json("report_context", context)
    create_report_bundle(run_path=run.path, workflow=run.workflow, event=metadata.event_id, context=context, results={name: item.to_dict() for name, item in components.items()}, figures=figures, language=resolved.report.language, template=resolved.report.template)
    return run


def _effective_tyre_prediction(tyre, overrides):
    automatic_available = tyre.provider != "complete_manual_fallback"
    compounds = {}
    for compound, automatic in tyre.compounds.items():
        override = overrides.get(compound)
        performance_override = getattr(override, "performance_delta_to_medium", None)
        degradation_override = getattr(override, "degradation_seconds_per_lap", None)
        compounds[compound] = {
            "performance_delta_to_medium": {
                "automatic_value": automatic.performance_delta_to_medium if automatic_available else None,
                "effective_value": automatic.performance_delta_to_medium if performance_override is None else performance_override,
                "source": "automatic" if performance_override is None else "manual_override",
                "uncertainty": automatic.performance_uncertainty,
            },
            "degradation_seconds_per_lap": {
                "automatic_value": automatic.degradation_seconds_per_lap if automatic_available else None,
                "effective_value": automatic.degradation_seconds_per_lap if degradation_override is None else degradation_override,
                "source": "automatic" if degradation_override is None else "manual_override",
                "uncertainty": automatic.degradation_uncertainty,
            },
            "identifiable": automatic.identifiable,
            "diagnostics": dict(automatic.diagnostics),
        }
    if abs(compounds["MEDIUM"]["performance_delta_to_medium"]["effective_value"]) > 1e-12:
        raise ValueError("Effective MEDIUM performance delta must remain zero")
    return {
        "reference_compound": "MEDIUM",
        "automatic_artifact_id": tyre.artifact_id if automatic_available else None,
        "manual_fallback_artifact_id": None if automatic_available else tyre.artifact_id,
        "compounds": compounds,
    }


def _manual_only_tyre_prediction(overrides, *, event: str, season: int, round_number: int):
    compounds = {}
    missing = []
    for compound in ("SOFT", "MEDIUM", "HARD"):
        override = overrides.get(compound)
        performance = getattr(override, "performance_delta_to_medium", None)
        degradation = getattr(override, "degradation_seconds_per_lap", None)
        if performance is None or degradation is None:
            missing.append(compound)
            continue
        compounds[compound] = TyreCompoundPrediction(
            performance_delta_to_medium=performance,
            degradation_seconds_per_lap=degradation,
            diagnostics={"source": "manual_only_fallback"},
        )
    if missing:
        raise ValueError(
            "Automatic tyre prediction is unavailable and the manual fallback is incomplete for: "
            + ", ".join(missing)
        )
    return PreRaceTyrePrediction(
        event=event,
        season=season,
        round_number=round_number,
        reference_compound="MEDIUM",
        compounds=compounds,
        generated_at="manual_input",
        provider="complete_manual_fallback",
        artifact_id=f"manual-{season}-{round_number:02d}",
        provenance={"automatic_load_attempted": True},
    )
