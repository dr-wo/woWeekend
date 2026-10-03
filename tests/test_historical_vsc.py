from types import SimpleNamespace

import pandas as pd
import pytest

from wodata.weekend_runs import WeekendRunStore
from woweekend.historical_vsc import (
    ESTIMATOR_SEMANTICS,
    load_single_race_vsc_evidence,
    query_historical_vsc_evidence,
)


def _metadata(*, season: int = 2026, layout_version: str | None = None):
    return SimpleNamespace(
        event_id=f"{season}-12",
        season=season,
        event_name="Dutch Grand Prix",
        official_event_name="FORMULA 1 DUTCH GRAND PRIX",
        country="Netherlands",
        location="Zandvoort",
        circuit_id="zandvoort",
        layout_version=layout_version,
    )


def _post_race(
    tmp_path,
    *,
    year: int,
    empirical: object | None,
    layout_version=None,
    generated_at: str | None = None,
):
    run = WeekendRunStore.create(
        event=f"{year}-15",
        workflow="post_race",
        data_root=tmp_path,
        generated_at=generated_at or f"{year}-09-01T12:00:00+00:00",
    )
    run.save_json(
        "event.metadata",
        {
            "country": "Netherlands",
            "location": "Zandvoort",
            "circuit_id": "zandvoort",
            "layout_version": layout_version,
        },
    )
    if empirical is not None:
        run.save_json("empirical_pit_loss", empirical)
    return run


def test_default_range_is_five_prior_seasons_and_never_operational(tmp_path):
    result = query_historical_vsc_evidence(
        metadata=_metadata(), data_root=tmp_path
    )

    assert result["year_range"] == [2021, 2025]
    assert [row["year"] for row in result["years"]] == list(range(2021, 2026))
    assert {row["status"] for row in result["years"]} == {"not_acquired"}
    assert result["aggregation"] is None
    assert result["operational_input"] is False
    assert result["candidate_available"] is False
    assert result["apply_available"] is False
    assert all(row["operational_input"] is False for row in result["years"])


def test_artifact_first_and_green_relative_value_is_evidence_only(tmp_path):
    _post_race(
        tmp_path,
        year=2025,
        empirical={
            "vsc": {
                "status": "available",
                "total": 11.8,
                "sample_count": 2,
                "samples": [{"driver": "AAA"}],
            }
        },
    )
    loader_calls = []
    result = query_historical_vsc_evidence(
        metadata=_metadata(),
        data_root=tmp_path,
        lookback_years=1,
        missing_loader=lambda *args: loader_calls.append(args),
    )

    row = result["years"][0]
    assert loader_calls == []
    assert row["status"] == "available_green_relative_excess"
    assert row["value_seconds"] == 11.8
    assert row["sample_count"] == 2
    assert row["estimator_semantics"] == ESTIMATOR_SEMANTICS
    assert row["candidate_available"] is False
    assert row["apply_available"] is False


def test_legacy_combined_artifact_is_not_promoted_and_falls_through(tmp_path):
    _post_race(
        tmp_path,
        year=2025,
        empirical={"sc_vsc": {"status": "available", "total": 8.9}},
    )

    calls = []
    row = query_historical_vsc_evidence(
        metadata=_metadata(),
        data_root=tmp_path,
        lookback_years=1,
        missing_loader=lambda year, identity: calls.append((year, identity)) or {
            "status": "vsc_no_qualified_pair",
            "sample_count": 0,
        },
    )["years"][0]

    assert [item[0] for item in calls] == [2025]
    assert row["status"] == "vsc_no_qualified_pair"
    assert "value_seconds" not in row
    assert row["operational_input"] is False
    assert row["artifact_context"]["artifacts"][0]["status"] == (
        "unsupported_legacy_combined"
    )


@pytest.mark.parametrize(
    ("invalid_total", "invalid_count"),
    [
        ("not-a-number", 1),
        (float("nan"), 1),
        (float("inf"), 1),
        (-1.0, 1),
        (11.8, 0),
    ],
)
def test_newest_malformed_available_artifact_does_not_shadow_older_valid_run(
    tmp_path, invalid_total, invalid_count,
):
    _post_race(
        tmp_path,
        year=2025,
        empirical={"vsc": {"status": "available", "total": 11.8, "sample_count": 2}},
        generated_at="2025-09-01T12:00:00+00:00",
    )
    _post_race(
        tmp_path,
        year=2025,
        empirical={
            "vsc": {
                "status": "available",
                "total": invalid_total,
                "sample_count": invalid_count,
                "samples": None,
            }
        },
        generated_at="2025-09-02T12:00:00+00:00",
    )

    row = query_historical_vsc_evidence(
        metadata=_metadata(), data_root=tmp_path, lookback_years=1
    )["years"][0]

    assert row["status"] == "available_green_relative_excess"
    assert row["value_seconds"] == 11.8


