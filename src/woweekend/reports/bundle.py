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
    if workflow == "post_race":
        standings = results.get("standings", {})
        if isinstance(standings, Mapping) and standings.get("status") == "UNAVAILABLE":
            figure_paths = [p for p in figure_paths if not p.name.startswith("standings_")]
        from .bsf_junction import bsf_has_output
        bsf = results.get("bsf_junction_reference", context.get("bsf_junction_reference")) or {}
        if not bsf_has_output(bsf):
            figure_paths = [p for p in figure_paths if not p.name.startswith("bsf_junction_reference_")]
        # Reserve both adjacent trackers before applying the attachment budget.
        canonical = [p for p in figure_paths if p.name == "race_performance_tracker_team_baseline.png"]
        companion = [p for p in figure_paths if p.name == "bsf_junction_reference_mercedes.png"]
        preferred = canonical + companion
        figure_paths = preferred + [p for p in figure_paths if p not in preferred]
        # PNGs are the authoring assets; vector exports remain in the run directory.
        figure_paths = [p for p in figure_paths if not (
            p.name.startswith("bsf_junction_reference_") and p.suffix == ".svg")]
    has_undercut = bool(results.get("modelled_undercut_power", {}).get("output"))
    if has_undercut:
        from .modelled_undercut import FIGURE
        figure_paths.sort(key=lambda path: path.name != FIGURE)
    base_files = (7 if workflow == "race_preparation" else 6 if workflow == "post_race" else 5) + int(has_undercut)
    if len(figure_paths) + base_files > MAX_BUNDLE_FILES:
        figure_paths = figure_paths[: MAX_BUNDLE_FILES - base_files]
    (root / "figures").mkdir(parents=True)
    _write(root / "CHATGPT_PROMPT.md", _prompt(language))
    _write(root / "REPORT_INSTRUCTION.md", _instruction(language, template, workflow))
    _json(root / "report_context.json", {**dict(context), "workflow": workflow, "event": event, "language": language, "template": template})
    _json(root / "analysis_results.json", results)
    if has_undercut:
        from .modelled_undercut import undercut_markdown
        _write(root / "MODELLED_UNDERCUT_POWER.md", undercut_markdown(results["modelled_undercut_power"]))
    if workflow == "post_race":
        from .bsf_junction import bsf_markdown
        _write(root / "BSF_JUNCTION_REFERENCE.md", bsf_markdown(
            results.get("bsf_junction_reference", context.get("bsf_junction_reference")),
            [Path("figures") / path.name for path in figure_paths],
        ))
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
        entry = {"file": f"figures/{source.name}", "purpose": "supplementary deterministic figure", "labels": "English"}
        if source.name == "modelled_undercut_power.png":
            entry.update(title="Modelled / predicted undercut power",
                         purpose="tyre-only effective strategy model; theoretical age-zero replacement anchor")
        if workflow == "post_race" and source.name == "bsf_junction_reference_mercedes.png":
            from .bsf_junction import CAPTION, TITLE
            entry.update(title=TITLE, caption=CAPTION, purpose="experimental BSF companion; canonical remains primary",
                         placement="immediately after canonical race-performance tracker section")
        elif workflow == "post_race" and source.name.startswith("bsf_junction_reference_"):
            entry.update(purpose="optional BSF diagnostic/provenance; not an authoritative ranking")
        manifest.append(entry)
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
5. `Degradation cutoffs`: render the supplied `cutoff_table` rows, including 0→1, 1→2, 2→3 and 3→4 when supplied and permitted by the event; never infer 4→5. The scanned scalar is absolute MEDIUM degradation in s/lap per tyre-age lap; other compound degradations scale proportionally, preserving the effective model ratios. Report initial and actual scan bounds (`initial_scan_min/max`, `effective_scan_min/max`) and each transition's tested `coverage.scan_min/max`. Preserve the supplied result status and `display_text` when available. Found values are numerical cutoffs; rules-unavailable 0→1 is `N/A — unavailable under rules`. An unresolved cutoff must say `not found through` the tested upper bound, not merely `N/A` or `cutoff > bound`. Use `coverage.status`, feasible flags and adjacent comparable sample counts to distinguish both classes feasible, lower/higher/both classes unavailable, and insufficient comparable samples. When comparable, report the supplied upper-boundary winner and margin in seconds: `higher_minus_lower_cost = higher-stop cost - lower-stop cost`, positive means the lower-stop class is cheaper. Never describe an unavailable class as a losing strategy. These are boundary comparisons, not proofs outside the tested range. For legacy artifacts without coverage fields, state `scan coverage unavailable`; do not invent bounds, margins or replacement values. When `anchor_coverage` is present, report its anchor range, left/right or coincident boundary, whether upper extension was anchor-driven, stop reason, explicit scan limit and actual tested upper bound. Preserve its `display_text` and boundary costs/margin. FP MEDIUM posterior markers are scalar contextual coverage anchors only: the conditional sensitivity uses the effective compound-ratio model, not the full FP tyre-model vector. Do not imply FP SOFT/HARD were projected, or say FP predicts a true/probabilistic optimal stop count. An anchor within `boundary_tolerance` of a cutoff is at that boundary. If blocked, state exactly why coverage is incomplete, including `coverage incomplete due to explicit scan_max` when applicable. Absence of anchor metadata in legacy artifacts means coverage-anchor analysis unavailable, not no FP evidence. Keep this detail in the cutoff section; never infer a crossing from the figure or sweep.
   After this section, include `MODELLED_UNDERCUT_POWER.md` and its figure when supplied. Label it `Modelled / predicted undercut power`. Preserve the model tyre-age axis, theoretical age-zero replacement reference and sign convention. This is distinct from Live Observed undercut estimate; do not blend them or call the modelled curve measured.
