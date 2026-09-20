import json
from datetime import datetime, timezone

import pytest

from wodata.event_operational import (
    event_operational_inputs_path,
    read_event_operational_inputs,
    write_event_operational_inputs,
)
from woweekend.config.common import ConfigError
from woweekend.config.race import RaceConfig
import woplanner.operational_inputs as operational_inputs_module
from woplanner.operational_inputs import (
    load_operational_race_inputs,
    save_operational_pit_losses,
)


def test_help_keys_are_ignored_and_defaults_resolve() -> None:
    raw = RaceConfig.template()
    raw["pit_loss"]["green"]["_units"] = "seconds"
    config = RaceConfig.parse(raw)
    assert config.report.language == "zh-CN"
    assert config.degradation_cutoff.scan_min is None


def test_unknown_normal_field_is_rejected_with_suggestion() -> None:
    raw = RaceConfig.template()
    raw["result_cout"] = 5
    with pytest.raises(ConfigError, match="result_count"):
        RaceConfig.parse(raw)


def test_generated_config_validates() -> None:
    RaceConfig.parse(RaceConfig.template())


def test_manual_tyre_input_allows_arbitrary_medium_baseline() -> None:
    raw = RaceConfig.template()
    raw["tyre_prediction"]["manual_override"]["MEDIUM"][
        "performance_delta_to_medium"
    ] = 0.3
    config = RaceConfig.parse(raw)
    assert (
        config.tyre_prediction.manual_override["MEDIUM"].performance_delta_to_medium
        == 0.3
    )


def test_whole_pit_loss_is_supported_without_sector_split() -> None:
    config = RaceConfig.parse(RaceConfig.template())
    assert config.pit_loss.green.effective_total == 20.5
    assert config.pit_loss.green.pit_in_s3 is None


def test_whole_and_split_pit_loss_are_rejected_as_ambiguous() -> None:
    raw = RaceConfig.template()
    raw["pit_loss"]["green"] = {
        "total": 20.5, "pit_in_s3": 10.4, "pit_out_s1": 10.1,
    }
    with pytest.raises(ValueError, match="provide either total or both"):
        RaceConfig.parse(raw)


def test_legacy_combined_pit_loss_is_not_promoted_to_sc_or_vsc() -> None:
    raw = RaceConfig.template()
    raw["pit_loss"]["sc"] = {"total": None}
    raw["pit_loss"]["vsc"] = {"total": None}
    raw["pit_loss"]["sc_vsc"] = {"total": 13.0}

    config = RaceConfig.parse(raw)

    assert config.pit_loss.sc.effective_total is None
    assert config.pit_loss.vsc.effective_total is None
    assert config.pit_loss.sc_vsc.effective_total == 13.0


def test_planner_save_round_trips_through_weekend_config_resolution(tmp_path) -> None:
    path = tmp_path / "race.json"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 14})
    raw["pit_loss"]["vsc"] = {"total": 13.0}
    path.write_text(json.dumps(raw))

    loaded = load_operational_race_inputs(path)
    assert loaded.vsc_pit_loss == 13.0

    def unexpected_resolver(*_args, **_kwargs):
        raise AssertionError("owned same-event identity should be reused")

    save_operational_pit_losses(
        path, green=20.5, sc=15.0, vsc=12.0,
        season=2026, round_number=14,
        event_identity={"country": "Spain", "location": "Madrid"},
        event_identity_event_key=(2026, 14),
        event_resolver=unexpected_resolver,
        updated_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        history_data_root=tmp_path / "data",
    )
    resolved = RaceConfig.parse(json.loads(path.read_text()))

    assert resolved.pit_loss.vsc.effective_total == 12.0
    persisted = json.loads(path.read_text())
    assert persisted["pit_loss"]["vsc"]["_provenance"] == {
        "source": "manual", "updated_via": "woPlanner", "event_id": "2026-14",
        "updated_at": "2026-09-19T00:00:00+00:00",
    }
    history = read_event_operational_inputs(
        season=2026, round_number=14, data_root=tmp_path / "data",
    )
    assert history["operational"]["pit_loss"]["vsc"]["total"] == 12.0


def test_planner_save_restores_current_config_if_history_mirror_fails(
    tmp_path, monkeypatch,
) -> None:
    path = tmp_path / "race.json"
    history_root = tmp_path / "data"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 14})
    raw["pit_loss"]["vsc"] = {"total": 13.0}
    path.write_text(json.dumps(raw, separators=(",", ":")))
    original_config = path.read_bytes()
    write_event_operational_inputs(
        season=2026,
        round_number=14,
        green_pit_loss=20.5,
        sc_pit_loss=None,
        vsc_pit_loss=13.0,
        event_identity={"country": "Spain", "location": "Madrid"},
        data_root=history_root,
    )
    history_path = event_operational_inputs_path(
        season=2026, round_number=14, data_root=history_root,
    )
    original_history = history_path.read_bytes()

    def fail_history(**kwargs):
        write_event_operational_inputs(**kwargs)
        raise OSError("history unavailable")

    monkeypatch.setattr(
        operational_inputs_module, "write_event_operational_inputs", fail_history,
    )

    with pytest.raises(RuntimeError, match="current config was restored"):
        save_operational_pit_losses(
            path, green=20.5, sc=15.0, vsc=12.0,
            season=2026, round_number=14,
            event_identity={"country": "Spain", "location": "Madrid"},
            event_identity_event_key=(2026, 14),
            history_data_root=history_root,
        )

    assert path.read_bytes() == original_config
    assert history_path.read_bytes() == original_history


