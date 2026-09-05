from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping

from wodata.weekend_runs import WeekendRunStore

from woweekend.artifacts.models import ComponentResult
from woweekend.config.common import resolved_dict
from woweekend.config.post_race import PostRaceConfig
from woweekend.reports.bundle import create_report_bundle
from wostrategy.analysis.pre_race_model_config import ensure_pre_race_model_config
from .common import create_run, finish_run, resolve_workflow_event


def compare_tyre_predictions(pre_race: Mapping[str, Any], retro: Mapping[str, Any]) -> dict[str, Any]:
    pre_compounds = dict(pre_race.get("compounds", {}))
    retro_compounds = dict(retro.get("compounds", {}))
    rows = []
    for compound in ("SOFT", "MEDIUM", "HARD"):
        before = pre_compounds.get(compound)
        after = retro_compounds.get(compound)
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            rows.append({"compound": compound, "available": False, "pre_race_available": before is not None, "retro_available": after is not None})
            continue
        pre_perf = _effective_value(before.get("performance_delta_to_medium"))
        retro_perf = after.get("performance_delta_to_medium")
        pre_deg = _effective_value(before.get("degradation_seconds_per_lap"))
        retro_deg = after.get("degradation_seconds_per_lap")
        rows.append({"compound": compound, "available": True, "performance_delta": {"pre_race": pre_perf, "retro_estimate": retro_perf, "prediction_minus_retro": None if pre_perf is None or retro_perf is None else float(pre_perf) - float(retro_perf), "pre_race_uncertainty": _uncertainty(before, "performance_delta_to_medium", "performance_uncertainty"), "retro_uncertainty": after.get("performance_uncertainty")}, "degradation": {"pre_race": pre_deg, "retro_estimate": retro_deg, "prediction_minus_retro": None if pre_deg is None or retro_deg is None else float(pre_deg) - float(retro_deg), "pre_race_uncertainty": _uncertainty(before, "degradation_seconds_per_lap", "degradation_uncertainty"), "retro_uncertainty": after.get("degradation_uncertainty")}, "identifiability": {"pre_race": before.get("identifiable"), "retro": after.get("identifiable")}})
    return {
        "reference_compound": "MEDIUM",
        "retro_is_estimate_not_ground_truth": True,
        "compounds": rows,
        "retro_provenance": dict(retro.get("provenance", {})),
    }


def _effective_value(value):
    return value.get("effective_value") if isinstance(value, Mapping) else value


def _uncertainty(row: Mapping[str, Any], effective_key: str, legacy_key: str):
    value = row.get(effective_key)
    if isinstance(value, Mapping):
        return value.get("uncertainty")
    return row.get(legacy_key)


def resolve_retro_pit_loss(*, empirical: Mapping[str, Any], saved_pre_race_config: Mapping[str, Any]) -> dict[str, Any]:
    pre = dict(saved_pre_race_config.get("pit_loss", {}))
    output = {}
    for output_key, empirical_key, pre_key in (("GREEN", "normal", "green"), ("SC/VSC", "sc_vsc", "sc_vsc")):
        row = empirical.get(empirical_key)
        if isinstance(row, Mapping) and row.get("sample_count", 0) and row.get("total") is not None:
            output[output_key] = {"value": float(row["total"]), "pit_in_s3": row.get("pit_in_s3"), "pit_out_s1": row.get("pit_out_s1"), "source": "race_empirical_median", "sample_count": int(row["sample_count"])}
            continue
        fallback = pre.get(pre_key, {}) if isinstance(pre.get(pre_key, {}), Mapping) else {}
        total = fallback.get("effective_total", fallback.get("total"))
        pit_in, pit_out = fallback.get("pit_in_s3"), fallback.get("pit_out_s1")
        if total is not None:
            output[output_key] = {"value": float(total), "pit_in_s3": pit_in, "pit_out_s1": pit_out, "source": "pre_race_fallback", "sample_count": 0}
        elif pit_in is not None and pit_out is not None:
            output[output_key] = {"value": float(pit_in) + float(pit_out), "pit_in_s3": float(pit_in), "pit_out_s1": float(pit_out), "source": "pre_race_fallback", "sample_count": 0}
        else:
            output[output_key] = {"value": None, "source": "unavailable", "sample_count": 0}
    return output


