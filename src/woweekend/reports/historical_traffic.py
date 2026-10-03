"""Cache-only historical traffic section assembled from one persisted result."""
from __future__ import annotations
import html
import json
from pathlib import Path


def _fmt(value):
    return 'unavailable' if value is None else f'{value:+.3f}'


def _years(years):
    return ', '.join(map(str,years)) or 'none'


def _group_label(group):
    # Presentation only: retain the persisted group key, membership and values.
    return {'2022_2025': 'Ground-effect regulation era (2022–25)',
            'other_years': 'Previous regulation era (2018–21)',
            'all': 'Complete eight-year window'}.get(group['group'], group['label'])


def _axis_label(output):
    if output == 'same_lap_advantage':
        return 'Estimated advantage (s)\nPositive = passing car faster'
    return 'Observed − reference (s)\nPositive = slower than reference'


def traffic_context(result, path):
    """Deterministic text and tables use saved record statistics, never plot bins."""
    groups=result['groups']
    conclusions=[]
    for group in groups:
        outputs={x['output']:x for x in group['outputs']}
        adv=outputs['same_lap_advantage']
        conclusions.append(f"{_group_label(group)}: {len(group['available_years'])}/{len(group['years'])} years available; "
            f"estimated advantage usable N={adv['usable_N']}, mean {_fmt(adv['mean_s'])} s, "
            f"observed successful-pass minimum {_fmt(adv['observed_successful_pass_minimum_advantage_s'])} s "
            f"and median {_fmt(adv['observed_successful_pass_median_advantage_s'])} s.")
    for key in ['same_lap_passer_cost','same_lap_passed_cost','lapping_lapper_cost','lapping_lapped_cost']:
        first,other=[next(o for o in g['outputs'] if o['output']==key) for g in groups[:2]]
        if first['usable_N'] and other['usable_N']:
            conclusions.append(f"{first['title']}: {_group_label(groups[0])} mean {_fmt(first['mean_s'])} s (N={first['usable_N']}); "
                f"{_group_label(groups[1])} mean {_fmt(other['mean_s'])} s (N={other['usable_N']}). "
                'These different selected samples do not establish a causal change between eras.')
        else:
            conclusions.append(f"{first['title']}: a two-group comparison is unavailable because at least one group has no usable records.")
    no_advantage = [g['year'] for g in result['yearly'] if g['available_years'] and
                    next(o for o in g['outputs'] if o['output']=='same_lap_advantage')['usable_N']==0]
    if no_advantage:
        conclusions.append(f"Race data is available for {_years(no_advantage)}, but those years supply no eligible paired advantage values under the unchanged reference/exclusion rules. Missing advantage is not zero.")
    minimum_ids={i for g in groups for o in g['outputs'] for i in o.get('minimum_source_interaction_ids',[])}
    return dict(status=result['status'],artifact_path=str(path),artifact_id=Path(path).parent.name,
        recipe_id=result['recipe_id'],circuit=result['circuit'],target_season=result['target_season'],
        years=result['years'],available_years=result['available_years'],unavailable_years=result['unavailable_years'],
        groups=groups,yearly=result['yearly'],coverage=result['coverage'],contract=result['contract'],
        reference_rule=result['reference_rule'],semantics=result['semantics'],provenance=result['provenance'],
        minimum_source_interactions=[r for r in result['interactions'] if r['interaction_id'] in minimum_ids],
        conclusions=conclusions,report_context_only=True,operational_input=False,refitted_during_render=False)


