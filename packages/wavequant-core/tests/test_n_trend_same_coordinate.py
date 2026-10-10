"""同一已确认高低点上的结构方向与后来 N 达标分别保留首次可知时间。"""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from wavequant.domain.market_structure.n_trend_confirmation import N_TARGET_CONFIRMATION
from wavequant.domain.market_structure.n_trend_reversals import n_target_reversals

from .test_n_trend_levels import _sample


@pytest.mark.parametrize('source_level', [0, 1, 2])
@pytest.mark.parametrize('up', [False, True])
@pytest.mark.parametrize('integer_clock', [False, True])
def test_later_n_evidence_attaches_to_an_existing_opposite_endpoint_without_rewriting_history(
    source_level: int, up: bool, integer_clock: bool,
) -> None:
    bars, source = _sample(up=up)
    if integer_clock:
        dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
        for point in source:
            known = point['available_at']
            assert isinstance(known, str)
            point['available_at'] = dates[known]
    field = 'source_turn_position' if source_level == 0 else f'source_level{source_level}_position'
    existing = dict(source[1], trend_level=source_level + 1, confirmation_rule='strict_same_level_market_key_break',
                    wave_direction_before='down' if up else 'up', wave_direction_after='down' if up else 'up',
                    confirmed_by=dict(source[1]), broken_key=dict(source[0]),
                    upgrade_evidence=dict(direction='down' if up else 'up', role='endpoint',
                                          available_at=source[1]['available_at']),
                    incoming_trend_state='confirmed',
                    incoming_trend_confirmation=dict(direction='down' if up else 'up',
                                                     available_at=source[1]['available_at']), **{field: 1})
    before = deepcopy(existing)

    equal_target = n_target_reversals([existing], source, bars[:10], source_level=source_level,
                                    n_target_trend_confirmation_enabled=True)
    assert equal_target == [before]
    selected = n_target_reversals([existing], source, bars, source_level=source_level,
                                 n_target_trend_confirmation_enabled=True)

    assert existing == before
    origin = next(point for point in selected if point['index'] == 1)
    proof = origin['n_target_confirmation']
    assert isinstance(proof, Mapping)
    assert proof['confirmation_rule'] == N_TARGET_CONFIRMATION
    assert proof['direction'] == ('up' if up else 'down')
    assert proof['available_at'] == (10 if integer_clock else '2024-01-11')
    assert {key: origin[key] for key in before} == before
    assert n_target_reversals(selected, source, bars, source_level=source_level,
                             n_target_trend_confirmation_enabled=True) == selected
    assert n_target_reversals(selected, source, bars, source_level=source_level,
                             n_target_trend_confirmation_enabled=False) == [before]
