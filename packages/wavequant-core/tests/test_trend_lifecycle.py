"""Causal six-state contracts; fixtures supply already certified geometry."""

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.trend_lifecycle import (
    LifecycleState, TrendLifecycle, lifecycle_interval_days,
)
from wavequant.domain.market_structure.trend_landmarks import bearish_turn_signals
from wavequant.domain.strategies.chart_entry_history import _copy_replay_state
from wavequant.domain.strategies.backtest_history_reuse import _CheckpointPickler
from wavequant.domain.models.model import Bar


def scenario(level=2, up=True):
    prices = [(10, 11, 9, 10), (10, 11, 9.5, 10.5), (11, 13, 10, 12),
              (12, 12.5, 11, 12), (12, 13, 11.5, 12.5), (13.2, 14.5, 12, 14)]
    start = datetime(2026, 1, 1)
    bars = [Bar(start + timedelta(days=i), 'sz.000678', *values, 100 + i * 10)
            for i, values in enumerate(prices)]

    def point(index, kind, value, known, **extra):
        return dict(index=index, ordinal=0, kind=kind, value=value, available_at=known,
                    time=(start + timedelta(days=index)).date().isoformat(), state='confirmed', **extra)

    origin = point(0, 'L', 9, 1)
    high = point(2, 'H', 13, 2)
    origin['observations'] = [dict(title='底部疑虑', available_at=1)]
    high['observations'] = [dict(title='翻空为多', available_at=2, confirmed_low=dict(origin),
                                 key=point(0, 'H', 12, 1))]
    low = point(3, 'L', 11, 4, observations=[dict(title='空多交替', available_at=4, flip_high=dict(high))])
    points = [origin, high, low]
    if not up:
        bars = [replace(bar, open=30-bar.open, high=30-bar.low, low=30-bar.high, close=30-bar.close)
                for bar in bars]
        names = {'底部疑虑': '头部疑虑', '翻空为多': '翻多为空', '空多交替': '多空交替'}

        def mirror(value):
            if isinstance(value, list):
                return [mirror(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {({'confirmed_low': 'confirmed_high', 'flip_high': 'flip_low'}.get(key, key)): mirror(item)
                      for key, item in value.items()}
            if 'value' in result:
                result['value'] = 30-result['value']
                result['kind'] = 'H' if result['kind'] == 'L' else 'L'
            if 'title' in result:
                result['title'] = names[result['title']]
            return result

        points = mirror(points)
    level_row = dict(trend_level=level, strokes=[dict(id='path', source_path='source', points=points)],
                     _path_market_end_indices={'source': None})
    return bars, level_row


def replay(bars, level, *, through=None, book=None, begin=0):
    engine = book or TrendLifecycle()
    dates = {bar.timestamp.date().isoformat(): i for i, bar in enumerate(bars)}
    events = []
    for now in range(begin, len(bars) if through is None else through):
        events.extend(engine.advance([level], bars, now, dates))
    return engine, events


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('up', [True, False])
def test_three_levels_have_identical_strict_causal_cycles(level, up):
    bars, geometry = scenario(level, up)
    _, events = replay(bars, geometry)
    states = [(row['bar_index'], row['state']) for row in events if row['event'] == 'trend_lifecycle_state']
    expected = ['空翻多', '空多交替', '转多确认'] if up else ['多翻空', '多空交替', '转空确认']
    assert states == list(zip([2, 4, 5], expected))
    assert lifecycle_interval_days(events, len(bars), level, up=up) == (False, False, False, False, True, True)
    assert not any(lifecycle_interval_days(events, len(bars), level, up=not up))


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('up', [True, False])
def test_suspicion_and_an_orphan_alternation_cannot_replace_a_flip(level, up):
    bars, geometry = scenario(level, up)
    geometry['strokes'][0]['points'][1]['observations'] = []
    _, events = replay(bars, geometry)
    assert any(event['event'] == 'trend_lifecycle_suspicion' for event in events)
    assert not any(event['event'] == 'trend_lifecycle_state' for event in events)
    if level:
        from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals
        assert confirmed_trading_intervals(events, len(bars), level) == ()


@pytest.mark.parametrize('level', [1, 2, 3])
def test_local_upgrade_below_frozen_bearish_key_cannot_open_guofang_like_window(level):
    # 国芳现场的冻结11.50、局部6.13、高点6.68关系；缩减结构，不冒充完整实股回放。
    from wavequant.domain.market_structure.trend_lifecycle import _facts
    from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals

    def point(index, kind, price, known):
        return dict(index=index, ordinal=0, kind=kind, value=price, available_at=known, state='confirmed')

    frozen = point(0, 'H', 11.50, 0)
    bottom = point(1, 'L', 4.95, 1)
    local = point(2, 'H', 6.1343558282208175, 2)
    origin = point(3, 'L', 5.0613496932515005, 4)
    origin['observations'] = [dict(title='底部疑虑', available_at=4, key=dict(frozen))]
    high = point(5, 'H', 6.680981595091979, 6)
    high['upgrade_evidence'] = dict(direction='up', role='endpoint', available_at=6,
        origin=dict(origin), broken_key=dict(local), confirmed_by=dict(high))
    low = point(6, 'L', 5.213190184049045, 7)
    low['observations'] = [dict(title='空多交替', available_at=7, flip_high=dict(high))]
    points = [frozen, bottom, local, origin, high, low]
    assert not any(fact.title == '翻空为多' for fact in _facts(points, {}, 7))
    bars = [Bar(datetime(2019, 1, 1) + timedelta(days=index), 'sh.601086', 5.3, 12, 5.2, 5.5, 100)
            for index in range(8)]
    geometry = dict(trend_level=level, strokes=[dict(id='path', points=points)])
    book, before = replay(bars, geometry, through=7)
    _, after = replay(bars, geometry, begin=7, book=TrendLifecycle.from_checkpoint(book.checkpoint()))
    assert not any(event.get('state') == '空翻多' for event in before + after)
    assert confirmed_trading_intervals(before + after, len(bars), level) == ()


def test_formal_same_level_flip_precedes_local_upgrade_metadata():
    from wavequant.domain.market_structure.trend_lifecycle import _facts

    bars, geometry = scenario()
    origin, high, _ = geometry['strokes'][0]['points']
    key = dict(index=0, ordinal=1, kind='H', value=12, available_at=1, state='confirmed')
    high['upgrade_evidence'] = dict(direction='up', role='endpoint', available_at=2,
        origin=dict(origin), broken_key=key, confirmed_by=dict(high))
    facts = [fact for fact in _facts([origin, key, high], {}, len(bars)-1) if fact.title == '翻空为多']
    assert len(facts) == 1 and facts[0].origin.index == origin['index']


@pytest.mark.parametrize('up', [True, False])
def test_gap_volume_candidates_never_replace_missing_alternation(up):
    bars, geometry = scenario(up=up)
    geometry['strokes'][0]['points'].pop()
    _, events = replay(bars, geometry)
    assert any(row['event'] == 'trend_lifecycle_candidate' for row in events)
    assert all(row['confirmed'] is False for row in events if row['event'] == 'trend_lifecycle_candidate')
    assert not any(row.get('state') in ('转多确认', '转空确认') for row in events)
    assert not any(lifecycle_interval_days(events, len(bars), 2, up=up))


@pytest.mark.parametrize('up', [True, False])
def test_touch_or_preconfirmation_cross_does_not_confirm(up):
    bars, geometry = scenario(up=up)
    threshold = 13 if up else 17
    bars[5] = replace(bars[5], close=threshold)
    _, events = replay(bars, geometry)
    assert not any(row.get('state') in ('转多确认', '转空确认') for row in events)
    bars, geometry = scenario(up=up)
    geometry['strokes'][0]['points'][2]['available_at'] = 5
    geometry['strokes'][0]['points'][2]['observations'][0]['available_at'] = 5
    _, events = replay(bars, geometry)
    assert not any(row.get('state') in ('转多确认', '转空确认') for row in events)


@pytest.mark.parametrize('up', [True, False])
def test_defense_failure_closes_search_window_before_breakout(up):
    bars, geometry = scenario(up=up)
    bars[5] = replace(bars[5], low=10 if up else bars[5].low, high=20 if not up else bars[5].high)
    _, events = replay(bars, geometry)
    assert any(row.get('reason') == 'defense_broken' for row in events)
    assert lifecycle_interval_days(events, 6, 2, up=up)[-1] is False


def test_checkpoint_replays_partial_candle_without_duplicates_or_mutation():
    from io import BytesIO
    import pickle

    bars, geometry = scenario()
    book, previous = replay(bars, geometry, through=5)
    checkpoint = book.checkpoint()
    stream = BytesIO()
    _CheckpointPickler(stream, protocol=5).dump(checkpoint)
    restored = TrendLifecycle.from_checkpoint(pickle.loads(stream.getvalue()))
    _, resumed = replay(bars, geometry, begin=5, book=restored)
    _, cold = replay(bars, geometry)
    assert previous + resumed == cold
    partial = list(bars)
    partial[5] = replace(partial[5], close=13)
    _, changed = replay(partial, geometry, begin=5, book=TrendLifecycle.from_checkpoint(checkpoint))
    assert not any(row.get('state') == LifecycleState.BULL_CONFIRMED for row in changed)
    assert book.checkpoint() == checkpoint


def test_revision_and_path_cutoff_revoke_permission_without_erasing_prior_events():
    bars, geometry = scenario()
    book, previous = replay(bars, geometry, through=5)
    revised = deepcopy(geometry)
    revised['strokes'][0]['points'][2]['observations'] = []
    _, events = replay(bars, revised, begin=5, book=book.copy())
    assert events[0]['reason'] == 'source_evidence_revised'
    assert lifecycle_interval_days(previous + events, 6, 2, up=True)[-1] is False
    closed = deepcopy(geometry)
    closed['_path_market_end_indices']['source'] = 5
    _, events = replay(bars, closed, begin=5, book=book.copy())
    assert events[0]['reason'] == 'source_path_ended'


def test_public_bear_confirmation_and_missing_alternation():
    bars, geometry = scenario(up=False)
    signals = bearish_turn_signals(geometry['strokes'], bars, trend_level=2)
    assert [(row['index'], row['breakout_level']) for row in signals] == [(5, 17)]
    geometry['strokes'][0]['points'].pop()
    assert bearish_turn_signals(geometry['strokes'], bars, trend_level=2) == []


def test_future_reference_inside_observation_cannot_supply_an_early_alternation():
    bars, geometry = scenario()
    observation = geometry['strokes'][0]['points'][2]['observations'][0]
    observation['flip_high']['available_at'] = 5
    _, events = replay(bars, geometry)
    alternations = [event for event in events
                    if event['event'] == 'trend_lifecycle_state' and event['state'] == '空多交替']
    assert [event['bar_index'] for event in alternations] == [5]
    assert not any(event.get('state') == '转多确认' for event in events)


def test_checkpoint_copy_accepts_serialized_lifecycle_in_chart_slot():
    bars, geometry = scenario()
    book, _ = replay(bars, geometry, through=5)
    state = ({}, [], {}, {}, set(), [], None, [], None, [], set(), (),
             None, None, {}, book.checkpoint(), {}, {}, {})
    restored = _copy_replay_state(state)
    assert restored[15].checkpoint() == book.checkpoint()
    assert restored[15] is not book


@pytest.mark.parametrize('level', [1, 2, 3])
def test_full_six_state_cycle_closes_buys_and_opens_sells(level):
    bars, geometry = scenario(level)
    following = [(14, 14.2, 13.5, 13.8), (13, 13, 10, 10.5),
                 (11, 12, 10.8, 11.5), (11.5, 11.8, 10.5, 11.2), (10.8, 11, 9, 9.5)]
    bars.extend(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'sz.000678', *prices, 200)
                for index, prices in enumerate(following, 6))
    high = dict(index=5, ordinal=0, kind='H', value=14.5, available_at=6,
                observations=[dict(title='头部疑虑', available_at=6)])
    low = dict(index=7, ordinal=0, kind='L', value=10, available_at=7,
               observations=[dict(title='翻多为空', available_at=7, confirmed_high=high)])
    counter = dict(index=8, ordinal=0, kind='H', value=12, available_at=9,
                   observations=[dict(title='多空交替', available_at=9, flip_low=low)])
    geometry['strokes'][0]['points'].extend([high, low, counter])
    _, events = replay(bars, geometry)
    states = [row['state'] for row in events if row['event'] == 'trend_lifecycle_state']
    assert states == ['空翻多', '空多交替', '转多确认', '多翻空', '多空交替', '转空确认']
    assert lifecycle_interval_days(events, len(bars), level, up=True) == (
        False, False, False, False, True, True, True, False, False, False, False)
    assert lifecycle_interval_days(events, len(bars), level, up=False)[9:] == (True, True)
