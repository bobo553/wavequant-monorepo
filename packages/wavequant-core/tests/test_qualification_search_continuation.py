"""资格终止后保留翻多背景，新的因果确认才允许再次开窗。"""

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.bull_alternation_confirmation import confirm_bull_alternation
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle, _Pivot
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingHistory
from wavequant.domain.strategies.market_calculations import MarketCalculations
from wavequant.domain.strategies.trading_intervals import TradingInterval, confirmed_trading_intervals, interval_days
from tests.test_source_bear_exit import bearish


def continuation_case(level: int) -> tuple[list[Bar], dict[str, object]]:
    prices = [(11, 12, 10, 11), (10, 11, 9, 10), (12, 13, 11, 12),
              (12, 12.5, 11, 12), (12, 12.5, 11.5, 12), (12, 12.5, 11.5, 12),
              (12, 12.5, 11.2, 12), (12, 12.5, 11.5, 12), (12, 12.5, 11.5, 12),
              (12, 12.5, 11.5, 12), (12, 12.5, 11.3, 12), (12, 12.5, 11.5, 12)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', *values, 100)
            for index, values in enumerate(prices)]

    def point(index: int, kind: str, price: float, known: int, **extra: object) -> dict[str, object]:
        return dict(index=index, ordinal=0, kind=kind, value=price, available_at=known,
                    time=bars[index].timestamp.date().isoformat(), state='confirmed', **extra)

    broken, origin = point(0, 'H', 12, 0), point(1, 'L', 9, 1)
    high = point(2, 'H', 13, 2, observations=[dict(title='翻空为多', available_at=2,
                                                confirmed_low=dict(origin), key=dict(broken))])
    low = point(3, 'L', 11, 4, observations=[dict(title='空多交替', available_at=4, flip_high=dict(high))])
    # 后续低点由当日正式几何提供；没有第二条翻多标签。
    later = point(6, 'L', 11.2, 8)
    third = point(10, 'L', 11.3, 11)
    return bars, dict(trend_level=level, strokes=[dict(id='upper', source_path='lower',
        points=[broken, origin, high, low, later, third])], _path_market_end_indices={'lower': None})


def collect(level: int, *, restore: bool = False, break_origin: bool = False) -> tuple[list[Mapping[str, object]], tuple[TradingInterval, ...]]:
    bars, row = continuation_case(level)
    if break_origin:
        bars[7] = replace(bars[7], low=8.9)
    with ChartDrawingHistory(start=0, count=len(bars), closed=()) as frames:
        for now in range(len(bars)):
            frames.append(now, 0, (), None)
        work = MarketCalculations(bars, frames, [], draw=lambda: None, trend=lambda level: None)
        events: list[Mapping[str, object]] = []
        for now in range(len(bars)):
            source = [bearish(now=now, level=level - 1)] if now in (5, 9) else []
            if restore and now == 6:
                work._lifecycles[level] = TrendLifecycle.from_checkpoint(work._lifecycles[level].checkpoint())
            events.extend(work._advance_lifecycle(level, row, now, source_events=source))
        return events, work._eligibility[level].intervals


@pytest.mark.parametrize('level', (1, 2, 3))
def test_closed_window_resumes_shared_search_and_reopens_repeatedly(level: int) -> None:
    events, windows = collect(level)
    assert [(window.start_index, window.end_index) for window in windows] == [(4, 5), (8, 9), (11, None)]
    assert [window.start_low_index for window in windows] == [3, 6, 10]
    assert all(window.start_flip_index == 2 for window in windows)
    assert confirmed_trading_intervals(events, 12, level) == windows
    assert interval_days(windows, 12) == (False, False, False, False, True, False,
                                         False, False, True, False, False, True)
    assert collect(level, restore=True) == (events, windows)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_origin_break_ends_research_without_reviving_old_flip(level: int) -> None:
    events, windows = collect(level, break_origin=True)
    assert [(window.start_index, window.end_index) for window in windows] == [(4, 5)]
    assert not any(event.get('buy_eligible') is True and event['bar_index'] > 5 for event in events)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_consumed_structural_proof_cannot_reopen_but_new_causal_low_can(level: int) -> None:
    bars, _ = continuation_case(level)
    broken, origin, high, old = (_Pivot(0, 0, 'H', 12, 0), _Pivot(1, 0, 'L', 9, 1),
                                _Pivot(2, 0, 'H', 13, 2), _Pivot(3, 0, 'L', 11, 4))
    common = dict(origin=origin, high=high, broken=broken, polyline=(), level=level, path='upper')
    assert confirm_bull_alternation(bars, 6, points=(broken, origin, high, old),
                                    after_index=5, **common) is None
    new = _Pivot(6, 0, 'L', 11.2, 8)
    proof = confirm_bull_alternation(bars, 8, points=(broken, origin, high, old, new),
                                     after_index=5, **common)
    assert proof is not None and proof.low == new
    assert confirm_bull_alternation(bars, 8, points=(broken, origin, high, old, new),
                                    after_index=8, **common) is None
    with pytest.raises(ValueError, match='causal'):
        confirm_bull_alternation(bars, 7, points=(broken, origin, high, old, new), after_index=5, **common)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_equal_origin_touch_is_defended_and_closed_floor_blocks_old_proof(level: int) -> None:
    bars, _ = continuation_case(level)
    bars[7] = replace(bars[7], low=9)
    broken, origin, high = _Pivot(0, 0, 'H', 12, 0), _Pivot(1, 0, 'L', 9, 1), _Pivot(2, 0, 'H', 13, 2)
    # 起点相等不会终止背景，但新交替低点仍须独立守住，不能使用与行情不符的旧低点。
    assert confirm_bull_alternation(bars, 8, origin=origin, high=high, broken=broken,
        points=(broken, origin, high), polyline=(), level=level, path='upper', after_index=5) is None
    # 使用真实触及起点的低点时，不满足严格高于原低点的交替门禁。
    equal_low = _Pivot(7, 0, 'L', 9, 8)
    assert confirm_bull_alternation(bars, 8, origin=origin, high=high, broken=broken,
        points=(broken, origin, high, equal_low), polyline=(), level=level, path='upper', after_index=5) is None


def test_old_lifecycle_checkpoint_cannot_silently_lose_qualification_search_clock() -> None:
    with pytest.raises(ValueError, match='incompatible lifecycle checkpoint'):
        TrendLifecycle.from_checkpoint(('six_state_daily_source_market_key_v31', -1, ()))
