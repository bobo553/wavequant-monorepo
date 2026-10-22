"""显式结构事实覆盖三日窗口与日内时钟，不替代完整趋势回归。"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.one_p_breakout_entry import (
    ONE_P_BREAKOUT_NO_ENTRY, OnePBreakoutStream, one_p_breakout_entry_rejection,
)


def market() -> list[Bar]:
    dates = ('2022-06-24', '2022-06-27', '2022-06-28', '2022-06-29',
             '2022-06-30', '2022-07-01', '2022-07-04')
    highs = (7, 8, 9, 11, 10, 10, 12)
    closes = (6.5, 8, 8.5, 9, 9, 9, 11)
    return [Bar(datetime.fromisoformat(day), 'TEST', 6, high, 5, close, 1000)
            for day, high, close in zip(dates, highs, closes, strict=True)]


def structure(*, known: int = 1, target: float = 8, identity: str = 'n-1') -> dict[str, object]:
    return dict(event='n_structure_completed', direction='up', bar_index=1, attack=1,
        known_at=known, n_id=identity, source_path='trend-path', one_p=target,
        target_eligible=False, structural_target=True)


def levels(level: int = 1, *, path: str = 'trend-path', key: float = 10,
           known: int = 0, direction: str = 'up') -> tuple[dict[str, object], ...]:
    reference = dict(index=0, ordinal=0, kind='H', value=key, available_at=0)
    point = dict(index=known, available_at=known,
        lifecycle_context=dict(direction=direction, available_at=known, key=reference))
    return (dict(trend_level=level, strokes=[dict(source_path=path, points=[point])]),)


def observe(bars: Sequence[Bar], events: Sequence[Mapping[str, object]], *, level: int = 1,
            frames: Mapping[int, Sequence[Mapping[str, object]]] | None = None) -> OnePBreakoutStream:
    stream = OnePBreakoutStream(bars)
    for index in range(len(bars)):
        stream.advance(index, (frames or {}).get(index, levels(level)), events)
    return stream


def rejected(stream: OnePBreakoutStream, index: int, *, high: float | None = None) -> dict[str, object] | None:
    return one_p_breakout_entry_rejection(stream.events, index,
        observed_high=stream.bars[index].high if high is None else high)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_mature_n_before_first_key_break_blocks_exactly_three_trading_days(level: int) -> None:
    stream = observe(market(), [structure()], level=level)
    assert rejected(stream, 2) is None
    for index in (3, 4, 5):
        proof = rejected(stream, index)
        assert proof is not None and proof['reason'] == ONE_P_BREAKOUT_NO_ENTRY
        assert proof['trend_level'] == level and proof['observation_day'] == index - 2
        assert proof['breakout_date'] == '2022-06-29' and proof['one_p_reached_date'] == '2022-06-27'
        assert proof['n_id'] == 'n-1' and proof['key_price'] == 10
        assert not {'event', 'bar_index', 'timestamp', 'symbol'} & proof.keys()
    assert rejected(stream, 6) is None
    assert all(row.get('event') != 'exit_signal' for row in stream.events)


@pytest.mark.parametrize('high, blocked', [(9.99, False), (10, False), (10.000001, True)])
def test_break_is_strict_and_minute_does_not_borrow_later_high(high: float, blocked: bool) -> None:
    stream = observe(market(), [structure()])
    assert (rejected(stream, 3, high=high) is not None) is blocked
    assert rejected(stream, 4, high=6) is not None


def test_weak_breakout_candle_starts_window_without_waiting_for_strong_release() -> None:
    bars = market()
    bars[3] = replace(bars[3], open=10, close=6)
    stream = observe(bars, [structure()])
    assert rejected(stream, 3) is not None
    assert rejected(stream, 4) is not None
    assert rejected(stream, 6) is None


@pytest.mark.parametrize('known, target', [(3, 8), (1, 11), (1, 12)])
def test_first_break_before_or_same_day_as_maturity_never_starts_later_window(known: int, target: float) -> None:
    bars = market()
    bars[4] = replace(bars[4], high=14, close=13)
    bars[5] = replace(bars[5], high=15, close=14)
    stream = observe(bars, [structure(known=known, target=target)])
    assert all(rejected(stream, index) is None for index in range(len(bars)))


def test_first_knowledge_uses_close_and_prior_touch_is_not_backdated() -> None:
    bars = market()
    bars[1] = replace(bars[1], high=9, close=7)
    bars[2] = replace(bars[2], high=7.9, close=7)
    stream = observe(bars, [structure(target=8)])
    assert rejected(stream, 3) is None
    bars[2] = replace(bars[2], high=8)
    control = observe(bars, [structure(target=8)])
    proof = rejected(control, 3)
    assert proof is not None and proof['one_p_reached_index'] == 2


def test_invalidation_before_break_removes_source_and_future_event_cannot_do_so_early() -> None:
    invalidated = dict(event='n_structure_invalidated', bar_index=2, known_at=2, n_id='n-1')
    stream = observe(market(), [structure(), invalidated])
    assert rejected(stream, 3) is None
    later = observe(market(), [structure(), dict(invalidated, bar_index=4, known_at=4)])
    assert rejected(later, 3) is not None and rejected(later, 4) is not None
    assert rejected(later, 5) is None


def test_formal_flip_preserves_started_window_but_ended_path_does_not() -> None:
    frames = {index: levels(direction='down', known=3) for index in range(3, 7)}
    stream = observe(market(), [structure()], frames=frames)
    assert rejected(stream, 4) is not None and rejected(stream, 5) is not None
    changed = {index: levels(path='next-epoch', known=3, direction='down') for index in range(3, 7)}
    replaced = observe(market(), [structure()], frames=changed)
    assert rejected(replaced, 3) is not None and rejected(replaced, 4) is None


def test_new_unbroken_key_replaces_old_context_without_borrowing_its_price() -> None:
    frames = {index: levels(key=12, known=2) for index in range(2, 7)}
    stream = observe(market(), [structure()], frames=frames)
    assert rejected(stream, 3) is None
    assert rejected(stream, 6) is None


def test_key_first_published_today_does_not_reject_earlier_minutes() -> None:
    frames = {index: () for index in range(3)}
    frames.update({index: levels(known=3) for index in range(3, 7)})
    stream = observe(market(), [structure()], frames=frames)
    assert rejected(stream, 3) is None
    assert rejected(stream, 4) is None
    assert rejected(stream, 6) is not None


@pytest.mark.parametrize('change', [
    {'one_p': float('nan')}, {'one_p': True}, {'one_p': 0},
    {'direction': 'down'}, {'event': 'n_completed'}, {'known_at': 99}, {'attack': 0},
    {'source_path': 'another-epoch'},
])
def test_invalid_future_transaction_or_previous_cycle_source_cannot_create_risk(change: dict[str, object]) -> None:
    stream = observe(market(), [dict(structure(), **change)])
    assert rejected(stream, 3) is None


def test_append_only_events_and_full_future_input_produce_same_current_observation() -> None:
    bars, completed = market(), structure()
    incremental = OnePBreakoutStream(bars)
    journal: list[Mapping[str, object]] = []
    for index in range(len(bars)):
        if index == 1:
            journal.append(completed)
        incremental.advance(index, levels(), journal)
    preloaded = observe(bars, [completed])
    assert incremental.events == preloaded.events
    prefix = observe(bars[:4], [completed])
    assert prefix.events == [event for event in preloaded.events if event['bar_index'] <= 3]


def test_newer_unreached_n_cannot_hide_older_mature_source() -> None:
    newer = dict(structure(identity='n-2', target=30), attack=2, bar_index=2, known_at=2)
    stream = observe(market(), [structure(), newer])
    proof = rejected(stream, 3)
    assert proof is not None and proof['n_id'] == 'n-1'


def test_current_watch_has_no_full_session_high_or_future_breakout_result() -> None:
    original = market()
    changed = list(original)
    changed[3] = replace(changed[3], high=9.5)
    left = observe(original[:4], [structure()])
    right = observe(changed[:4], [structure()])
    assert left.events == right.events
    assert rejected(left, 3, high=9.5) is None and rejected(right, 3, high=9.5) is None


def test_rejection_does_not_assume_audit_is_sorted() -> None:
    stream = observe(market(), [structure()])
    assert one_p_breakout_entry_rejection(list(reversed(stream.events)), 4, observed_high=6) is not None


def test_duplicate_or_skipped_date_is_rejected() -> None:
    stream = OnePBreakoutStream(market())
    with pytest.raises(ValueError, match='consecutive'):
        stream.advance(1, levels(), [])
    stream.advance(0, levels(), [])
    with pytest.raises(ValueError, match='consecutive'):
        stream.advance(0, levels(), [])


def test_formal_level_path_prefixes_keep_the_same_source_epoch() -> None:
    source = dict(structure(), source_path='secondary-reversal-lecture-0')
    frames = {index: levels(path='lecture-0') for index in range(7)}
    assert rejected(observe(market(), [source], frames=frames), 3) is not None
    source['source_path'] = 'secondary-reversal-lecture-9'
    assert rejected(observe(market(), [source], frames=frames), 3) is None


def test_intraday_channel_removes_buy_and_explains_shared_rejection() -> None:
    from wavequant.application.analytics.intraday_entry import _reject_two_t_entry
    from wavequant.domain.models.model import Signal

    bars = market()
    stream = observe(bars, [structure()])
    index = 4
    signal = Signal(bars[index].timestamp, 'TEST', index, 'LONG', 9, 5, 'candidate',
                    bars[index].timestamp, 0, None, 'bull')
    signals, audit = [signal], list(stream.events)
    assert _reject_two_t_entry(bars, index, (), signals, audit,
        observed_high=6, observed_close=6, at_close=False,
        decision_timestamp=bars[index].timestamp.isoformat())
    assert signals == []
    assert audit[-1]['candidate_channel'] == 'global_one_p_breakout_guard'
    assert audit[-1]['reason'] == ONE_P_BREAKOUT_NO_ENTRY
