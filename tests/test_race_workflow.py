import json
from pathlib import Path

import pytest

from wodata.events import EventMetadata
from wodata.event_operational import (
    find_event_operational_inputs_by_identity,
    read_event_operational_inputs,
    write_event_operational_inputs,
)
from wostrategy.model.tyre_prediction import (
    RELATIVE_PERFORMANCE_FRAME,
    PreRaceTyrePrediction,
    TyreCompoundPrediction,
    canonicalize_operational_tyre_prediction,
)
from wodata.artifacts import write_weekend_model_config
from wostrategy.analysis.pre_race_model_config import ensure_pre_race_model_config
from woweekend.config.race import RaceConfig
from woweekend.workflows.race import (
    RacePreparationPrerequisiteError,
    _load_previous_season_empirical_vsc,
    _load_previous_season_operational_inputs,
    _report_temperature_context,
    _source_prediction_with_overrides,
    run_race,
)
from wodata.weekend_runs import WeekendRunStore


def _tyres(**kwargs):
    return PreRaceTyrePrediction(
        event=f"{kwargs.get('season', 2026):04d}-{kwargs.get('round_number', 7):02d}",
        season=kwargs.get("season", 2026),
        round_number=kwargs.get("round_number", 7),
        reference_compound="MEDIUM",
        compounds={
            "SOFT": TyreCompoundPrediction(-0.4, 0.2, 0.1, 0.03),
            "MEDIUM": TyreCompoundPrediction(0.0, 0.1, 0.1, 0.02),
            "HARD": TyreCompoundPrediction(0.3, 0.05, 0.1, 0.01),
        }, generated_at="2026-01-01T00:00:00+00:00", provider="test",
        artifact_id="test-artifact",
    )


def _arbitrary_baseline_tyres(**kwargs):
    tyre = _tyres(**kwargs)
    return PreRaceTyrePrediction(
        event=tyre.event,
        season=tyre.season,
        round_number=tyre.round_number,
        reference_compound="MEDIUM",
        compounds={
            "SOFT": TyreCompoundPrediction(-0.2, 0.2),
            "MEDIUM": TyreCompoundPrediction(0.3, 0.1),
            "HARD": TyreCompoundPrediction(0.8, 0.05),
        },
        generated_at=tyre.generated_at,
        provider="test",
        artifact_id="arbitrary-baseline",
    )


def _hard_reference_tyres(**kwargs):
    tyre = _tyres(**kwargs)
    return PreRaceTyrePrediction(
        event=tyre.event, season=tyre.season, round_number=tyre.round_number,
        reference_compound="HARD",
        compounds={
            "SOFT": TyreCompoundPrediction(-.5, .2),
            "MEDIUM": TyreCompoundPrediction(-.2, .1),
            "HARD": TyreCompoundPrediction(0.0, .05),
        },
        generated_at=tyre.generated_at, provider="test",
        artifact_id="hard-reference", performance_frame=RELATIVE_PERFORMANCE_FRAME,
        reference_declared=True, formal_relative_output=True,
    )


def _medium_reference_tyres(**kwargs):
    tyre = _tyres(**kwargs)
    return PreRaceTyrePrediction(
        event=tyre.event, season=tyre.season, round_number=tyre.round_number,
        reference_compound="MEDIUM",
        compounds={
            "SOFT": TyreCompoundPrediction(-.3, .2),
            "MEDIUM": TyreCompoundPrediction(0.0, .1),
            "HARD": TyreCompoundPrediction(.3, .05),
        },
        generated_at=tyre.generated_at, provider="test",
        artifact_id="medium-reference", performance_frame=RELATIVE_PERFORMANCE_FRAME,
        reference_declared=True, formal_relative_output=True,
    )


def _ensure(**kwargs):
    return {
        "status": "reused",
        "path": str(Path(kwargs["data_root"]) / "model_config.json"),
        "producer_api": "test",
    }


def _write_canonical_tyre_prediction(root: Path) -> None:
    path = (
        root / "wostrategy/tyre_prediction/schema_v1/year=2026/predictions/"
        "round=7/prediction.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "season": 2026,
        "round": 7,
        "event": "Test Grand Prix",
        "prediction_timestamp": "2026-01-01T00:00:00+00:00",
        "model": "test",
        "prediction_fingerprint": "canonical-test-artifact",
        "compound_allocation": {"SOFT": "C5", "MEDIUM": "C4", "HARD": "C3"},
        "compounds": {
            "C5": {"compound_role": "SOFT", "performance_mean": -0.4,
                   "performance_std": 0.1, "degradation_mean": 0.2,
                   "degradation_std": 0.03},
            "C4": {"compound_role": "MEDIUM", "performance_mean": 0.0,
                   "performance_std": 0.1, "degradation_mean": 0.1,
                   "degradation_std": 0.02},
            "C3": {"compound_role": "HARD", "performance_mean": 0.3,
                   "performance_std": 0.1, "degradation_mean": 0.05,
                   "degradation_std": 0.01},
        },
    }))


