from pathlib import Path

from wodata.events import EventMetadata
from wodata.weekend_runs import WeekendRunStore

from woweekend.config.post_race import PostRaceConfig
from woweekend.workflows.post_race import (
    compare_tyre_predictions,
    resolve_retro_pit_loss,
    run_post_race,
)


def test_comparison_is_precomputed() -> None:
    before = {"compounds": {"MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.1}}}
    after = {"compounds": {"MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.12}}}
    row = compare_tyre_predictions(before, after)["compounds"][1]
    assert row["degradation"]["prediction_minus_retro"] == -0.01999999999999999


def test_pit_loss_uses_median_then_saved_fallback() -> None:
    output = resolve_retro_pit_loss(empirical={"normal": {"sample_count": 2, "total": 20.8, "pit_in_s3": 10.5, "pit_out_s1": 10.3}}, saved_pre_race_config={"pit_loss": {"green": {"pit_in_s3": 10, "pit_out_s1": 10}, "sc_vsc": {"pit_in_s3": 5.8, "pit_out_s1": 5.5}}})
    assert output["GREEN"]["source"] == "race_empirical_median"
    assert output["SC/VSC"]["source"] == "pre_race_fallback"


def _event(*args, **kwargs):
    return EventMetadata(
        event_id="2026-03", season=2026, round_number=3,
        event_name="Test Grand Prix", official_event_name=None,
        country=None, location=None, event_format="conventional", is_sprint=False,
        session_names=("Qualifying", "Race"), race_start="2026-03-01T12:00:00+00:00",
        total_laps=6, total_laps_source="test",
    )


def _pre_race_run(root: Path):
    run = WeekendRunStore.create(
        event="2026-03", workflow="race_preparation", data_root=root,
        generated_at="2026-02-28T12:00:00+00:00",
    )
    run.save_json("tyre_prediction", {
        "compounds": {
            "SOFT": {"performance_delta_to_medium": -0.4, "degradation_seconds_per_lap": 0.2},
            "MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.1},
            "HARD": {"performance_delta_to_medium": 0.3, "degradation_seconds_per_lap": 0.05},
        }
    })
    run.save_json("config.resolved", {"pit_loss": {"green": {"total": 20.0}}})
    run.save_manifest({"status": "SUCCESS"})
    return run


def _retro():
    return {
        "compounds": {
            "SOFT": {"performance_delta_to_medium": -0.5, "degradation_seconds_per_lap": 0.21},
            "MEDIUM": {"performance_delta_to_medium": 0.0, "degradation_seconds_per_lap": 0.11},
            "HARD": {"performance_delta_to_medium": 0.4, "degradation_seconds_per_lap": 0.06},
        },
        "quality": [{"WeightedRMSESeconds": 0.4, "EffectiveSampleSize": 12.0}],
        "provenance": {
            "artifact_directory": "test-retro",
            "execution": {"monte_carlo_executed": True, "executed_rounds": [3]},
        },
    }


def test_post_dependencies_share_fresh_retro_and_trackers_cover_season(tmp_path: Path) -> None:
    pre = _pre_race_run(tmp_path)
    raw = PostRaceConfig.template()
    raw.update({"data_root": str(tmp_path), "pre_race_run_id": pre.run_id})
    retro = _retro()
    green_inputs = []
    def live_history(**kwargs):
        figures = Path(kwargs["run_path"]) / "figures"
        figures.mkdir(parents=True, exist_ok=True)
        for name in ("live_mc_degradation_evolution.png", "live_mc_model_quality_evolution.png"):
            (figures / name).write_bytes(b"png")
        return {
            "mode": kwargs["mode"],
            "updates": [{"leader_lap": 3}, {"leader_lap": 6}],
            "replay": {"complete": True, "final_leader_lap": 6},
        }
    providers = {
        "retro_tyre_estimate": lambda **kwargs: retro,
        "standings": lambda **kwargs: {},
        "qualifying_performance": lambda **kwargs: {},
        "qualifying_performance_tracker": lambda **kwargs: {
            "race_range": [1, 3], "requested_rounds": [1, 2, 3],
            "included_rounds": [1, 2, 3], "relative_team_performance": [{"Race": 1}],
        },
        "race_performance": lambda **kwargs: {},
        "race_performance_tracker": lambda **kwargs: {
            "race_range": [1, 3], "requested_rounds": [1, 2, 3],
            "included_rounds": [1, 2, 3], "relative_team_performance": [{"Race": 1}],
        },
        "empirical_pit_loss": lambda **kwargs: {
            "normal": {"sample_count": 2, "total": 19.0, "pit_in_s3": 9.0, "pit_out_s1": 10.0}
        },
        "retro_green_optimum": lambda **kwargs: green_inputs.append(kwargs["retro_tyre_estimate"]) or {"strategies": [{"rank": 1}]},
        "live_mc_history": live_history,
    }
    run = run_post_race(
        PostRaceConfig.parse(raw), event="2026-03", input_config=raw,
        providers=providers, event_resolver=_event,
    )
    persisted_retro = run.load_json("retro_tyre_estimate")
    comparison = run.load_json("tyre_comparison")
    optimum = run.load_json("retro_green_optimum")
    assert persisted_retro["quality"][0]["WeightedRMSESeconds"] == 0.4
    assert comparison["retro_provenance"] == persisted_retro["provenance"]
    assert optimum["retro_provenance"] == persisted_retro["provenance"]
    assert green_inputs == [persisted_retro]
    assert run.load_json("qualifying_performance_tracker")["requested_rounds"] == [1, 2, 3]
    assert run.load_json("race_performance_tracker")["requested_rounds"] == [1, 2, 3]
    assert run.load_json("live_mc_history")["mode"] == "algorithm_only"
    bundle = next(run.path.glob("report_bundle.*"))
    assert (bundle / "figures" / "live_mc_degradation_evolution.png").exists()
    assert (bundle / "figures" / "live_mc_model_quality_evolution.png").exists()
    assert sum(path.is_file() for path in bundle.rglob("*")) <= 20


def test_retro_failure_blocks_comparison_and_green_optimum(tmp_path: Path) -> None:
    pre = _pre_race_run(tmp_path)
    raw = PostRaceConfig.template()
    raw.update({"data_root": str(tmp_path), "pre_race_run_id": pre.run_id})
    green_called = []
    providers = {
        "retro_tyre_estimate": lambda **kwargs: {},
        "standings": lambda **kwargs: {},
        "qualifying_performance": lambda **kwargs: {},
        "qualifying_performance_tracker": lambda **kwargs: {},
        "race_performance": lambda **kwargs: {},
        "race_performance_tracker": lambda **kwargs: {},
        "empirical_pit_loss": lambda **kwargs: {},
        "retro_green_optimum": lambda **kwargs: green_called.append(True),
    }
    run = run_post_race(
        PostRaceConfig.parse(raw), event="2026-03", input_config=raw,
        providers=providers, event_resolver=_event,
    )
    results = run.load_json("analysis_results")
    assert results["retro_tyre_estimate"]["status"] == "FAILED"
    assert results["tyre_comparison"]["status"] == "FAILED"
    assert results["retro_green_optimum"]["status"] == "FAILED"
    assert green_called == []
