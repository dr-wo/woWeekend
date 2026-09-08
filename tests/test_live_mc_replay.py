from pathlib import Path
from types import SimpleNamespace

import pytest

from woweekend.config.post_race import PostRaceConfig
from woweekend.reports.bundle import create_report_bundle
from woweekend.workflows.live_mc_replay import (
    _history_update,
    _manual_override_events,
    plot_live_mc_history,
)
from wodata.live.models import LiveTimingMessage
from datetime import datetime, timezone


def _summary(lap: int = 12):
    rows = []
    for compound, degradation, delta in (
        ("SOFT", .22, -.45), ("MEDIUM", .13, 0.0), ("HARD", .08, .35)
    ):
        for parameter, value in (("degradation", degradation), ("compound_delta", delta)):
            rows.append(
                {
                    "source_scope": "aggregate",
                    "parameter": parameter,
                    "compound": compound,
                    "median": value,
                    "p10": value - .02,
                    "p90": value + .02,
                    "support_status": "measured",
                    "usable_lap_count": 20,
                    "weighted_rmse": .42,
                }
            )
    return SimpleNamespace(
        manifest={
            "input_fingerprint": f"through-lap-{lap}",
            "config_hash": "config",
            "sampler_quality": {
                "observed_compounds": ("SOFT", "MEDIUM", "HARD"),
                "ess": 125.0,
                "ess_fraction": .25,
                "sample_count": 500,
                "weighted_rmse_seconds": .42,
                "best_rmse_seconds": .31,
                "fuel_track_alias": False,
            },
        },
        parameters=tuple(rows),
    )


def test_algorithm_only_is_default_and_modes_validate() -> None:
    assert PostRaceConfig.parse(PostRaceConfig.template()).live_replay.mode == "algorithm_only"
    raw = PostRaceConfig.template()
    raw["live_replay"]["mode"] = "operational"
    assert PostRaceConfig.parse(raw).live_replay.mode == "operational"
    raw["live_replay"]["mode"] = "hindsight"
    with pytest.raises(ValueError, match="algorithm_only"):
        PostRaceConfig.parse(raw)


def test_operational_override_does_not_change_calculated_history() -> None:
    result = SimpleNamespace(analysis_id="analysis", eligible_lap_count=40, sample_count=500)
    manual = {("degradation", "SOFT"): .9}
    algorithm = _history_update(
        summary=_summary(), leader_lap=12, timestamp="2026-01-01T00:00:00+00:00",
        result=result, baseline={(r["parameter"], r["compound"]): r["median"] for r in _summary().parameters}, mode="algorithm_only", manual_active=True,
        manual_values=manual,
    )
    operational = _history_update(
        summary=_summary(), leader_lap=12, timestamp="2026-01-01T00:00:00+00:00",
        result=result, baseline={(r["parameter"], r["compound"]): r["median"] for r in _summary().parameters}, mode="operational", manual_active=True,
        manual_values=manual,
    )
    first = algorithm["compounds"]["SOFT"]["degradation"]
    second = operational["compounds"]["SOFT"]["degradation"]
    assert first["calculated_value"] == second["calculated_value"] == pytest.approx(.22)
    assert first["effective_value"] == pytest.approx(.22)
    assert second["effective_value"] == pytest.approx(.9)
    assert second["manual_override"]["active"] is True


def test_historical_override_message_is_replayable() -> None:
    now = datetime.now(timezone.utc)
    message = LiveTimingMessage(
        "TyreModelOverride",
        {"active": True, "values": {"Deg_SOFT": .91, "Perf_HARD_vs_MEDIUM": .4}},
        now, now,
    )
    events = _manual_override_events((message,))
    assert events[0][1] is True
    assert events[0][2][("degradation", "SOFT")] == pytest.approx(.91)
    assert events[0][2][("compound_delta", "HARD")] == pytest.approx(.4)


def test_plots_are_english_and_prioritised_in_twenty_file_bundle(tmp_path: Path) -> None:
    result = SimpleNamespace(analysis_id="analysis", eligible_lap_count=40, sample_count=500)
    updates = [
        _history_update(
            summary=_summary(lap), leader_lap=lap,
            timestamp=f"2026-01-01T00:{lap:02d}:00+00:00", result=result,
            baseline={(r["parameter"], r["compound"]): r["median"] for r in _summary().parameters}, mode="algorithm_only", manual_active=False, manual_values={},
        )
        for lap in (10, 12, 14)
    ]
    figures = plot_live_mc_history(
        {"updates": updates}, figures_dir=tmp_path / "figures",
        retro_tyre_estimate={"compounds": {"SOFT": {"degradation_seconds_per_lap": .2}}},
    )
    extras = []
    for index in range(20):
        path = tmp_path / f"z{index:02d}.png"
        path.write_bytes(b"png")
        extras.append(path)
    bundle = create_report_bundle(
        run_path=tmp_path, workflow="post_race", event="2026-01", context={},
        results={}, figures=(*extras, *figures),
    )
    assert all((bundle / "figures" / path.name).is_file() for path in figures)
    assert sum(path.is_file() for path in bundle.rglob("*")) <= 20


