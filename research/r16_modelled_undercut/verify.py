"""Offline R16 regeneration: real race calculations; saved descriptive context.

Run with woStrategy's venv, the three src roots on PYTHONPATH and MPLBACKEND=Agg.
Writes an isolated verification data root; never updates canonical run pointers.
"""
from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import shutil

from wodata.events import EventMetadata
from wostrategy.analysis.pre_race_tyre_prediction import load_pre_race_tyre_prediction_source
from woweekend.config.race import RaceConfig
from woweekend.workflows import race
from woweekend.reports.bundle import regenerate_report_bundle

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parents[2] / 'woData'
SOURCE = DATA / 'woweekend/runs/schema_v1/event=2026-16/workflow=race_preparation/runs/20261003T225359.171082Z-f370e12cf1'
POST = DATA / 'woweekend/runs/schema_v1/event=2026-16/workflow=post_race/runs/20261004T121826.318664Z-d8504fa00c'
BSF_SAVED = DATA.parent / 'woStrategy/research/r16_tyre_life_repair_20261006/run/report_bundle.en-GB.engineering/analysis_results.json'

def read(name):
    return json.loads((SOURCE / f'{name}.json').read_text())

def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}

before, post_before = hashes(SOURCE), hashes(POST)
bsf_before = BSF_SAVED.read_bytes()
raw = read('config.input')
raw['data_root'] = str(ROOT / 'data')
metadata = EventMetadata(**read('event.metadata'))
# The normal provider reads the canonical cache; the workflow applies overrides
# and constructs ONE effective StrategyModel for strategies and the new curve.
def tyres(**kwargs):
    kwargs['data_root'] = DATA
    return load_pre_race_tyre_prediction_source(**kwargs)

def traffic(**kwargs):
    figures = []
    for source in (SOURCE / 'figures').glob('traffic_history_*'):
        target = Path(kwargs['run_path']) / 'figures' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        figures.append(target)
    return read('historical_traffic_context'), figures

with patch.object(race, '_report_fp_evidence', return_value=read('practice_tyre_evidence')), \
     patch.object(race, 'load_history', return_value=read('historical_interruptions')), \
     patch.object(race, 'query_historical_vsc_evidence', return_value=read('historical_empirical_vsc_context')), \
     patch.object(race, '_load_previous_season_operational_inputs', return_value=read('historical_operational_inputs_context')), \
     patch.object(race, 'historical_traffic_section', side_effect=traffic):
    run = race.run_race(RaceConfig.parse(raw), event='2026-16', input_config=raw,
                        event_resolver=lambda *args, **kwargs: metadata,
                        tyre_provider=tyres,
                        model_config_ensurer=lambda **kwargs: read('live_mc_model_config'))

curve = run.load_json('modelled_undercut_power')
assert len(curve['points']) == 513
for compound, values in read('tyre_prediction.effective')['compounds'].items():
    assert curve['model']['compound_delta'][compound] == values['performance_delta_to_medium']['effective_value']
    assert curve['model']['degradation'][compound] == values['degradation_seconds_per_lap']['effective_value']
comparisons = {}
for name in ('strategy_unrestricted', 'strategy_rules_compliant', 'cutoff_unrestricted', 'cutoff_rules_compliant'):
    comparisons[name] = run.load_json(name) == read(name)
    assert comparisons[name], name
bundle = run.path / 'report_bundle.zh-CN.engineering'
assert (bundle / 'MODELLED_UNDERCUT_POWER.md').exists()
assert (bundle / 'figures/modelled_undercut_power.png').exists()
assert json.loads((bundle / 'analysis_results.json').read_text())['modelled_undercut_power']['output'] == curve
regenerated = regenerate_report_bundle(run_path=run.path, language='en-GB')
assert (regenerated / 'analysis_results.json').read_bytes() == (bundle / 'analysis_results.json').read_bytes()
assert hashes(SOURCE) == before
assert hashes(POST) == post_before
post_results = json.loads((POST / 'analysis_results.json').read_text())
assert BSF_SAVED.read_bytes() == bsf_before
bsf_status = json.loads(bsf_before)['bsf_junction_reference']['status']
assert bsf_status == 'DEGRADED'
evidence = {
    'source_run': str(SOURCE), 'verification_run': str(run.path),
    'points': len(curve['points']), 'compound_transitions': 9, 'model_age_range': [0, 56],
    'canonical_numerical_outputs_equal': comparisons,
    'canonical_race_run_hashes_unchanged': True, 'canonical_post_run_hashes_unchanged': True,
    'older_canonical_post_bsf_status': post_results['bsf_junction_reference']['status'],
    'saved_bsf_reference': str(BSF_SAVED),
    'bsf_component_status': bsf_status,
    'saved_bsf_reference_unchanged': True,
    'report_bundle': str(bundle), 'saved_output_regeneration': str(regenerated),
    'offline_scope': 'Real strategy and cutoff calculations; cached event metadata, FP and historical report context; model config reused. No post-race/BSF regeneration.'
}
(ROOT / 'verification.json').write_text(json.dumps(evidence, indent=2) + '\n')
print(json.dumps(evidence, indent=2), flush=True)
