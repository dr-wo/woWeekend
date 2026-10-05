"""Saved-output report fragment. Rendering never runs an estimator."""
from __future__ import annotations
from pathlib import Path
import re

TITLE = 'BSF Junction Engineering Reference — Relative to Mercedes'
CAPTION = (
    'Experimental companion reference using local/junction consensus with explicit '
    'fallback. Negative values are faster than Mercedes. P/F/N denotes projected '
    'support, canonical fallback and prior-only completion respectively. This is '
    'not equal-fuel, age-zero or pure car performance.'
)
TRACKER = 'bsf_junction_reference_mercedes.png'


def bsf_has_output(component):
    """Degraded calculation is usable output; failed calculation is not."""
    return (component or {}).get('status') in {'SUCCESS', 'DEGRADED'}


def degradation_markdown(component):
    output = component.get('output') or {}
    current = output.get('current_event') or {}
    degraded = (component.get('status') == 'DEGRADED'
                or output.get('status') == 'degraded_input'
                or current.get('status') == 'degraded_input')
    if not degraded:
        return ''
    warnings = output.get('warnings') or current.get('warnings') or component.get('warnings')
    if not warnings:
        warnings = ['Incomplete clean-lap input reduced evidence coverage. Saved exclusion '
                    'and graph-support diagnostics are unavailable. Do not interpret '
                    'this event as a fully supported graph estimate.']
    return ('> **Degraded execution — incomplete clean-lap input**\n>\n'
            + '\n>\n'.join('> ' + warning.replace('\n', '\n> ') for warning in warnings)
            + '\n\n')

_INLINE_IMAGE = re.compile(r'!\[([^\]]*)\]\(\s*(<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)')
_HTML_IMAGE = re.compile(r'<img\b[^>]*>', re.I)
_SRC = re.compile(r'''(?<!\S)src\s*=\s*(?:(["'])(.*?)\1|([^\s"'`=<>]+))''', re.I | re.S)
_REFERENCE_IMAGE = re.compile(r'!\[([^\]]*)\]\[([^\]]*)\]')
_SHORTCUT_IMAGE = re.compile(r'!\[([^\]]+)\](?![\[(])')
_DEFINITION = re.compile(r'^ {0,3}\[([^\]]+)\]:\s*(<[^>]+>|\S+).*$', re.M)


def is_bsf_image(reference, label=''):
    return 'bsf' in (Path(reference).name + ' ' + label).lower()


def _src_value(match):
    return match.group(2) if match.group(1) is not None else match.group(3)


def image_references(report):
    """Image destinations used by the assembled Markdown, including HTML/refs."""
    references = [m.group(2).strip('<>') for m in _INLINE_IMAGE.finditer(report)]
    for image in _HTML_IMAGE.finditer(report):
        src = _SRC.search(image.group())
        if src:
            references.append(_src_value(src))
    definitions = {m.group(1).casefold(): m.group(2).strip('<>')
                   for m in _DEFINITION.finditer(report)}
    for image in _REFERENCE_IMAGE.finditer(report):
        key = (image.group(2) or image.group(1)).casefold()
        if key in definitions:
            references.append(definitions[key])
    for image in _SHORTCUT_IMAGE.finditer(report):
        key = image.group(1).casefold()
        if key in definitions:
            references.append(definitions[key])
    return references


