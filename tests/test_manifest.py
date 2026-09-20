import hashlib
from copy import deepcopy
from pathlib import Path
import subprocess
from types import MappingProxyType

from wodata.artifacts import write_weekend_model_config
from wodata.live import CompletedLap, CompletedSector, LiveSessionSnapshot
from woplanner.analysis.service import OfflineAnalysisService

from woweekend.artifacts.manifest import (
    _code_identities,
    _git_commits,
    build_manifest,
    calculation_input_identity,
    replay_provenance,
    resolved_model_config_provenance,
)


def _git(directory, *args):
    return subprocess.run(
        ["git", *args], cwd=directory, check=True, text=True,
        capture_output=True,
    ).stdout.strip()


def test_code_identity_discovers_workspace_head_and_dirty_state(tmp_path):
    repository = tmp_path / "woWeekend"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    tracked = repository / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "baseline")
    head = _git(repository, "rev-parse", "HEAD")

    clean = _code_identities(tmp_path)["woWeekend"]
    assert clean["head"] == head
    assert clean["dirty"] is False
    assert _git_commits(tmp_path)["woWeekend"] == head

    tracked.write_text("after\n", encoding="utf-8")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    dirty = _code_identities(tmp_path)["woWeekend"]
    assert dirty["dirty"] is True
    assert set(dirty["changed_paths"]) == {"tracked.txt", "new.txt"}


def test_default_discovery_finds_the_current_repository():
    repository = Path(__file__).resolve().parents[1]
    assert _git_commits()["woWeekend"] == _git(repository, "rev-parse", "HEAD")


def test_manifest_references_exact_immutable_run_inputs(tmp_path):
    for name, content in (
        ("config.input.json", b'{"source": "input"}\n'),
        ("config.resolved.json", b'{"source": "resolved"}\n'),
        ("event.metadata.json", b'{"event": "2026-01"}\n'),
    ):
        (tmp_path / name).write_bytes(content)

    manifest = build_manifest(
        workflow="race_preparation",
        event="2026-01",
        run_id="run-1",
        components={},
        run_path=tmp_path,
        workspace_root=tmp_path / "empty-workspace",
    )

    assert set(manifest["inputs"]) == {
        "config.input.json", "config.resolved.json", "event.metadata.json"
    }
    reference = manifest["inputs"]["config.resolved.json"]
    assert reference["uri"] == "config.resolved.json"
    assert reference["sha256"] == hashlib.sha256(
        (tmp_path / "config.resolved.json").read_bytes()
    ).hexdigest()
    assert manifest["provenance_scope"]["complete_as_run_claim"] is False


def test_resolved_model_config_keeps_producer_and_consumer_identity_separate():
    provenance = resolved_model_config_provenance(
        {"artifact_id": "old", "producer_code_identity": {"head": "old-head"}},
        {"sample_count": 500, "settings_source": "race-review"},
    )
    assert provenance["producer_code_identity"] == {"head": "old-head"}
    assert provenance["consumer_resolution"]["status"] == "resolved"
    assert provenance["consumer_resolution"]["resolved_artifact_content"] == {
        "sample_count": 500, "settings_source": "race-review"
    }
    assert provenance["consumer_resolution"]["hash_scope"] == (
        "full_resolved_model_config_content"
    )
    assert len(provenance["consumer_resolution"]["resolved_artifact_content_hash"]) == 64

    generated = resolved_model_config_provenance({"status": "generated"}, {})
    assert generated["producer_code_identity"]["reference"] == (
        "run manifest consumer_code_identity"
    )

    legacy = resolved_model_config_provenance({"artifact_id": "legacy"}, None)
    assert legacy["producer_code_identity"]["status"] == (
        "unavailable_for_cached_or_legacy_artifact"
    )
    assert legacy["consumer_resolution"]["status"] == "unavailable"