def run_post_race(config: PostRaceConfig, *, event: str, input_config: dict[str, Any] | None = None, providers: Mapping[str, Callable[..., Any]] | None = None, model_config_ensurer: Callable[..., Any] = ensure_pre_race_model_config, event_resolver=None):
    custom_providers_supplied = bool(providers)
    providers = dict(providers or {})
    metadata = resolve_workflow_event(
        event, config, include_race_distance=True,
        **({"resolver": event_resolver} if event_resolver is not None else {}),
    )
    race_start = config.race_start or metadata.race_start
    if race_start is None:
        raise ValueError(
            "Race start is unavailable from FastF1 metadata; set race_start as a manual override"
        )
    resolved = resolved_dict(config)
    resolved.update({"event_id": metadata.event_id, "event_name": metadata.event_name, "season": metadata.season, "round_number": metadata.round_number, "race_start": race_start, "race_start_source": "manual_override" if config.race_start else "fastf1_event_schedule", "resolved_total_laps": metadata.total_laps, "total_laps_source": metadata.total_laps_source, "event_metadata": metadata.to_dict()})
    run = create_run(event=metadata.event_id, workflow="post_race", data_root=config.data_root, input_config=input_config or resolved_dict(config), resolved_config=resolved, event_metadata=metadata)
    review_cache = {}
    def review():
        if not review_cache:
            from wostrategy.analysis.post_race_review import get_post_race_review
            review_cache.update(get_post_race_review(
                season=metadata.season, round_number=metadata.round_number,
                data_root=config.data_root,
                overrides=dict(config.race_performance.overrides),
                force_refresh=True,
            ))
        return review_cache
    providers.setdefault("standings", lambda **kwargs: _default_standings(run.path, **kwargs))
    providers.setdefault("qualifying_performance", _default_qualifying_performance)
    providers.setdefault("qualifying_performance_tracker", lambda **kwargs: _default_qualifying_performance_tracker(run.path, **kwargs))
    providers.setdefault("race_performance", lambda **kwargs: review()["race_performance"])
    providers.setdefault("race_performance_tracker", lambda **kwargs: _default_race_performance_tracker(run.path, data_root=config.data_root, **kwargs))
    providers.setdefault("retro_tyre_estimate", lambda **kwargs: review()["retro_tyre_estimate"])
    providers.setdefault("empirical_pit_loss", lambda **kwargs: review()["empirical_pit_loss"])
    components: dict[str, ComponentResult] = {}
    try:
        pre_run = (WeekendRunStore.load(event=metadata.event_id, workflow="race_preparation", run_id=config.pre_race_run_id, data_root=config.data_root) if config.pre_race_run_id else WeekendRunStore.select_before(event=metadata.event_id, workflow="race_preparation", before=race_start, data_root=config.data_root, required_artifacts=("tyre_prediction",)))
        try:
            pre_tyre = pre_run.load_json("tyre_prediction.effective")
        except FileNotFoundError:
            pre_tyre = pre_run.load_json("tyre_prediction")
        pre_config = pre_run.load_json("config.resolved")
        components["pre_race_artifacts"] = ComponentResult("SUCCESS", {"run_id": pre_run.run_id, "generated_at": pre_run.generated_at.isoformat(), "tyre_prediction": pre_tyre}, provenance={"selection": "explicit_run_id" if config.pre_race_run_id else "final_usable_before_race_start", "recomputed": False})
    except Exception as exc:
        pre_tyre, pre_config = {}, {}
        components["pre_race_artifacts"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    # Retro MC is deliberately first: all downstream tyre comparison and
    # retrospective strategy outputs are gated on this fresh canonical result.
    calls = {
        "retro_tyre_estimate": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.race_performance.overrides)),
        "standings": ({"season": metadata.season, "round_number": metadata.round_number}, {}),
        "qualifying_performance": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.qualifying_performance.overrides)),
        "qualifying_performance_tracker": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.qualifying_performance.overrides)),
        "race_performance": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.race_performance.overrides)),
        "race_performance_tracker": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.race_performance.overrides)),
        "empirical_pit_loss": ({"season": metadata.season, "round_number": metadata.round_number}, {}),
    }
    for name, (base, overrides) in calls.items():
        provider = providers.get(name)
        if provider is None:
            message = f"No public {name} workflow adapter was supplied."
            components[name] = ComponentResult("FAILED", error=message)
            continue
        try:
            output = provider(**base, **overrides)
            output = _jsonable(output)
            if name == "retro_tyre_estimate":
                _validate_retro_mc_output(output)
            components[name] = ComponentResult("SUCCESS", output, provenance={"owner": "woStanding" if name == "standings" else "woStrategy", "overrides": overrides})
        except Exception as exc:
            components[name] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}", provenance={"overrides": overrides})
    live_provider = providers.get("live_mc_history")
    if live_provider is None and not custom_providers_supplied:
        live_provider = _default_live_mc_history
    model_config_output = None
    model_config_error = None
    if pre_tyre and metadata.total_laps is not None:
        try:
            ensured = model_config_ensurer(
                season=metadata.season,
                round_number=metadata.round_number,
                data_root=config.data_root,
                session_names=metadata.session_names,
            )
            model_config_output = (
                ensured.to_dict() if hasattr(ensured, "to_dict") else dict(ensured)
            )
        except Exception as exc:
            model_config_error = f"{type(exc).__name__}: {exc}"
    if model_config_error is not None:
        components["live_mc_history"] = ComponentResult(
            "FAILED",
            error=f"Live-MC model-config generation failed: {model_config_error}",
            provenance={
                "owner": "woPlanner/woStrategy",
                "mode": config.live_replay.mode,
                "failure_isolated": True,
                "model_config": {
                    "status": "generation_failed",
                    "error": model_config_error,
                },
            },
        )
    elif live_provider is None:
        components["live_mc_history"] = ComponentResult(
            "FAILED", error="No public live_mc_history workflow adapter was supplied."
        )
    elif not pre_tyre:
        components["live_mc_history"] = ComponentResult(
            "FAILED", error="The saved pre-race tyre prediction is required for live replay."
        )
    elif metadata.total_laps is None:
        components["live_mc_history"] = ComponentResult(
            "FAILED", error="The scheduled race distance is required for replay completeness."
        )
    else:
        try:
            retro_component = components.get("retro_tyre_estimate")
            retro_output = (
                retro_component.output
                if retro_component and retro_component.status == "SUCCESS"
                and isinstance(retro_component.output, Mapping)
                else {}
            )
            live_output = live_provider(
                season=metadata.season,
                round_number=metadata.round_number,
                total_laps=metadata.total_laps,
                data_root=config.data_root,
                mode=config.live_replay.mode,
                pre_race_tyre=pre_tyre,
                retro_tyre_estimate=retro_output,
                run_path=run.path,
                session_names=metadata.session_names,
                ensured_model_config=model_config_output,
            )
            model_config_provenance = dict(
                dict(live_output.get("provenance") or {}).get("model_config") or {}
            ) if isinstance(live_output, Mapping) else {}
            components["live_mc_history"] = ComponentResult(
                "SUCCESS",
                _jsonable(live_output),
                provenance={
                    "owner": "woPlanner/woStrategy",
                    "mode": config.live_replay.mode,
                    "full_race_retro_is_separate": True,
                    "model_config": model_config_provenance,
                },
            )
        except Exception as exc:
            components["live_mc_history"] = ComponentResult(
                "FAILED",
                error=f"{type(exc).__name__}: {exc}",
                provenance={
                    "owner": "woPlanner/woStrategy",
                    "mode": config.live_replay.mode,
                    "failure_isolated": True,
                    "model_config": model_config_output or {
                        "status": "generation_failed_or_unavailable"
                    },
                },
            )
    retro = components.get("retro_tyre_estimate")
    if pre_tyre and retro and retro.status == "SUCCESS" and isinstance(retro.output, Mapping):
        comparison = compare_tyre_predictions(pre_tyre, retro.output)
        components["tyre_comparison"] = ComponentResult("SUCCESS", comparison)
    else:
        components["tyre_comparison"] = ComponentResult("FAILED", error="Saved pre-race and Retro MC tyre estimates are both required.")
    empirical = components.get("empirical_pit_loss")
    empirical_value = empirical.output if empirical and empirical.status == "SUCCESS" and isinstance(empirical.output, Mapping) else {}
    pit_resolution = resolve_retro_pit_loss(empirical=empirical_value, saved_pre_race_config=pre_config)
    components["pit_loss_resolution"] = ComponentResult("SUCCESS" if any(item["value"] is not None for item in pit_resolution.values()) else "FAILED", pit_resolution, error=None if any(item["value"] is not None for item in pit_resolution.values()) else "No empirical or saved pre-race pit loss is available.")
    if (
        retro and retro.status == "SUCCESS" and isinstance(retro.output, Mapping)
        and metadata.total_laps is not None
        and pit_resolution["GREEN"]["value"] is not None
    ):
        try:
            provider = providers.get("retro_green_optimum")
            if provider is not None:
                green = provider(
                    retro_tyre_estimate=retro.output,
                    total_laps=metadata.total_laps,
                    green_pit_loss=pit_resolution["GREEN"]["value"],
                )
            else:
                from wostrategy.analysis.retro_strategy import calculate_retro_green_optimum
                green = calculate_retro_green_optimum(
                    retro_tyre_estimate=retro.output,
                    total_laps=metadata.total_laps,
                    green_pit_loss=pit_resolution["GREEN"]["value"],
                )
            if isinstance(green, Mapping):
                green = dict(green)
                green["retro_provenance"] = dict(retro.output.get("provenance", {}))
            components["retro_green_optimum"] = ComponentResult("SUCCESS", _jsonable(green), provenance={"owner": "woStrategy", "uses_actual_event_timeline": False})
        except Exception as exc:
            components["retro_green_optimum"] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}")
    else:
        components["retro_green_optimum"] = ComponentResult("FAILED", error="Retro tyre estimate, race distance, and GREEN pit loss are required.")
    components["event_aware_hindsight_optimum"] = ComponentResult(
        "UNAVAILABLE",
        error="Deterministic SC/VSC counterfactual API is not yet extracted from woPlanner.",
        provenance={"substitute_used": False},
    )
    for name, item in components.items():
        if item.status == "SUCCESS":
            try:
                run.save_json(name, item.output)
            except (TypeError, ValueError):
                item.warnings.append("Output was not JSON serializable and remains manifest-only.")
    manifest = finish_run(run, components)
    context = {
        "warnings": manifest["warnings"],
        "fallbacks": pit_resolution,
        "pre_race_recomputed": False,
        "event_metadata": metadata.to_dict(),
        "tyre_estimate_flow": [
            "Pre-race prediction",
            "Historical live MC evolution",
            "Final full-race Retro MC",
        ],
        "full_race_retro_is_separate_from_live_replay": True,
    }
    run.save_json("report_context", context)
    figures = tuple((run.path / "figures").glob("*")) if (run.path / "figures").exists() else ()
    create_report_bundle(run_path=run.path, workflow=run.workflow, event=metadata.event_id, context=context, results={name: item.to_dict() for name, item in components.items()}, figures=figures, language=config.report.language, template=config.report.template)
    return run


