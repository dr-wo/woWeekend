import json
from pathlib import Path
import re
import pytest

from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle
from woweekend.reports.finalize import finalize_pre_race_report, insert_traffic_before_limitations
from wodata.weekend_runs import WeekendRunStore


def test_insert_preserves_other_sections_and_orders_immediately_before_limits():
    draft='# Existing report\n\n## Strategy result\n\nKeep these exact numbers: 1.23.\n\n## 10. Limitations and provenance\n\nKeep this exact caveat.\n\n## Appendix\n\nKeep appendix.\n'
    fragment='## Historical Traffic\n\n### Grouped summary\n\nSaved values.\n'
    got=insert_traffic_before_limitations(draft,fragment)
    assert got.replace(fragment.rstrip()+'\n\n','')==draft
    headings=re.findall(r'^## (.*)$',got,re.M)
    assert headings==['Strategy result','Historical Traffic','10. Limitations and provenance','Appendix']
    misplaced=draft.replace('## Strategy result',fragment+'\n## Strategy result')
    assert insert_traffic_before_limitations(misplaced,fragment)==got
    with pytest.raises(ValueError,match='Limitations'):
        insert_traffic_before_limitations('## Strategy\n',fragment)


def test_missing_context_gets_unavailable_final_section_and_no_fit(tmp_path,monkeypatch):
    from wostrategy.analysis import circuit_traffic_history as analysis
    monkeypatch.setattr(analysis.core,'analyse_session',lambda *a,**kw:pytest.fail('Historical refit during final assembly'))
    bundle=create_report_bundle(run_path=tmp_path,workflow='race_preparation',event='2026-13',context={},results={},language='en-GB')
    draft=tmp_path/'draft.md';draft.write_text('# Report\n\n## Strategy\n\nUnchanged.\n\n## Limitations\n\nExisting.\n')
    report=finalize_pre_race_report(bundle_path=bundle,draft_path=draft)
    text=report.read_text()
    assert 'Unavailable:' in text
    assert re.findall(r'^## (.*)$',text,re.M)[-2:]==['Historical Traffic','Limitations']
    assert json.loads((report.parent/'assembly.json').read_text())['historical_refit_during_render'] is False


def test_normal_cli_report_only_consumes_saved_snapshot(tmp_path,monkeypatch,capsys):
    from woweekend.cli.race import main
    from wostrategy.analysis import circuit_traffic_history as analysis
    monkeypatch.setattr(analysis,'load_cached_history',lambda **kw:pytest.fail('Final rendering must use the saved run, not canonical latest'))
    monkeypatch.setattr(analysis.core,'analyse_session',lambda *a,**kw:pytest.fail('Historical refit'))
    run=WeekendRunStore.create(event='2026-13',workflow='race_preparation',data_root=tmp_path)
    run.save_json('report_context',{'historical_traffic':{'status':'unavailable','reason':'No prepared data'}})
    run.save_json('analysis_results',{})
    run.save_manifest({'status':'SUCCESS'})
    draft=tmp_path/'draft.md';draft.write_text('# Report\n\n## Limitations\n\nKeep me.\n')
    monkeypatch.setenv('WODATA_ROOT',str(tmp_path))
    monkeypatch.setattr('sys.argv',['woweekend-race','--event','2026-13','--report-only',run.run_id,'--language','en-GB','--report-draft',str(draft)])
    main()
    report=Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert report.exists() and 'No prepared data' in report.read_text()
    assert 'Historical Traffic' in report.read_text()


def test_regeneration_and_finalization_keep_saved_history_and_five_plots(tmp_path,monkeypatch):
    # Real saved Monza evidence is validated separately by the CLI acceptance run;
    # here use the same persisted-record fixture API without any detector/fitter.
    from wostrategy.analysis.circuit_traffic_history import summarize_records
    from woweekend.reports.historical_traffic import traffic_context
    rows=[dict(year=2022,kind='same_lap',interaction_id='minimum',identifiable=True,
               unresolved_compound_interaction=False,advantage_s=-.25,advantage_usable=True)]
    value=summarize_records(circuit='Monza',target_season=2026,records=rows,coverage=[dict(year=2022,status='available')])
    context=traffic_context(value,tmp_path/'abc/result.json')
    figures=[]
    for i,o in enumerate(context['groups'][0]['outputs'],1):
        p=tmp_path/'figures'/f"traffic_history_{i}_{o['output']}.png";p.parent.mkdir(exist_ok=True);p.write_bytes(b'figure');figures.append(p)
    for i in range(20):
        p=tmp_path/'figures'/f'other_{i}.png';p.write_bytes(b'other');figures.append(p)
    (tmp_path/'manifest.json').write_text(json.dumps({'workflow':'race_preparation','event':'2026-13','warnings':[]}))
    (tmp_path/'report_context.json').write_text(json.dumps({'historical_traffic':context}))
    (tmp_path/'analysis_results.json').write_text('{}')
    from wostrategy.analysis import circuit_traffic_history as analysis
    monkeypatch.setattr(analysis,'load_cached_history',lambda **kw:pytest.fail('Canonical lookup during report-only'))
    bundle=regenerate_report_bundle(run_path=tmp_path,language='en-GB')
    assert len(list(bundle.rglob('*.*')))<=20
    assert len(list((bundle/'figures').glob('traffic_history_*.png')))==5
    fragment=(bundle/'HISTORICAL_TRAFFIC_CONTEXT.md').read_text()
    assert '-0.250' in fragment and 'minimum' in fragment
    assert fragment.count('![ ')==0
    assert len(re.findall(r'!\[.*?\]\(figures/traffic_history_',fragment))==5
    draft=tmp_path/'draft.md';draft.write_text('# Report\n\n## Limitations\n\nPreserve.\n')
    report=finalize_pre_race_report(bundle_path=bundle,draft_path=draft)
    assert 'Grouped summary' in report.read_text() and 'Data-grounded conclusions' in report.read_text()
    assert len(list((report.parent/'figures').glob('traffic_history_*.png')))==5
    assert 'Historical Traffic' in (bundle/'REPORT_INSTRUCTION.md').read_text()
