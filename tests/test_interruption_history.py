import json
import pandas as pd
from wodata.interruption_history import load_history, write_history
from wostrategy.analysis.interruption_history import extract_race, history_markdown
from woweekend.reports.bundle import create_report_bundle, regenerate_report_bundle
from woweekend.workflows.race import run_race
from woweekend.config.race import RaceConfig
from test_race_workflow import _event, _tyres, _ensure


def prepared(root):
    identity = {'country': 'Testland', 'location': 'Test Circuit', 'event_name': 'Prior Race'}
    race = extract_race({'season': 2025, 'round_number': 7, 'event_id': '2025-07', 'event_identity': identity}, {
        'track_status': pd.DataFrame([(0, '1'), (20, '4'), (30, '5'), (40, '1')], columns=['Time', 'Status']),
        'session_status': pd.DataFrame([(10, 'Started'), (100, 'Finished')], columns=['Time', 'Status']),
        'official_start': 10,
        'laps': pd.DataFrame({'LapNumber': [1, 2], 'Time': [15, 100], 'DriverNumber': ['1', '1']}),
        'timing_stream': pd.DataFrame([(0, '1', 1, 'LAP 1'), (15, '1', 1, 'LAP 2'), (100, '1', 1, 'LAP 3')],
                                     columns=['Time', 'Driver', 'Position', 'GapToLeader'])})
    payload = {'schema_version': 1, 'semantic_role': 'historical_race_interruptions', 'status': 'available',
               'target': {'season': 2026, 'round_number': 7, 'event_identity': identity}, 'races': [race]}
    write_history(payload, root)
    return payload


def test_real_workflow_and_report_preserve_canonical_values_without_acquisition(tmp_path, monkeypatch):
    payload = prepared(tmp_path)
    import wodata.interruption_history as contract
    monkeypatch.setattr(contract, 'load_race_source', lambda *a, **k: (_ for _ in ()).throw(AssertionError('network attempted')))
    raw = RaceConfig.template()
    raw.update({'total_laps': 6, 'result_count': 2, 'data_root': str(tmp_path)})
    raw['degradation_cutoff'] = {'scan_min': .05, 'scan_max': .15, 'coarse_step': .05, 'refine_tolerance': .01}
    run = run_race(RaceConfig.parse(raw), event='2026-07', tyre_provider=_tyres,
                   model_config_ensurer=_ensure, event_resolver=_event)
    assert run.load_json('historical_interruptions') == payload
    assert run.load_json('report_context')['historical_interruptions'] == payload
    results = run.load_json('analysis_results')
    assert results['historical_interruptions']['output'] == payload
    bundle = run.path / 'report_bundle.zh-CN.engineering'
    assert (bundle / 'HISTORICAL_INTERRUPTION_CONTEXT.md').read_text() == history_markdown(payload)
    assert 'at least 1 (incomplete): ~L2' in (bundle / 'HISTORICAL_INTERRUPTION_CONTEXT.md').read_text()
    assert 'Historical Safety Car / Red Flag context' in (bundle / 'REPORT_INSTRUCTION.md').read_text()
    # Regeneration uses the run snapshot, even if the prepared source changes.
    contract.history_path(2026, 7, tmp_path).unlink()
    regenerated = regenerate_report_bundle(run_path=run.path, language='en-GB')
    assert (regenerated / 'HISTORICAL_INTERRUPTION_CONTEXT.md').read_text() == history_markdown(payload)


def test_legacy_report_preserves_unavailable_and_file_limit(tmp_path):
    figures = []
    for i in range(20):
        path = tmp_path / f'{i}.png'; path.write_bytes(b'fixture'); figures.append(path)
    bundle = create_report_bundle(run_path=tmp_path, workflow='race_preparation', event='2026-07',
        context={}, results={}, figures=figures)
    text = (bundle / 'HISTORICAL_INTERRUPTION_CONTEXT.md').read_text()
    assert 'unavailable' in text
    assert 'No SC' not in text
    assert sum(p.is_file() for p in bundle.rglob('*')) == 20
