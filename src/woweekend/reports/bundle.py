from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping


MAX_BUNDLE_FILES = 20


def create_report_bundle(
    *,
    run_path: str | Path,
    workflow: str,
    event: str,
    context: Mapping[str, Any],
    results: Mapping[str, Any],
    figures: Iterable[str | Path] = (),
    language: str = "zh-CN",
    template: str = "engineering",
    output_name: str | None = None,
) -> Path:
    root = Path(run_path) / (output_name or f"report_bundle.{language}.{template}")
    if root.exists():
        raise FileExistsError(f"Report bundle already exists: {root}")
    image_suffixes = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    figure_paths = sorted([
        Path(value) for value in figures
        if Path(value).exists() and Path(value).suffix.lower() in image_suffixes
    ], key=lambda path: (0 if path.name.startswith("live_mc_") else 1, path.name))
    if len(figure_paths) + 5 > MAX_BUNDLE_FILES:
        figure_paths = figure_paths[: MAX_BUNDLE_FILES - 5]
    (root / "figures").mkdir(parents=True)
    _write(root / "CHATGPT_PROMPT.md", _prompt(language))
    _write(root / "REPORT_INSTRUCTION.md", _instruction(language, template, workflow))
    _json(root / "report_context.json", {**dict(context), "workflow": workflow, "event": event, "language": language, "template": template})
    _json(root / "analysis_results.json", results)
    manifest = []
    for source in figure_paths:
        destination = root / "figures" / source.name
        shutil.copy2(source, destination)
        manifest.append({"file": f"figures/{source.name}", "purpose": "supplementary deterministic figure", "labels": "English"})
    _json(root / "figure_manifest.json", {"figures": manifest})
    count = sum(1 for path in root.rglob("*") if path.is_file())
    if count > MAX_BUNDLE_FILES:
        raise RuntimeError(f"Report bundle contains {count} files; maximum is {MAX_BUNDLE_FILES}")
    return root


def regenerate_report_bundle(*, run_path: str | Path, language: str, template: str = "engineering") -> Path:
    run = Path(run_path)
    with (run / "manifest.json").open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    with (run / "analysis_results.json").open(encoding="utf-8") as handle:
        results = json.load(handle)
    context_path = run / "report_context.json"
    context = json.loads(context_path.read_text(encoding="utf-8")) if context_path.exists() else {"warnings": manifest.get("warnings", [])}
    figures = [path for path in (run / "figures").glob("*") if path.is_file()] if (run / "figures").exists() else []
    return create_report_bundle(run_path=run, workflow=str(manifest["workflow"]), event=str(manifest["event"]), context=context, results=results, figures=figures, language=language, template=template)


def _prompt(language: str) -> str:
    return f"""Follow `REPORT_INSTRUCTION.md` and generate the requested {language} report.\n\nNumerical conclusions in JSON/CSV are authoritative. Images are for interpretation and presentation only. Do not recompute or replace deterministic conclusions.\n"""


def _instruction(language: str, template: str, workflow: str = "") -> str:
    article = "an" if template[:1].lower() in "aeiou" else "a"
    race = "" if workflow != "race_preparation" else """

For a race-preparation report, use these explicit sections:

1. `Tyre inputs used for strategy`: lead with effective values and their exact source family. Manual overrides, when present, drive all primary conclusions.
2. `Practice evidence`: report the supplied FP1/FP2/FP3 coordinates, support and structural-identifiability labels. State that FP is diagnostic-only and distinguish a posterior coordinate from a physically identifiable measurement.
3. `Automatic model baseline`: explain the selected P0/D0 or other supplied source, including whether Pirelli affected allocation, descriptors, or both.
4. `Automatic model result without manual override`: include this appendix only when the deterministic component is supplied; do not substitute FP diagnostic values.

Preserve `(default)` in human-facing fallback/default source labels. Explicitly report freshness warnings without implying that FP modified production inputs.
"""
    return f"""# Report instruction\n\nGenerate {article} {template} report in `{language}` from the supplied deterministic artifacts. Explain the outputs, structure the narrative, compare supplied pre/post results, preserve supplied caveats and warnings, and integrate useful figures.{race}\n\nNumerical conclusions provided in JSON/CSV are authoritative. Do not derive replacement numerical conclusions from images. Do not recompute tyre degradation, select another strategy, alter cutoff values, determine sporting legality, calculate standings, or calculate unsupplied prediction errors. Clearly retain fallback provenance and unavailable components.\n"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