def render_traffic_section(context, *, run_path):
    """Render the saved grouped histograms; ALL never stacks over its own subsets."""
    root=Path(run_path);root.mkdir(parents=True,exist_ok=True)
    figures=[]
    esc=lambda v:html.escape(str(v))
    parts=['<!doctype html><html lang="en"><meta charset="utf-8"><title>Historical circuit traffic</title>',
        '<style>body{font:16px system-ui;max-width:1250px;margin:35px auto;color:#172630;padding:20px}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:9px;border-bottom:1px solid #ddd;text-align:left}img{width:100%}.note{background:#eef3f6;padding:16px}code{overflow-wrap:anywhere}</style>',
        '<h1>Pre-race historical traffic</h1>']
    if not context.get('groups'):
        parts.append('<p>Unavailable: '+esc(context.get('reason','no saved artifact'))+'</p>')
    else:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        import numpy as np
        groups=context['groups']
        parts += [f"<p>{esc(context['circuit'])} · target season {context['target_season']} · {context['years'][0]}–{context['years'][-1]} · {esc(context['status'])}</p>",
            f"<p>Available years: {_years(context['available_years'])}. Unavailable years: {_years(context['unavailable_years'])}.</p>",
            '<p class="note">Signed whole-lap observed-minus-reference residuals; means are interaction-weighted. Timing-corroborated successful passes are not independently verified competitive passes. The observed successful-pass minimum and median advantages are sample statistics, not a physical minimum requirement or a 50% passing-success threshold. The retrospective team-pooled reference can use later evidence; it is not true driver-specific free-air pace. No automatic strategy inputs are derived.</p>',
            '<h2>Three-group summary</h2><table><tr><th>Group / available years</th><th>Distribution</th><th>Usable N</th><th>Mean (s)</th><th>Identifiable / excluded candidates</th><th>Age extrapolated N</th></tr>']
        for g in groups:
            for o in g['outputs']:
                parts.append(f"<tr><td>{esc(_group_label(g))}<br>{_years(g['available_years'])}</td><td>{esc(o['title'])}</td><td>{o['usable_N']}</td><td>{_fmt(o['mean_s'])}</td><td>{o['identifiable_interactions']} / {o['excluded_N']}</td><td>{o['age_extrapolated_N']}</td></tr>")
        parts += ['</table><h2>Observed successful-pass advantages</h2><table><tr><th>Group</th><th>N</th><th>Minimum (s)</th><th>Median / P50 (s)</th><th>Minimum source interaction(s)</th></tr>']
        for g in groups:
            o=next(x for x in g['outputs'] if x['output']=='same_lap_advantage')
            parts.append(f"<tr><td>{esc(_group_label(g))}</td><td>{o['usable_N']}</td><td>{_fmt(o['observed_successful_pass_minimum_advantage_s'])}</td><td>{_fmt(o['observed_successful_pass_median_advantage_s'])}</td><td>{esc(', '.join(o['minimum_source_interaction_ids']) or 'unavailable')}</td></tr>")
        parts += ['</table><p>Advantage is passed-car reference minus passing-car reference at their actual tyre states: positive means the passing car is estimated faster. It is not headway or raw pass-lap time difference. P50 is the exact median of eligible records; for even N, it is the arithmetic mean of the two middle values. No bin midpoint or average of annual medians is used.</p>',
            '<h2>Five distributions</h2><p>Common bins across groups; frequencies, not densities. ALL is a separate panel containing both disjoint subsets. Dashed lapping outlines indicate the assigned-BLUE subset; remaining observations are timing-inferred lapping. A BLUE message does not uniquely identify its lapper.</p>']
        folder=root/'figures';folder.mkdir(exist_ok=True)
        for index in range(5):
            fig,axes=plt.subplots(1,3,figsize=(17,4.9),sharey=True)
            title=groups[0]['outputs'][index]['title']
            for j,(ax,g) in enumerate(zip(axes,groups)):
                o=g['outputs'][index];hist=o['histogram'];edges=hist['edges_s']
                labels=[f'<{b:g}' if a is None else f'≥{a:g}' if b is None else f'[{a:g}, {b:g})' for a,b in zip(edges,edges[1:])]
                xx=np.arange(len(labels))
                ax.bar(xx,hist['counts'],color=['#3579af','#d48736','#628c6a'][j],width=.9)
                if 'blue_evidence' in o:
                    blue=o['blue_evidence']['evidenced']
                    ax.step(np.r_[-.5,xx+.5],np.r_[blue['counts'],blue['counts'][-1]],where='post',color='#222',linestyle='--',label=f"BLUE N={blue['N']}")
                    ax.legend(fontsize=8)
                ax.set_title(f"{_group_label(g)}\nAvailable: {_years(g['available_years'])}\nN={o['usable_N']} · mean {_fmt(o['mean_s'])} s",fontsize=10)
                ax.set_xticks(xx);ax.set_xticklabels(labels,rotation=55,ha='right',fontsize=8)
                ax.set_xlabel(_axis_label(o['output']),fontsize=9)
                ax.grid(axis='y',alpha=.15);ax.set_axisbelow(True)
                if not o['usable_N']:ax.text(.5,.5,'UNAVAILABLE\nNo eligible values',ha='center',va='center',transform=ax.transAxes)
            axes[0].set_ylabel('Interaction frequency')
            fig.suptitle(title,fontsize=15);fig.tight_layout(rect=[0,0,1,.94])
            name=f"traffic_history_{index+1}_{groups[0]['outputs'][index]['output']}.png"
            path=folder/name;fig.savefig(path,dpi=135);plt.close(fig);figures.append(path)
            parts.append(f'<h3>{esc(title)}</h3><img src="figures/{name}" alt="{esc(title)}">')
        parts += ['<h2>Data-grounded conclusions</h2><ul>']+[f'<li>{esc(t)}</li>' for t in context['conclusions']]+['</ul>',
            '<h2>Coverage and exclusions</h2><table><tr><th>Year</th><th>Status / reason</th><th>Race</th></tr>']
        for c in context['coverage']:
            parts.append(f"<tr><td>{c['year']}</td><td>{esc(c['status'])}: {esc(c.get('reason',''))}</td><td>{esc(c.get('event',''))}</td></tr>")
        parts += ['</table><p>Green, dry, accurate, non-pit eligibility and restart/incident handling remain unchanged. Whole laps shared by multiple candidate pairings are excluded as compound interactions. Missing references remain missing. The fitting coordinate is retained-record StintLapNumber−1, with total TyreLife recorded separately; nonzero offsets remain unsupported. Extrapolation is retained and labelled. The two cars can have different usable samples. Old compound names outside SOFT/MEDIUM/HARD are not remapped to increase coverage.</p>',
            f"<p>Saved artifact: <code>{esc(context['artifact_path'])}</code><br>Recipe: <code>{context['recipe_id']}</code></p>"]
    parts.append('</html>')
    (root/'historical_traffic.html').write_text('\n'.join(parts))
    (root/'historical_traffic_context.json').write_text(json.dumps(context,indent=2,allow_nan=False)+'\n')
    return figures