def _event(*args, **kwargs):
    return EventMetadata(
        "2026-07", 2026, 7, "Test Grand Prix", None, "Testland", "Test Circuit",
        "conventional", False, ("Practice 1", "Qualifying", "Race"),
        "2026-01-02T12:00:00+00:00", 6, "test",
    )


def _distance_event(*, scheduled=None, session=None):
    return EventMetadata(
        event_id="2026-07", season=2026, round_number=7,
        event_name="Test Grand Prix", official_event_name=None,
        country="Testland", location="Test Circuit",
        event_format="conventional", is_sprint=False,
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


def test_race_produces_both_strategy_modes_and_cutoffs(tmp_path: Path, monkeypatch) -> None:
    import woweekend.reports.modelled_undercut as presentation
    import woweekend.workflows.race as workflow
    seen = []
    original_save = presentation.save_modelled_undercut
    original_search = workflow.search_best_compound_sequences
    def save(run, model, max_age):
        seen.append(model)
        return original_save(run, model, max_age)
    def search(state, model, *args, **kwargs):
        assert model is seen[0]
        return original_search(state, model, *args, **kwargs)
    monkeypatch.setattr(presentation, "save_modelled_undercut", save)
    monkeypatch.setattr(workflow, "search_best_compound_sequences", search)
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "result_count": 2, "data_root": str(tmp_path)})
    raw["degradation_cutoff"] = {"scan_min": 0.05, "scan_max": 0.15, "coarse_step": 0.05, "refine_tolerance": 0.01}
    run = run_race(RaceConfig.parse(raw), event="2026-07", input_config=raw, tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event)
    assert run.load_json("strategy_unrestricted")
    compliant = run.load_json("strategy_rules_compliant")
    assert compliant and len(set(compliant[0]["compounds"])) >= 2
    unrestricted = run.load_json("cutoff_unrestricted")
    compliant_cutoff = run.load_json("cutoff_rules_compliant")
    assert "primary_0_to_1" in unrestricted
    assert unrestricted["cutoff_table"][0]["display_label"] == "0→1"
    assert "primary_1_to_2" in compliant_cutoff
    assert compliant_cutoff["primary_0_to_1"] is None
    assert compliant_cutoff["primary_0_to_1_status"] == "unavailable_rules_compliant"
    assert compliant_cutoff["cutoff_table"][0]["cutoff_medium_degradation"] is None
    assert unrestricted["initial_scan_max"] == unrestricted["effective_scan_max"] == 0.15
    bundled = json.loads((run.path / "report_bundle.zh-CN.engineering/analysis_results.json").read_text())
    assert bundled["cutoff_unrestricted"]["output"]["cutoff_table"] == unrestricted["cutoff_table"]
    assert compliant_cutoff["cutoff_table"][0]["coverage"]["status"] == "unavailable_rules_compliant"
    undercut = run.load_json("modelled_undercut_power")
    assert len(undercut["points"]) == 9 * 7
    assert undercut["model"]["degradation"] == dict(seen[0].degradation)
    assert (run.path / "figures/modelled_undercut_power.png").exists()
    assert (run.path / "report_bundle.zh-CN.engineering/MODELLED_UNDERCUT_POWER.md").exists()


def test_missing_tyre_prediction_fails_before_run_or_downstream_work(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    downstream_calls = []

    def ensure(**kwargs):
        downstream_calls.append(kwargs)
        return _ensure(**kwargs)

    with pytest.raises(RacePreparationPrerequisiteError) as caught:
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            model_config_ensurer=ensure, event_resolver=_event,
        )

    message = str(caught.value)
    assert "Test Grand Prix (2026-07, round 7)" in message
    assert "canonical pre-race tyre prediction" in message
    assert "wostrategy.script.tyre_prediction_pipeline --year 2026" in message
    assert "complete-manual fallback" in message
    assert downstream_calls == []
    assert not list(tmp_path.rglob("manifest.json"))
    assert not list(tmp_path.rglob("report_bundle.*"))


