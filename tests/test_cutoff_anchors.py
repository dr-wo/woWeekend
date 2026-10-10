import pytest

from woweekend.workflows.race import _fp_medium_cutoff_markers, _cutoff_output_with_fp_markers


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -1, 'invalid'])
def test_invalid_medium_coordinate_is_neither_marker_nor_anchor(value):
    evidence = {'sessions': [{'session': 'FP1', 'status': 'available', 'quantities': {
        'degradation:MEDIUM': {'posterior_coordinate': {'median': value},
                               'support': {'status': 'measured', 'usable_laps': 3, 'usable_runs': 1}}
    }}]}
    assert _fp_medium_cutoff_markers(evidence) == []


def test_existing_support_gate_and_median_only_anchor_contract():
    quantity = {'posterior_coordinate': {'median': .15, 'p10': .1, 'p90': .5, 'unit': 's/lap'},
                'support': {'status': 'measured', 'usable_laps': 3, 'usable_runs': 1}}
    evidence = {'sessions': [
        {'session': 'FP1', 'status': 'unavailable', 'quantities': {'degradation:MEDIUM': quantity}},
        {'session': 'FP2', 'status': 'available', 'quantities': {'degradation:MEDIUM': quantity}},
        {'session': 'FP3', 'status': 'available', 'quantities': {'degradation:SOFT': quantity}},
    ]}
    markers = _fp_medium_cutoff_markers(evidence)
    assert len(markers) == 1
    assert markers[0]['session'] == 'FP2'
    assert markers[0]['medium_degradation'] == .15
    assert markers[0]['p90'] == .5
    output = _cutoff_output_with_fp_markers({'primary_1_to_2': .09}, markers)
    assert output['fp_markers_modify_production_inputs'] is False
    assert 'do not predict' in output['fp_marker_interpretation']
    assert output['primary_1_to_2'] == .09
    quantity['support']['status'] = 'prior_only'
    assert _fp_medium_cutoff_markers(evidence) == []
