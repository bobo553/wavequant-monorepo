"""六态折线来源只解析翻向窗口，保持公开全量入口的知识时钟与事件。"""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_lifecycle, trend_lines
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.trend_lines import _OwnedPolylineTurns, polyline_turns
from wavequant.domain.models.model import Bar


def _case(
    level: int, prefix: int = 0, *, formal_alternation: bool = True,
) -> tuple[list[Bar], dict[str, object], list[dict[str, object]]]:
    prices = [(5.2, 5.4, 5.1, 5.3, 100), (3.1, 3.2, 3., 3.1, 100),
        (4.8, 5., 4.7, 4.9, 100), (4., 4.09, 3.96, 4.07, 100),
        (5.51, 5.78, 5.32, 5.32, 200), (4.12, 4.15, 4., 4.09, 100),
        (4.10, 4.21, 4.06, 4.21, 150), (4.19, 4.25, 4.19, 4.22, 3364310),
        (4.25, 4.64, 4.22, 4.64, 13786411)]
    rows = [(4.5, 5., 4., 4.5, 100)] * prefix + prices
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TAIL', *row)
            for index, row in enumerate(rows)]

    def point(index: int, kind: str, value: float, known: int) -> dict[str, object]:
        return dict(index=index, ordinal=0, kind=kind, value=value, available_at=known,
            time=bars[index].timestamp.date().isoformat(), state='confirmed')

    key, background, prior, origin, high = (
        point(prefix, 'H', 5.4, prefix), point(prefix + 1, 'L', 3., prefix + 1),
        point(prefix + 2, 'H', 5., prefix + 2), point(prefix + 3, 'L', 3.96, prefix + 3),
        point(prefix + 4, 'H', 5.78, prefix + 4))
    high['observations'] = [dict(title='翻空为多', available_at=prefix + 4,
        key=dict(key), confirmed_low=dict(background))]
    counter = point(prefix + 5, 'L', 4., prefix + 6)
    # 正式交替观察在第 8 日才可知，避免把其他确认规则当成本次等价优化的前提。
    if formal_alternation:
        counter['observations'] = [dict(title='空多交替', available_at=prefix + 8,
            confirmed_low=dict(background), flip_high=dict(high))]
    row: dict[str, object] = dict(trend_level=level,
        strokes=[dict(id='path', points=[key, background, prior, origin, high, counter])])
    raw = [point(index, 'H' if index % 2 else 'L', 5. if index % 2 else 4., index)
           for index in range(prefix)]
    raw.extend((dict(high), point(prefix + 5, 'L', 4., prefix + 6),
                point(prefix + 6, 'H', 4.21, prefix + 6)))
    return bars, row, raw


@pytest.mark.parametrize('level', [0, 1, 2, 3])
@pytest.mark.parametrize('resume', ['none', 'copy', 'checkpoint'])
@pytest.mark.parametrize('future_raw', [False, True])
@pytest.mark.parametrize('formal_alternation', [False, True])
def test_tail_source_preserves_daily_events_and_checkpoint_clocks(
    level: int, resume: str, future_raw: bool, formal_alternation: bool,
) -> None:
    bars, row, raw = _case(level, formal_alternation=formal_alternation)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    reference, incremental = TrendLifecycle(), TrendLifecycle()
    events: list[dict[str, object]] = []
    for index in range(len(bars)):
        if index == 7 and resume != 'none':
            incremental = (incremental.copy() if resume == 'copy'
                           else TrendLifecycle.from_checkpoint(incremental.checkpoint()))
        visible = raw if future_raw else [point for point in raw if cast(int, point['available_at']) <= index]
        expected = reference.advance((row,), bars, index, dates, polyline=visible)
        actual = incremental.advance((row,), bars, index, dates, polyline=visible, _owned_structure=True)
        assert actual == expected
        assert incremental.checkpoint() == reference.checkpoint()
        events.extend(actual)
    flip = next(event for event in events if event.get('state') == '空翻多')
    assert flip['bar_index'] == 4 and flip['origin_index'] == 1
    if formal_alternation:
        confirmation = next(event for event in events if event.get('state') == '空多交替')
        assert confirmation['bar_index'] == 8 and confirmation['origin_index'] == 1


