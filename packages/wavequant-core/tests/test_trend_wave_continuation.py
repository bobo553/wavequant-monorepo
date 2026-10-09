"""Formal publication and its live extension belong to one causal wave."""

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_publication import trend_wave_identity
from wavequant.domain.models.model import Bar


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2024_wave_continuation.json').read_text(encoding='utf-8'))


def identity(proof, level=3, path=None):
    return trend_wave_identity(proof, trend_level=level, source_path=path or FIXTURE['formal_stroke']['source_path'])


def test_real_formal_and_developing_paths_have_one_first_known_wave_identity():
    formal, tail = FIXTURE['formal_stroke'], FIXTURE['developing_stroke']
    leg = formal['confirmed_legs'][0]
    assert [p['value'] for p in leg['points']] == [3.45, 18.9]
    assert [p['value'] for p in tail['points']] == [3.45, 19.63]
    assert leg['confirmation']['available_at'] == tail['confirmation']['available_at'] == '2025-03-11'
    assert identity(leg['confirmation']) == identity(tail['confirmation']) == leg['wave_id'] == tail['wave_id']
    assert leg['wave_id'].startswith('trend-wave-v1:')
    assert formal['wave_display_policy'] == tail['wave_display_policy'] == 'one_connection_per_confirmed_wave_v109'


@pytest.mark.parametrize('change', ('label', 'direct_render_fields', 'ordinal_zero', 'equivalent_numeric_value'))
def test_labels_and_direct_route_serialization_do_not_start_another_wave(change):
    proof = deepcopy(FIXTURE['formal_stroke']['confirmed_legs'][0]['confirmation'])
    before = deepcopy(proof)
    if change == 'label':
        proof['origin']['label'] = 'another display label'
    elif change == 'direct_render_fields':
        proof.pop('flip_high', None)
        proof.pop('alternation_low', None)
    elif change == 'ordinal_zero':
        proof['origin'].pop('ordinal', None)
    else:
        proof['confirmed_by']['value'] = 11.440
    assert identity(proof) == identity(before)


@pytest.mark.parametrize('change', ('level', 'path', 'direction', 'date', 'origin', 'ordinal', 'key', 'trigger', 'whole_origin'))
def test_independent_causal_facts_cannot_merge_only_because_the_price_origin_matches(change):
    proof = deepcopy(FIXTURE['formal_stroke']['confirmed_legs'][0]['confirmation'])
    expected = identity(proof)
    if change == 'level':
        assert identity(proof, level=2) != expected
        return
    if change == 'path':
        assert identity(proof, path='other-source-path') != expected
        return
    if change == 'direction': proof['direction'] = 'down'
    elif change == 'date': proof['available_at'] = '2025-03-12'
    elif change == 'origin': proof['origin']['index'] += 1
    elif change == 'ordinal': proof['origin']['ordinal'] = 1
    elif change == 'key': proof['broken_key']['value'] += .01
    elif change == 'trigger': proof['confirmed_by']['index'] += 1
    else: proof['wave_origin'] = dict(proof['origin'], index=proof['origin']['index'] - 1)
    assert identity(proof) != expected


def test_source_cycles_keep_their_flip_and_counter_evidence_in_wave_identity():
    data = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2019_trend_legs.json').read_text(encoding='utf-8'))
    proof = deepcopy(data['secondary_stroke']['confirmed_legs'][0]['confirmation'])
    assert proof['confirmation_rule'] == 'source_key_break_alternation_then_market_turn'
    expected = identity(proof, level=2)
    assert expected is not None
    changed = deepcopy(proof); changed['alternation_low']['available_at'] = '2019-01-28'
    assert identity(changed, level=2) != expected
    changed = deepcopy(proof); changed['retracement_origin'] = dict(proof['retracement_origin'], value=4.5)
    assert identity(changed, level=2) != expected
    changed = deepcopy(proof); changed.pop('alternation_low')
    assert identity(changed, level=2) is None


@pytest.mark.parametrize('cutoff,peak', [('2025-03-21', 18.9), ('2025-04-02', 18.9), ('2025-05-15', 19.63)])
def test_the_real_july_floor_keeps_its_wave_identity_through_causal_replay(cutoff, peak):
    raw = json.loads((Path(__file__).parent / 'fixtures/xiangyang_2025_trend_break.json').read_text(encoding='utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *prices) for day, *prices in raw['rows'] if day <= cutoff]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    third = tertiary_trends(second, bars)
    tail = third['developing_strokes'][-1]
    origin = next(p for p in tail['points'] if p['development_role'] == 'confirmed_direction_origin')
    assert origin['time'] == '2024-07-25' and tail['confirmed_endpoint']['value'] == peak
    assert tail['confirmation']['available_at'] == '2025-03-11'
    assert tail['points'][-1]['time'] <= cutoff
    for stroke in third['strokes']:
        for leg in stroke['confirmed_legs']:
            if leg['points'][0]['time'] == '2024-07-25':
                assert leg['wave_id'] == tail['wave_id']
                assert leg['points'][-1]['available_at'] <= cutoff
