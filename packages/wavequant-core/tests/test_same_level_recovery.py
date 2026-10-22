"""破位后不得由局部交替重开；三级同规则，真实案例与合成边界分开。"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.market_calculations import market_calculations
from wavequant.domain.strategies.recovery_entry_history import merge_recovery_entry_history
from wavequant.domain.strategies.same_level_recovery import RECOVERY_CLEARED, RECOVERY_REQUIRED
from wavequant.domain.strategies.trading_intervals import (
    TradingEligibility, confirmed_trading_intervals, interval_days,
)


def point(index: int, kind: str, value: float, known: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind, value=value, available_at=known, state='confirmed')


def sample(level: int) -> tuple[list[Bar], dict[str, object]]:
    # 四个报价锚取自用户，其余日期行情与结构确认时钟合成，不冒充实股趋势。
    values = [
        ('2020-12-21', 19.18, 19.94, 18.78, 19.83),
        ('2021-01-04', 21.75, 22.23, 21.45, 21.55),
        ('2021-01-26', 19.53, 19.53, 18.39, 18.39),
        ('2021-05-10', 16.0, 17.0, 15.0, 16.5),
        ('2021-07-09', 15.0, 16.0, 14.0, 15.5),
        ('2021-07-28', 16.0, 17.0, 15.0, 16.5),
        ('2021-08-30', 15.0, 16.0, 14.0, 15.5),
        ('2021-10-28', 16.0, 17.0, 15.0, 16.5),
        ('2021-12-01', 11.0, 12.0, 10.0, 11.0),
        ('2021-12-30', 16.28, 19.13, 15.35, 17.95),
        ('2022-01-21', 10.0, 11.0, 9.0, 10.0),
        ('2022-01-24', 12.0, 13.0, 11.0, 12.0),
        ('2022-01-25', 18.0, 19.13, 17.0, 18.0),
        ('2022-01-26', 19.0, 19.2, 18.0, 19.1),
        ('2022-01-27', 19.0, 20.0, 18.0, 19.5),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'TEST', op, high, low, close, 100)
            for day, op, high, low, close in values]
    points = [point(0, 'L', 18.78, 1), point(1, 'H', 22.23, 1),
              point(8, 'L', 10.0, 9), point(9, 'H', 19.13, 10), point(10, 'L', 9.0, 11)]
    return bars, dict(trend_level=level, strokes=[dict(id='p', points=points)])


def alternation(now: int, level: int, *, flip: int | None = None, path: str = 'p') -> dict[str, object]:
    attack = max(0, now - 1) if flip is None else flip
    return dict(event='trend_lifecycle_state', state='空多交替', flip_confirmed=True,
        trend_level=level, source_path=path, bar_index=now, available_index=now,
        flip_index=attack, flip_known_index=attack, counter_index=now, alternation_index=now)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('owned', (False, True))
@pytest.mark.parametrize('opened', (False, True))
def test_break_freezes_key_across_local_alternations_and_new_decline_can_replace_it(
    level: int, owned: bool, opened: bool,
) -> None:
    bars, row = sample(level)
    before = deepcopy(row)
    gate = TradingEligibility(level)
    events: list[Mapping[str, object]] = []
    for now in range(len(bars)):
        today = gate.advance_recovery(row, bars, now, {}, owned=owned)
        events.extend(today)
        if now == 1 and opened:
            opening = alternation(now, level)
            events.append(opening)
            assert gate.observe(opening) is True
        if now in (3, 5, 7, 12):
            local = alternation(now, level)
            events.append(local)
            assert gate.observe(local) is None
        if now == 13:
            assert today[0]['event'] == RECOVERY_CLEARED
            assert not gate.active
            stale = alternation(now, level, flip=7)
            events.append(stale)
            assert gate.observe(stale) is None
        if now == 14:
            fresh = alternation(now, level, flip=13)
            events.append(fresh)
            assert gate.observe(fresh) is True
        if 2 <= now <= 13:
            assert not gate.active
    required = [event for event in events if event['event'] == RECOVERY_REQUIRED]
    assert [event['bar_index'] for event in required] == [2, 11]
    assert [cast(Mapping[str, object], event['recovery_high'])['index'] for event in required] == [1, 9]
    assert gate.intervals[-1].start_index == 14
    assert confirmed_trading_intervals(events, len(bars), level) == gate.intervals
    assert confirmed_trading_intervals(events, 13, level) == gate.intervals[:-1]
    assert row == before


@pytest.mark.parametrize('change', ('future', 'developing', 'unfinished', 'higher_low', 'source_revision'))
def test_new_high_without_confirmed_decline_does_not_lower_frozen_threshold(change: str) -> None:
    bars, row = sample(2)
    strokes = cast(list[dict[str, object]], row['strokes'])
    points = cast(list[dict[str, object]], strokes[0]['points'])
    if change == 'future':
        points[-1]['available_at'] = 14
    elif change == 'developing':
        points[-1]['state'] = 'developing'
    elif change == 'unfinished':
        points[-2].update(wave_completion_policy='whole_wave_v117', wave_identity_state='owned')
    elif change == 'higher_low':
        points[-1]['value'] = 10.5
    gate = TradingEligibility(2)
    events: list[Mapping[str, object]] = []
    for now in range(14):
        current = dict(row, strokes=[dict(id='p', points=[])]) if change == 'source_revision' and now >= 3 else row
        events.extend(gate.advance_recovery(current, bars, now, {}))
        if now >= 2:
            assert gate.observe(alternation(now, 2)) is None
    assert not any(event['event'] == RECOVERY_CLEARED for event in events)
    assert not gate.active


@pytest.mark.parametrize('level', (1, 2, 3))
def test_touch_and_high_already_broken_do_not_create_a_frozen_barrier(level: int) -> None:
    bars, row = sample(level)
    for changed in (replace(bars[2], low=18.78), replace(bars[2], high=22.24)):
        prefix = [*bars[:2], changed]
        gate = TradingEligibility(level)
        events = [event for now in range(3) for event in gate.advance_recovery(row, prefix, now, {})]
        assert not events
        assert gate.observe(alternation(2, level)) is True


def test_other_path_and_level_do_not_inherit_barrier() -> None:
    bars, row = sample(2)
    gate = TradingEligibility(2)
    for now in range(3):
        gate.advance_recovery(row, bars, now, {})
    assert gate.observe(alternation(3, 2)) is None
    assert gate.observe(alternation(3, 2, path='other')) is True
    other = TradingEligibility(1)
    assert other.advance_recovery(row, bars, 3, {}) == []
    assert other.observe(alternation(3, 1)) is True


def test_same_day_break_blocks_new_alternation_before_any_window_exists() -> None:
    bars, row = sample(2)
    gate = TradingEligibility(2)
    for now in range(3):
        gate.advance_recovery(row, bars, now, {})
    assert gate.observe(alternation(2, 2)) is None
    assert gate.intervals == ()


def test_lexin_full_origin_history_has_no_reported_2021_qualification_windows() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/lexin_2026_squeeze.json').read_text(encoding='utf-8'))
    bars = tuple(Bar(datetime.fromisoformat(day), raw['symbol'], *values)
                 for day, *values in raw['bars'] if day <= '2022-01-21')
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    # 复用完整2018起同源复权行情；不得把四个单日报价拼成真实结构。
    assert bars[dates['2020-12-21']].low == pytest.approx(18.78, abs=.005)
    assert bars[dates['2021-01-04']].high == pytest.approx(22.23, abs=.005)
    assert bars[dates['2021-01-26']].low == pytest.approx(18.39, abs=.005)
    assert bars[dates['2021-12-30']].high == pytest.approx(19.13, abs=.005)
    with market_calculations(bars) as calculations:
        calculations.calculate_trend_line(2)
        windows = calculations.trading_intervals(2)
        events = calculations.history_events(2)
        assert confirmed_trading_intervals(events, len(bars), 2) == windows
        days = interval_days(windows, len(bars))
        for start, end in (('2021-05-10', '2021-07-09'), ('2021-07-28', '2021-08-30'),
                           ('2021-10-28', '2022-01-21')):
            assert not any(days[dates[start]:dates[end] + 1]), (start, end)
            assert not any(window.start_low_index is not None
                           and dates[start] <= window.start_low_index <= dates[end] for window in windows)
        assert not any(days[dates['2021-01-26']:])
        broken = next(event for event in events if event['event'] == RECOVERY_REQUIRED
                      and cast(Mapping[str, object], event['recovery_high'])['index'] == dates['2021-01-04'])
        # Jan4 H首次于Feb19可知；Jan26已有更早破位门槛，不能倒填新高点证据。
        assert broken['bar_index'] == dates['2021-02-19']
        assert cast(Mapping[str, object], broken['recovery_high'])['index'] == dates['2021-01-04']
        assert cast(Mapping[str, object], broken['broken_low'])['index'] == dates['2020-12-21']


@pytest.mark.parametrize('level', (1, 2, 3))
def test_strict_reclaim_of_original_high_allows_only_a_fresh_confirmed_cycle(level: int) -> None:
    bars, row = sample(level)
    bars[3] = replace(bars[3], high=22.24)
    gate = TradingEligibility(level)
    events: list[Mapping[str, object]] = []
    for now in range(4):
        events.extend(gate.advance_recovery(row, bars, now, {}))
    assert events[-1]['event'] == RECOVERY_CLEARED
    assert not gate.active
    stale = alternation(3, level, flip=1)
    assert gate.observe(stale) is None
    fresh = alternation(4, level, flip=3)
    gate.advance_recovery(row, bars, 4, {})
    assert gate.observe(fresh) is True
    assert gate.intervals[0].start_index == 4
    assert confirmed_trading_intervals([*events, stale, fresh], 5, level) == gate.intervals


@pytest.mark.parametrize('change', ('touch', 'future', 'wrong_key', 'wrong_path', 'wrong_level'))
def test_malformed_recovery_event_cannot_unlock_a_blocked_path(change: str) -> None:
    bars, row = sample(2)
    gate = TradingEligibility(2)
    events = [event for now in range(3) for event in gate.advance_recovery(row, bars, now, {})]
    event = dict(events[0], event=RECOVERY_CLEARED, bar_index=3, available_index=3, market_high=22.24)
    if change == 'touch':
        event['market_high'] = 22.23
    elif change == 'future':
        event['available_index'] = 4
    elif change == 'wrong_key':
        event['recovery_high'] = point(1, 'H', 20.0, 1)
    elif change == 'wrong_path':
        event['source_path'] = 'other'
    else:
        event['trend_level'] = 1
    assert gate.observe(event) is None
    assert gate.observe(alternation(3, 2)) is None
    assert not gate.active



def whole_flip_sample(level: int) -> tuple[list[Bar], dict[str, object]]:
    # 仅四个命名日沿用用户报价；中间行情、结构及确认时钟显式缩减。
    values = [
        ('2020-08-14', 8.97, 8.97, 7.34, 7.34),
        ('2021-02-08', 3.85, 3.89, 3.81, 3.85),
        ('2022-10-10', 6.0, 6.33, 5.8, 6.1),
        ('2022-10-31', 4.5, 4.6, 4.44, 4.5),
        ('2023-08-01', 10.58, 11.02, 10.04, 10.10),
        ('2024-02-06', 4.5, 4.6, 4.3, 4.4),
        ('2024-02-08', 4.32, 4.75, 4.22, 4.73),
        ('2024-03-15', 5.9, 6.44, 5.9, 6.44),
    ]
    bars = [Bar(datetime.fromisoformat(day), 'TEST', op, high, low, close, 100)
            for day, op, high, low, close in values]
    key, origin = point(0, 'H', 8.97, 0), point(1, 'L', 3.81, 1)
    high = point(4, 'H', 11.02, 5)
    high['observations'] = [dict(title='翻空为多', available_at=5,
                                  key=dict(key), confirmed_low=dict(origin))]
    points = [key, origin, point(2, 'H', 6.33, 2), point(3, 'L', 4.44, 3),
              high, point(6, 'L', 4.22, 7)]
    return bars, dict(trend_level=level, strokes=[dict(id='p', points=points)])


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('owned', (False, True))
@pytest.mark.parametrize('low', (4.3, 3.81, 3.80))
def test_formal_whole_flip_defends_its_frozen_origin_instead_of_internal_low(
    level: int, owned: bool, low: float,
) -> None:
    bars, row = whole_flip_sample(level)
    bars[5] = replace(bars[5], low=low)
    gate = TradingEligibility(level)
    events: list[Mapping[str, object]] = []
    before = deepcopy(row)
    dates: dict[str, int] = {}
    for now in range(len(bars)):
        events.extend(gate.advance_recovery(row, bars, now, dates, owned=owned))
    confirmation = dict(alternation(7, level, flip=4), flip_known_index=5,
                        counter_index=6, counter_price=4.22)
    changed = gate.observe(confirmation)
    if low < 3.81:
        required = next(event for event in events if event['event'] == RECOVERY_REQUIRED)
        assert required['bar_index'] == 5
        assert cast(Mapping[str, object], required['broken_low'])['index'] == 1
        assert changed is None and not gate.active
    else:
        assert not events
        assert changed is True
        assert gate.intervals[0].start_index == 7
        assert gate.intervals[0].start_low_index == 6
    assert confirmed_trading_intervals([*events, confirmation], len(bars), level) == gate.intervals
    assert row == before


@pytest.mark.parametrize('invalid', ('absent', 'future', 'foreign_origin', 'changed_origin', 'missing_key', 'unbroken_key'))
def test_only_current_path_causal_strict_flip_proof_can_replace_adjacent_low(invalid: str) -> None:
    bars, row = whole_flip_sample(2)
    points = cast(list[dict[str, object]], cast(list[dict[str, object]], row['strokes'])[0]['points'])
    high = points[4]
    observation = cast(list[dict[str, object]], high['observations'])[0]
    if invalid == 'absent':
        high.pop('observations')
    elif invalid == 'future':
        observation['available_at'] = 7
    elif invalid == 'foreign_origin':
        points.pop(1)
    elif invalid == 'changed_origin':
        observation['confirmed_low'] = dict(points[1], value=3.5)
    elif invalid == 'missing_key':
        observation.pop('key')
    else:
        points[0]['value'] = 11.02
        observation['key'] = dict(points[0])
    gate = TradingEligibility(2)
    events = [event for now in range(6) for event in gate.advance_recovery(row, bars, now, {})]
    required = next(event for event in events if event['event'] == RECOVERY_REQUIRED)
    assert required['bar_index'] == 5
    assert cast(Mapping[str, object], required['broken_low'])['index'] == 3
    assert gate.observe(alternation(5, 2, flip=4)) is None
    assert not gate.active


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('owned', (False, True))
def test_guofang_published_whole_flip_opens_february_counter_after_real_confirmation(
    level: int, owned: bool,
) -> None:
    # 已保存的本级结构与完整日线，验证六态/恢复门槛/资格/上下文衔接；
    # 这是冻结结构回放，三级完整几何重建由实股全链路用例另行覆盖。
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2024_impulse_defense.json').read_text('utf-8'))
    bars = [Bar(datetime.fromisoformat(day), raw['symbol'], *values)
            for day, *values in raw['bars'] if day <= '2024-03-15']
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    row = dict(trend_level=level, strokes=[dict(id='p', points=raw['points'])])
    engine, gate = TrendLifecycle(), TradingEligibility(level)
    events: list[Mapping[str, object]] = []
    days = []
    for now in range(len(bars)):
        events.extend(gate.advance_recovery(row, bars, now, dates, owned=owned))
        polyline = [dict(point, available_at=dates[point['available_at']]) for point in raw['polyline']
                    if point['available_at'] in dates and dates[point['available_at']] <= now]
        today = engine.advance([row], bars, now, dates, polyline=polyline)
        for event in today:
            gate.observe(event)
        events.extend(today)
        days.append(gate.active)
    low, confirmed = dates['2024-02-08'], dates['2024-03-15']
    flip = dates['2023-08-01']
    window = next(window for window in gate.intervals if window.start_low_index == low)
    assert window.start_flip_index == flip
    assert window.start_index == confirmed
    assert not any(days[low:confirmed]) and days[confirmed]
    assert not any(event['event'] == RECOVERY_REQUIRED and event['bar_index'] == dates['2024-02-06']
                   for event in events)
    assert confirmed_trading_intervals(events, len(bars), level) == gate.intervals
    assert confirmed_trading_intervals(events, confirmed, level) == ()
    contexts = merge_recovery_entry_history(bars, {}, events)
    context = next(context for context in contexts[confirmed] if context.trend_level == level)
    assert context.origin_index == dates['2021-02-08']
    assert context.flip_high_index == flip
    assert context.alternation_low_index == low
    assert contexts[confirmed - 1] == ()