6. `Historical VSC evidence`: render the per-year rows in `historical_vsc_evidence` without averaging them. Preserve each status, source, sample support and layout-comparability field. Any supplied estimator number is GREEN-relative pit-sector excess evidence, not an operational VSC pit window. Explicitly state that the evidence did not populate or alter current-event operational inputs and that no empirical Apply action is available. When a year is unavailable, preserve its supplied reason.
7. `Prior-event operational assumptions`: report `previous_season_operational_inputs` separately from empirical VSC evidence. Label these as the values previously adopted by the operator, not observations, and state that they did not populate or alter current-event calculations. When unavailable, preserve the supplied reason.
8. `Historical Safety Car / Red Flag context`: include the supplied `HISTORICAL_INTERRUPTION_CONTEXT.md` table and `historical_interruptions` values. Show editions, SC periods/ranges, Red Flag occurrences/laps and unavailable coverage. Pure VSC is excluded. SC ranges cover laps active for any portion; withdrawal exactly at a lap boundary excludes the next lap. Red Flags use entry laps. Preserve minimum counts, uncertain fragments and unavailable laps; only affirmative TrackStatus integrity supports confirmed zeros. Preserve “No event observed; coverage unproven” when integrity is unknown. This is descriptive context only and does not alter strategy inputs.
9. `Historical Traffic`: include the authoritative `HISTORICAL_TRAFFIC_CONTEXT.md` fragment, grouped summary/conclusions and all five traffic figures from `historical_traffic`. Place this level-2 section immediately BEFORE the existing level-2 `Limitations` section, with no intervening section. Preserve the three groups, signed statistics, missingness, BLUE distinction and provenance exactly. The observed successful-pass minimum and median advantages are sample statistics, not physical requirements or a 50% success threshold. Render an explicit unavailable section when history is missing. Report rendering must never prepare or refit historical data.
10. `Limitations`: retain the report's existing limitations and provenance text immediately after Historical Traffic.

Preserve `(default)` in human-facing fallback/default source labels. Explicitly report freshness warnings without implying that FP modified production inputs.
"""
    post = "" if workflow != "post_race" else """

If the standings component is UNAVAILABLE, state its supplied current-round results warning explicitly. Omit its tables/projections and standings figures; do not present previous-round totals as current-round standings or describe missing results as zero-point awards.

Preserve the existing canonical long-run/race-performance tracker and its section exactly; it remains the primary production performance output. Insert the supplied `BSF_JUNCTION_REFERENCE.md` section immediately after that canonical tracker section, before the existing next section. Preserve all other section order and text. Include the supplied BSF figure, compact caption and P/F/N caveats verbatim. Preserve any degraded-input warning prominently, including excluded rows, affected drivers, missing covariates and graph-support diagnostics; degraded output must never be described as clean success or a fully supported graph estimate. Describe BSF only as an experimental companion/reference for inspecting differences from canonical. P is provenance, not confidence; F is canonical fallback; N is numerical completion only and is excluded from the performance axis. Do not create a second authoritative ranking, claim BSF is more accurate, or infer a physical car/driver/fuel decomposition. If unavailable, retain the supplied explicit unavailable section without substituting another figure. The diagnostic and support figures are optional supplementary assets. Final saved-output assembly is available through `woweekend-post --report-only RUN_ID --event YYYY-RR --report-draft PATH`.
"""
    return f"""# Report instruction\n\nGenerate {article} {template} report in `{language}` from the supplied deterministic artifacts. Explain the outputs, structure the narrative, compare supplied pre/post results, preserve supplied caveats and warnings, and integrate useful figures.{race}{post}\n\nNumerical conclusions provided in JSON/CSV are authoritative. Do not derive replacement numerical conclusions from images. Do not recompute tyre degradation, select another strategy, alter cutoff values, determine sporting legality, calculate standings, or calculate unsupplied prediction errors. Clearly retain fallback provenance and unavailable components.\n"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
