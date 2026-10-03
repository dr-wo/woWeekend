import json

from wodata.circuit_traffic import write_circuit_traffic
from wostrategy.analysis.circuit_traffic_history import summarize_records
from woweekend.reports.historical_traffic import historical_traffic_section
from woweekend.reports.bundle import create_report_bundle,regenerate_report_bundle


def test_unavailable_report_does_not_compute_or_load(tmp_path,monkeypatch):
    from wostrategy.analysis import circuit_traffic_history as analysis
    monkeypatch.setattr(analysis.core,'analyse_session',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no refit')))
    context,figures=historical_traffic_section(circuit='Absent',target_season=2026,data_root=tmp_path,run_path=tmp_path/'run')
    assert context['status']=='unavailable'
    assert not figures
    assert not context['refitted_during_render']
    assert 'Unavailable' in (tmp_path/'run/historical_traffic.html').read_text()


def test_saved_values_figures_and_bundle_agree(tmp_path,monkeypatch):
    from matplotlib.axes import Axes
    axis_labels=[]
    titles=[]
    original_xlabel,original_title=Axes.set_xlabel,Axes.set_title
    def capture_xlabel(self,label,*args,**kwargs):
        axis_labels.append(label)
        return original_xlabel(self,label,*args,**kwargs)
    def capture_title(self,label,*args,**kwargs):
        titles.append(label)
        return original_title(self,label,*args,**kwargs)
    monkeypatch.setattr(Axes,'set_xlabel',capture_xlabel)
    monkeypatch.setattr(Axes,'set_title',capture_title)
    from wostrategy.analysis import circuit_traffic_history as analysis
    records=[dict(year=2022,kind='same_lap',interaction_id='minimum',identifiable=True,
        unresolved_compound_interaction=False,advantage_s=-.25,advantage_usable=True,
        a_cost_usable=False,b_cost_usable=False,a_cost_s=None,b_cost_s=None)]
    result=summarize_records(circuit='Monza',target_season=2026,records=records,coverage=[dict(year=2022,status='available')])
    path=write_circuit_traffic(result,data_root=tmp_path)
    monkeypatch.setattr(analysis.core,'analyse_session',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no refit')))
    run=tmp_path/'run'
    context,figures=historical_traffic_section(circuit='Monza',target_season=2026,data_root=tmp_path,run_path=run)
    assert context['groups']==result['groups']
    assert context['artifact_path']==str(path)
    assert len(figures)==5
    assert context['minimum_source_interactions'][0]['interaction_id']=='minimum'
    rendered=(run/'historical_traffic.html').read_text()
    assert '-0.250' in rendered and '50% passing-success threshold' in rendered
    assert 'UNAVAILABLE' not in rendered  # Empty plots show unavailable; text values stay unavailable.
    assert axis_labels.count('Estimated advantage (s)\nPositive = passing car faster')==3
    assert axis_labels.count('Observed − reference (s)\nPositive = slower than reference')==12
    labels=['Ground-effect regulation era (2022–25)',
            'Previous regulation era (2018–21)','Complete eight-year window']
    for label in labels:
        assert label in rendered
        assert sum(title.startswith(label+'\n') for title in titles)==5
    assert '2022–2025 within window' not in rendered
    assert 'Other years within window' not in rendered
    bundle=create_report_bundle(run_path=run,workflow='race_preparation',event='2026-16',context={'historical_traffic':context},results={},figures=figures)
    saved=json.loads((bundle/'report_context.json').read_text())['historical_traffic']
    assert saved==context
    assert len(json.loads((bundle/'figure_manifest.json').read_text())['figures'])==5
    fragment=(bundle/'HISTORICAL_TRAFFIC_CONTEXT.md').read_text()
    assert all(label in fragment for label in labels)
    assert '2022–2025 within window' not in fragment
    assert 'Other years within window' not in fragment
    (run/'manifest.json').write_text(json.dumps(dict(workflow='race_preparation',event='2026-16',warnings=[])))
    (run/'analysis_results.json').write_text('{}')
    (run/'report_context.json').write_text(json.dumps({'historical_traffic':context}))
    again=regenerate_report_bundle(run_path=run,language='en-GB')
    assert json.loads((again/'report_context.json').read_text())['historical_traffic']==context


def test_pre_race_workflow_includes_saved_history_without_refit(tmp_path,monkeypatch):
    from test_race_workflow import _tyres,_ensure,_event
    from woweekend.config.race import RaceConfig
    from woweekend.workflows.race import run_race
    from wostrategy.analysis import circuit_traffic_history as analysis
    value=summarize_records(circuit='Test Circuit',target_season=2026,records=[],coverage=[])
    write_circuit_traffic(value,data_root=tmp_path)
    monkeypatch.setattr(analysis.core,'analyse_session',lambda *a,**kw: (_ for _ in ()).throw(AssertionError('no refit')))
    raw=RaceConfig.template()
    raw.update(total_laps=6,result_count=2,data_root=str(tmp_path))
    raw['degradation_cutoff']={'scan_min':.05,'scan_max':.15,'coarse_step':.05,'refine_tolerance':.01}
    run=run_race(RaceConfig.parse(raw),event='2026-07',input_config=raw,tyre_provider=_tyres,model_config_ensurer=_ensure,event_resolver=_event)
    context=run.load_json('report_context')['historical_traffic']
    assert context['groups']==value['groups']
    assert context['status']=='unavailable'
    assert context['operational_input'] is False
    assert len(list((run.path/'figures').glob('traffic_history_*.png')))==5
    bundle=next(run.path.glob('report_bundle.*'))
    assert len(list((bundle/'figures').glob('traffic_history_*.png')))==5
    results=run.load_json('analysis_results')
    assert results['historical_traffic_context']['status']=='SUCCESS'
    assert run.load_json('strategy_unrestricted')  # optional unavailable history did not gate strategy