def test_invalid_tyre_fails_before_race_distance_enrichment(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})
    resolver_calls = []

    def resolver(*args, include_race_distance, **kwargs):
        resolver_calls.append(include_race_distance)
        if include_race_distance:
            raise AssertionError("race-distance enrichment must not run")
        return _distance_event()

    def incomplete(**kwargs):
        return PreRaceTyrePrediction(
            event="2026-07", season=2026, round_number=7,
            reference_compound="MEDIUM",
            compounds={"MEDIUM": TyreCompoundPrediction(0.0, 0.1)},
            generated_at="now", provider="test", artifact_id="incomplete",
        )

    with pytest.raises(RacePreparationPrerequisiteError, match="SOFT, HARD"):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            tyre_provider=incomplete, model_config_ensurer=_ensure,
            event_resolver=resolver,
        )

    assert resolver_calls == [False]
    assert not list(tmp_path.rglob("manifest.json"))


def test_medium_only_canonical_artifact_is_rejected_before_run(tmp_path: Path) -> None:
    _write_canonical_tyre_prediction(tmp_path)
    path = (
        tmp_path / "wostrategy/tyre_prediction/schema_v1/year=2026/predictions/"
        "round=7/prediction.json"
    )
    payload = json.loads(path.read_text())
    payload["compound_allocation"] = {"MEDIUM": "C4"}
    payload["compounds"] = {"C4": payload["compounds"]["C4"]}
    path.write_text(json.dumps(payload))
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})

    with pytest.raises(RacePreparationPrerequisiteError, match="SOFT, HARD"):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            model_config_ensurer=_ensure, event_resolver=_event,
        )

    assert not list(tmp_path.rglob("manifest.json"))


@pytest.mark.parametrize(
    ("compounds", "reason"),
    [
        ({"MEDIUM": TyreCompoundPrediction(0.0, 0.1)}, "SOFT, HARD"),
        ({"MEDIUM": TyreCompoundPrediction(0.0, 0.1),
          "HARD": TyreCompoundPrediction(0.3, 0.05)}, "SOFT"),
        ({"SOFT": TyreCompoundPrediction(-0.3, 0.2),
          "MEDIUM": TyreCompoundPrediction(0.0, 0.1)}, "HARD"),
        ({"SOFT": TyreCompoundPrediction(float("nan"), 0.2),
          "MEDIUM": TyreCompoundPrediction(0.0, 0.1),
          "HARD": TyreCompoundPrediction(0.3, 0.05)}, "must be finite"),
        ({"SOFT": TyreCompoundPrediction(-0.3, 0.2),
          "MEDIUM": TyreCompoundPrediction(0.0, float("inf")),
          "HARD": TyreCompoundPrediction(0.3, 0.05)}, "must be finite"),
        ({"SOFT": TyreCompoundPrediction(-0.3, 0.2),
          "MEDIUM": TyreCompoundPrediction(0.0, 0.1),
          "HARD": TyreCompoundPrediction(0.3, -0.05)}, "cannot be negative"),
    ],
)
def test_unusable_canonical_input_fails_before_run(
    tmp_path: Path, compounds, reason,
) -> None:
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})

    def provider(**kwargs):
        return PreRaceTyrePrediction(
            event="2026-07", season=2026, round_number=7,
            reference_compound="MEDIUM", compounds=compounds,
            generated_at="now", provider="test", artifact_id="invalid",
        )

    with pytest.raises(RacePreparationPrerequisiteError, match=reason):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            tyre_provider=provider, model_config_ensurer=_ensure,
            event_resolver=_event,
        )
    assert not list(tmp_path.rglob("manifest.json"))


def test_wrong_event_tyre_input_is_prerequisite_failure(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})

    def wrong_event(**kwargs):
        tyre = _tyres(**kwargs)
        return PreRaceTyrePrediction(
            event="2025-01", season=2025, round_number=1,
            reference_compound=tyre.reference_compound,
            compounds=tyre.compounds, generated_at=tyre.generated_at,
            provider=tyre.provider, artifact_id=tyre.artifact_id,
        )

    with pytest.raises(RacePreparationPrerequisiteError, match="season 2025"):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            tyre_provider=wrong_event, model_config_ensurer=_ensure,
            event_resolver=_event,
        )
    assert not list(tmp_path.rglob("manifest.json"))


