"""真实空绘图 epoch 也已断路，旧来源不可等待后来 seed 再裁剪资格行情。"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

import wavequant.domain.market_structure.trend_lines as lines
import wavequant.domain.strategies.chart_entry_history as chart
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.trend_pipeline import calculate_trend_hierarchy
from wavequant.domain.models.model import Bar


def _bars() -> list[Bar]:
    macro = (120, 240, 110, 230, 100, 220, 110, 230, 120, 240, 110, 230, 105, 225, 115)
    values = [value for left, right in zip(macro, macro[1:])
              for value in (5 * left, 5 * left + 4 * (right - left), 5 * left + 3 * (right - left))][:40]
    start = datetime(2026, 1, 1)
    first: list[Bar] = []
    for index, value in enumerate(values):
        low, high = (value, value + .25) if index % 2 == 0 else (value - .25, value)
        middle = (low + high) / 2
        first.append(Bar(start + timedelta(days=index), 'EMPTY_EPOCH', middle, high, low, middle, 1000))
    bridge = Bar(start + timedelta(days=40), 'EMPTY_EPOCH', 1055, 2100, 10, 1055, 1000)
    second = [replace(bar, timestamp=start + timedelta(days=41 + index), open=bar.open + 100,
                      high=bar.high + 100, low=bar.low + 100, close=bar.close + 100)
              for index, bar in enumerate(first)]
    return [*first, bridge, *second]


@pytest.mark.parametrize('asof', [40, 41, 42])
def test_chart_all_levels_retain_the_first_empty_epoch_barrier_without_backdating_geometry(asof: int) -> None:
    bars = _bars()[:asof + 1]
    with chart._staged_chart_frames(bars, resume_start=0, closed=(), previous_epoch=None,
                                   previous_raw=()) as (frames, stages):
        records = list(zip(frames.iter_rows(), stages[0].iter_rows(), stages[1].iter_rows()))
        before, current = records[39], records[-1]
        assert (current[0].epoch, len(current[0].raw)) == ((40, 0) if asof == 40 else (41, 0 if asof == 41 else 2))
        for prepared, preceding in ((current[1], before[1]), (current[2], before[2])):
            caps = cast(Mapping[str, object], prepared['_path_market_end_indices'])
            assert list(caps.values()) == [40]
            old_paths = cast(Sequence[Mapping[str, object]], preceding['strokes'])
            paths = cast(Sequence[Mapping[str, object]], prepared['strokes'])
            fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')
            assert [[tuple(point[field] for field in fields) for point in cast(
                Sequence[Mapping[str, object]], path['points'])] for path in paths] == [[
                tuple(point[field] for field in fields) for point in cast(
                    Sequence[Mapping[str, object]], path['points'])] for path in old_paths]
        assert cast(Mapping[str, object], before[1]['_path_market_end_indices']) == {'lecture-0': None}


@pytest.mark.parametrize('asof', [40, 41, 42, 80])
def test_snapshot_driver_and_chart_never_lend_empty_epoch_market_to_the_old_path(
    monkeypatch: pytest.MonkeyPatch, asof: int,
) -> None:
    bars = _bars()[:asof + 1]
    calls: list[tuple[int, str, int]] = []

    def observe(candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]],
                market: Sequence[Bar], *, trend_level: int, **kwargs: object) -> list[dict[str, object]]:
        path = cast(str, source[0]['source_path'])
        calls.append((trend_level, path, len(market)))
        return []

    monkeypatch.setattr(lines, 'qualify_trend_structure', observe)
    calculate_trend_hierarchy(bars, drawing=lecture_drawing(bars))
    snapshot_calls = [call for call in calls if call[1].endswith('lecture-0')]
    assert snapshot_calls and {length for _, _, length in snapshot_calls} == {40}
    calls.clear()
    chart.chart_entry_history(bars)
    historical_calls = [call for call in calls if call[1].endswith('lecture-0')]
    assert historical_calls and max(length for _, _, length in historical_calls) == 40


def test_chart_checkpoint_keeps_the_first_empty_epoch_cap_during_warm_partial_and_appended_replay() -> None:
    bars = _bars()
    cache: dict[str, object] = {}
    chart.chart_entry_history(bars[:42], prefix_cache=cache)
    checkpoint = cast(tuple[object, ...], cache['checkpoint'])
    closed = cast(Sequence[Mapping[str, object]], checkpoint[5])
    points = cast(Sequence[Mapping[str, object]], closed[0]['points'])
    assert {point['source_path_end_index'] for point in points} == {40}
    assert chart.chart_entry_history(bars[:43], prefix_cache=cache) == chart.chart_entry_history(bars[:43])
    partial = [*bars[:42], replace(bars[42], close=bars[42].high)]
    assert chart.chart_entry_history(partial, prefix_cache=cache) == chart.chart_entry_history(partial)
    assert chart.chart_entry_history(bars, prefix_cache=cache) == chart.chart_entry_history(bars)