def test_replay_provenance_preserves_algorithm_only_cutoff_and_input_hashes():
    effective_inputs = {
        "event": {"year": 2026, "round_number": 1, "total_laps": 50},
        "mode": "algorithm_only",
        "service_resolved_calculation": {
            "architecture": "retro",
            "model_config": {"sample_count": 500, "fuel_rate_bounds": [0, .08]},
        },
        "pre_race_tyre_values": {"MEDIUM": {"degradation": .1, "compound_delta": 0}},
        "effective_manual_override_events": [],
    }
    provenance = replay_provenance(
        mode="algorithm_only",
        model_config={
            "consumer_resolution": {"resolved_artifact_content_hash": "config-hash"}
        },
        calculation_options={"sample_count": 500},
        pre_race_tyre={"automatic_artifact_id": "tyre-1", "compounds": {}},
        effective_calculation_inputs=effective_inputs,
    )
    assert provenance["replay_classification"] == "algorithm_only"
    assert provenance["evidence_cutoff"] == "leader_lap_completion_clock_cut"
    assert provenance["complete_as_run_reconstruction"] is False
    assert provenance["model_config"]["consumer_resolution"][
        "resolved_artifact_content_hash"
    ] == "config-hash"
    assert provenance["effective_calculation_inputs"]["hash_scope"] == (
        "effective_replay_configuration_and_control_inputs_v1"
    )
    assert len(provenance["effective_calculation_inputs"]["fingerprint"]) == 64
    assert provenance["pre_race_tyre_input"]["artifact_id"] == "tyre-1"
    assert provenance["pre_race_tyre_input"]["artifact_hash_scope"] == (
        "full_pre_race_tyre_artifact_content"
    )
    assert provenance["pre_race_tyre_input"]["producer_code_identity"]["status"] == (
        "unavailable_for_legacy_artifact"
    )


def test_calculation_fingerprint_ignores_locator_metadata_but_tracks_each_input():
    effective = {
        "event": {"year": 2026, "round_number": 1, "total_laps": 50},
        "mode": "algorithm_only",
        "service_resolved_calculation": {
            "architecture": "retro",
            "model_config": {"sample_count": 500, "fuel_rate_bounds": [0, .08]},
            "analysis_options": {"quick_lap_threshold": 1.1},
        },
        "pre_race_tyre_values": {"MEDIUM": {"degradation": .1, "compound_delta": 0}},
        "effective_manual_override_events": [],
    }
    first = replay_provenance(
        mode="algorithm_only",
        model_config={"path": "/one/config.json", "description": "first"},
        calculation_options={"sample_count": 500},
        pre_race_tyre={"path": "/one/tyre.json", "generated_at": "one"},
        effective_calculation_inputs=effective,
    )
    second = replay_provenance(
        mode="algorithm_only",
        model_config={"path": "/two/config.json", "description": "second"},
        calculation_options={"sample_count": 500},
        pre_race_tyre={"path": "/two/tyre.json", "generated_at": "two"},
        effective_calculation_inputs=deepcopy(effective),
    )
    assert first["effective_calculation_inputs"]["fingerprint"] == second[
        "effective_calculation_inputs"
    ]["fingerprint"]
    assert first["pre_race_tyre_input"]["artifact_content_hash"] != second[
        "pre_race_tyre_input"
    ]["artifact_content_hash"]

    mutations = (
        ("mode", "operational"),
        ("event.total_laps", 51),
        ("model_config.sample_count", 501),
        ("analysis_options.quick_lap_threshold", 1.09),
        ("tyre.MEDIUM.degradation", .11),
    )
    original = calculation_input_identity(effective)["fingerprint"]
    for name, value in mutations:
        changed = deepcopy(effective)
        if name == "mode":
            changed["mode"] = value
        elif name == "event.total_laps":
            changed["event"]["total_laps"] = value
        elif name == "model_config.sample_count":
            changed["service_resolved_calculation"]["model_config"]["sample_count"] = value
        elif name == "analysis_options.quick_lap_threshold":
            changed["service_resolved_calculation"]["analysis_options"][
                "quick_lap_threshold"
            ] = value
        else:
            changed["pre_race_tyre_values"]["MEDIUM"]["degradation"] = value
        assert calculation_input_identity(changed)["fingerprint"] != original


def _live_snapshot(*, scheduled_total_laps):
    laps = tuple(
        CompletedLap(
            "1", "AAA", "Team", lap, 90.0 + lap * .1, 90.0 * lap,
            1, None, "MEDIUM", float(lap), 1, True,
        )
        for lap in range(1, 5)
    )
    sectors = []
    for lap in laps:
        finish = float(lap.lap_number * 100.0)
        for number, code, offset, position in (
            ("98", "AHEAD", -5.0, 1),
            (lap.driver_number, lap.driver_code, 0.0, 2),
            ("99", "BEHIND", 5.0, 3),
        ):
            for index, sector in enumerate(("S1", "S2", "S3"), start=1):
                sectors.append(CompletedSector(
                    number, code, "Team", lap.lap_number, sector,
                    lap.lap_number * 3 - 3 + index, 30.0, 30.0 * index,
                    position, None, session_time_seconds=finish + offset,
                ))
    return LiveSessionSnapshot(
        MappingProxyType({}), "Started", (), laps,
        MappingProxyType({"98": 1, "1": 2, "99": 3}), None,
        tuple(sectors), scheduled_total_laps=scheduled_total_laps,
    )


