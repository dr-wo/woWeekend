"""DBG-10: exercise the default standings adapter through saved report inputs."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from wodata.weekend_runs import WeekendRunStore
from woweekend.config.post_race import PostRaceConfig
from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle
from woweekend.workflows.post_race import run_post_race
from wostanding.script import points_progression as script

from test_post import _event, _model_config, _providers, _retro


FIXTURES = Path(script.__file__).resolve().parents[3] / "tests/fixtures/dbg10"


def _install_season(monkeypatch, published):
    history = pd.read_csv(FIXTURES / "published_r1_r15_event_points.csv")
    schedule = pd.concat([history[["RoundNumber", "EventName"]].drop_duplicates(),
        pd.DataFrame([{"RoundNumber": 16, "EventName": "Bahrain Grand Prix"}])], ignore_index=True)
    monkeypatch.setattr(script.fastf1, "get_event_schedule", lambda *a, **kw: schedule)

    def get_session(year, round_number, session_name):
        assert session_name == "R"
        if round_number < 16:
            results = history.loc[history.RoundNumber.eq(round_number)].rename(
                columns={"Driver": "Abbreviation", "Team": "TeamName"})
        elif published:
            payload = json.loads((FIXTURES / "published_r16_upstream.json").read_text())
            rows = payload["payload"]["MRData"]["RaceTable"]["Races"][0]["Results"]
            results = pd.read_json(FIXTURES / "published_r16_session_results.json")
            assert dict(zip(results.Abbreviation, results.Points)) == {
                row["Driver"]["code"]: float(row["points"]) for row in rows}
        else:
            results = pd.read_json(FIXTURES / "empty_r16_session_results.json")
        return SimpleNamespace(results=results, load=lambda **kw: None)

    monkeypatch.setattr(script.fastf1, "get_session", get_session)


@pytest.mark.parametrize("published", [False, True])
def test_recovered_r16_flows_through_workflow_artifacts_and_report(monkeypatch, tmp_path, published):
    _install_season(monkeypatch, published)
    pre = WeekendRunStore.create(event="2026-16", workflow="race_preparation", data_root=tmp_path,
                                 generated_at="2026-10-03T12:00:00+00:00")
    pre.save_json("tyre_prediction", _retro())
    pre.save_json("config.resolved", {"resolved_total_laps": 6, "pit_loss": {"green": {"total": 20}}})
    pre.save_manifest({"status": "SUCCESS"})
    raw = PostRaceConfig.template()
    raw.update({"data_root": str(tmp_path), "pre_race_run_id": pre.run_id})
    providers = _providers(lambda **kw: {"updates": [{"leader_lap": 6}],
                                        "replay": {"complete": True}})
    # Exercise the production standings adapter, with only FastF1 IO stubbed.
    providers.pop("standings")
    metadata = replace(_event(), event_id="2026-16", round_number=16,
                       event_name="Bahrain Grand Prix", race_start="2026-10-04T07:00:00+00:00")
    run = run_post_race(PostRaceConfig.parse(raw), event="2026-16", input_config=raw,
                       providers=providers, model_config_ensurer=_model_config,
                       event_resolver=lambda *a, **kw: metadata)
    results = run.load_json("analysis_results")
    standing = results["standings"]
    assert results["pre_race_artifacts"]["status"] == "SUCCESS"
    assert results["retro_tyre_estimate"]["status"] == "SUCCESS"
    assert results["race_performance"]["status"] == "SUCCESS"
    assert results["live_mc_history"]["status"] == "SUCCESS"
    output = run.load_json("standings")
    assert standing["output"] == output
    bundle = next(run.path.glob("report_bundle.*"))
    bundled = json.loads((bundle / "analysis_results.json").read_text())
    assert bundled["standings"] == standing
    manifest = run.load_json("manifest")
    if published:
        assert standing["status"] == "SUCCESS"
        assert standing["warnings"] == []
        expected = json.loads((FIXTURES / "verified_r16_totals.json").read_text())
        for owner, entity in [("drivers", "Driver"), ("constructors", "Team")]:
            rows = [r for r in output[owner]["actual"] if r["RoundNumber"] == 16]
            assert {r[entity]: r["CumulativePoints"] for r in rows} == expected[owner]
        assert (bundle / "figures/standings_drivers.png").exists()
        assert (bundle / "figures/standings_teams.png").exists()
        assert not any("current-round standings" in w for w in manifest["warnings"])
    else:
        assert standing["status"] == manifest["outputs"]["standings"] == "UNAVAILABLE"
        assert manifest["status"] == "PARTIAL"
        assert output["round_number"] == 16 and output["reason"] == "upstream_results_unavailable"
        assert output["drivers"] == output["constructors"] == {"actual": [], "actual_plus_projected": []}
        warning = standing["warnings"][0]
        assert "R16 Race results are not available yet" in warning
        assert warning in manifest["warnings"]
        assert warning in run.load_json("report_context")["warnings"]
        assert warning in json.loads((bundle / "report_context.json").read_text())["warnings"]
        assert not list((run.path / "figures").glob("standings_*"))
        assert not list((bundle / "figures").glob("standings_*"))
        instruction = (bundle / "REPORT_INSTRUCTION.md").read_text()
        assert "do not present previous-round totals as current-round standings" in instruction
    regenerated = regenerate_report_bundle(run_path=run.path, language="en-GB")
    assert json.loads((regenerated / "analysis_results.json").read_text())["standings"] == standing


def test_report_bundle_filters_old_standings_figures_when_unavailable(tmp_path):
    paths = []
    for name in ("standings_drivers.png", "standings_teams.png", "race_performance_tracker_team_baseline.png"):
        path = tmp_path / name
        path.write_bytes(b"png")
        paths.append(path)
    bundle = create_report_bundle(run_path=tmp_path, workflow="post_race", event="2026-16",
        context={}, results={"standings": {"status": "UNAVAILABLE", "warnings": ["R16 pending"]}},
        figures=paths)
    assert not list((bundle / "figures").glob("standings_*"))
    assert (bundle / "figures/race_performance_tracker_team_baseline.png").exists()
    assert not any("standings_" in row["file"] for row in
                   json.loads((bundle / "figure_manifest.json").read_text())["figures"])
