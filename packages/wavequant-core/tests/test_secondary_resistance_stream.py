from copy import deepcopy
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import secondary_resistance as module
from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
from wavequant.domain.strategies.secondary_resistance import (
    SecondaryResistanceStream,
    secondary_resistance_history,
)


def _example():
    prices = [
        (9, 10, 8, 9),
        (9, 9.5, 8.8, 9.4),
        (9.6, 11, 9.5, 10.1),
        (10.2, 11.1, 10, 10.3),
        (10.4, 11.2, 10.3, 10.5),
        (10.6, 11.6, 10.5, 11.5),
    ]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', *row, 1000)
            for i, row in enumerate(prices)]
    history = {i: {2: [dict(index=0, kind='H', value=8)]} for i in range(len(bars))}
    events = [
        dict(bar_index=1, event='hierarchy_resistance_key', key=dict(index=0, value=10)),
        dict(bar_index=3, event='hierarchy_resistance_key', key=dict(index=2, value=11)),
    ]
    return bars, history, events


@pytest.mark.parametrize('include_resolved', [False, True])
def test_daily_resistance_uses_the_batch_reducer_once(monkeypatch, include_resolved):
    bars, history, events = _example()
    expected = [secondary_resistance_history(
        bars[:index + 1], history, key_events=events, include_resolved=include_resolved,
    ) for index in range(len(bars))]
    original = module._secondary_resistance_days
    starts = []

    def counted(*args, **kwargs):
        starts.append(True)
        yield from original(*args, **kwargs)

    monkeypatch.setattr(module, '_secondary_resistance_days', counted)
    stream = SecondaryResistanceStream(bars, include_resolved=include_resolved)
    try:
        for index in range(len(bars)):
            visible = [event for event in events if event['bar_index'] <= index]
            assert stream.advance(index, history, visible) == expected[index]
        assert starts == [True]
        assert stream.advance(len(bars) - 1, history, events) == expected[-1]
        assert starts == [True]
    finally:
        stream.close()


@pytest.mark.parametrize('revision', ['price', 'kind', 'removed_day', 'late_day'])
def test_public_history_changes_replay_consumed_dates(revision):
    bars, history, _ = _example()
    for levels in history.values():
        levels[2][0]['value'] = 10
    if revision == 'late_day':
        del history[1]
    stream = SecondaryResistanceStream(bars)
    try:
        before = deepcopy(stream.advance(4, history, _history_owner=object()))
        if revision == 'price':
            history[1][2][0]['value'] = 12
        elif revision == 'kind':
            history[1][2][0]['kind'] = 'L'
        elif revision == 'removed_day':
            del history[1]
        else:
            history[1] = {2: [dict(index=0, kind='H', value=10)]}
        revised = stream.advance(4, history, _history_owner=object())
        assert revised == secondary_resistance_history(bars[:5], history, include_resolved=True)
        assert revised != before
    finally:
        stream.close()


@pytest.mark.parametrize('revision', ['price', 'removed', 'late', 'date', 'clear'])
def test_key_event_changes_replay_consumed_dates_even_for_owned_history(revision):
    bars, history, all_events = _example()
    events = [] if revision == 'late' else [deepcopy(all_events[0])]
    owner = HierarchyHistoryStream(bars)
    owner.history.update(history)
    stream = SecondaryResistanceStream(bars)
    try:
        before = deepcopy(stream.advance(4, owner.history, events, _history_owner=owner))
        if revision == 'price':
            events[0]['key']['value'] = 12
        elif revision == 'removed':
            events.clear()
        elif revision == 'late':
            events.append(all_events[0])
        elif revision == 'date':
            events[0]['bar_index'] = 3
        else:
            events[0]['key'] = None
        revised = stream.advance(4, owner.history, events, _history_owner=owner)
        assert revised == secondary_resistance_history(
            bars[:5], history, key_events=events, include_resolved=True,
        )
        assert revised != before
    finally:
        stream.close()
        owner.close()


def test_only_hierarchy_owned_history_skips_past_observation_copies(monkeypatch):
    bars, history, events = _example()
    owner = HierarchyHistoryStream(bars)
    owner.history.update(history)
    original = SecondaryResistanceStream._row
    seen = []

    def observed(levels):
        seen.append(levels)
        return original(levels)

    monkeypatch.setattr(SecondaryResistanceStream, '_row', staticmethod(observed))
    stream = SecondaryResistanceStream(bars)
    try:
        for index in range(len(bars)):
            stream.advance(index, owner.history, events, _history_owner=owner)
        assert len(seen) == len(bars) - 1
        copied_history = deepcopy(owner.history)
        stream.advance(len(bars) - 1, copied_history, events, _history_owner=owner)
        assert len(seen) == 2 * (len(bars) - 1)
    finally:
        stream.close()
        owner.close()


def test_future_keys_are_not_read_before_the_following_trading_day(monkeypatch):
    bars, history, events = _example()
    original = module._secondary_resistance_days
    starts = []

    def counted(*args, **kwargs):
        starts.append(True)
        yield from original(*args, **kwargs)

    monkeypatch.setattr(module, '_secondary_resistance_days', counted)
    stream = SecondaryResistanceStream(bars)
    try:
        assert stream.advance(1, history, events) == {}
        assert stream.advance(1, history, [dict(events[0], key=None)]) == {}
        assert starts == [True]
        assert stream.advance(2, history, events)[2]['secondary_high'] == 10
        assert starts == [True]
    finally:
        stream.close()


def test_resistance_backtracking_replays_and_closed_stream_rejects_advance():
    bars, history, events = _example()
    stream = SecondaryResistanceStream(bars)
    assert stream.advance(5, history, events)[5]['secondary_resistance_resolved']
    assert stream.advance(3, history, events) == secondary_resistance_history(
        bars[:4], history, key_events=events, include_resolved=True,
    )
    assert stream.advance(5, history, events)[5]['secondary_resistance_resolved']
    stream.close()
    stream.close()
    with pytest.raises(ValueError, match='closed'):
        stream.advance(5, history, events)


def test_empty_resistance_history_and_batch_results_are_independent():
    stream = SecondaryResistanceStream([])
    try:
        assert stream.advance(-1, {}) == {}
    finally:
        stream.close()
    bars, history, events = _example()
    result = secondary_resistance_history(bars, history, key_events=events)
    result[2]['secondary_high'] = -1
    assert secondary_resistance_history(bars, history, key_events=events)[2]['secondary_high'] == 10
