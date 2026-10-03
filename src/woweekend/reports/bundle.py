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
    ], key=lambda path: (0 if workflow == "race_preparation" and path.name.startswith("traffic_history_") else 1 if path.name.startswith("live_mc_") else 2, path.name))
    base_files = 7 if workflow == "race_preparation" else 5
    if len(figure_paths) + base_files > MAX_BUNDLE_FILES:
        figure_paths = figure_paths[: MAX_BUNDLE_FILES - base_files]
    (root / "figures").mkdir(parents=True)
    _write(root / "CHATGPT_PROMPT.md", _prompt(language))
    _write(root / "REPORT_INSTRUCTION.md", _instruction(language, template, workflow))
    _json(root / "report_context.json", {**dict(context), "workflow": workflow, "event": event, "language": language, "template": template})
    _json(root / "analysis_results.json", results)
    if workflow == "race_preparation":
        from wodata.interruption_history import unavailable_history
        from wostrategy.analysis.interruption_history import history_markdown
        history = context.get("historical_interruptions")
        if not isinstance(history, Mapping):
            history = unavailable_history(0, 0, "Historical interruption context unavailable in this legacy run")
        _write(root / "HISTORICAL_INTERRUPTION_CONTEXT.md", history_markdown(history))
        from .historical_traffic import traffic_markdown
        _write(root / "HISTORICAL_TRAFFIC_CONTEXT.md", traffic_markdown(
            context.get("historical_traffic"),
            [Path("figures") / path.name for path in figure_paths],
        ))
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
2. `Practice evidence`: report the supplied FP1/FP2/FP3 coordinates, support and structural-identifiability labels. Associate supplied AirTemp and TrackTemp ranges with their session/compound evidence, preserve independent coverage and unusual recorded-zero flags, and state explicitly when legacy provenance is unavailable. Do not reconstruct missing temperature context. State that FP is diagnostic-only and distinguish a posterior coordinate from a physically identifiable measurement.
3. `Automatic model baseline`: explain the selected P0/D0 or other supplied source, including whether Pirelli affected allocation, descriptors, or both.
4. `Automatic model result without manual override`: include this appendix only when the deterministic component is supplied; do not substitute FP diagnostic values.
5. `Degradation cutoffs`: render the supplied `cutoff_table` rows, including 0→1, 1→2, and 2→3. Show an unavailable 0→1 value as `N/A` with its supplied status; never infer a crossing from the figure or sweep.
6. `Historical VSC evidence`: when `previous_season_empirical_vsc` is available, report its empirical value, source event and sample support as context only. Explicitly state that it did not populate or alter current-event operational inputs. When unavailable, preserve the supplied reason.
7. `Prior-event operational assumptions`: report `previous_season_operational_inputs` separately from empirical VSC evidence. Label these as the values previously adopted by the operator, not observations, and state that they did not populate or alter current-event calculations. When unavailable, preserve the supplied reason.
8. `Historical Safety Car / Red Flag context`: include the supplied `HISTORICAL_INTERRUPTION_CONTEXT.md` table and `historical_interruptions` values. Show editions, SC periods/ranges, Red Flag occurrences/laps and unavailable coverage. Pure VSC is excluded. SC ranges cover laps active for any portion; withdrawal exactly at a lap boundary excludes the next lap. Red Flags use entry laps. Preserve minimum counts, uncertain fragments and unavailable laps; only affirmative TrackStatus integrity supports confirmed zeros. Preserve “No event observed; coverage unproven” when integrity is unknown. This is descriptive context only and does not alter strategy inputs.
9. `Historical Traffic`: include the authoritative `HISTORICAL_TRAFFIC_CONTEXT.md` fragment, grouped summary/conclusions and all five traffic figures from `historical_traffic`. Place this level-2 section immediately BEFORE the existing level-2 `Limitations` section, with no intervening section. Preserve the three groups, signed statistics, missingness, BLUE distinction and provenance exactly. The observed successful-pass minimum and median advantages are sample statistics, not physical requirements or a 50% success threshold. Render an explicit unavailable section when history is missing. Report rendering must never prepare or refit historical data.
10. `Limitations`: retain the report's existing limitations and provenance text immediately after Historical Traffic.

Preserve `(default)` in human-facing fallback/default source labels. Explicitly report freshness warnings without implying that FP modified production inputs.
"""
    return f"""# Report instruction\n\nGenerate {article} {template} report in `{language}` from the supplied deterministic artifacts. Explain the outputs, structure the narrative, compare supplied pre/post results, preserve supplied caveats and warnings, and integrate useful figures.{race}\n\nNumerical conclusions provided in JSON/CSV are authoritative. Do not derive replacement numerical conclusions from images. Do not recompute tyre degradation, select another strategy, alter cutoff values, determine sporting legality, calculate standings, or calculate unsupplied prediction errors. Clearly retain fallback provenance and unavailable components.\n"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