def test_arbitrary_canonical_baseline_is_normalized_once_downstream(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_arbitrary_baseline_tyres, model_config_ensurer=_ensure,
        event_resolver=_event,
    )
    effective = run.load_json("tyre_prediction.effective")["compounds"]
    values = {
        compound: row["performance_delta_to_medium"]["effective_value"]
        for compound, row in effective.items()
    }
    assert values == pytest.approx({"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.5})
    assert run.load_json("tyre_prediction")["provenance"][
        "operational_normalization"
    ]["input_medium_performance_baseline"] == pytest.approx(0.3)
    assert run.load_json("strategy_unrestricted")


@pytest.mark.parametrize(
    ("compound", "override", "expected", "baseline"),
    [
        ("MEDIUM", 0.3, {"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.5}, 0.3),
        ("MEDIUM", 0.4, {"SOFT": -0.6, "MEDIUM": 0.0, "HARD": 0.4}, 0.4),
        ("SOFT", -0.1, {"SOFT": -0.4, "MEDIUM": 0.0, "HARD": 0.5}, 0.3),
        ("HARD", 0.9, {"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.6}, 0.3),
    ],
)
def test_partial_performance_override_is_applied_in_source_gauge(
    tmp_path: Path, compound, override, expected, baseline,
) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    raw["tyre_prediction"]["manual_override"][compound][
        "performance_delta_to_medium"
    ] = override
    source = _arbitrary_baseline_tyres(season=2026, round_number=7)
    original = {
        key: value.performance_delta_to_medium
        for key, value in source.compounds.items()
    }

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=lambda **kwargs: source, model_config_ensurer=_ensure,
        event_resolver=_event,
    )
    saved = run.load_json("tyre_prediction")
    effective = run.load_json("tyre_prediction.effective")["compounds"]
    values = {
        key: value["performance_delta_to_medium"]["effective_value"]
        for key, value in effective.items()
    }
    automatic = {
        key: value["performance_delta_to_medium"]["automatic_value"]
        for key, value in effective.items()
    }

    assert values == pytest.approx(expected)
    assert automatic == pytest.approx({"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.5})
    assert saved["provenance"]["operational_normalization"][
        "input_medium_performance_baseline"
    ] == pytest.approx(baseline)
    assert saved["provenance"]["partial_override"] == {
        "merge_frame": source.performance_frame,
        "source_reference_compound": source.reference_compound,
        "reference_reinferred_after_merge": False,
        "normalized_before_merge": False,
    }
    assert saved["provenance"]["operational_normalization"][
        "normalization_count"
    ] == 1
    assert {
        key: value.performance_delta_to_medium
        for key, value in source.compounds.items()
    } == original


def test_degradation_only_override_preserves_raw_performance_baseline(
    tmp_path: Path,
) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    raw["tyre_prediction"]["manual_override"]["HARD"][
        "degradation_seconds_per_lap"
    ] = 0.07
    source = _arbitrary_baseline_tyres(season=2026, round_number=7)

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=lambda **kwargs: source, model_config_ensurer=_ensure,
        event_resolver=_event,
    )
    saved = run.load_json("tyre_prediction")
    effective = run.load_json("tyre_prediction.effective")["compounds"]
    performance = {
        key: value["performance_delta_to_medium"]["effective_value"]
        for key, value in effective.items()
    }
    degradation = {
        key: value["degradation_seconds_per_lap"]["effective_value"]
        for key, value in effective.items()
    }

    assert performance == pytest.approx({"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.5})
    assert degradation == pytest.approx({"SOFT": 0.2, "MEDIUM": 0.1, "HARD": 0.07})
    assert saved["provenance"]["operational_normalization"][
        "input_medium_performance_baseline"
    ] == pytest.approx(0.3)
    assert source.compounds["HARD"].degradation_seconds_per_lap == 0.05


def test_partial_override_inherits_declared_source_reference_without_reinference(
    tmp_path: Path,
) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    raw["tyre_prediction"]["manual_override"]["HARD"][
        "performance_delta_to_medium"
    ] = .1

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_hard_reference_tyres, model_config_ensurer=_ensure,
        event_resolver=_event,
    )
    saved = run.load_json("tyre_prediction")
    values = {
        compound: row["performance_delta_to_medium"]
        for compound, row in saved["compounds"].items()
    }

    assert values == pytest.approx({"SOFT": -.3, "MEDIUM": 0.0, "HARD": .3})
    normalization = saved["provenance"]["operational_normalization"]
    assert normalization["source_reference_compound"] == "HARD"
    assert normalization["source_reference_selection_rule"] == "declared"
    assert saved["provenance"]["partial_override"][
        "reference_reinferred_after_merge"
    ] is False


def test_partial_medium_override_roundtrip_is_explicit_non_formal_intermediate():
    source = _medium_reference_tyres(season=2026, round_number=7)
    override = type("Override", (), {
        "performance_delta_to_medium": .1,
        "degradation_seconds_per_lap": None,
    })()
    intermediate = _source_prediction_with_overrides(
        source, {"MEDIUM": override}
    )

    assert intermediate.reference_compound == "MEDIUM"
    assert intermediate.performance_frame == RELATIVE_PERFORMANCE_FRAME
    assert intermediate.formal_relative_output is False
    assert {
        name: item.performance_delta_to_medium
        for name, item in intermediate.compounds.items()
    } == {"SOFT": -.3, "MEDIUM": .1, "HARD": .3}
    state = intermediate.provenance["intermediate_state"]
    assert state["formal_relative_output"] is False
    assert state["reference_zero_required"] is False
    assert state["normalization_applied"] is False

    roundtrip = PreRaceTyrePrediction.from_dict(
        json.loads(json.dumps(intermediate.to_dict()))
    )
    assert roundtrip.reference_compound == "MEDIUM"
    assert roundtrip.formal_relative_output is False
    assert roundtrip.provenance["intermediate_state"] == state
    assert roundtrip.compounds == intermediate.compounds

    operational = canonicalize_operational_tyre_prediction(
        roundtrip,
        expected_season=2026,
        expected_round_number=7,
        expected_event_id="2026-07",
    )
    assert {
        name: item.performance_delta_to_medium
        for name, item in operational.compounds.items()
    } == pytest.approx({"SOFT": -.4, "MEDIUM": 0.0, "HARD": .2})
    assert operational.provenance["operational_normalization"][
        "normalization_count"
    ] == 1


def test_complete_manual_arbitrary_baseline_uses_same_operational_contract(
    tmp_path: Path,
) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    for compound, performance, degradation in (
        ("SOFT", -0.2, 0.2), ("MEDIUM", 0.3, 0.1), ("HARD", 0.8, 0.05),
    ):
        raw["tyre_prediction"]["manual_override"][compound] = {
            "performance_delta_to_medium": performance,
            "degradation_seconds_per_lap": degradation,
        }

    def missing(**kwargs):
        raise FileNotFoundError("missing")

    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=missing, model_config_ensurer=_ensure,
        event_resolver=_event,
    )
    effective = run.load_json("tyre_prediction.effective")["compounds"]
    values = {
        compound: row["performance_delta_to_medium"]["effective_value"]
        for compound, row in effective.items()
    }
    assert values == pytest.approx({"SOFT": -0.5, "MEDIUM": 0.0, "HARD": 0.5})
    assert run.load_json("tyre_prediction")["provenance"][
        "operational_normalization"
    ]["input_medium_performance_baseline"] == pytest.approx(0.3)
    assert run.load_json("tyre_prediction")["provenance"][
        "operational_normalization"
    ]["source_reference_selection_rule"] == "no_zero_default_medium"


def test_incomplete_manual_fallback_uses_shared_required_field_validation(
    tmp_path: Path,
) -> None:
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})
    for compound, performance, degradation in (
        ("SOFT", -0.2, 0.2), ("MEDIUM", 0.3, 0.1), ("HARD", 0.8, None),
    ):
        raw["tyre_prediction"]["manual_override"][compound] = {
            "performance_delta_to_medium": performance,
            "degradation_seconds_per_lap": degradation,
        }

    with pytest.raises(
        RacePreparationPrerequisiteError,
        match="HARD degradation_seconds_per_lap is required",
    ):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            tyre_provider=lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
            model_config_ensurer=_ensure, event_resolver=_event,
        )
    assert not list(tmp_path.rglob("manifest.json"))


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("performance_delta_to_medium", float("nan"), "must be finite"),
        ("performance_delta_to_medium", float("inf"), "must be finite"),
        ("degradation_seconds_per_lap", -0.01, "cannot be negative"),
    ],
)
def test_complete_manual_invalid_numeric_value_fails_before_run(
    tmp_path: Path, field, value, reason,
) -> None:
    raw = RaceConfig.template()
    raw.update({"data_root": str(tmp_path)})
    for compound, performance, degradation in (
        ("SOFT", -0.2, 0.2), ("MEDIUM", 0.3, 0.1), ("HARD", 0.8, 0.05),
    ):
        raw["tyre_prediction"]["manual_override"][compound] = {
            "performance_delta_to_medium": performance,
            "degradation_seconds_per_lap": degradation,
        }
    raw["tyre_prediction"]["manual_override"]["SOFT"][field] = value

    with pytest.raises(RacePreparationPrerequisiteError, match=reason):
        run_race(
            RaceConfig.parse(raw), event="2026-07", input_config=raw,
            tyre_provider=lambda **kwargs: (_ for _ in ()).throw(FileNotFoundError()),
            model_config_ensurer=_ensure, event_resolver=_event,
        )
    assert not list(tmp_path.rglob("manifest.json"))


def test_existing_canonical_tyre_prediction_proceeds_normally(tmp_path: Path) -> None:
    _write_canonical_tyre_prediction(tmp_path)
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        model_config_ensurer=_ensure, event_resolver=_event,
    )

    assert run.load_json("manifest")["status"] == "SUCCESS"
    assert run.load_json("tyre_prediction")["artifact_id"] == "canonical-test-artifact"
    assert (run.path / "report_bundle.zh-CN.engineering").is_dir()


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
        "human_source": "manual override",
        "uncertainty": None,
    }


