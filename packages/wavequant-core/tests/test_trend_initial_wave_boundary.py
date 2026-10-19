"""首段上下文必须保留未突破边界，局部方向不提前取得升级身份。"""

from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.trend_level_upgrades import (
    _TrendUpgradeState, promote_trend_key_breaks,
)
from wavequant.domain.market_structure.trend_levels import _completed_source_point, _source_turns


PRICES = [13.02, 4.657089031357015, 6.423208573722655, 4.667601647680621,
          8.97474528141774, 3.811046142228825, 5.3676706228575, 4.050031354268106,
          7.261939342406007, 4.437287627806584, 11.024346589744814]


def _source(prices, level, mirror=False):
    # 稀疏结构只验证算法；以下合成时钟不代表实股确认日。
    start = date(2020, 1, 1)
    return [dict(index=i, ordinal=0, kind=('L' if mirror else 'H') if i % 2 == 0
                 else ('H' if mirror else 'L'), value=100-price if mirror else price,
                 time=(start + timedelta(days=2*i)).isoformat(),
                 available_at=(start + timedelta(days=2*i+1)).isoformat(),
                 trend_level=level-1, state='reversal', label=f'p{i}')
            for i, price in enumerate(prices)]


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_local_rebound_cannot_replace_unbroken_initial_boundary(level, mirror):
    source = _source(PRICES, level, mirror)
    original = deepcopy(source)
    for size in range(1, 7):
        assert promote_trend_key_breaks(source[:size], [], trend_level=level) == []
    established = promote_trend_key_breaks(source[:7], [], trend_level=level)
    assert [point['index'] for point in established] == [0]
    assert established[0]['available_at'] == source[6]['available_at']
    assert _completed_source_point(established[0]) is not None
    result = promote_trend_key_breaks(source, [], trend_level=level)
    # 已成立波段仍按直接来源末跌高处理，不冻结到初始最高价。
    assert [point['index'] for point in result] == [0, 5]
    assert result[1]['lower_wave_identity_evidence']['reversal_key']['index'] == 4
    assert all(_completed_source_point(point) is not None for point in result)
    assert source == original


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_initial_boundary_retries_after_pre_origin_evidence_expires(level, mirror):
    source = _source([11.25, 8.66, 13.02, 7.95, 10.66, 8.83, 11.58, 8.47, 11.5, 4.909], level, mirror)
    assert promote_trend_key_breaks(source[:5], [], trend_level=level) == []
    result = promote_trend_key_breaks(source, [], trend_level=level)
    assert [point['index'] for point in result] == [2]
    assert result[0]['initial_boundary'] is True
    assert result[0]['available_at'] == source[8 if level == 1 else 9]['available_at']
    assert _completed_source_point(result[0]) is not None


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('price', [12.9, 13.02, 13.03])
def test_initial_direction_changes_only_after_strict_boundary_cross(level, mirror, price):
    source = _source([13.02, 4.657, 6.423, 4.668, price], level, mirror)
    result = promote_trend_key_breaks(source, [], trend_level=level)
    assert [point['index'] for point in result] == ([1] if price > 13.02 else [])


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
def test_pending_boundary_survives_append_and_copy_without_publishing_early(level, mirror):
    source = _source(PRICES, level, mirror)
    state = _TrendUpgradeState([], [], trend_level=level, event_sink=None)
    for count in range(1, len(source) + 1):
        assert state.append(_source_turns(source[:count], level-1)[-1:], [])
        result = state.advance()
        assert result == promote_trend_key_breaks(source[:count], [], trend_level=level)
        if count == 5:
            assert state.owned_direction is None
            assert state.pending_origin.index == 0
            assert state.snapshot() == []
            fork = deepcopy(state)
            assert fork.append(_source_turns(source, level-1)[count:], [])
            assert [item['index'] for item in fork.advance()] == [0, 5]
            assert state.snapshot() == []


@pytest.mark.parametrize('level', [1, 2, 3])
def test_strict_channel_does_not_bootstrap_from_pending_price_context(level):
    source = _source(PRICES, level)
    state = _TrendUpgradeState(_source_turns(source, level-1), [], trend_level=level,
                               event_sink=None, strict_only=True)
    assert state.advance() == []
    assert state.pending_origin is None
    assert state.owned_direction is None


def test_guofang_frozen_history_retains_2018_to_2021_tertiary_leg():
    from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
    from wavequant.domain.market_structure.lecture_trend import reversal_trends
    from wavequant.domain.market_structure.secondary_trend import secondary_trends
    from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
    from wavequant.domain.models.model import Bar

    fixture = json.loads((Path(__file__).parent / 'fixtures' / 'guofang_2024_impulse_defense.json').read_text('utf-8'))
    # 覆盖端点闭合知识日 2024-03-21；此前前缀不应借用未来闭合。
    bars = tuple(Bar(datetime.fromisoformat(day), fixture['symbol'], *values)
                 for day, *values in fixture['bars'])
    drawing = lecture_drawing(bars)
    first = reversal_trends(drawing, bars)
    second = secondary_trends(first, bars)
    third = tertiary_trends(second, bars)
    legs = [(left, right) for stroke in third['strokes']
            for left, right in zip(stroke['points'], stroke['points'][1:])]
    def dates(point):
        return bars[point['index']].timestamp.date().isoformat()

    expected = [(left, right) for left, right in legs
                if (dates(left), dates(right)) == ('2018-02-01', '2021-02-08')]
    assert expected
    assert all(left['kind'] == 'H' and right['kind'] == 'L' for left, right in expected)
    assert not any((dates(left), dates(right)) == ('2019-11-18', '2021-02-08') for left, right in legs)
