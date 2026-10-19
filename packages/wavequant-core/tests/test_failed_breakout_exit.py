"""假突破资格退出：只使用当时已知翻空、突破前量能及截至当天的收盘。"""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from tests.test_source_bear_exit import opening, parent
from tests.test_source_bear_structure_exit import sample
from wavequant.domain.market_structure.failed_breakout_exit import (
    FAILED_BREAKOUT_EVENT, FailedBreakoutState, prepare_failed_breakout,
)
from wavequant.domain.market_structure.source_bear_exit import (
    SOURCE_BEAR_EVENT, confirmed_source_bear, source_bearish_exits,
)
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _facts, _pivot
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.trading_intervals import TradingEligibility


def scenario():
    start = datetime(2020, 1, 1)
    bars = [Bar(start + timedelta(days=index), 'test', 8, 9, 7, 8, 100) for index in range(30)]
    bars[1] = replace(bars[1], low=6)
    bars[3] = replace(bars[3], high=10, close=9)
    bars[5] = replace(bars[5], low=5)
    bars[25] = replace(bars[25], open=9, high=10.5, low=8.9, close=9.5, volume=150)
    bars[26] = replace(bars[26], open=9.5, high=9.8, low=9.1, close=9.4, volume=150)
    bars[27] = replace(bars[27], open=10, high=10.5, low=8, close=8.5, volume=200)

    def point(index, kind, price, known):
        return dict(index=index, ordinal=0, kind=kind, value=price, available_at=known, state='confirmed')

    broken, origin, low = point(1, 'L', 6, 2), point(3, 'H', 10, 4), point(5, 'L', 5, 6)
    low['observations'] = [dict(title='翻多为空', available_at=6,
                                confirmed_high=dict(origin), key=dict(broken))]
    return bars, dict(id='lower', source_path='source', points=[broken, origin, low])


def seed_for(stroke, now, dates):
    facts = _facts(stroke['points'], dates, now)
    points = tuple(p for raw in stroke['points'] if (p := _pivot(raw, dates, now)) is not None)
    return prepare_failed_breakout(facts, points)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_same_rule_closes_direct_parent_at_all_levels(level):
    bars, stroke = scenario()
    seed = seed_for(stroke, 27, {})
    assert seed is not None
    state, proof = FailedBreakoutState(seed, seed.known).advance(bars, 27, level - 1, 'lower')
    assert proof is not None and confirmed_source_bear(proof, 27)
    assert proof['longest_held_sessions'] == 2
    assert proof['breakout_level'] == 9 and proof['volume_mean'] == 100
    gate = TradingEligibility(level)
    gate.observe(opening(level))
    exits = source_bearish_exits([parent(level)], [proof], 27)
    assert gate.observe(exits[0]) is False
    assert (gate.intervals[0].end_index, gate.intervals[0].end_key_index,
            gate.intervals[0].end_key_price) == (27, 3, 9)
    assert state.advance(bars, 29, level - 1, 'lower')[1] is None


@pytest.mark.parametrize('change', ['three_closes', 'touch', 'small_body', 'small_volume',
                                   'zero_volume', 'no_breakout', 'future_flip', 'missing_key'])
def test_incomplete_or_stabilized_breakout_does_not_end_permission(change):
    bars, stroke = scenario()
    if change == 'three_closes':
        # 三根都站上原收盘9；其中两根最高价仍低于原最高价10。
        bars[24] = replace(bars[24], high=9.7, close=9.2)
    elif change == 'touch':
        bars[27] = replace(bars[27], close=9)
    elif change == 'small_body':
        bars[27] = replace(bars[27], open=8.6)
    elif change == 'small_volume':
        bars[27] = replace(bars[27], volume=199.99)
    elif change == 'zero_volume':
        bars[:25] = [replace(bar, volume=0) for bar in bars[:25]]
    elif change == 'no_breakout':
        bars[25] = replace(bars[25], high=10)
        bars[27] = replace(bars[27], high=10)
    elif change == 'future_flip':
        stroke['points'][2]['observations'][0]['available_at'] = 28
    else:
        stroke['points'].pop(0)
    seed = seed_for(stroke, 27, {})
    proof = FailedBreakoutState(seed, seed.known).advance(bars, 27, 1, 'lower')[1] if seed else None
    assert proof is None


