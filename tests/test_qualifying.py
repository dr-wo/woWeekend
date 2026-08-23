from pathlib import Path

from wodata.weekend_runs import WeekendRunStore
from woweekend.workflows.qualifying import QualifyingSnapshotRecorder, effective_track_value
from woweekend.workflows.qualifying import historical_qualifying_transitions

import pandas as pd


def test_manual_override_stays_effective() -> None:
    first = effective_track_value(-0.02, manual_active=True, manual_value=-0.05)
    second = effective_track_value(-0.03, manual_active=True, manual_value=-0.05)
    assert first["effective_value"] == second["effective_value"] == -0.05
    assert effective_track_value(-0.03, manual_active=False, manual_value=-0.05)["effective_value"] == -0.03


def test_only_transition_snapshots_are_saved(tmp_path: Path) -> None:
    run = WeekendRunStore.create(event="event", workflow="qualifying_preparation", data_root=tmp_path)
    recorder = QualifyingSnapshotRecorder(run)
    recorder.save_transition("Q1", {"value": 1})
    recorder.save_transition("Q1", {"value": 2})
    assert run.load_json("snapshot.Q1") == {"value": 1}


def test_historical_transitions_use_q_only_shared_estimator() -> None:
    part = pd.DataFrame({
        "LapTime": pd.to_timedelta(["0:01:20", "0:01:20.4", "0:01:20.8"]),
    })
    class HistoricalLaps:
        def pick_accurate(self):
            return self

        def split_qualifying_sessions(self):
            return [part, part, part]

    result = historical_qualifying_transitions(
        HistoricalLaps(), session="Q", push_threshold_fraction=0.02,
    )
    assert [part for part, _ in result] == ["Q1", "Q2", "Q3"]
    assert all(value["source_scope"] == "QUALIFYING_ONLY" for _, value in result)
    assert all(value["manual_override"] == {"active": False, "value": None} for _, value in result)