def test_manual_override_adds_automatic_counterfactual(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "result_count": 2, "data_root": str(tmp_path)})
    raw["tyre_prediction"]["manual_override"]["MEDIUM"]["degradation_seconds_per_lap"] = .15
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event,
    )
    effective = run.load_json("tyre_prediction.effective")
    assert effective["compounds"]["MEDIUM"]["degradation_seconds_per_lap"]["source"] == "manual_override"
    assert effective["compounds"]["SOFT"]["degradation_seconds_per_lap"]["source"] == "automatic_model"
    appendix = run.load_json("automatic_model_without_manual_override")
    assert appendix["tyre_values"]["MEDIUM"]["degradation_seconds_per_lap"] == .1
    assert appendix["rules_compliant_strategy"]
    assert "primary_1_to_2" in appendix["degradation_cutoffs"]


def test_practice_evidence_is_deterministic_and_report_facing(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event,
    )
    evidence = run.load_json("practice_tyre_evidence")
    assert evidence["source_family"] == "fp_diagnostic"
    assert evidence["production_strategy_inputs_modified"] is False
    assert [row["status"] for row in evidence["sessions"]] == ["unavailable"] * 3
    assert all(
        row["temperature_context"]["status"] == "unavailable_no_tyre_evidence"
        for row in evidence["sessions"]
    )
    assert "physically identifiable" in evidence["confounding"][1]


