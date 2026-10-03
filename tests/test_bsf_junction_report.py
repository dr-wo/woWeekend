import json
from pathlib import Path
import re
import pytest

from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle
from woweekend.reports.bsf_junction import bsf_markdown, insert_bsf_after_canonical, TITLE, TRACKER
from woweekend.reports.finalize import finalize_post_race_report
from woweekend.config.post_race import PostRaceConfig
from woweekend.workflows.post_race import run_post_race
from test_post import _pre_race_run, _event, _providers, _model_config

CANONICAL = 'race_performance_tracker_team_baseline.png'
DRAFT = ('# Post-race report\n\n## Long-run performance tracker\n\n'
         f'![Canonical tracker](figures/{CANONICAL})\n\nKeep canonical numbers: 1.23.\n\n'
         '## Existing subsequent section\n\nKeep this table and conclusion exactly.\n')


def test_registration_budget_and_final_placement_preserve_canonical(tmp_path,monkeypatch):
    figures = []
    for name in [CANONICAL,TRACKER]+[f'live_mc_{i}.png' for i in range(25)]:
        p = tmp_path/'figures'/name
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(name.encode())
        figures.append(p)
    component = {'status':'SUCCESS','output':{'method':'projected_mean'}}
    bundle = create_report_bundle(run_path=tmp_path,workflow='post_race',event='2026-15',
        context={'bsf_junction_reference':component},results={'bsf_junction_reference':component},
        figures=figures,language='en-GB')
    assert len([p for p in bundle.rglob('*') if p.is_file()]) == 20
    manifest = json.loads((bundle/'figure_manifest.json').read_text())['figures']
    assert [z['file'] for z in manifest[:2]] == [f'figures/{CANONICAL}',f'figures/{TRACKER}']
    assert 'immediately after' in manifest[1]['placement']
    assert 'not equal-fuel' in manifest[1]['caption']
    assert (bundle/'figures'/CANONICAL).read_bytes() == CANONICAL.encode()
    fragment = (bundle/'BSF_JUNCTION_REFERENCE.md').read_text()
    text = insert_bsf_after_canonical(DRAFT,fragment)
    assert text.replace(fragment.rstrip()+'\n\n','') == DRAFT
    assert insert_bsf_after_canonical(text,fragment) == text
    assert re.findall(r'^## (.*)$',text,re.M) == ['Long-run performance tracker',TITLE,'Existing subsequent section']
    # Assembly must never call a current/latest estimator.
    monkeypatch.setattr('wostrategy.analysis.bsf_junction.workflow.get_bsf_event',
                        lambda **kw:pytest.fail('recalculation during report rendering'))
    draft = tmp_path/'draft.md'; draft.write_text(DRAFT)
    report = finalize_post_race_report(bundle_path=bundle,draft_path=draft)
    assert report.read_text() == text
    assert (report.parent/'figures'/CANONICAL).read_bytes() == CANONICAL.encode()
    assert json.loads((report.parent/'assembly.json').read_text())['analysis_during_render'] is False


def test_unavailable_companion_is_explicit_and_does_not_register_stale_plot(tmp_path):
    canonical = tmp_path/CANONICAL; canonical.write_bytes(b'canonical')
    stale = tmp_path/TRACKER; stale.write_bytes(b'stale companion')
    component = {'status':'FAILED','error':'Input bank unavailable'}
    bundle = create_report_bundle(run_path=tmp_path,workflow='post_race',event='2026-15',
        context={'bsf_junction_reference':component},results={'bsf_junction_reference':component},
        figures=[canonical,stale])
    fragment = (bundle/'BSF_JUNCTION_REFERENCE.md').read_text()
    assert 'Companion unavailable: Input bank unavailable' in fragment
    assert TRACKER not in fragment
    assert (bundle/'figures'/CANONICAL).exists()
    assert not (bundle/'figures'/TRACKER).exists()
    draft = tmp_path/'draft.md'; draft.write_text(DRAFT)
    report = finalize_post_race_report(bundle_path=bundle,draft_path=draft)
    assert 'Canonical outputs remain primary' in report.read_text()
    assert 'Keep this table and conclusion exactly.' in report.read_text()


