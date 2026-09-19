import json
from pathlib import Path

import pytest

from wodata.events import EventMetadata
from wostrategy.model.tyre_prediction import PreRaceTyrePrediction, TyreCompoundPrediction
from wodata.artifacts import write_weekend_model_config
from wostrategy.analysis.pre_race_model_config import ensure_pre_race_model_config
from woweekend.config.race import RaceConfig
from woweekend.workflows.race import RacePreparationPrerequisiteError, run_race


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
    unrestricted = run.load_json("cutoff_unrestricted")
    compliant_cutoff = run.load_json("cutoff_rules_compliant")
    assert "primary_0_to_1" in unrestricted
    assert unrestricted["cutoff_table"][0]["display_label"] == "0→1"
    assert "primary_1_to_2" in compliant_cutoff
    assert compliant_cutoff["primary_0_to_1"] is None
    assert compliant_cutoff["primary_0_to_1_status"] == "unavailable_rules_compliant"
    assert compliant_cutoff["cutoff_table"][0]["cutoff_medium_degradation"] is None


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
    assert "physically identifiable" in evidence["confounding"][1]


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
    run = run_race(
        RaceConfig.parse(raw), event="2026-07", input_config=raw,
        tyre_provider=_tyres, model_config_ensurer=_ensure, event_resolver=_event,
    )
    cutoff = run.load_json("cutoff_rules_compliant")
    marker = cutoff["fp_diagnostic_medium_markers"][0]
    assert marker["session"] == "FP2"
    assert marker["medium_degradation"] == .08
    assert marker["production_input"] is False
    assert "not fully separable" in cutoff["fp_marker_limitation"]


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