def _default_live_mc_history(
    *, season: int, round_number: int, total_laps: int, data_root, mode: str,
    pre_race_tyre, retro_tyre_estimate, run_path, session_names=None,
    ensured_model_config=None, **_kwargs,
):
    from .live_mc_replay import reconstruct_live_mc_history

    return reconstruct_live_mc_history(
        year=season,
        round_number=round_number,
        total_laps=total_laps,
        data_root=data_root,
        mode=mode,
        pre_race_tyre=pre_race_tyre,
        retro_tyre_estimate=retro_tyre_estimate,
        figures_dir=Path(run_path) / "figures",
        session_names=None if session_names is None else tuple(session_names),
        ensured_model_config=ensured_model_config,
    )


def _default_standings(run_path, *, season: int, round_number: int):
    from wostanding.script.points_progression import run_points_progression

    result, figures = run_points_progression(
        year=season, race_range=[1, round_number], include_projection=True,
        output_path=run_path / "figures" / "standings.png", output_format="png", show=False,
    )
    return {
        "drivers": {
            "actual": result.driver_points.to_dict("records"),
            "actual_plus_projected": figures["drivers"][2].to_dict("records"),
        },
        "constructors": {
            "actual": result.team_points.to_dict("records"),
            "actual_plus_projected": figures["teams"][2].to_dict("records"),
        },
        "method": "woStanding existing points progression and straight-line projection",
    }


