import json
from pathlib import Path

from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle


def test_bundle_has_required_files_and_limit(tmp_path: Path) -> None:
    bundle = create_report_bundle(run_path=tmp_path, workflow="race_preparation", event="event", context={}, results={})
    files = [path for path in bundle.rglob("*") if path.is_file()]
    assert len(files) <= 20
    assert {path.name for path in files} >= {"CHATGPT_PROMPT.md", "REPORT_INSTRUCTION.md", "report_context.json", "analysis_results.json", "figure_manifest.json"}
    assert not any("telemetry" in path.name.lower() for path in files)


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
