"""Exercise the real pre-race producer and woWeekend adapter contract."""

import json
from types import SimpleNamespace

import pandas as pd
from wodata.artifacts import weekend_model_root

from wostrategy.script import pre_race_analysis as producer
from woweekend.workflows.race import _report_fp_evidence


def test_producer_temperature_binds_to_current_artifact_instance(tmp_path, monkeypatch):
    csv_path = tmp_path / "expert.csv"
    laps = pd.DataFrame({
        "Driver": ["AAA"] * 8, "Team": ["Test"] * 8,
        "LapNumber": range(1, 9), "TyreLife": list(range(1, 5)) * 2,
        "Stint": [1] * 4 + [2] * 4,
        "Compound": ["MEDIUM"] * 4 + ["SOFT"] * 4,
        "LapTime": pd.to_timedelta([80 + i * .05 for i in range(8)], unit="s"),
        "PitOutTime": [pd.NaT] * 8, "PitInTime": [pd.NaT] * 8,
        "MeanTimeDeltaToDriverAhead": [5.0] * 8,
        "MeanTimeDeltaToDriverBehind": [2.0] * 8,
        "AirTemp": [25.0] * 8, "TrackTemp": [0.0] + [40.0] * 7,
    })
    config = dict(producer.SCRIPT_CONFIG)
    config.update({
        "year": 2026, "round_number": 7, "sessions": ("FP2",),
        "input_csv_by_session": {"FP2": csv_path},
        "sample_count": 64, "data_root": tmp_path, "plot_output": None,
    })
    real_model = producer.run_joint_weekend_model
    instances = iter(("2026-09-26T12:00:00Z", "2026-09-26T12:01:00Z"))
    results = []

    def model_with_instance_time(*args, **kwargs):
        result = real_model(
            *args, **kwargs, created_at=pd.Timestamp(next(instances)).to_pydatetime()
        )
        results.append(result)
        return result

    monkeypatch.setattr(producer, "run_joint_weekend_model", model_with_instance_time)
    root = weekend_model_root(2026, 7, tmp_path)
    report_path = root / "fp_tyre_evidence_report.json"
    manifest_path = root / "sessions/FP2/manifest.json"

    def adapted_context():
        report = _report_fp_evidence(
            season=2026, round_number=7, data_root=tmp_path,
            tyre=SimpleNamespace(
                generated_at="2026-01-01T00:00:00+00:00", artifact_id="test"
            ),
        )
        assert report["sessions"][2]["temperature_context"]["status"] == (
            "unavailable_no_tyre_evidence"
        )
        return report["sessions"][1]["temperature_context"]

    laps.to_csv(csv_path, index=False)
    assert producer.run_from_script_config(config) == 0
    report_a = report_path.read_text()
    context_a = adapted_context()
    assert context_a["status"] == "available"
    source = context_a["source"]
    assert source["cache_origin"] == "expert_csv"
    assert source["kind"] == "supporting_lap_temperature_from_expert_csv"
    assert "cadence is unknown" in source["sampling_note"]
    assert "minute-resolution" not in source["sampling_note"]
    medium = context_a["compound_contexts"][0]
    assert medium["supporting_laps"][0]["track_temp_c"] == 0.0
    assert medium["track_temp_c"]["recorded_zero_observed"] is True

    laps["AirTemp"] = 26.0
    laps["TrackTemp"] = 42.0
    laps.to_csv(csv_path, index=False)
    assert producer.run_from_script_config(config) == 0
    report_b = report_path.read_text()
    manifest_b = manifest_path.read_text()
    context_b = adapted_context()
    assert context_b["status"] == "available"
    assert context_a["analysis_id"] == context_b["analysis_id"]
    assert context_a["input_fingerprint"] == context_b["input_fingerprint"]
    assert context_a["model_created_at"] != context_b["model_created_at"]
    assert context_b["model_created_at"] == json.loads(manifest_b)["created_at"]
    assert results[0].aggregate_snapshot.analysis_id == results[1].aggregate_snapshot.analysis_id
    assert [p.median for p in results[0].aggregate_snapshot.parameters] == [
        p.median for p in results[1].aggregate_snapshot.parameters
    ]
    pd.testing.assert_frame_equal(results[0].candidate_costs, results[1].candidate_costs)

    # Identical analysis_id must not allow instance A's weather against instance B.
    report_path.write_text(report_a)
    assert adapted_context()["status"] == "stale_provenance_unavailable"
    report_path.write_text(report_b)
    assert adapted_context() == context_b

    # The real adapter must fail closed even with an otherwise available report.
    manifest_path.unlink()
    assert adapted_context()["status"] == "unavailable_unbound_provenance"
    for missing in ("analysis_id", "created_at"):
        manifest = json.loads(manifest_b)
        del manifest[missing]
        manifest_path.write_text(json.dumps(manifest))
        assert adapted_context()["status"] == "unavailable_unbound_provenance"
    manifest_path.write_text(manifest_b)
    assert adapted_context() == context_b

    for missing in ("analysis_id", "model_created_at"):
        report = json.loads(report_b)
        del report["sessions"][1]["temperature_context"][missing]
        report_path.write_text(json.dumps(report))
        assert adapted_context()["status"] == "unavailable_unbound_provenance"
