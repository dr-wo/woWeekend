from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import sys

import pandas as pd
import pytest

from wodata.events import EventMetadata
from wodata.pirelli_preview import (
    PirelliPreviewRecord, preview_json_path, write_preview_json,
)
from woweekend.config.race import RaceConfig
from woweekend.workflows.race import RacePreparationPrerequisiteError
from woweekend.workflows.race_prerequisites import prepare_race_prerequisites


def _preview(root, **changes):
    record = PirelliPreviewRecord.from_extraction(
        {"event": "Bahrain Grand Prix", "circuit": "Sepang",
         "tyre_stress": 4, "asphalt_abrasion": 4, "asphalt_grip": 2,
         "hard_compound": "C2", "medium_compound": "C3", "soft_compound": "C4"},
        season=2026, round_number=16, source_file="1920_16-bh26-preview-en.jpg",
        source_hash="processed-source", extraction_model="manual-codex",
    )
    record = replace(record, **changes)
    write_preview_json(record, preview_json_path(2026, 16, root))


def _dependencies(root, calls, sessions=("Practice 1", "Practice 2", "Practice 3", "Race")):
    def event_resolver(event, **kwargs):
        assert event == "2026-16"
        assert kwargs == {"include_race_distance": False}
        return EventMetadata(
            "2026-16", 2026, 16, "Bahrain Grand Prix", None, "Bahrain", "Kuala Lumpur",
            "conventional", False, sessions, None, None, None,
        )

    def fp_fetcher(values):
        calls.append(values["session"])
        assert values["year"] == 2026 and values["round_number"] == 16
        assert values["data_root"] == root
        assert values["download"] and not values["build"] and not values["play"]
        assert not values["overwrite"]
        return SimpleNamespace(archive_dir=root / values["session"], reused_download=True)

    def session_loader(**kwargs):
        calls.append("laps")
        names = kwargs["sessions"]
        return SimpleNamespace(
            sessions={name: pd.DataFrame({"LapNumber": [1]}) for name in names},
            sources={name: "wodata-cache" for name in names},
            cache_paths={name: root / name / "laps.pkl" for name in names},
        )

    def tyre_runner(**kwargs):
        calls.append("tyre")
        assert kwargs == {"season": 2026, "data_root": root, "processed_previews_only": True,
                          "refresh_fp_calibration": False}
        return SimpleNamespace(predictions=[SimpleNamespace(round_number=16)])

    def tyre_provider(**kwargs):
        calls.append("validated tyre")
        return SimpleNamespace(provenance={"path": str(root / "prediction.json")}, artifact_id="tyre-id")

    def model_config_ensurer(**kwargs):
        calls.append("FP model")
        assert kwargs["require_fp_evidence"] is True
        assert kwargs["session_names"] == sessions
        return {"status": "reused", "path": str(root / "model_config.json")}

    return dict(event_resolver=event_resolver, fp_fetcher=fp_fetcher,
                session_loader=session_loader, tyre_runner=tyre_runner,
                tyre_provider=tyre_provider, model_config_ensurer=model_config_ensurer)


def _config(root):
    return RaceConfig.parse({"data_root": str(root)})


def test_r16_order_and_rerun_preserve_existing_producer_contracts(tmp_path):
    _preview(tmp_path)
    calls = []
    dependencies = _dependencies(tmp_path, calls)
    first = prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)
    assert calls == ["FP1", "FP2", "FP3", "laps", "tyre", "validated tyre", "FP model"]
    assert first["sessions"] == ["FP1", "FP2", "FP3"]
    assert all(row["reused"] for row in first["fp_archives"].values())
    assert first["live_mc_model_config"]["status"] == "reused"
    assert first == prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)
    assert not list(tmp_path.rglob("report_bundle.*"))


@pytest.mark.parametrize("problem", ["missing", "review", "identity", "changed image"])
def test_processed_pirelli_errors_stop_before_fp_download(tmp_path, problem):
    if problem != "missing":
        _preview(tmp_path)
        path = preview_json_path(2026, 16, tmp_path)
        payload = json.loads(path.read_text())
        if problem == "review":
            payload.update(asphalt_grip=None, review_status="manual_review_required")
        elif problem == "identity":
            payload["round"] = 15
        else:
            (path.parent.parent / payload["source_file"]).write_bytes(b"new-image")
        path.write_text(json.dumps(payload))
    calls = []
    with pytest.raises(RacePreparationPrerequisiteError, match="processed Pirelli input"):
        prepare_race_prerequisites(_config(tmp_path), event="2026-16",
                                   **_dependencies(tmp_path, calls))
    assert calls == []


def test_sprint_fetches_only_practice_sessions_present_in_schedule(tmp_path):
    _preview(tmp_path)
    calls = []
    result = prepare_race_prerequisites(
        _config(tmp_path), event="2026-16",
        **_dependencies(tmp_path, calls, ("Practice 1", "Sprint Qualifying", "Sprint", "Race")),
    )
    assert result["sessions"] == ["FP1"]
    assert calls[0:2] == ["FP1", "laps"]


@pytest.mark.parametrize("broken", ["fp_fetcher", "session_loader", "tyre_runner",
                                     "tyre_provider", "model_config_ensurer"])
def test_stage_failure_never_starts_later_stages(tmp_path, broken):
    _preview(tmp_path)
    calls = []
    dependencies = _dependencies(tmp_path, calls)

    def fail(*args, **kwargs):
        calls.append("failed")
        raise RuntimeError("specific producer failure")

    dependencies[broken] = fail
    with pytest.raises(RacePreparationPrerequisiteError, match="specific producer failure"):
        prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)
    assert calls[-1] == "failed"
    assert not list(tmp_path.rglob("report_bundle.*"))