def test_report_adapter_does_not_reconstruct_legacy_or_stale_temperature():
    legacy = _report_temperature_context(
        None, analysis_id="fp2-current", model_available=True,
        model_created_at="2026-09-26T12:00:00+00:00",
    )
    assert legacy["status"] == "legacy_provenance_unavailable"
    stale = _report_temperature_context(
        {"temperature_context": {
            "status": "available", "analysis_id": "fp2-old",
            "model_created_at": "2026-09-26T12:00:00+00:00",
            "compound_contexts": [{"compound": "MEDIUM"}],
        }},
        analysis_id="fp2-current",
        model_created_at="2026-09-26T12:00:00+00:00",
        model_available=True,
    )
    assert stale["status"] == "stale_provenance_unavailable"
    assert "compound_contexts" not in stale
    unbound = _report_temperature_context(
        {"temperature_context": {"status": "available"}},
        analysis_id="fp2-current",
        model_created_at="2026-09-26T12:00:00+00:00",
        model_available=True,
    )
    assert unbound["status"] == "unavailable_unbound_provenance"


def test_cutoff_output_includes_supported_fp_medium_markers(tmp_path: Path) -> None:
    from wodata.artifacts import weekend_model_root
    import csv
    import json

    root = weekend_model_root(2026, 7, tmp_path)
    session = root / "sessions" / "FP2"
    session.mkdir(parents=True)
    (session / "manifest.json").write_text(json.dumps({
        "analysis_id": "fp2-analysis", "created_at": "2026-01-01T01:00:00+00:00",
    }))
    columns = [
        "parameter", "compound", "p10", "median", "p90", "unit",
        "support_status", "usable_lap_count", "usable_run_count",
    ]
    with (session / "latest_parameters.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerow({
            "parameter": "degradation", "compound": "MEDIUM", "p10": .04,
            "median": .08, "p90": .12, "unit": "s/lap", "support_status": "measured",
            "usable_lap_count": 12, "usable_run_count": 2,
        })
    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "data_root": str(tmp_path)})
    baseline = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event,
    )
    unchanged_products = {
        name: baseline.load_json(name)
        for name in (
            "tyre_prediction.effective",
            "strategy_unrestricted",
            "strategy_rules_compliant",
            "cutoff_unrestricted",
            "cutoff_rules_compliant",
        )
    }
    (root / "fp_tyre_evidence_report.json").write_text(json.dumps({
        "report_fingerprint": "fp-report-with-temperature",
        "production_strategy_inputs_modified": False,
        "temperature_context_contract": {
            "version": 1,
            "scope": "session_compound_supporting_laps",
            "reporting_only": True,
        },
        "sessions": [{
            "session": "FP2",
            "temperature_context": {
                "status": "partial",
                "analysis_id": "fp2-analysis",
                "model_created_at": "2026-01-01T01:00:00+00:00",
                "coverage": {
                    "evidence_laps": 2,
                    "air_temp_laps": 1,
                    "track_temp_laps": 2,
                },
                "compound_contexts": [{
                    "compound": "MEDIUM",
                    "evidence_lap_count": 2,
                    "evidence_run_count": 1,
                    "supporting_laps": [
                        {"run_id": "FP2:a:1", "lap_number": 10,
                         "air_temp_c": 25.0, "track_temp_c": 39.0},
                        {"run_id": "FP2:a:1", "lap_number": 11,
                         "air_temp_c": None, "track_temp_c": 40.0},
                    ],
                    "air_temp_c": {"status": "partial", "available_laps": 1,
                                   "missing_laps": 1, "minimum": 25.0,
                                   "maximum": 25.0, "unit": "degC",
                                   "recorded_zero_observed": False},
                    "track_temp_c": {"status": "available", "available_laps": 2,
                                     "missing_laps": 0, "minimum": 39.0,
                                     "maximum": 40.0, "unit": "degC",
                                     "recorded_zero_observed": False},
                }],
            },
        }],
    }), encoding="utf-8")
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event,
    )
    for name, baseline_product in unchanged_products.items():
        assert run.load_json(name) == baseline_product
    cutoff = run.load_json("cutoff_rules_compliant")
    marker = cutoff["fp_diagnostic_medium_markers"][0]
    assert marker["session"] == "FP2"
    assert marker["medium_degradation"] == .08
    assert marker["production_input"] is False
    assert "not fully separable" in cutoff["fp_marker_limitation"]
    practice = run.load_json("practice_tyre_evidence")
    fp2 = next(row for row in practice["sessions"] if row["session"] == "FP2")
    assert practice["temperature_context_contract"]["version"] == 1
    assert fp2["temperature_context"]["coverage"] == {
        "evidence_laps": 2,
        "air_temp_laps": 1,
        "track_temp_laps": 2,
    }
    assert fp2["temperature_context"]["compound_contexts"][0][
        "supporting_laps"
    ][0]["lap_number"] == 10


