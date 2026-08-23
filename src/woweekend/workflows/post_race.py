from __future__ import annotations

from dataclasses import asdict
from typing import Any, Callable, Mapping

from wodata.weekend_runs import WeekendRunStore

from woweekend.artifacts.models import ComponentResult
from woweekend.config.common import resolved_dict
from woweekend.config.post_race import PostRaceConfig
from woweekend.reports.bundle import create_report_bundle
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
    return {"reference_compound": "MEDIUM", "retro_is_estimate_not_ground_truth": True, "compounds": rows}


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


def run_post_race(config: PostRaceConfig, *, event: str, input_config: dict[str, Any] | None = None, providers: Mapping[str, Callable[..., Any]] | None = None, event_resolver=None):
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
    def review(**kwargs):
        if not review_cache:
            from wostrategy.analysis.post_race_review import get_post_race_review
            review_cache.update(get_post_race_review(
                season=metadata.season, round_number=metadata.round_number,
                data_root=config.data_root,
                overrides=kwargs.get("overrides", {}),
            ))
        return review_cache
    providers.setdefault("standings", lambda **kwargs: _default_standings(run.path, **kwargs))
    providers.setdefault("qualifying_performance", _default_qualifying_performance)
    providers.setdefault("race_performance", lambda season, round_number, **kwargs: review(overrides=kwargs)["race_performance"])
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
    calls = {
        "standings": ({"season": metadata.season, "round_number": metadata.round_number}, {}),
        "qualifying_performance": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.qualifying_performance.overrides)),
        "race_performance": ({"season": metadata.season, "round_number": metadata.round_number}, dict(config.race_performance.overrides)),
        "retro_tyre_estimate": ({"season": metadata.season, "round_number": metadata.round_number}, {}),
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
            components[name] = ComponentResult("SUCCESS", output, provenance={"owner": "woStanding" if name == "standings" else "woStrategy", "overrides": overrides})
        except Exception as exc:
            components[name] = ComponentResult("FAILED", error=f"{type(exc).__name__}: {exc}", provenance={"overrides": overrides})
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
    context = {"warnings": manifest["warnings"], "fallbacks": pit_resolution, "pre_race_recomputed": False, "event_metadata": metadata.to_dict()}
    run.save_json("report_context", context)
    figures = tuple((run.path / "figures").glob("*")) if (run.path / "figures").exists() else ()
    create_report_bundle(run_path=run.path, workflow=run.workflow, event=metadata.event_id, context=context, results={name: item.to_dict() for name, item in components.items()}, figures=figures, language=config.report.language, template=config.report.template)
    return run


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

    result = run_quali_performance_tracker(year=season, race=round_number, **overrides)
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