@pytest.mark.parametrize('body,expected', [(.03, True), (.029, False)])
def test_body_and_volume_inclusive_thresholds_and_equal_close_resets_count(body, expected):
    bars, stroke = scenario()
    # 等于末升收盘不算站上；突破日后的量不改写冻结均量。
    bars[26] = replace(bars[26], close=9, volume=100_000)
    bars[27] = replace(bars[27], open=100, high=100, close=100 * (1 - body), low=8)
    # 同比例放大原末升高和收盘，使边界阴线仍低于末升收盘。
    bars[3] = replace(bars[3], high=110, close=98)
    stroke['points'][1]['value'] = 110
    stroke['points'][2]['observations'][0]['confirmed_high']['value'] = 110
    bars[25] = replace(bars[25], open=99, high=111, close=100)
    bars[26] = replace(bars[26], open=99, high=100, close=98)
    seed = seed_for(stroke, 27, {})
    assert seed is not None
    _, proof = FailedBreakoutState(seed, seed.known).advance(bars, 27, 1, 'lower')
    assert (proof is not None) is expected
    if proof:
        assert proof['volume_mean'] == 100 and proof['longest_held_sessions'] == 1


@pytest.mark.parametrize('restore', ['copy', 'checkpoint', 'combine'])
def test_replay_checkpoint_path_isolation_and_no_duplicate_exit(restore):
    bars, stroke = scenario()
    lower, upper = dict(trend_level=1, strokes=[stroke]), parent(2)
    together, source, target = TrendLifecycle(), TrendLifecycle(), TrendLifecycle()
    observed = []
    for now in range(len(bars)):
        combined = together.advance([lower, upper], bars, now, {}, _owned_structure=True)
        child = source.advance([lower], bars, now, {}, _owned_structure=True)
        exits = target.advance([upper], bars, now, {}, source_events=child, _owned_structure=True)
        assert [e for e in combined if e['event'] == SOURCE_BEAR_EVENT] == exits
        observed.extend(exits)
        if now in (25, 26, 27):
            if restore == 'copy':
                together, source, target = together.copy(), source.copy(), target.copy()
            elif restore == 'checkpoint':
                together = TrendLifecycle.from_checkpoint(together.checkpoint())
                source = TrendLifecycle.from_checkpoint(source.checkpoint())
                target = TrendLifecycle.from_checkpoint(target.checkpoint())
            else:
                together = TrendLifecycle.combine([source, target])
    assert [event['bar_index'] for event in observed] == [27]
    proof = observed[0]['source_confirmation']
    assert source_bearish_exits([parent(3)], [proof], 27) == []
    assert source_bearish_exits([dict(trend_level=2, strokes=[dict(id='other', source_path='unrelated')])],
                               [proof], 27) == []


@pytest.mark.parametrize('change', ['revised', 'ended'])
def test_revised_seed_and_ended_path_drop_pending_breakout(change):
    bars, stroke = scenario()
    engine = TrendLifecycle()
    changed = deepcopy(stroke)
    changed['points'][2]['observations'] = []
    observed = []
    for now in range(len(bars)):
        current = stroke if now < 27 else changed
        strokes = [] if change == 'ended' and now >= 27 else [current]
        observed.extend(engine.advance([dict(trend_level=1, strokes=strokes)], bars, now, {},
                                       _owned_structure=True))
    assert not any(event['event'] == FAILED_BREAKOUT_EVENT for event in observed)


@pytest.mark.parametrize('change', [{'bar_index': 26}, {'available_index': 28}, {'attack_index': 28},
    {'longest_held_sessions': 3}, {'breakout_level': 8.5}, {'observed_volume': 199},
    {'volume_mean': float('nan')}, {'volume_baseline_end': 25}, {'flip_confirmed': False}, {'attack_high': 10}])
def test_invalid_proof_cannot_route_to_permission(change):
    bars, stroke = scenario()
    seed = seed_for(stroke, 27, {})
    assert seed is not None
    proof = FailedBreakoutState(seed, seed.known).advance(bars, 27, 1, 'lower')[1]
    assert proof is not None
    assert not confirmed_source_bear(dict(proof, **change), 27)


def test_xianfeng_september_9_requires_no_september_10_point_or_later_wave_completion():
    bars, stroke = sample()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    now = dates['2020-09-09']
    stroke['points'] = stroke['points'][:3]
    seed = seed_for(stroke, now, dates)
    assert seed is not None
    _, proof = FailedBreakoutState(seed, seed.known).advance(bars[:now + 1], now, 1, 'lower')
    assert proof is not None and confirmed_source_bear(proof, now)
    assert proof['confirmation_close'] == 5.18 and proof['breakout_level'] == 5.48
    assert proof['attack_index'] == dates['2020-09-07'] and proof['longest_held_sessions'] == 2