def _captured_calculation(
    data_root, *, total_laps, source, sample_count=32, quick_lap_threshold=1.1,
):
    service = OfflineAnalysisService(data_root)
    snapshot = _live_snapshot(
        scheduled_total_laps=total_laps if source == "live_lap_count" else None
    )
    result = service.recalculate_from_live_snapshot(
        year=2026,
        round_number=5,
        snapshot=snapshot,
        sample_count=sample_count,
        quick_lap_threshold=quick_lap_threshold,
        min_clean_laps=3,
        scheduled_total_laps_override=(
            total_laps if source == "manual_override" else None
        ),
        persist=False,
    )
    assert result.status == "updated"
    capture = service.last_live_calculation_inputs
    assert capture is not None
    captured_source = dict(capture.get("input_provenance") or {}).get(
        "scheduled_total_laps_source"
    ) or capture["event_context"].get("scheduled_total_laps_source")
    assert captured_source == source
    return capture


def _effective_payload(capture, *, tyre=.1, manual_events=()):
    return {
        "event": {
            "year": 2026,
            "round_number": 5,
            "total_laps": capture["event_context"]["scheduled_total_laps"],
        },
        "mode": "operational" if manual_events else "algorithm_only",
        "service_resolved_calculation": capture,
        "pre_race_tyre_values": {
            "MEDIUM": {"degradation": tyre, "compound_delta": 0.0},
        },
        "effective_manual_override_events": list(manual_events),
    }


def test_real_service_capture_fingerprint_excludes_only_descriptive_provenance(
    tmp_path,
):
    write_weekend_model_config(
        {
            "sample_count": 32,
            "random_seed": 42,
            "fuel_rate_bounds": [0.0, 0.1],
            "track_rate_bounds": [-0.05, 0.05],
            "default_degradation_bounds": [0.0, 0.2],
            "default_compound_delta_bounds": [-1.0, 1.0],
            "reference_compound": "HARD",
            "clean_lap_noise_sigma": 0.35,
            "expected_sessions": ["R"],
            "expected_compounds": ["MEDIUM", "HARD"],
        },
        year=2026,
        round_number=5,
        data_root=tmp_path,
    )
    manual = _captured_calculation(
        tmp_path, total_laps=57, source="manual_override"
    )
    live = _captured_calculation(
        tmp_path, total_laps=57, source="live_lap_count"
    )
    live.setdefault("input_provenance", {}).update({
        "path": "/different/location.json",
        "description": "different descriptive label",
    })
    manual_payload = _effective_payload(manual)
    live_payload = _effective_payload(live)
    manual_identity = calculation_input_identity(manual_payload)
    live_identity = calculation_input_identity(live_payload)
    assert manual_identity["fingerprint"] == live_identity["fingerprint"]
    assert "input_provenance" not in manual_identity["content"][
        "service_resolved_calculation"
    ]

    changed_total = _effective_payload(_captured_calculation(
        tmp_path, total_laps=58, source="live_lap_count"
    ))
    changed_config = _effective_payload(_captured_calculation(
        tmp_path, total_laps=57, source="live_lap_count", sample_count=33
    ))
    changed_filter = _effective_payload(_captured_calculation(
        tmp_path, total_laps=57, source="live_lap_count",
        quick_lap_threshold=1.09,
    ))
    changed_tyre = _effective_payload(live, tyre=.11)
    manual_event = _effective_payload(live, manual_events=(
        {"message_index": 10, "active": True,
         "values": {"MEDIUM": {"degradation": .12}}},
    ))
    for changed in (
        changed_total, changed_config, changed_filter, changed_tyre, manual_event,
    ):
        assert calculation_input_identity(changed)["fingerprint"] != live_identity[
            "fingerprint"
        ]
    changed_manual_value = _effective_payload(live, manual_events=(
        {"message_index": 10, "active": True,
         "values": {"MEDIUM": {"degradation": .13}}},
    ))
    changed_manual_position = _effective_payload(live, manual_events=(
        {"message_index": 11, "active": True,
         "values": {"MEDIUM": {"degradation": .12}}},
    ))
    manual_identity = calculation_input_identity(manual_event)
    assert calculation_input_identity(changed_manual_value)["fingerprint"] != (
        manual_identity["fingerprint"]
    )
    assert calculation_input_identity(changed_manual_position)["fingerprint"] != (
        manual_identity["fingerprint"]
    )
    assert "observation stream" in manual_identity["scope_definition"]
    assert "sample bank" in manual_identity["scope_definition"]

    provenance = replay_provenance(
        mode="algorithm_only",
        model_config={},
        calculation_options={},
        pre_race_tyre={},
        effective_calculation_inputs=manual_payload,
    )
    assert provenance["calculation_input_provenance"][
        "service_resolved_calculation"
    ]["scheduled_total_laps_source"] == "manual_override"