def test_missing_fp2_cannot_be_silently_excluded_from_required_acquisition(tmp_path):
    _preview(tmp_path)
    calls = []
    dependencies = _dependencies(tmp_path, calls)
    original_loader = dependencies["session_loader"]

    def unavailable(**kwargs):
        loaded = original_loader(**kwargs)
        del loaded.sessions["FP2"]
        loaded.sources["FP2"] = "FastF1 returned no laps"
        return loaded

    dependencies["session_loader"] = unavailable
    with pytest.raises(RacePreparationPrerequisiteError, match="FP2: FastF1 returned no laps"):
        prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)
    assert calls == ["FP1", "FP2", "FP3", "laps"]


def test_not_ready_tyre_model_reports_history_prerequisite(tmp_path):
    _preview(tmp_path)
    dependencies = _dependencies(tmp_path, [])
    dependencies["tyre_runner"] = lambda **kwargs: SimpleNamespace(
        predictions=[], training_data_path=tmp_path / "training_data.csv",
    )
    with pytest.raises(RacePreparationPrerequisiteError, match="Race Retro summaries.*readiness"):
        prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)


def test_unavailable_event_metadata_is_a_clear_prerequisite_failure(tmp_path):
    calls = []
    dependencies = _dependencies(tmp_path, calls)

    def missing_event(*args, **kwargs):
        raise LookupError("event schedule unavailable")

    dependencies["event_resolver"] = missing_event
    with pytest.raises(RacePreparationPrerequisiteError, match="event metadata.*schedule unavailable"):
        prepare_race_prerequisites(_config(tmp_path), event="2026-16", **dependencies)
    assert calls == []


def test_cli_runs_prerequisites_before_race_and_passes_prepared_model(tmp_path, monkeypatch, capsys):
    from woweekend.cli.race import main
    import woweekend.workflows.race as race
    import woweekend.workflows.race_prerequisites as prerequisites

    config_path = tmp_path / "race.json"
    config_path.write_text(json.dumps(RaceConfig.template()))
    calls = []
    prepared = {"data_root": str(tmp_path), "live_mc_model_config": {"status": "generated"}}

    def prepare(config, **kwargs):
        calls.append("prerequisites")
        return prepared

    def run(config, **kwargs):
        calls.append("race")
        assert config.data_root == str(tmp_path)
        assert kwargs["prerequisites"] is prepared
        assert kwargs["model_config_ensurer"]() == {"status": "generated"}
        assert kwargs["pit_loss_total"] == 22
        return SimpleNamespace(path=tmp_path / "run", load_json=lambda name: {"status": "SUCCESS"})

    monkeypatch.setattr(prerequisites, "prepare_race_prerequisites", prepare)
    monkeypatch.setattr(race, "run_race", run)
    monkeypatch.setattr(sys, "argv", ["woweekend-race", "--event", "2026-16",
                                    "--config", str(config_path), "--pit-loss-total", "22"])
    main()
    assert calls == ["prerequisites", "race"]
    assert str(tmp_path / "run") in capsys.readouterr().out


def test_cli_prerequisite_failure_has_nonzero_exit_and_no_race(tmp_path, monkeypatch, capsys):
    from woweekend.cli.race import main
    import woweekend.workflows.race as race
    import woweekend.workflows.race_prerequisites as prerequisites

    path = tmp_path / "race.json"
    path.write_text(json.dumps(RaceConfig.template()))

    def fail(*args, **kwargs):
        raise RacePreparationPrerequisiteError("2026-16: missing processed Pirelli input")

    monkeypatch.setattr(prerequisites, "prepare_race_prerequisites", fail)
    monkeypatch.setattr(race, "run_race", lambda *args, **kwargs: pytest.fail("race started"))
    monkeypatch.setattr(sys, "argv", ["woweekend-race", "--event", "2026-16", "--config", str(path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert "missing processed Pirelli input" in capsys.readouterr().err


def test_cli_config_generation_does_not_run_prerequisites(tmp_path, monkeypatch):
    from woweekend.cli.race import main
    import woweekend.workflows.race_prerequisites as prerequisites

    monkeypatch.setattr(prerequisites, "prepare_race_prerequisites",
                        lambda *args, **kwargs: pytest.fail("prerequisites started"))
    path = tmp_path / "race.json"
    monkeypatch.setattr(sys, "argv", ["woweekend-race", "--generate-config", str(path)])
    main()
    assert RaceConfig.parse(json.loads(path.read_text()))


def test_cli_report_only_does_not_run_prerequisites(tmp_path, monkeypatch):
    from woweekend.cli.race import main
    import woweekend.cli.common as common
    import woweekend.workflows.race_prerequisites as prerequisites

    monkeypatch.setattr(prerequisites, "prepare_race_prerequisites",
                        lambda *args, **kwargs: pytest.fail("prerequisites started"))
    monkeypatch.setattr(common.WeekendRunStore, "load",
                        lambda **kwargs: SimpleNamespace(path=tmp_path))
    monkeypatch.setattr(common, "regenerate_report_bundle", lambda **kwargs: tmp_path / "bundle")
    monkeypatch.setattr(sys, "argv", ["woweekend-race", "--event", "2026-16",
                                    "--report-only", "saved-run"])
    main()