def test_planner_save_validates_complete_prospective_config_before_replacement(
    tmp_path,
) -> None:
    path = tmp_path / "race.json"
    history_root = tmp_path / "data"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 14})
    raw["race"]["total_laps"] = -1
    raw["pit_loss"]["vsc"] = {"total": 13.0}
    path.write_text(json.dumps(raw, separators=(",", ":")))
    original_config = path.read_bytes()
    write_event_operational_inputs(
        season=2026,
        round_number=14,
        total_laps=57,
        green_pit_loss=20.5,
        sc_pit_loss=None,
        vsc_pit_loss=13.0,
        event_identity={"country": "Spain", "location": "Madrid"},
        data_root=history_root,
    )
    history_path = event_operational_inputs_path(
        season=2026, round_number=14, data_root=history_root,
    )
    original_history = history_path.read_bytes()

    with pytest.raises(ValueError, match="Prospective race.json is invalid"):
        save_operational_pit_losses(
            path,
            green=20.5,
            sc=None,
            vsc=12.0,
            season=2026,
            round_number=14,
            event_identity={"country": "Spain", "location": "Madrid"},
            history_data_root=history_root,
        )

    assert path.read_bytes() == original_config
    assert history_path.read_bytes() == original_history
    assert read_event_operational_inputs(
        season=2026, round_number=14, data_root=history_root,
    )["operational"]["pit_loss"]["vsc"]["total"] == 13.0


def test_planner_identity_resolution_failure_precedes_all_mutation(tmp_path) -> None:
    path = tmp_path / "race.json"
    history_root = tmp_path / "data"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 15})
    raw["pit_loss"]["vsc"] = {"total": 13.0}
    path.write_text(json.dumps(raw, separators=(",", ":")))
    original_config = path.read_bytes()

    def fail_resolver(*_args, **_kwargs):
        raise RuntimeError("canonical schedule unavailable")

    with pytest.raises(RuntimeError, match="canonical schedule unavailable"):
        save_operational_pit_losses(
            path,
            green=20.5,
            sc=None,
            vsc=12.0,
            season=2026,
            round_number=15,
            event_identity={"country": "Spain", "location": "Madrid"},
            event_identity_event_key=(2026, 14),
            event_resolver=fail_resolver,
            history_data_root=history_root,
        )

    assert path.read_bytes() == original_config
    assert not history_root.exists()


def test_planner_re_resolves_conflicting_supplied_identity_before_persistence(
    tmp_path,
) -> None:
    path = tmp_path / "race.json"
    history_root = tmp_path / "data"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 15})
    path.write_text(json.dumps(raw))
    calls = []

    class CanonicalMetadata:
        event_name = "British Grand Prix"
        official_event_name = None
        country = "Great Britain"
        location = "Silverstone"
        circuit = "Silverstone"
        circuit_id = "2"

    def resolver(event, *, include_race_distance):
        calls.append((event, include_race_distance))
        return CanonicalMetadata()

    save_operational_pit_losses(
        path,
        green=20.5,
        sc=None,
        vsc=12.0,
        season=2026,
        round_number=15,
        event_identity={
            "event_name": "Spanish Grand Prix",
            "country": "Spain",
            "location": "Madrid",
        },
        event_identity_event_key=(2026, 14),
        event_resolver=resolver,
        history_data_root=history_root,
    )

    assert calls == [("2026-15", False)]
    record = read_event_operational_inputs(
        season=2026, round_number=15, data_root=history_root,
    )
    assert record["event_identity"] == {
        "event_name": "British Grand Prix",
        "country": "Great Britain",
        "location": "Silverstone",
        "circuit": "Silverstone",
        "circuit_id": "2",
    }
    assert "Spain" not in record["event_identity"].values()


def test_planner_save_enriches_identity_before_live_metadata(tmp_path) -> None:
    path = tmp_path / "race.json"
    raw = RaceConfig.template()
    raw.update({"season": 2026, "round_number": 14})
    path.write_text(json.dumps(raw))

    class Metadata:
        event_name = "Spanish Grand Prix"
        official_event_name = "FORMULA 1 TEST SPANISH GRAND PRIX 2026"
        country = "Spain"
        location = "Madrid"

    calls = []

    def resolver(event, *, include_race_distance):
        calls.append((event, include_race_distance))
        return Metadata()

    save_operational_pit_losses(
        path,
        green=20.5,
        sc=None,
        vsc=None,
        season=2026,
        round_number=14,
        event_identity={},
        event_resolver=resolver,
        history_data_root=tmp_path / "data",
    )

    record = read_event_operational_inputs(
        season=2026, round_number=14, data_root=tmp_path / "data",
    )
    assert calls == [("2026-14", False)]
    assert record["event_identity"] == {
        "country": "Spain",
        "event_name": "Spanish Grand Prix",
        "location": "Madrid",
        "official_event_name": "FORMULA 1 TEST SPANISH GRAND PRIX 2026",
    }
    assert record["event_identity_status"] == "available"
