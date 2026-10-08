import json

from wostrategy.algorithm.exact_strategy_search import StrategyModel
from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle
from woweekend.reports.modelled_undercut import FIGURE, save_modelled_undercut


class SavedRun:
    def __init__(self, path):
        self.path = path

    def save_json(self, name, value):
        (self.path / f"{name}.json").write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")


def test_saved_output_is_independent_of_live_and_bsf_and_reproducible(tmp_path):
    run = SavedRun(tmp_path)
    model = StrategyModel({"SOFT": -.4, "HARD": .3}, {"SOFT": .2, "HARD": .05})
    (tmp_path / "live_observed_undercut.json").write_text('{"estimate": 99999}')
    result, figure = save_modelled_undercut(run, model, 8)
    first = (tmp_path / "modelled_undercut_power.json").read_bytes()
    (tmp_path / "live_observed_undercut.json").write_text("invalid JSON must never be read")
    save_modelled_undercut(run, model, 8)
    assert first == (tmp_path / "modelled_undercut_power.json").read_bytes()
    assert figure.read_bytes().startswith(b"\x89PNG")
    results = {"modelled_undercut_power": {"status": "SUCCESS", "output": result},
               "bsf_junction_reference": {"status": "DEGRADED", "warnings": ["VER TyreLife"]}}
    bundle = create_report_bundle(run_path=tmp_path, workflow="race_preparation", event="2026-16",
                                  context={}, results=results, figures=[figure])
    assert (bundle / "figures" / FIGURE).exists()
    fragment = (bundle / "MODELLED_UNDERCUT_POWER.md").read_text()
    assert "theoretical model age-zero anchor" in fragment
    assert "Live Observed" in fragment
    assert json.loads((bundle / "analysis_results.json").read_text()) == results
    assert json.loads((bundle / "figure_manifest.json").read_text())["figures"][0]["title"] == result["title"]
    run.save_json("manifest", {"workflow": "race_preparation", "event": "2026-16"})
    run.save_json("analysis_results", results)
    regenerated = regenerate_report_bundle(run_path=tmp_path, language="en-GB")
    assert (bundle / "analysis_results.json").read_bytes() == (regenerated / "analysis_results.json").read_bytes()
    assert (bundle / "MODELLED_UNDERCUT_POWER.md").read_bytes() == (regenerated / "MODELLED_UNDERCUT_POWER.md").read_bytes()


def test_undercut_figure_reserved_within_bundle_budget(tmp_path):
    run = SavedRun(tmp_path)
    result, figure = save_modelled_undercut(run, StrategyModel({"HARD": 0}, {"HARD": .1}), 2)
    figures = [figure]
    for n in range(25):
        path = tmp_path / f"traffic_history_{n:02}.png"
        path.write_bytes(b"png")
        figures.append(path)
    bundle = create_report_bundle(run_path=tmp_path, workflow="race_preparation", event="2026-16",
                                  context={}, results={"modelled_undercut_power": {"output": result}}, figures=figures)
    assert (bundle / "figures" / FIGURE).exists()
    assert len([p for p in bundle.rglob("*") if p.is_file()]) == 20