@pytest.mark.parametrize('bsf_fails',[False,True])
def test_normal_post_workflow_runs_companion_after_canonical_and_isolates_failure(tmp_path,bsf_fails):
    pre = _pre_race_run(tmp_path)
    raw = PostRaceConfig.template()
    raw.update(data_root=str(tmp_path),pre_race_run_id=pre.run_id)
    calls = []
    canonical_value = {'scores':[0.0,1.234],'provenance':'unchanged canonical'}
    providers = _providers(lambda **kw:{'updates':[]})
    def canonical(**kwargs):
        calls.append('canonical')
        return canonical_value
    def companion(**kwargs):
        assert calls[-1] == 'canonical'
        assert kwargs == {'season':2026,'round_number':3}
        calls.append('bsf')
        if bsf_fails:
            raise ValueError('Unsupported canonical input bank')
        return {'status':'available','method':'projected_mean'}
    providers.update(race_performance=canonical,race_performance_tracker=canonical,
                     bsf_junction_reference=companion)
    run = run_post_race(PostRaceConfig.parse(raw),event='2026-03',input_config=raw,
                        providers=providers,model_config_ensurer=_model_config,event_resolver=_event)
    assert run.load_json('race_performance') == canonical_value
    assert run.load_json('race_performance_tracker') == canonical_value
    assert canonical_value == {'scores':[0.0,1.234],'provenance':'unchanged canonical'}
    results = run.load_json('analysis_results')
    assert results['bsf_junction_reference']['status'] == ('FAILED' if bsf_fails else 'SUCCESS')
    assert results['race_performance']['status'] == 'SUCCESS'
    assert results['standings']['status'] == 'SUCCESS'
    assert results['bsf_junction_reference']['provenance']['failure_isolated']
    assert run.load_json('report_context')['bsf_junction_reference'] == results['bsf_junction_reference']
    assert (next(run.path.glob('report_bundle.*'))/'BSF_JUNCTION_REFERENCE.md').exists()


def test_report_only_cli_uses_saved_outputs(tmp_path,monkeypatch,capsys):
    from wodata.weekend_runs import WeekendRunStore
    from woweekend.cli.post import main
    run = WeekendRunStore.create(event='2026-15',workflow='post_race',data_root=tmp_path)
    run.save_json('report_context',{'bsf_junction_reference':{'status':'FAILED','error':'Saved unavailable'}})
    run.save_json('analysis_results',{})
    run.save_manifest({'status':'PARTIAL'})
    draft = tmp_path/'draft.md'; draft.write_text(DRAFT)
    monkeypatch.setenv('WODATA_ROOT',str(tmp_path))
    monkeypatch.setattr('wostrategy.analysis.bsf_junction.workflow.get_bsf_event',
                        lambda **kw:pytest.fail('current bank lookup during report-only'))
    monkeypatch.setattr('sys.argv',['woweekend-post','--event','2026-15','--report-only',run.run_id,
                                  '--language','en-GB','--report-draft',str(draft)])
    main()
    report = Path(capsys.readouterr().out.strip().splitlines()[-1])
    assert 'Saved unavailable' in report.read_text()


def test_failed_current_producer_cannot_reuse_a_stale_companion(tmp_path):
    pre = _pre_race_run(tmp_path)
    raw = PostRaceConfig.template()
    raw.update(data_root=str(tmp_path),pre_race_run_id=pre.run_id)
    providers = _providers(lambda **kw:{'updates':[]})
    def failed_current(**kwargs):
        raise ValueError('Current producer failed')
    providers.update(race_performance=failed_current,
        bsf_junction_reference=lambda **kw:pytest.fail('must not use a stale producer bank'))
    run = run_post_race(PostRaceConfig.parse(raw),event='2026-03',input_config=raw,
                        providers=providers,model_config_ensurer=_model_config,event_resolver=_event)
    results = run.load_json('analysis_results')
    assert results['bsf_junction_reference']['status'] == 'FAILED'
    assert 'Current canonical' in results['bsf_junction_reference']['error']
    assert results['race_performance_tracker']['status'] == 'SUCCESS'
    assert results['standings']['status'] == 'SUCCESS'


def test_failed_final_report_removes_stale_bsf_from_all_sections(tmp_path):
    canonical = tmp_path / CANONICAL; canonical.write_bytes(b'canonical')
    component = {'status': 'FAILED', 'error': 'Approved original bank unavailable'}
    bundle = create_report_bundle(run_path=tmp_path, workflow='post_race', event='2026-15',
        context={'bsf_junction_reference': {'status': 'SUCCESS'}},
        results={'bsf_junction_reference': component}, figures=[canonical])
    # An obsolete fragment and file in the bundle must not override saved FAILED.
    (bundle / 'BSF_JUNCTION_REFERENCE.md').write_text(bsf_markdown({'status': 'SUCCESS'}, [TRACKER]))
    (bundle / 'figures' / TRACKER).write_bytes(b'old bundle image')
    old = 'BSF_MINUS_CANONICAL_RELATIVE_TO_MERCEDES.png'
    unrelated = 'tyre_supplement.png'
    (tmp_path / 'old').mkdir()
    (tmp_path / 'old' / old).write_bytes(b'old draft diagnostic')
    (tmp_path / unrelated).write_bytes(b'keep unrelated')
    draft = tmp_path / 'draft.md'
    draft.write_text(DRAFT + '\n## Supplementary checks\n\n'
        f'![Old BSF diagnostic](old/{old})\n\n'
        f'<img src="old/{old}" alt="BSF">\n\n'
        f'![BSF ref][old-bsf]\n\n[old-bsf]: old/{old}\n\n'
        f'![BSF shortcut]\n\n[BSF shortcut]: old/{old}\n\n'
        f'![Tyre supplement]({unrelated})\n\n'
        '## BSF old companion\n\nOutdated companion conclusion.\n\n'
        f'![Old tracker](figures/{TRACKER})\n\n'
        '### BSF nested section\n\nOld nested conclusion.\n\n'
        f'![Unrelated reference][tyre]\n\n[tyre]: {unrelated}\n')
    report = finalize_post_race_report(bundle_path=bundle, draft_path=draft)
    text = report.read_text()
    assert 'Companion unavailable: Approved original bank unavailable' in text
    assert 'Outdated companion conclusion' not in text
    assert 'Old nested conclusion' not in text
    assert old not in text and TRACKER not in text
    assert not re.search(r'!\[[^\]]*BSF|<img[^>]*BSF', text, re.I)
    assert not any('bsf' in p.name.lower() for p in report.parent.rglob('*'))
    assert 'Supplementary checks' in text
    assert (report.parent / unrelated).read_bytes() == b'keep unrelated'
    assert (report.parent / 'figures' / CANONICAL).read_bytes() == b'canonical'