def _default_qualifying_performance(*, season: int, round_number: int, **overrides):
    from wostrategy.script.quali_performance_tracker import run_quali_performance_tracker

    settings = dict(overrides)
    settings.pop("target_team", None)
    result = run_quali_performance_tracker(year=season, race=round_number, **settings)
    if result == "Wet":
        return {"status": "Wet", "relative_team_performance": []}
    return {
        "relative_team_performance": result.quickest_teams.to_dict("records"),
        "track_evolution": {
            "fit_model": result.evolution_fit_model,
            "rate": result.evolution_rate_seconds_per_lap,
            "x_column": result.track_evolution_x_column,
            "unit": result.track_evolution_slope_unit,
        },
        "method": "woStrategy existing qualifying performance review",
    }


def _default_qualifying_performance_tracker(
    run_path: str | Path,
    *,
    season: int,
    round_number: int,
    **overrides,
):
    from wostrategy.script.quali_performance_tracker import (
        SCRIPT_CONFIG,
        plot_quali_performance_range,
    )

    settings = dict(overrides)
    target_team = str(settings.pop("target_team", SCRIPT_CONFIG["target_team"]))
    output_path = Path(run_path) / "figures" / "qualifying_performance_tracker.png"
    summary, figures = plot_quali_performance_range(
        year=season,
        race_start=1,
        race_end=round_number,
        target_team=target_team,
        output_path=output_path,
        **settings,
    )
    _close_figures(figures)
    included = sorted(int(value) for value in summary["Race"].unique())
    requested = list(range(1, round_number + 1))
    figure_paths = sorted(
        str(path.relative_to(run_path))
        for path in output_path.parent.glob(f"{output_path.stem}_*.png")
    )
    return {
        "season": season,
        "race_range": [1, round_number],
        "requested_rounds": requested,
        "included_rounds": included,
        "unavailable_rounds": sorted(set(requested).difference(included)),
        "target_team": target_team,
        "relative_team_performance": summary.to_dict("records"),
        "figures": figure_paths,
        "method": "wostrategy.script.quali_performance_tracker.plot_quali_performance_range",
    }