def test_r13_live_sources_values_and_plot_survive_history_roundtrip(tmp_path, monkeypatch):
    import json
    import numpy as np
    from matplotlib.figure import Figure

    baseline = {
        (parameter, compound): value
        for compound, deg, delta in (("SOFT", .24, -.4), ("MEDIUM", .20, 0), ("HARD", .15, .3))
        for parameter, value in (("degradation", deg), ("compound_delta", delta))
    }
    result = SimpleNamespace(analysis_id="r13", eligible_lap_count=40, sample_count=500)
    updates = []
    for lap, direct in ((16, {}), (17, {"MEDIUM": .215}),
                        (18, {"MEDIUM": .351, "HARD": .219}),
                        (19, {"MEDIUM": .345, "HARD": .207})):
        summary = _summary(lap)
        summary.manifest["sampler_quality"].update(
            architecture="live-retro", observed_compounds=list(direct)
        )
        # The live sampler emits no SOFT row at all in R13.
        summary.parameters = tuple(
            dict(row, median=direct[row["compound"]] if row["parameter"] == "degradation"
                 else row["median"])
            for row in summary.parameters if row["compound"] in direct
        )
        updates.append(_history_update(
            summary=summary, leader_lap=lap, timestamp="2026-01-01T00:00:00Z",
            result=result, baseline=baseline, mode="algorithm_only",
            manual_active=False, manual_values={},
        ))
    history = json.loads(json.dumps({"updates": updates}))
    for compound, expected in {
        "SOFT": ["base", "live_derived", "live_derived", "live_derived"],
        "MEDIUM": ["base", "live_direct", "live_direct", "live_direct"],
        "HARD": ["base", "live_derived", "live_direct", "live_direct"],
    }.items():
        assert [u["compounds"][compound]["degradation"]["source"] for u in history["updates"]] == expected
    for update in history["updates"]:
        soft, medium, hard = [update["compounds"][c]["degradation"] for c in ("SOFT", "MEDIUM", "HARD")]
        assert soft["effective_value"] >= medium["effective_value"] >= hard["effective_value"]
        assert soft["effective_value"] == pytest.approx(medium["effective_value"] * 1.2)
        assert soft["calculated_value"] is None
        assert soft["informed"] is False
    assert updates[1]["compounds"]["HARD"]["degradation"]["algorithm_value"] == pytest.approx(.215 * .75)
    assert updates[2]["compounds"]["SOFT"]["degradation"]["anchor_compound"] == "MEDIUM"

    figures = []
    original = Figure.savefig
    def capture(figure, *args, **kwargs):
        figures.append(figure)
        return original(figure, *args, **kwargs)
    monkeypatch.setattr(Figure, "savefig", capture)
    plot_live_mc_history(history, figures_dir=tmp_path, retro_tyre_estimate={})
    lines = {line.get_label(): line for line in figures[0].axes[0].lines}
    assert not any("assumed" in label or "informed" in label for label in lines)
    assert lines["SOFT (base)"].get_linestyle() == ":"
    assert lines["SOFT (live_derived)"].get_linestyle() == "--"
    assert lines["HARD (live_direct)"].get_linestyle() == "-"
    np.testing.assert_allclose(lines["SOFT (live_derived)"].get_ydata(),
                               [np.nan, .258, .4212, .414], equal_nan=True)
    np.testing.assert_allclose(lines["HARD (live_derived)"].get_ydata(),
                               [np.nan, .16125, np.nan, np.nan], equal_nan=True)
    # Operational figures must use the effective override, not algorithm_value.
    value = history["updates"][2]["compounds"]["SOFT"]["degradation"]
    value.update(effective_value=.9, effective_source="manual_override")
    plot_live_mc_history(history, figures_dir=tmp_path, retro_tyre_estimate={})
    lines = {line.get_label(): line for line in figures[2].axes[0].lines}
    assert lines["SOFT (manual_override)"].get_ydata()[2] == .9
    assert np.isnan(lines["SOFT (live_derived)"].get_ydata()[2])
