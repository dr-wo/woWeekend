from pathlib import Path

from wodata.events import EventMetadata
from wostrategy.model.tyre_prediction import PreRaceTyrePrediction, TyreCompoundPrediction
from wodata.artifacts import write_weekend_model_config
from wostrategy.analysis.pre_race_model_config import ensure_pre_race_model_config
from woweekend.config.race import RaceConfig
from woweekend.workflows.race import run_race


def _tyres(**kwargs):
    return PreRaceTyrePrediction(
        event="event", season=2026, round_number=1, reference_compound="MEDIUM",
        compounds={
            "SOFT": TyreCompoundPrediction(-0.4, 0.2, 0.1, 0.03),
            "MEDIUM": TyreCompoundPrediction(0.0, 0.1, 0.1, 0.02),
            "HARD": TyreCompoundPrediction(0.3, 0.05, 0.1, 0.01),
        }, generated_at="2026-01-01T00:00:00+00:00", provider="test",
        artifact_id="test-artifact",
    )


def _ensure(**kwargs):
    return {
        "status": "reused",
        "path": str(Path(kwargs["data_root"]) / "model_config.json"),
        "producer_api": "test",
    }


def _event(*args, **kwargs):
    return EventMetadata(
        "2026-07", 2026, 7, "Test Grand Prix", None, None, None,
        "conventional", False, ("Practice 1", "Qualifying", "Race"),
        "2026-01-02T12:00:00+00:00", 6, "test",
    )


def _distance_event(*, scheduled=None, session=None):
    return EventMetadata(
        event_id="2026-07", season=2026, round_number=7,
        event_name="Test Grand Prix", official_event_name=None,
        country=None, location=None, event_format="conventional", is_sprint=False,
        session_names=("Practice 1", "Qualifying", "Race"),
        race_start="2026-01-02T12:00:00+00:00",
        total_laps=scheduled if scheduled is not None else session,
        total_laps_source=(
            "event_metadata" if scheduled is not None
            else "fastf1_session" if session is not None else None
        ),
        scheduled_race_laps=scheduled,
        scheduled_race_laps_source=("test_metadata" if scheduled is not None else None),
        session_total_laps=session,
        session_total_laps_source=("test_session" if session is not None else None),
    )


def test_race_produces_both_strategy_modes_and_cutoffs(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "result_count": 2, "data_root": str(tmp_path)})
    raw["degradation_cutoff"] = {"scan_min": 0.05, "scan_max": 0.15, "coarse_step": 0.05, "refine_tolerance": 0.01}
    run = run_race(RaceConfig.parse(raw), event="2026-07", input_config=raw, tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event)
    assert run.load_json("strategy_unrestricted")
    compliant = run.load_json("strategy_rules_compliant")
    assert compliant and len(set(compliant[0]["compounds"])) >= 2
    assert "primary_1_to_2" in run.load_json("cutoff_rules_compliant")


def test_race_preparation_generates_missing_live_model_config(tmp_path: Path) -> None:
    def producer(values):
        write_weekend_model_config(
            {
                "season": 2026,
                "round_number": 7,
                "source_sessions": ["FP1"],
                "sample_count": 100,
                "random_seed": 42,
                "fuel_rate_bounds": [0.0, .1],
                "track_rate_bounds": [-.05, .05],
                "default_degradation_bounds": [0.0, .2],
                "default_compound_delta_bounds": [-1.0, 1.0],
                "reference_compound": "MEDIUM",
                "clean_lap_noise_sigma": .35,
            },
            year=2026,
            round_number=7,
            data_root=tmp_path,
        )
        return 0

    def ensure(**kwargs):
        return ensure_pre_race_model_config(**kwargs, runner=producer)

    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    run = run_race(
        RaceConfig.parse(raw),
        event="2026-07",
        input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=ensure,
        event_resolver=_event,
    )

    prerequisite = run.load_json("live_mc_model_config")
    assert prerequisite["status"] == "generated"
    assert Path(prerequisite["path"]).is_file()


def test_missing_manual_pit_loss_is_actionable(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    raw["pit_loss"]["green"] = {"pit_in_s3": None, "pit_out_s1": None}
    run = run_race(RaceConfig.parse(raw), event="2026-07", input_config=raw, tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event)
    manifest = run.load_json("manifest")
    assert manifest["status"] == "FAILED"
    assert "provide either one total value" in manifest["warnings"][0]


def test_complete_manual_tyre_fallback_does_not_claim_automatic_values(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    for compound, performance, degradation in (
        ("SOFT", -0.4, 0.2), ("MEDIUM", 0.0, 0.1), ("HARD", 0.3, 0.05),
    ):
        raw["tyre_prediction"]["manual_override"][compound] = {
            "performance_delta_to_medium": performance,
            "degradation_seconds_per_lap": degradation,
        }

    def missing(**kwargs):
        raise FileNotFoundError("no current cache")

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=missing, model_config_ensurer=_ensure, event_resolver=_event,
    )
    effective = run.load_json("tyre_prediction.effective")
    assert effective["automatic_artifact_id"] is None
    assert effective["compounds"]["SOFT"]["performance_delta_to_medium"] == {
        "automatic_value": None,
        "effective_value": -0.4,
        "source": "manual_override",
        "uncertainty": None,
    }


def test_scheduled_event_laps_are_used_and_recorded(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw["data_root"] = str(tmp_path)
    raw["degradation_cutoff"] = {
        "scan_min": 0.05, "scan_max": 0.15,
        "coarse_step": 0.05, "refine_tolerance": 0.01,
    }
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(scheduled=6),
    )
    resolved = run.load_json("config.resolved")
    assert resolved["total_laps"] is None
    assert resolved["resolved_total_laps"] == 6
    assert resolved["total_laps_source"] == "event_metadata"
    distance = run.load_json("race_distance")
    assert distance["source"] == "event_metadata"
    assert distance["source_detail"] == "test_metadata"
    assert distance["total_laps"] == 6


def test_manual_race_laps_override_event_metadata(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw["data_root"] = str(tmp_path)
    raw["race"]["total_laps"] = 5
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(scheduled=6),
    )
    distance = run.load_json("race_distance")
    assert distance["source"] == "manual_override"
    assert distance["source_detail"] == "race.total_laps"
    assert distance["total_laps"] == 5


def test_session_laps_are_used_after_scheduled_metadata(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw["data_root"] = str(tmp_path)
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(session=6),
    )
    distance = run.load_json("race_distance")
    assert distance["source"] == "fastf1_session"
    assert distance["source_detail"] == "test_session"
    assert distance["total_laps"] == 6


def test_missing_all_race_lap_sources_fails_without_mutating_prior_run(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw["data_root"] = str(tmp_path)
    failed = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(),
    )
    before = (failed.path / "manifest.json").read_bytes()
    assert failed.load_json("manifest")["status"] == "FAILED"
    assert "canonical scheduled_race_laps" in failed.load_json("manifest")["warnings"][0]
    assert not (failed.path / "strategy_unrestricted.json").exists()

    recovered = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(scheduled=6),
    )
    assert recovered.run_id != failed.run_id
    assert (failed.path / "manifest.json").read_bytes() == before
