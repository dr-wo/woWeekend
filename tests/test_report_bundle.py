import json
from pathlib import Path

from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle


def test_bundle_has_required_files_and_limit(tmp_path: Path) -> None:
    bundle = create_report_bundle(run_path=tmp_path, workflow="race_preparation", event="event", context={}, results={})
    files = [path for path in bundle.rglob("*") if path.is_file()]
    assert len(files) <= 20
    assert {path.name for path in files} >= {"CHATGPT_PROMPT.md", "REPORT_INSTRUCTION.md", "report_context.json", "analysis_results.json", "figure_manifest.json"}
    assert not any("telemetry" in path.name.lower() for path in files)
    instruction = (bundle / "REPORT_INSTRUCTION.md").read_text()
    assert "render the supplied `cutoff_table` rows" in instruction
    assert "0→1" in instruction
    assert "`N/A`" in instruction


def test_report_only_uses_saved_outputs(tmp_path: Path) -> None:
    (tmp_path / "manifest.json").write_text(json.dumps({"workflow": "race_preparation", "event": "event", "warnings": []}))
    (tmp_path / "analysis_results.json").write_text("{}")
    output = regenerate_report_bundle(run_path=tmp_path, language="en-GB")
    assert (output / "report_context.json").exists()


def test_bundle_consolidates_tables_and_only_copies_real_figures(tmp_path: Path) -> None:
    image = tmp_path / "cutoff.png"
    image.write_bytes(b"png")
    csv = tmp_path / "small_table.csv"
    csv.write_text("a,b\n1,2\n")
    bundle = create_report_bundle(
        run_path=tmp_path, workflow="race_preparation", event="2026-07",
        context={}, results={"table": [{"a": 1, "b": 2}]}, figures=(image, csv),
    )
    assert (bundle / "figures" / "cutoff.png").exists()
    assert not (bundle / "figures" / "small_table.csv").exists()


def test_bundle_preserves_cutoff_coverage_and_reports_legacy_limitations(tmp_path: Path) -> None:
    from wodata.contracts import StrategySearchState
    from wostrategy.algorithm.exact_strategy_search import StrategyModel
    from wostrategy.analysis.degradation_cutoff import calculate_degradation_cutoffs

    cutoff = calculate_degradation_cutoffs(
        model=StrategyModel({"MEDIUM": 0.0}, {"MEDIUM": 0.2}),
        state=StrategySearchState(0, 12, "MEDIUM", 0, None, 3), pit_loss=2.0,
        scan_min=0.05, scan_max=0.15, coarse_step=0.05,
        evaluator=lambda value, stops, rules: float(stops),
    ).to_dict()
    results = {"cutoff_unrestricted": {"status": "SUCCESS", "output": cutoff}}
    bundle = create_report_bundle(
        run_path=tmp_path, workflow="race_preparation", event="2026-16",
        context={}, results=results,
    )
    assert json.loads((bundle / "analysis_results.json").read_text()) == json.loads(json.dumps(results))
    row = json.loads((bundle / "analysis_results.json").read_text())["cutoff_unrestricted"]["output"]["cutoff_table"][2]
    assert row["coverage"]["upper_boundary"]["higher_minus_lower_cost"] == 1.0
    assert row["cutoff_medium_degradation"] is None
    instruction = (bundle / "REPORT_INSTRUCTION.md").read_text()
    assert "not found through" in instruction
    assert "scan coverage unavailable" in instruction
    assert "Never describe an unavailable class as a losing strategy" in instruction
    assert "s/lap per tyre-age lap" in instruction
    assert "scalar contextual coverage anchors only" in instruction
    assert "coverage incomplete due to explicit scan_max" in instruction
    assert "never infer 4→5" in instruction
    assert "Absence of anchor metadata in legacy artifacts" in instruction
