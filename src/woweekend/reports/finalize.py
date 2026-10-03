"""Minimal final assembly of an authored pre-race report with saved traffic.

The normal workflow produces an authoring bundle. This step keeps the authored
report intact and inserts its deterministic traffic fragment before Limitations.
It reads the immutable bundle only; no analysis/cache/strategy calls occur here.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re
import shutil

_HEADER = re.compile(r'^##[ \t]+(.+?)[ \t]*$', re.M)


def _title(value):
    return re.sub(r'^\d+[.)]\s*', '', value).strip().casefold()


def insert_traffic_before_limitations(draft, fragment):
    headers = list(_HEADER.finditer(draft))
    # Replace only a previous traffic section, including when placed elsewhere.
    spans = []
    for index, match in enumerate(headers):
        if _title(match.group(1)) in {'historical traffic', 'historical circuit traffic'}:
            spans.append((match.start(), headers[index+1].start() if index+1<len(headers) else len(draft)))
    for start, end in reversed(spans):
        draft = draft[:start] + draft[end:]
    limits = next((m for m in _HEADER.finditer(draft) if _title(m.group(1)).startswith('limitations')), None)
    if limits is None:
        raise ValueError('Authored pre-race report needs an existing level-2 Limitations section')
    return draft[:limits.start()] + fragment.rstrip() + '\n\n' + draft[limits.start():]


def finalize_pre_race_report(*, bundle_path, draft_path):
    bundle = Path(bundle_path)
    draft_path = Path(draft_path)
    context = json.loads((bundle / 'report_context.json').read_text())
    if context.get('workflow') != 'race_preparation':
        raise ValueError('Final traffic assembly applies only to race-preparation reports')
    draft = draft_path.read_text(encoding='utf-8')
    fragment_path = bundle / 'HISTORICAL_TRAFFIC_CONTEXT.md'
    if fragment_path.exists():
        fragment = fragment_path.read_text(encoding='utf-8')
    else:
        from .historical_traffic import traffic_markdown
        fragment = traffic_markdown(context.get('historical_traffic'),
                                    [Path('figures') / p.name for p in (bundle / 'figures').glob('*')])
    report = insert_traffic_before_limitations(draft, fragment)
    root = bundle.parent / f"final_report.{context['language']}.{context['template']}"
    if root.exists():
        raise FileExistsError(f'Final report already exists: {root}')
    root.mkdir()
    figures = root / 'figures'
    figures.mkdir()
    for source in (bundle / 'figures').glob('*'):
        if source.is_file():
            shutil.copy2(source, figures / source.name)
    # Preserve other authored relative figure references without replacing run figures.
    for reference in re.findall(r'!\[[^\]]*\]\(([^)]+)\)', draft):
        relative = Path(reference)
        if relative.is_absolute() or '..' in relative.parts or '://' in reference:
            continue
        source = draft_path.parent / relative
        destination = root / relative
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    output = root / 'pre_race_report.md'
    output.write_text(report, encoding='utf-8')
    traffic = context.get('historical_traffic') or {}
    (root / 'assembly.json').write_text(json.dumps({
        'workflow': context['workflow'], 'event': context['event'],
        'bundle_path': str(bundle.resolve()), 'authored_draft': str(draft_path.resolve()),
        'authored_draft_sha256': hashlib.sha256(draft.encode()).hexdigest(),
        'traffic_artifact_id': traffic.get('artifact_id'),
        'traffic_status': traffic.get('status', 'unavailable'),
        'historical_refit_during_render': False,
        'section_order': 'Historical Traffic immediately before existing Limitations',
        'other_authored_sections_preserved': True,
    }, indent=2) + '\n')
    return output


def finalize_post_race_report(*, bundle_path, draft_path):
    """Place the saved experimental companion after the canonical tracker section.

    This is report assembly only: no estimator, historical preparation or cache
    calls. The existing authoring pipeline remains responsible for report prose.
    """
    from .bsf_junction import (
        bsf_markdown, insert_bsf_after_canonical, sanitize_bsf_draft,
        image_references, is_bsf_image, TRACKER,
    )

    bundle, draft_path = Path(bundle_path), Path(draft_path)
    context = json.loads((bundle / 'report_context.json').read_text())
    if context.get('workflow') != 'post_race':
        raise ValueError('BSF final assembly applies only to post-race reports')
    draft = draft_path.read_text(encoding='utf-8')
    results_path = bundle / 'analysis_results.json'
    results = json.loads(results_path.read_text()) if results_path.exists() else {}
    bsf = results.get('bsf_junction_reference', context.get('bsf_junction_reference')) or {}
    manifest_path = bundle / 'figure_manifest.json'
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    approved = {}
    if bsf.get('status') == 'SUCCESS':
        for entry in manifest.get('figures', []):
            relative = Path(entry['file'])
            source = bundle / relative
            if (relative.parent == Path('figures') and is_bsf_image(str(relative))
                    and source.is_file() and source.resolve().parent == (bundle / 'figures').resolve()):
                approved[relative.name] = source
    if TRACKER not in approved:
        approved = {}
    # Saved component status and manifest are authoritative. An old fragment or
    # authored diagnostic cannot restore failed/missing companion output.
    fragment = bsf_markdown(bsf, approved.values())
    report = insert_bsf_after_canonical(sanitize_bsf_draft(draft, approved), fragment)
    root = bundle.parent / f"final_report.{context['language']}.{context['template']}"
    if root.exists():
        raise FileExistsError(f'Final report already exists: {root}')
    root.mkdir()
    figures = root / 'figures'
    figures.mkdir()
    for reference in image_references(report):
        relative = Path(reference)
        if relative.is_absolute() or '..' in relative.parts or '://' in reference:
            continue
        if is_bsf_image(reference):
            source = approved.get(relative.name)
            if source is None:
                continue
        else:
            saved = bundle / relative
            source = saved if saved.is_file() else draft_path.parent / relative
        destination = root / relative
        if source.is_file() and not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    output = root / 'post_race_report.md'
    output.write_text(report, encoding='utf-8')
    (root / 'assembly.json').write_text(json.dumps({
        'workflow': 'post_race', 'event': context['event'],
        'bundle_path': str(bundle.resolve()), 'authored_draft': str(draft_path.resolve()),
        'authored_draft_sha256': hashlib.sha256(draft.encode()).hexdigest(),
        'bsf_status': bsf.get('status', 'unavailable'),
        'bsf_approved_figures': sorted(approved),
        'analysis_during_render': False,
        'section_order': 'BSF immediately after existing canonical tracker section',
        'other_authored_sections_preserved': True,
    }, indent=2) + '\n')
    return output