def test_scheduled_event_laps_are_used_and_recorded(tmp_path: Path) -> None:
    raw = RaceConfig.template()
    raw["data_root"] = str(tmp_path)
    raw["degradation_cutoff"] = {
        "scan_min": 0.05, "scan_max": 0.15,
        "coarse_step": 0.05, "refine_tolerance": 0.01,
    }
    write_event_operational_inputs(
        season=2026,
        round_number=7,
        green_pit_loss=20.5,
        vsc_pit_loss=12.0,
        event_identity={"country": "Testland", "location": "Test Circuit"},
        data_root=tmp_path,
    )
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres,
        model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: _distance_event(scheduled=57),
    )
    resolved = run.load_json("config.resolved")
    assert resolved["total_laps"] is None
    assert resolved["resolved_total_laps"] == 57
    assert resolved["total_laps_source"] == "event_metadata"
    distance = run.load_json("race_distance")
    assert distance["source"] == "event_metadata"
    assert distance["source_detail"] == "test_metadata"
    assert distance["total_laps"] == 57
    history = read_event_operational_inputs(
        season=2026, round_number=7, data_root=tmp_path,
    )
    assert history["operational"]["race"]["total_laps"] == 57
    assert history["operational"]["pit_loss"]["vsc"]["total"] == 12.0
    assert history["event_identity"] == {
        "country": "Testland",
        "event_name": "Test Grand Prix",
        "location": "Test Circuit",
    }
    history_component = run.load_json("analysis_results")["event_operational_history"]
    assert history_component["status"] == "SUCCESS"
    assert history_component["provenance"]["history_resolved_race_distance"] is False
    historical = find_event_operational_inputs_by_identity(
        season=2026,
        event_identity={"country": "Testland", "location": "Test Circuit"},
        data_root=tmp_path,
    )
    assert historical is not None
    assert historical["operational"]["race"]["total_laps"] == 57

    write_event_operational_inputs(
        season=2026,
        round_number=7,
        vsc_pit_loss=11.5,
        data_root=tmp_path,
    )
    updated = read_event_operational_inputs(
        season=2026, round_number=7, data_root=tmp_path,
    )
    assert updated["operational"]["race"]["total_laps"] == 57
    assert updated["operational"]["pit_loss"]["green"]["total"] == 20.5
    assert updated["operational"]["pit_loss"]["vsc"]["total"] == 11.5


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
    history = read_event_operational_inputs(
        season=2026, round_number=7, data_root=tmp_path,
    )
    assert history["operational"]["race"]["total_laps"] == 5


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