def test_real_insufficient_evidence_shape_uses_loader_for_classification(tmp_path):
    _post_race(
        tmp_path,
        year=2025,
        empirical={
            "vsc": {
                "status": "unavailable",
                "reason": "insufficient_evidence",
                "sample_count": 0,
            }
        },
    )

    row = query_historical_vsc_evidence(
        metadata=_metadata(),
        data_root=tmp_path,
        lookback_years=1,
        missing_loader=lambda *_: {
            "status": "no_vsc",
            "sample_count": 0,
            "reason": "Race laps contain no VSC TrackStatus code",
        },
    )["years"][0]

    assert row["status"] == "no_vsc"
    assert row["artifact_context"]["artifacts"][0]["status"] == (
        "artifact_insufficient_evidence"
    )


def test_malformed_unavailable_sample_count_falls_through_to_loader(tmp_path):
    _post_race(
        tmp_path,
        year=2025,
        empirical={
            "vsc": {
                "status": "unavailable",
                "reason": "insufficient_evidence",
                "sample_count": "bad",
            }
        },
    )

    calls = []
    row = query_historical_vsc_evidence(
        metadata=_metadata(),
        data_root=tmp_path,
        lookback_years=1,
        missing_loader=lambda year, identity: calls.append((year, identity)) or {
            "status": "no_vsc",
            "sample_count": 0,
            "reason": "Race laps contain no VSC TrackStatus code",
        },
    )["years"][0]

    assert [item[0] for item in calls] == [2025]
    assert row["status"] == "no_vsc"
    assert row["artifact_context"]["artifacts"][0]["status"] == (
        "artifact_parse_failed"
    )


def test_single_race_loader_matches_stable_identity_not_round_number():
    schedule = pd.DataFrame([
        {
            "Country": "Netherlands",
            "Location": "Zandvoort",
            "RoundNumber": 15,
        },
        {"Country": "Italy", "Location": "Monza", "RoundNumber": 16},
    ])
    loaded = []

    result = load_single_race_vsc_evidence(
        2025,
        {"country": "Netherlands", "location": "Zandvoort"},
        schedule_loader=lambda year: schedule,
        race_laps_loader=lambda year, round_number: loaded.append(
            (year, round_number)
        ) or pd.DataFrame({"TrackStatus": ["1", "16", "671"]}),
        estimator=lambda laps: {
            "vsc": {"status": "unavailable", "sample_count": 0, "samples": []}
        },
    )

    assert loaded == [(2025, 15)]
    assert result["source_event"] == "2025-15"
    assert result["status"] == "vsc_no_qualified_pair"
    assert result["sample_count"] == 0


def test_default_schedule_loader_excludes_testing_events(monkeypatch):
    calls = []
    schedule = pd.DataFrame([{
        "Country": "Netherlands",
        "Location": "Zandvoort",
        "RoundNumber": 15,
    }])

    def schedule_loader(year, *, include_testing):
        calls.append((year, include_testing))
        return schedule

    monkeypatch.setattr("fastf1.get_event_schedule", schedule_loader)
    result = load_single_race_vsc_evidence(
        2025,
        {"country": "Netherlands", "location": "Zandvoort"},
        race_laps_loader=lambda *_: pd.DataFrame({"TrackStatus": ["1"]}),
        estimator=lambda _laps: {},
    )

    assert calls == [(2025, False)]
    assert result["status"] == "no_vsc"


@pytest.mark.parametrize(
    "status",
    [
        "no_event",
        "no_vsc",
        "vsc_no_qualified_pair",
        "not_acquired",
        "load_failed",
        "parse_failed",
        "known_layout_mismatch",
        "unknown_layout",
    ],
)
def test_fallback_statuses_are_preserved_separately(tmp_path, status):
    row = query_historical_vsc_evidence(
        metadata=_metadata(),
        data_root=tmp_path,
        lookback_years=1,
        missing_loader=lambda *_: {"status": status, "reason": status},
    )["years"][0]

    assert row["status"] == status
    assert row["reason"] == status
    assert row["operational_input"] is False


def test_explicit_layout_mismatch_is_reported(tmp_path):
    _post_race(
        tmp_path,
        year=2025,
        empirical={"vsc": {"status": "available", "total": 11.8}},
        layout_version="old",
    )

    row = query_historical_vsc_evidence(
        metadata=_metadata(layout_version="current"),
        data_root=tmp_path,
        lookback_years=1,
    )["years"][0]

    assert row["status"] == "known_layout_mismatch"
    assert "layout_version differs" in row["reason"]


def test_lookback_is_configurable_and_excludes_target_year(tmp_path):
    result = query_historical_vsc_evidence(
        metadata=_metadata(), data_root=tmp_path, lookback_years=2
    )

    assert result["year_range"] == [2024, 2025]
    assert [row["year"] for row in result["years"]] == [2024, 2025]
    assert all(row["year"] != 2026 for row in result["years"])