def sanitize_bsf_draft(draft, approved):
    """Only successful saved-bundle figures may survive authored BSF content."""
    headers = list(re.finditer(r'^(#{1,6})[ \t]+(.+?)[ \t]*$', draft, re.M))
    spans = []
    for i, header in enumerate(headers):
        if 'bsf' not in header.group(2).lower():
            continue
        end = next((h.start() for h in headers[i+1:]
                    if len(h.group(1)) <= len(header.group(1))), len(draft))
        if spans and header.start() <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((header.start(), end))
    definitions = {m.group(1).casefold(): m for m in _DEFINITION.finditer(draft)}
    # Preserve unrelated supplementary images even inside an old BSF section.
    for start, end in reversed(spans):
        section = draft[start:end]
        unrelated = [m.group() for m in _INLINE_IMAGE.finditer(section)
                     if not is_bsf_image(m.group(2).strip('<>'), m.group(1))]
        unrelated += [m.group() for m in _HTML_IMAGE.finditer(section)
                      if _SRC.search(m.group()) and not is_bsf_image(_src_value(_SRC.search(m.group())), m.group())]
        for image in list(_REFERENCE_IMAGE.finditer(section)) + list(_SHORTCUT_IMAGE.finditer(section)):
            key = (image.group(2) or image.group(1)) if image.re == _REFERENCE_IMAGE else image.group(1)
            definition = definitions.get(key.casefold())
            if definition and not is_bsf_image(definition.group(2).strip('<>'), image.group(1)):
                unrelated.extend([image.group(), definition.group()])
        draft = draft[:start] + ('\n\n'.join(unrelated) + '\n\n' if unrelated else '') + draft[end:]

    def replacement(label, reference):
        if not is_bsf_image(reference, label):
            return None
        name = Path(reference).name
        # The current primary tracker is inserted once at the canonical boundary.
        if name == TRACKER or name not in approved:
            return ''
        return f'![{label}](figures/{name})'

    def inline(match):
        value = replacement(match.group(1), match.group(2).strip('<>'))
        return match.group() if value is None else value

    def html(match):
        src = _SRC.search(match.group())
        value = replacement(match.group(), _src_value(src)) if src else None
        if value is None:
            return match.group()
        if not value:
            return ''
        return _SRC.sub(lambda m: f'src="figures/{Path(_src_value(src)).name}"', match.group())

    draft = _INLINE_IMAGE.sub(inline, draft)
    draft = _HTML_IMAGE.sub(html, draft)
    definitions = {m.group(1).casefold(): m.group(2).strip('<>')
                   for m in _DEFINITION.finditer(draft)}
    def referenced(match):
        ref = definitions.get((match.group(2) or match.group(1)).casefold(), '')
        value = replacement(match.group(1), ref)
        return match.group() if value is None else value
    draft = _REFERENCE_IMAGE.sub(referenced, draft)
    def shortcut(match):
        value = replacement(match.group(1), definitions.get(match.group(1).casefold(), ''))
        return match.group() if value is None else value
    draft = _SHORTCUT_IMAGE.sub(shortcut, draft)
    draft = _DEFINITION.sub(lambda m: '' if is_bsf_image(m.group(2).strip('<>')) else m.group(), draft)
    return draft


def bsf_markdown(component, figure_paths):
    component = component or {}
    available = {Path(p).name for p in figure_paths}
    header = f'## {TITLE}\n\n'
    if not bsf_has_output(component) or TRACKER not in available:
        reason = component.get('error') or 'Saved companion figure is unavailable in this bundle.'
        return header + f'Companion unavailable: {reason}\n\nCanonical outputs remain primary. No substitute BSF figure is supplied.\n'
    return (header + degradation_markdown(component) + f'![{TITLE}](figures/{TRACKER})\n\n'
            + CAPTION + '\n\nP is provenance, not a confidence grade. N appears only in the support strip. '
            'Use this companion to inspect differences from canonical alongside its evidence flags.\n')


def insert_bsf_after_canonical(draft, fragment):
    """Insert only this companion at the canonical figure boundary; preserve text."""
    # Idempotent replacement of this exact companion fragment.
    headers = list(re.finditer(r'^##[ \t]+(.+?)[ \t]*$', draft, re.M))
    for i, match in reversed(list(enumerate(headers))):
        if match.group(1).strip() == TITLE:
            end = headers[i+1].start() if i+1 < len(headers) else len(draft)
            draft = draft[:match.start()] + draft[end:]
    image = re.search(r'!\[[^\]]*\]\([^\n)]*(?:race_performance_tracker|post_race_performance_tracker)[^\n)]*\)', draft)
    if image is None:
        raise ValueError('Post-race draft has no canonical long-run performance tracker figure')
    # The existing tracker section's prose stays intact. Companion follows it,
    # before the next existing level-2 section, without moving that section.
    next_section = re.search(r'^##[ \t]+', draft[image.end():], re.M)
    at = image.end()+next_section.start() if next_section else len(draft)
    return draft[:at].rstrip() + '\n\n' + fragment.rstrip() + '\n\n' + draft[at:]