@pytest.mark.parametrize('prefix', [64, 512])
def test_lifecycle_tail_cost_does_not_parse_or_reproject_older_history(monkeypatch, prefix: int) -> None:
    bars, row, raw = _case(2, prefix)
    for _ in range(8):
        bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), volume=100))
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    reference, incremental = TrendLifecycle(), TrendLifecycle()
    warmed = prefix + 8
    expected: list[tuple[list[dict[str, object]], tuple[object, ...]]] = []
    for index in range(len(bars)):
        visible = [point for point in raw if cast(int, point['available_at']) <= index]
        events = reference.advance((row,), bars, index, dates, polyline=visible)
        if index < warmed:
            assert incremental.advance((row,), bars, index, dates,
                polyline=visible, _owned_structure=True) == events
        else:
            expected.append((events, reference.checkpoint()))

    triples = pivots = 0
    original_turn, original_pivot = trend_lines._polyline_turn, trend_lifecycle._pivot

    def counted_turn(left: Mapping[str, object], point: Mapping[str, object], right: Mapping[str, object],
                     *, source_path: str) -> dict[str, object] | None:
        nonlocal triples
        triples += 1
        return original_turn(left, point, right, source_path=source_path)

    def counted_pivot(value: object, date_indices: Mapping[str, int], now: int) -> trend_lifecycle._Pivot | None:
        nonlocal pivots
        pivots += 1
        return original_pivot(value, date_indices, now)

    monkeypatch.setattr(trend_lines, '_polyline_turn', counted_turn)
    monkeypatch.setattr(trend_lifecycle, '_pivot', counted_pivot)
    for index, (events, checkpoint) in enumerate(expected, start=warmed):
        assert incremental.advance((row,), bars, index, dates,
            polyline=raw, _owned_structure=True) == events
        assert incremental.checkpoint() == checkpoint
    assert triples <= 3 * len(expected)
    assert pivots <= 8 * len(expected)


@pytest.mark.parametrize('indices', [
    (0, 1, 1, 2, 2, 3),
    (0, 4, 2, 3, 4, 5),
    (0, '1', 2, 3, 4, 5),
])
def test_indexed_projection_keeps_duplicate_dates_and_falls_back_for_unordered_clocks(indices) -> None:
    raw = [dict(index=index, ordinal=position, time=str(position), value=10. if position % 2 else 5.,
        kind='H' if position % 2 else 'L', state='confirmed', available_at=position)
        for position, index in enumerate(indices)]
    projector = _OwnedPolylineTurns()
    for start in range(6):
        expected = polyline_turns(raw, source_path='path')
        actual = projector.project(raw, source_path='path', _borrow_readonly=True, _start_index=start)
        assert [point for point in actual if type(point['index']) is int and point['index'] >= start] == [
            point for point in expected if type(point['index']) is int and point['index'] >= start]
        if any(type(index) is not int for index in indices) or indices == (0, 4, 2, 3, 4, 5):
            assert actual == expected
    detached = projector.project(raw, source_path='path')
    borrowed = projector.project(raw, source_path='path', _borrow_readonly=True)
    detached.clear()
    assert borrowed == polyline_turns(raw, source_path='path')


def test_public_mutable_polyline_does_not_reuse_owned_tail_after_mode_switch() -> None:
    bars, row, raw = _case(2)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    reference, incremental = TrendLifecycle(), TrendLifecycle()
    for index in range(7):
        visible = [point for point in raw if cast(int, point['available_at']) <= index]
        reference.advance((row,), bars, index, dates, polyline=visible)
        incremental.advance((row,), bars, index, dates, polyline=visible, _owned_structure=True)
    revised = deepcopy(raw)
    revised[1]['value'] = 3.99
    for index in range(7, len(bars)):
        owned = index == 8
        assert incremental.advance((row,), bars, index, dates, polyline=revised,
            _owned_structure=owned) == reference.advance((row,), bars, index, dates, polyline=revised)
        assert incremental.checkpoint() == reference.checkpoint()