def test_previous_season_vsc_is_report_context_only(tmp_path: Path) -> None:
    write_event_operational_inputs(
        season=2025, round_number=9, total_laps=57,
        green_pit_loss=24.2, sc_pit_loss=None, vsc_pit_loss=12.0,
        event_identity={"country": "Testland", "location": "Test Circuit"},
        data_root=tmp_path,
    )
    prior = WeekendRunStore.create(
        event="2025-09", workflow="post_race", data_root=tmp_path,
        generated_at="2025-06-01T12:00:00+00:00",
    )
    prior.save_json("event.metadata", {"country": "Testland", "location": "Test Circuit"})
    prior.save_json("empirical_pit_loss", {
        "vsc": {
            "status": "available", "total": 11.8, "sample_count": 2,
            "samples": [{"driver": "AAA", "pit_in_lap": 20, "pit_out_lap": 21}],
        }
    })
    current = EventMetadata(
        event_id="2026-07", season=2026, round_number=7,
        event_name="Renamed Event", official_event_name=None,
        country="Testland", location="Test Circuit", event_format="conventional",
        is_sprint=False, session_names=("Race",), race_start=None,
        total_laps=6, total_laps_source="test",
    )

    context = _load_previous_season_empirical_vsc(metadata=current, data_root=tmp_path)
    operational_context = _load_previous_season_operational_inputs(
        metadata=current, data_root=tmp_path,
    )

    assert context["value"] == 11.8
    assert context["source_event"] == "2025-09"
    assert context["operational_input"] is False
    assert operational_context["operational"]["pit_loss"]["vsc"]["total"] == 12.0
    assert operational_context["empirical_observation"] is False

    raw = RaceConfig.template()
    raw.update({"total_laps": 6, "result_count": 2, "data_root": str(tmp_path)})
    raw["pit_loss"]["vsc"] = {"total": 13.0}
    history_loads = []

    def missing_history_loader(year, identity):
        history_loads.append((year, identity["country"], identity["location"]))
        return {"status": "no_event", "reason": "test schedule has no event"}

    first = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: current,
        historical_vsc_loader=missing_history_loader,
    )
    write_event_operational_inputs(
        season=2025, round_number=9, total_laps=57,
        green_pit_loss=24.2, sc_pit_loss=None, vsc_pit_loss=10.0,
        event_identity={"country": "Testland", "location": "Test Circuit"},
        data_root=tmp_path,
    )
    revised_prior = WeekendRunStore.create(
        event="2025-09", workflow="post_race", data_root=tmp_path,
        generated_at="2025-06-02T12:00:00+00:00",
    )
    revised_prior.save_json(
        "event.metadata", {"country": "Testland", "location": "Test Circuit"}
    )
    revised_prior.save_json("empirical_pit_loss", {
        "vsc": {"status": "available", "total": 9.9, "sample_count": 1,
                "samples": [{"driver": "BBB", "pit_in_lap": 30, "pit_out_lap": 31}]}
    })
    second = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure,
        event_resolver=lambda *args, **kwargs: current,
        historical_vsc_loader=missing_history_loader,
    )

    assert first.load_json("strategy_unrestricted") == second.load_json("strategy_unrestricted")
    assert second.load_json("config.resolved")["pit_loss"]["vsc"]["total"] == 13.0
    history = second.load_json("historical_empirical_vsc_context")
    assert history["year_range"] == [2021, 2025]
    assert history["years"][-1]["value_seconds"] == 9.9
    assert history["years"][-1]["operational_input"] is False
    assert history["candidate_available"] is False
    assert history["apply_available"] is False
    assert {item[0] for item in history_loads} == {2021, 2022, 2023, 2024}
    historical_operational = second.load_json("historical_operational_inputs_context")
    assert historical_operational["operational"]["pit_loss"]["vsc"]["total"] == 10.0
    assert historical_operational["current_calculation_input"] is False
    assert historical_operational["empirical_observation"] is False


def test_real_r14_migration_record_is_discoverable_by_production_history_loader() -> None:
    data_root = Path(__file__).resolve().parents[2] / "woData"
    future_metadata = EventMetadata(
        event_id="2027-14",
        season=2027,
        round_number=14,
        event_name="Spanish Grand Prix",
        official_event_name=None,
        country="Spain",
        location="Madrid",
        event_format="conventional",
        is_sprint=False,
        session_names=("Race",),
        race_start=None,
        total_laps=None,
        total_laps_source=None,
    )

    context = _load_previous_season_operational_inputs(
        metadata=future_metadata,
        data_root=data_root,
    )

    assert context["status"] == "available"
    assert context["source_event"] == "2026-14"
    assert context["operational"]["race"]["total_laps"] == 57
    assert context["operational"]["pit_loss"]["green"]["total"] == 24.2
    assert context["operational"]["pit_loss"]["sc"]["total"] is None
    assert context["operational"]["pit_loss"]["vsc"]["total"] is None
    assert context["operational"]["pit_loss"]["legacy_sc_vsc"]["total"] == 13.0