def _default_race_performance_tracker(
    run_path: str | Path,
    *,
    season: int,
    round_number: int,
    data_root: str | Path | None,
    **overrides,
):
    from wostrategy.analysis.post_race_review import run_race_performance_review_adapter
    from wostrategy.script.race_performance_review import (
        SCRIPT_CONFIG,
        plot_race_performance_results,
    )

    settings = dict(overrides)
    reference_team = str(settings.pop("reference_team", SCRIPT_CONFIG["reference_team"]))
    uncertainty = bool(settings.pop("plot_uncertainty_band", SCRIPT_CONFIG["plot_uncertainty_band"]))
    rmse_background = bool(settings.pop("plot_rmse_background", SCRIPT_CONFIG["plot_rmse_background"]))
    races = list(range(1, round_number + 1))
    result = run_race_performance_review_adapter(
        season=season,
        races=races,
        data_root=data_root,
        overrides=settings,
        use_cached_monte_carlo=True,
    )
    output_path = Path(run_path) / "figures" / "race_performance_tracker.png"
    summary, figures = plot_race_performance_results(
        team_baseline_summaries=result["team_baseline_summaries"],
        team_baseline_samples=result["team_baseline_samples"],
        race_event_names=result["race_event_names"],
        race_sample_diagnostics=result["race_sample_diagnostics"],
        year=season,
        reference_team=reference_team,
        output_path=output_path,
        plot_uncertainty_band=uncertainty,
        plot_rmse_background=rmse_background,
    )
    _close_figures(figures)
    included = sorted(int(value) for value in summary["Race"].unique())
    return {
        "season": season,
        "race_range": [1, round_number],
        "requested_rounds": races,
        "included_rounds": included,
        "unavailable_rounds": sorted(set(races).difference(included)),
        "reference_team": reference_team,
        "relative_team_performance": summary.to_dict("records"),
        "figures": [
            str(path.relative_to(run_path))
            for path in sorted(output_path.parent.glob(f"{output_path.stem}_*.png"))
        ],
        "method": "wostrategy.script.race_performance_review.run_race_performance_review + plot_race_performance_results",
    }


def _validate_retro_mc_output(output: Any) -> None:
    if not isinstance(output, Mapping):
        raise ValueError("Canonical Retro MC output is not an object.")
    compounds = output.get("compounds")
    if not isinstance(compounds, Mapping) or not compounds:
        raise ValueError("Canonical Retro MC produced no compound estimates.")
    numerical = [
        values.get(field)
        for values in compounds.values()
        if isinstance(values, Mapping)
        for field in ("performance_delta_to_medium", "degradation_seconds_per_lap")
    ]
    if not any(isinstance(value, (int, float)) for value in numerical):
        raise ValueError("Canonical Retro MC compound estimates contain no numerical output.")
    quality = output.get("quality")
    if not isinstance(quality, list) or not quality:
        raise ValueError("Canonical Retro MC produced no RMSE/model-quality diagnostics.")
    provenance = output.get("provenance")
    execution = provenance.get("execution") if isinstance(provenance, Mapping) else None
    if not isinstance(execution, Mapping) or execution.get("monte_carlo_executed") is not True:
        raise ValueError("Retro result does not prove that canonical Monte Carlo executed in this Post run.")


def _close_figures(figures: Mapping[str, tuple[Any, Any]]) -> None:
    import matplotlib.pyplot as plt

    for figure, _ in figures.values():
        plt.close(figure)


def _jsonable(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "to_dict"):
        try:
            return value.to_dict("records")
        except TypeError:
            return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)