def historical_traffic_section(*, circuit, target_season, data_root, run_path):
    from wostrategy.analysis.circuit_traffic_history import load_cached_history
    try:
        result,path=load_cached_history(circuit=circuit,target_season=target_season,data_root=data_root)
        context=traffic_context(result,path)
        context['artifact_resolution'] = {'status': 'reused', 'prepared_in_this_workflow': False,
            'policy': 'explicit historical preparation followed by cache-only workflow lookup'}
    except (FileNotFoundError,ValueError,KeyError,OSError) as exc:
        context=dict(status='unavailable',circuit=circuit,target_season=target_season,
            reason=f'{type(exc).__name__}: {exc}',report_context_only=True,operational_input=False,refitted_during_render=False)
        context['artifact_resolution'] = {'status': 'unavailable', 'prepared_in_this_workflow': False,
            'preparation_entrypoint': 'woweekend-traffic-history --circuit <Location> --target-season <season>',
            'policy': 'optional history follows the explicit-preparation convention; rendering does not acquire or fit'}
    return context,render_traffic_section(context,run_path=run_path)


def traffic_markdown(context, figures=()):
    """Authoritative report fragment from the run's saved context and figures."""
    lines = ['## Historical Traffic', '']
    if not context or not context.get('groups'):
        lines += ['Unavailable: ' + str((context or {}).get('reason', 'No historical traffic context in this saved run.')),
                  '', 'Historical traffic is optional report context; no historical fitting is performed during rendering.', '']
        return '\n'.join(lines)
    lines += [f"{context['circuit']} — target season {context['target_season']}; calendar window {context['years'][0]}–{context['years'][-1]}. Status: **{context['status']}**.",
              f"Available years: {_years(context['available_years'])}. Unavailable years: {_years(context['unavailable_years'])}.", '',
              'Signed whole-lap residual = observed time − retrospective reference. Pooled means are interaction-weighted. Each car can have a different usable sample. ALL is shown separately from its two disjoint subsets.', '',
              '### Grouped summary', '',
              '| Group / available years | Distribution | Usable N | Mean (s) | Identifiable / excluded candidates | Age-extrapolated N |',
              '| --- | --- | ---: | ---: | ---: | ---: |']
    for group in context['groups']:
        for output in group['outputs']:
            lines.append(f"| {_group_label(group)} / {_years(group['available_years'])} | {output['title']} | {output['usable_N']} | {_fmt(output['mean_s'])} | {output['identifiable_interactions']} / {output['excluded_N']} | {output['age_extrapolated_N']} |")
    lines += ['', '### Observed successful-pass minimum and median advantages', '',
              'Advantage = passed-car reference − passing-car reference at the actual tyre states; positive means the passing car is estimated faster. These observed successful-pass minimum and median advantages are sample statistics, not a minimum physical requirement or a 50% passing-success threshold. P50 is the exact record median, averaging the middle two values for even N.', '',
              '| Group | N | Minimum (s) | Median / P50 (s) | Minimum source interaction(s) |',
              '| --- | ---: | ---: | ---: | --- |']
    for group in context['groups']:
        output = next(o for o in group['outputs'] if o['output'] == 'same_lap_advantage')
        lines.append(f"| {_group_label(group)} | {output['usable_N']} | {_fmt(output['observed_successful_pass_minimum_advantage_s'])} | {_fmt(output['observed_successful_pass_median_advantage_s'])} | {', '.join(output['minimum_source_interaction_ids']) or 'unavailable'} |")
    lines += ['', '### Five distributions', '',
              'Common bins and frequencies across groups; each figure has separate subset and ALL panels. Dashed lapping outlines show the assigned-BLUE subset. Remaining lapping records are inferred; a BLUE message does not uniquely attribute the lapper.', '']
    paths = {Path(p).name: Path(p).as_posix() for p in figures}
    for index, output in enumerate(context['groups'][0]['outputs'], 1):
        name = f"traffic_history_{index}_{output['output']}.png"
        if name in paths:
            lines += [f"![{output['title']}]({paths[name]})", '']
        else:
            lines += [f"{output['title']} figure unavailable in this saved run.", '']
    lines += ['### BLUE evidence and inferred lapping', '',
              '| Group | Car | BLUE N / mean (s) | Inferred-only N / mean (s) |',
              '| --- | --- | ---: | ---: |']
    for group in context['groups']:
        for output in group['outputs']:
            if 'blue_evidence' in output:
                blue = output['blue_evidence']['evidenced']
                inferred = output['blue_evidence']['inferred_only']
                lines.append(f"| {_group_label(group)} | {output['title']} | {blue['N']} / {_fmt(blue['mean_s'])} | {inferred['N']} / {_fmt(inferred['mean_s'])} |")
    lines += ['', '### Data-grounded conclusions', '']
    lines += ['- ' + conclusion for conclusion in context['conclusions']]
    lines += ['', '### Calculation and reference provenance', '',
              'Timing/position/sector/next-boundary corroboration identifies the observed successful-pass sample; competitive intent is not independently verified. Green/dry, pit/restart/incident handling and compound-interaction exclusions are retained. Lapping aligns session time across different lap counters. Missing references remain unavailable; signed values and labelled extrapolation are preserved.', '',
              'The fixed retrospective team-pooled reference uses fitting age StintLapNumber−1 and records total TyreLife−1 separately. Nonzero tyre-age offsets remain unsupported. Target windows and their buffers are excluded from fitting; later reference evidence is permitted. Team borrowing and future selection remain limitations. These estimates do not establish true driver-specific free-air pace, causal per-pass costs or overtaking probabilities, and they do not change strategy inputs.', '',
              f"Persisted artifact: `{context['artifact_path']}`. Artifact ID: `{context['artifact_id']}`. Recipe: `{context['recipe_id']}`.", '']
    return '\n'.join(lines)
