"""公共计算函数共享依赖及六态历史，并隔离调用方修改与作用域寿命。"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.bull_alternation_confirmation import AlternationPolicy
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import market_calculations as calculations_module
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingHistory
from wavequant.domain.strategies.market_calculations import (
    MarketCalculations, market_calculations, normalize_calculation_level,
)
from wavequant.domain.strategies.trading_intervals import confirmed_trading_intervals, interval_days


def _row(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def test_market_lifecycle_closes_buy_permission_on_known_reversal_low_break():
    with _calculation_case() as (calculations, _calls, bars):
        calculations.calculate_trend_line(2)
        windows = calculations.trading_intervals(2)
        events = calculations.history_events(2)
        assert len(windows) == 1
        assert (windows[0].start_low_index, windows[0].start_index, windows[0].end_index) == (3, 4, 6)
        assert windows[0].end_reason == 'reversal_low_broken'
        assert any(event['event'] == 'trading_reversal_low_broken' and event['buy_eligible'] is False
                   for event in events)
        assert confirmed_trading_intervals(events, len(bars), 2) == windows
        assert interval_days(windows, len(bars)) == (False, False, False, False, True, True, False)


def test_lifecycle_event_suffix_preserves_same_day_events_without_visiting_old_history() -> None:
    class OrderedEvents(list[dict[str, object]]):
        def __iter__(self) -> Iterator[dict[str, object]]:
            raise AssertionError('a suffix query must seek before iterating the dated event ledger')

    with _calculation_case() as (calculations, _calls, bars):
        calculations.calculate_polyline()
        rows = [dict(bar_index=index, event=f'event-{index}-{sequence}')
                for index in range(len(bars)) for sequence in range(20)]
        calculations._pending_events[2] = OrderedEvents(rows)
        calculations.reading_live_day = True
        assert calculations._lifecycle_events_since(2, len(bars) - 1) == tuple(rows[-20:])
        assert calculations._lifecycle_events_since(2, len(bars)) == ()
        calculations.reading_live_day = False
        calculations._lifecycle_events[2] = tuple(rows)
        assert calculations._lifecycle_events_since(2, 0) == tuple(rows)


def _case(*, up: bool = True) -> tuple[list[Bar], list[dict[str, object]]]:
    values = [(10, 11, 9, 10), (10, 11, 9.5, 10.5), (11, 13, 10, 12),
              (12, 12.5, 11, 12), (12, 13, 11.5, 12.5), (13.2, 14.5, 12, 14),
              (13, 13.5, 10, 11)]
    start = datetime(2026, 1, 1)
    bars = [Bar(start + timedelta(days=index), 'CALCULATIONS', *price, 100 + index * 10)
            for index, price in enumerate(values)]

    def point(index: int, kind: str, value: float, known: int, **extra: object) -> dict[str, object]:
        return dict(index=index, ordinal=0, kind=kind, value=value, available_at=known,
                    time=(start + timedelta(days=index)).date().isoformat(), state='confirmed', **extra)

    origin = point(0, 'L', 9, 1, observations=[dict(title='底部疑虑', available_at=1)])
    high = point(2, 'H', 13, 2, observations=[dict(title='翻空为多', available_at=2, confirmed_low=dict(origin))])
    low = point(3, 'L', 11, 4, observations=[dict(title='空多交替', available_at=4, flip_high=dict(high))])
    points = [origin, high, low]
    if not up:
        bars = [replace(bar, open=30-bar.open, high=30-bar.low, low=30-bar.high, close=30-bar.close)
                for bar in bars]
        names = {'底部疑虑': '头部疑虑', '翻空为多': '翻多为空', '空多交替': '多空交替'}

        def mirror(value: object) -> object:
            if isinstance(value, list):
                return [mirror(item) for item in value]
            if not isinstance(value, dict):
                return value
            result = {({'confirmed_low': 'confirmed_high', 'flip_high': 'flip_low'}.get(key, key)): mirror(item)
                      for key, item in value.items()}
            if 'value' in result:
                result['value'] = 30-cast(float, result['value'])
                result['kind'] = 'H' if result['kind'] == 'L' else 'L'
            if 'title' in result:
                result['title'] = names[cast(str, result['title'])]
            return result

        points = cast(list[dict[str, object]], mirror(points))
    return bars, points


@contextmanager
def _calculation_case(*, up: bool = True) -> Iterator[tuple[MarketCalculations, list[int], list[Bar]]]:
    bars, points = _case(up=up)
    calls: list[int] = []
    with ExitStack() as resources:
        frames = resources.enter_context(ChartDrawingHistory(start=0, count=len(bars), closed=()))
        levels: list[TrendChangeRows[dict[str, object]]] = []

        def draw() -> None:
            calls.append(0)
            for index in range(len(bars)):
                frames.append(index, 0, (), None)

        def trend(level: int) -> None:
            calls.append(level)
            history = resources.enter_context(TrendChangeRows(_row, row_count=len(bars)))
            history.write_change(0, dict(trend_level=level,
                strokes=[dict(id='path', source_path='source', points=points)],
                _path_market_end_indices={'source': None}))
            levels.append(history)

        yield MarketCalculations(bars, frames, levels, draw=draw, trend=trend), calls, bars


@pytest.mark.parametrize(('level', 'expected'), [('折线', 0), ('polyline', 0), (0, 0), (1, 1), (2, 2), (3, 3)])
def test_level_aliases_normalize_without_changing_business_level(level: object, expected: int) -> None:
    assert normalize_calculation_level(level) == expected


@pytest.mark.parametrize('level', [True, False, None, 1.0, -1, 4, '1', '一级', '', [], {}])
def test_invalid_level_is_rejected(level: object) -> None:
    with pytest.raises(ValueError, match='calculation level'):
        normalize_calculation_level(level)


def test_geometry_functions_lazily_build_only_missing_dependencies() -> None:
    with _calculation_case() as (functions, calls, _bars):
        assert not functions.levels
        assert calls == []
        third = functions.calculate_trend_line(level=3)
        assert calls == [0, 1, 2, 3]
        assert third is functions.levels[2]
        assert functions.calculate_trend_line(1) is functions.levels[0]
        assert functions.calculate_polyline() is functions.frames
        assert functions.calculate_polyline() is functions.calculate_polyline()
        assert calls == [0, 1, 2, 3]
        snapshot = functions.levels
        assert isinstance(snapshot, tuple)


def test_independent_polyline_function_is_reused_by_trend_dependencies() -> None:
    with _calculation_case() as (functions, calls, _bars):
        frames = functions.calculate_polyline()
        assert functions.calculate_polyline() is frames
        assert not functions.levels
        assert calls == [0]
        second = functions.calculate_trend_line(level=2)
        assert second is functions.levels[1]
        assert functions.calculate_polyline() is frames
        assert calls == [0, 1, 2]


@pytest.mark.parametrize('level', [True, False, None, 1.0, -1, 0, 4, '折线', 'polyline', '1'])
def test_trend_line_rejects_polyline_aliases_and_invalid_levels_without_drawing(level: object) -> None:
    with _calculation_case() as (functions, calls, _bars):
        with pytest.raises(ValueError):
            functions.calculate_trend_line(cast(int, level))
        assert calls == []
        assert not functions.levels


@pytest.mark.parametrize('up', [True, False])
def test_trend_calculation_completes_all_six_states_and_reads_never_replay(
    monkeypatch: pytest.MonkeyPatch, up: bool,
) -> None:
    dates_seen: list[tuple[object, int]] = []

    class CountedLifecycle(TrendLifecycle):
        def advance(
            self, levels: Sequence[Mapping[str, object]], bars: Sequence[Bar], now: int,
            dates: Mapping[str, int], *, polyline: Sequence[Mapping[str, object]] = (),
            polyline_path: str | None = None, source_events: Sequence[Mapping[str, object]] = (),
            confirmation_policy: AlternationPolicy = AlternationPolicy(), _owned_structure: bool = False,
            _market_index: _MarketRangeIndex | None = None,
        ) -> list[dict[str, object]]:
            dates_seen.append((levels[0]['trend_level'], now))
            return super().advance(levels, bars, now, dates, polyline=polyline, polyline_path=polyline_path,
                source_events=source_events, confirmation_policy=confirmation_policy,
                _owned_structure=_owned_structure, _market_index=_market_index)

    monkeypatch.setattr(calculations_module, 'TrendLifecycle', CountedLifecycle)
    with _calculation_case(up=up) as (functions, calls, bars):
        with pytest.raises(RuntimeError, match='calculate'):
            functions.history_events(2)
        assert calls == []
        functions.calculate_trend_line(2)
        assert dates_seen == [(level, now) for level in (0, 1, 2) for now in range(len(bars))]
        expected_states = ['空翻多', '空多交替', '转多确认'] if up else ['多翻空', '多空交替', '转空确认']
        all_events = functions.history_events(2)
        states = [(event['bar_index'], event['state']) for event in all_events
                  if event['event'] == 'trend_lifecycle_state']
        assert states == list(zip((2, 4, 5), expected_states))
        assert any(event.get('reason') == 'defense_broken' for event in all_events)
        for _ in range(6):
            assert functions.history_events(2) == all_events
            functions.calculate_trend_line(2)
        assert dates_seen == [(level, now) for level in (0, 1, 2) for now in range(len(bars))]
        assert calls == [0, 1, 2]


def test_returned_events_cannot_mutate_shared_history_or_other_functions() -> None:
    with _calculation_case() as (functions, _calls, _bars):
        functions.calculate_trend_line(2)
        expected = functions.history_events(2)
        returned = functions.history_events(2)
        assert returned
        returned[0]['state'] = 'caller mutation'
        returned[0]['origin_price'] = -1
        copied_history = functions.history_events(2)
        copied_history[0].clear()
        assert functions.history_events(2) == expected
        assert next(event for event in functions.history_events(2)
                    if event['event'] == 'trend_lifecycle_state')['state'] == '空翻多'


def test_each_level_has_its_own_event_identity_and_cache() -> None:
    with _calculation_case() as (functions, calls, _bars):
        functions.calculate_trend_line(3)
        second = functions.history_events(2)
        third = functions.history_events(3)
        assert second and third
        assert {event['trend_level'] for event in second} == {2}
        assert {event['trend_level'] for event in third} == {3}
        assert functions.history_events(2) == second
        assert calls == [0, 1, 2, 3]


def test_trading_schedule_is_owned_by_trend_confirmation_and_reads_never_replay(monkeypatch) -> None:
    with _calculation_case() as (functions, _calls, _bars):
        functions.calculate_trend_line(3)
        intervals = functions.trading_intervals(2)
        assert len(intervals) == 1
        assert intervals[0].start_index == 4
        assert intervals[0].trend_level == 2
        assert [event['bar_index'] for event in functions.history_events(2)
                if event.get('buy_eligible') is True] == [4]

        def forbidden(*args, **kwargs):
            raise AssertionError('reading a schedule must never replay trend states')

        monkeypatch.setattr(TrendLifecycle, 'advance', forbidden)
        assert functions.trading_intervals(2) == intervals
        assert functions.trading_intervals(3)[0].trend_level == 3


def test_closed_scope_cannot_serve_cached_events_or_geometry() -> None:
    with _calculation_case() as (functions, _calls, _bars):
        functions.calculate_trend_line(2)
        functions.history_events(2)
    with pytest.raises(RuntimeError, match='closed'):
        functions.calculate_trend_line(2)
        functions.history_events(2)
    with pytest.raises(RuntimeError, match='closed'):
        functions.calculate_polyline()
    with pytest.raises(RuntimeError, match='closed'):
        functions.calculate_trend_line(1)
    with pytest.raises(RuntimeError, match='closed'):
        _ = functions.frames
    with pytest.raises(RuntimeError, match='closed'):
        _ = functions.levels


def test_partial_history_is_geometry_only_and_rejects_incomplete_lifecycle() -> None:
    bars, _points = _case()
    with ChartDrawingHistory(start=2, count=len(bars)-2, closed=()) as frames:
        calls: list[int] = []

        def draw() -> None:
            calls.append(0)
            for index in range(2, len(bars)):
                frames.append(index, 0, (), None)

        def unexpected_trend(level: int) -> None:
            raise AssertionError(f'partial polyline must not request trend level {level}')

        functions = MarketCalculations(bars, frames, [], draw=draw, trend=unexpected_trend)
        with pytest.raises(ValueError, match='complete history'):
            functions.history_events('折线')
        assert calls == []
        assert functions.calculate_polyline() is frames
        assert calls == [0]


def test_public_scope_supports_polyline_six_states_without_building_first_level() -> None:
    bars, _points = _case()
    with market_calculations(bars) as functions:
        assert not functions.levels
        first_flip = functions.history_events(level='折线')
        frames = functions.calculate_polyline()
        assert frames.start == 0
        assert frames.row_count == len(bars)
        assert len(list(frames.iter_rows())) == len(bars)
        expected = functions.history_events('折线')
        assert functions.history_events('polyline') == functions.history_events(0) == expected
        assert functions.history_events('折线') == first_flip
        assert all(event['trend_level'] == 0 for event in expected)
        assert functions.calculate_polyline() is frames
        assert not functions.levels
    assert frames.closed
    with pytest.raises(RuntimeError, match='closed'):
        functions.history_events('折线')