@pytest.mark.parametrize('quote', ['"', "'", ''], ids=['double', 'single', 'unquoted'])
def test_successful_final_report_uses_only_approved_saved_bsf_images(tmp_path, quote):
    diagnostic = 'bsf_junction_reference_minus_canonical.png'
    unapproved = 'BSF_OLD_DIAGNOSTIC.png'
    sources = []
    for name in [CANONICAL, TRACKER, diagnostic]:
        p = tmp_path / name; p.write_bytes(('current ' + name).encode()); sources.append(p)
    component = {'status': 'SUCCESS'}
    bundle = create_report_bundle(run_path=tmp_path, workflow='post_race', event='2026-15',
        context={'bsf_junction_reference': component}, results={'bsf_junction_reference': component}, figures=sources)
    old = tmp_path / 'old'; old.mkdir()
    (old / diagnostic).write_bytes(b'old same-name diagnostic')
    (old / unapproved).write_bytes(b'old unapproved diagnostic')
    # Even a stray BSF file in the bundle is not approved by its saved manifest.
    (bundle / 'figures' / unapproved).write_bytes(b'old stray bundle diagnostic')
    draft = tmp_path / 'draft.md'
    draft.write_text(DRAFT + '\n## Supplementary checks\n\n'
        f'![BSF diagnostic](old/{diagnostic})\n\n'
        f'<img src={quote}old/{diagnostic}{quote} alt=diagnostic>\n\n'
        f'![BSF obsolete](old/{unapproved})\n')
    report = finalize_post_race_report(bundle_path=bundle, draft_path=draft)
    text = report.read_text()
    assert text.index(CANONICAL) < text.index(TRACKER) < text.index('Existing subsequent section')
    assert unapproved not in text and 'old/' not in text
    assert f'figures/{diagnostic}' in text
    assert (report.parent / 'figures' / diagnostic).read_bytes() == ('current ' + diagnostic).encode()
    assert not (report.parent / 'figures' / unapproved).exists()


@pytest.mark.parametrize('quote', ['"', "'", ''], ids=['double', 'single', 'unquoted'])
@pytest.mark.parametrize('status', ['FAILED', 'unavailable'])
def test_unavailable_final_report_removes_html_bsf_for_all_src_forms(tmp_path, quote, status):
    from woweekend.reports.bsf_junction import image_references
    canonical = tmp_path / CANONICAL
    canonical.write_bytes(b'canonical unchanged')
    component = {'status': status, 'error': 'Saved companion unavailable'}
    bundle = create_report_bundle(run_path=tmp_path, workflow='post_race', event='2026-15',
        context={'bsf_junction_reference': component}, results={'bsf_junction_reference': component},
        figures=[canonical])
    diagnostic = 'bsf_junction_reference_minus_canonical.png'
    old = tmp_path / 'old'; old.mkdir()
    (old / diagnostic).write_bytes(b'stale BSF diagnostic')
    unrelated = 'tyre_supplement.png'
    (tmp_path / unrelated).write_bytes(b'preserve unrelated')
    stale_tag = f'<img src={quote}old/{diagnostic}{quote} alt=diagnostic>'
    unrelated_tag = f'<img src={quote}{unrelated}{quote} alt=tyres>'
    draft = tmp_path / 'draft.md'
    draft.write_text(DRAFT + '\n## Supplementary checks\n\n'
        + stale_tag + '\n\n' + unrelated_tag + '\n')
    # The collector and sanitizer must agree on all valid HTML src forms.
    assert image_references(draft.read_text()) == [f'figures/{CANONICAL}', f'old/{diagnostic}', unrelated]
    report = finalize_post_race_report(bundle_path=bundle, draft_path=draft)
    text = report.read_text()
    assert 'Companion unavailable: Saved companion unavailable' in text
    assert diagnostic not in text and stale_tag not in text
    assert unrelated_tag in text and 'Supplementary checks' in text
    assert image_references(text) == [f'figures/{CANONICAL}', unrelated]
    assert not any('bsf' in p.name.lower() for p in report.parent.rglob('*'))
    assert (report.parent / 'figures' / CANONICAL).read_bytes() == b'canonical unchanged'
    assert (report.parent / unrelated).read_bytes() == b'preserve unrelated'
