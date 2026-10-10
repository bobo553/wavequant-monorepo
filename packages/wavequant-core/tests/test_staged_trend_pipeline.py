"""整只股票按折线、一级、二级、三级完成阶段，日期前缀仍独立可知。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import patch

import pytest

import wavequant.domain.market_structure.trend_lines as trend_lines_module
import wavequant.domain.market_structure.trend_pipeline as trend_pipeline
import wavequant.domain.strategies.chart_entry_history as chart_entry
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.trend_judgement import TrendJudgement
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import hierarchical_entry


START = datetime(2026, 1, 1)
MACRO = (
    120, 240, 110, 230, 100, 220, 110, 230, 120, 240,
    110, 230, 105, 225, 115, 235, 125, 245, 115, 235,
    110, 230, 120, 240, 130, 250, 120, 240, 90, 210,
    100, 220, 110, 230, 100, 220, 80, 200, 90, 210, 100, 220,
)
StructureHistory = dict[int, dict[int, tuple[dict[str, object], ...]]]


@dataclass(frozen=True)
class StageEvent:
    level: int
    source_count: int
    point_count: int
    asof: int | str | None = None


def _nested_bars() -> list[Bar]:
    # 每段宏观波内加入反向微波，实际折线必须逐级归并才能得到三级高点。
    values = [value for left, right in zip(MACRO, MACRO[1:])
              for value in (5 * left, 5 * left + 4 * (right - left), 5 * left + 3 * (right - left))]
    values.append(5 * MACRO[-1])
    bars: list[Bar] = []
    for index, value in enumerate(values):
        low, high = (value, value + .25) if index % 2 == 0 else (value - .25, value)
        middle = (low + high) / 2
        bars.append(Bar(START + timedelta(days=index), 'NESTED', middle, high, low, middle, 1000))
    return bars


def _geometry(points: Sequence[Mapping[str, object]]) -> list[tuple[object, ...]]:
    return [(point['index'], point.get('ordinal', 0), point['kind'], point['value'], point['available_at'])
            for point in points]


def _stroke_points(bundle: Mapping[str, object]) -> list[Mapping[str, object]]:
    return [point for path in cast(Sequence[Mapping[str, object]], bundle['strokes'])
            for point in cast(Sequence[Mapping[str, object]], path['points'])]


def _multiple_path_bars(*, count: int | None = None) -> list[Bar]:
    first = _nested_bars()[:count]
    # 无阴阳顺序的十字星母子关系形成真实断点，不可借前一路径确认后一条。
    bridge = Bar(START + timedelta(days=len(first)), 'NESTED', 1055, 2100, 10, 1055, 1000)
    offset = len(first) + 1
    second = [replace(bar, timestamp=START + timedelta(days=offset + index), open=bar.open + 100,
                      high=bar.high + 100, low=bar.low + 100, close=bar.close + 100)
              for index, bar in enumerate(first)]
    return [*first, bridge, *second]


def _assert_stages(trace: Sequence[StageEvent], expected_levels: tuple[int, ...]) -> None:
    levels = [event.level for event in trace]
    assert set(levels) == set(expected_levels)
    # 不限定内部复用次数；完成一个级别之前，不可开始任何下一级的日期或路径。
    assert levels == sorted(levels), f'趋势阶段交错，前16次调用：{levels[:16]}'


def _observe_hierarchy(
    bars: Sequence[Bar], *, prefix_cache: dict[str, object] | None = None,
) -> tuple[object, StructureHistory, list[StageEvent]]:
    trace: list[StageEvent] = []
    structures: StructureHistory = {}
    real_judge = hierarchical_entry.judge_trend
    real_turns = hierarchical_entry.polyline_turns

    def observe_turns(points: Sequence[Mapping[str, object]], *, source_path: str = 'polyline') -> list[dict[str, object]]:
        result = real_turns(points, source_path=source_path)
        trace.append(StageEvent(0, len(points), len(result)))
        return result

    def observe_judge(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                      asof: int | str | None = None) -> TrendJudgement:
        result = real_judge(points, trend_level=trend_level, asof=asof)
        trace.append(StageEvent(trend_level, len(points), len(result.points), asof))
        return result

    # spy 调用真实函数，既不替换折线、证书，也不预造高层结构。
    with patch.object(hierarchical_entry, 'polyline_turns', side_effect=observe_turns), patch.object(
            hierarchical_entry, 'judge_trend', side_effect=observe_judge):
        result = hierarchical_entry.hierarchical_history(bars, structure_sink=structures, prefix_cache=prefix_cache)
    return result, structures, trace


def _observe_chart(
    bars: Sequence[Bar], *, prefix_cache: dict[str, object] | None = None,
) -> tuple[object, dict[int, tuple[Mapping[str, object], ...]], list[StageEvent]]:
    trace: list[StageEvent] = []
    geometry: dict[int, tuple[Mapping[str, object], ...]] = {}
    real_judge = trend_lines_module.judge_trend
    real_polyline = chart_entry.polyline_high_low_points

    def polyline(source: Mapping[str, object]) -> list[dict[str, object]]:
        result = real_polyline(source)
        trace.append(StageEvent(0, len(result), len(result)))
        return result

    def observe(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                asof: int | str | None = None) -> TrendJudgement:
        result = real_judge(points, trend_level=trend_level, asof=asof)
        trace.append(StageEvent(trend_level, len(points), len(result.points), asof))
        geometry[trend_level] = result.points
        return result

    with patch.object(chart_entry, 'polyline_high_low_points', side_effect=polyline), patch.object(
            trend_lines_module, 'judge_trend', side_effect=observe):
        result = chart_entry.chart_entry_history(bars, prefix_cache=prefix_cache)
    return result, geometry, trace


def test_hierarchical_history_completes_all_polyline_and_level_stages_before_the_next_level() -> None:
    bars = _nested_bars()
    _, structures, trace = _observe_hierarchy(bars)
    assert len(bars) == 124
    assert {level: max(len(daily[level]) for daily in structures.values()) for level in (1, 2, 3)} == {
        1: 39, 2: 9, 3: 1}
    assert _geometry(structures[107][3]) == []
    assert _geometry(structures[108][3]) == [(75, 0, 'H', 1250, 108)]
    assert structures[123][3] == structures[108][3]
    _assert_stages(trace, (0, 1, 2, 3))


def test_chart_entry_history_completes_every_date_of_a_level_before_judging_the_next_level() -> None:
    bars = _nested_bars()
    _, geometry, trace = _observe_chart(bars)
    assert _geometry(geometry[3]) == [(75, 0, 'H', 1250, (START + timedelta(days=108)).date().isoformat())]
    assert any(event.level == 2 and event.point_count == 9 for event in trace)
    assert any(event.level == 3 and event.point_count == 1 for event in trace)
    _assert_stages(trace, (0, 1, 2, 3))


@pytest.mark.parametrize('enabled', (False, True))
def test_complete_stock_pipeline_matches_all_adapter_fields_and_finishes_geometry_before_permissions(enabled: bool) -> None:
    bars = _multiple_path_bars()
    drawing = cast(Mapping[str, object], lecture_drawing(bars))
    before = deepcopy(drawing)
    first = reversal_trends(drawing, bars, n_target_trend_confirmation_enabled=enabled)
    second = secondary_trends(first, bars, n_target_trend_confirmation_enabled=enabled)
    third = tertiary_trends(second, bars, n_target_trend_confirmation_enabled=enabled)
    expected = dict(lecture_drawing=drawing, reversal_trends=first, secondary_trends=second, tertiary_trends=third)
    trace: list[StageEvent] = []
    real_polyline = trend_pipeline.polyline_high_low_points
    real_judge = trend_lines_module.judge_trend
    real_attach = trend_pipeline.attach_trend_permissions

    def polyline(source: Mapping[str, object]) -> list[dict[str, object]]:
        result = real_polyline(source)
        trace.append(StageEvent(0, len(result), len(result)))
        return result

    def judge(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
              asof: int | str | None = None) -> TrendJudgement:
        result = real_judge(points, trend_level=trend_level, asof=asof)
        trace.append(StageEvent(trend_level, len(points), len(result.points), asof))
        return result

    def attach(prepared: Mapping[str, object], market: Sequence[Bar] = (), *,
               n_target_trend_confirmation_enabled: bool = False,
               qualified_source_points: Sequence[Mapping[str, object]] | None = None) -> dict[str, object]:
        level = cast(int, prepared['trend_level'])
        result = real_attach(prepared, market, qualified_source_points=qualified_source_points,
                             n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        trace.append(StageEvent(level + 3, len(_stroke_points(prepared)), len(_stroke_points(result))))
        return result

    with patch.object(trend_pipeline, 'polyline_high_low_points', side_effect=polyline), patch.object(
            trend_lines_module, 'judge_trend', side_effect=judge), patch.object(
            trend_pipeline, 'attach_trend_permissions', side_effect=attach):
        observed = trend_pipeline.calculate_trend_hierarchy(bars, drawing=drawing,
            n_target_trend_confirmation_enabled=enabled)
    # 比较完整结果，包括许可、来源、标签及证明日期，不能只比较绘图坐标。
    assert observed == expected
    assert drawing == before
    assert len(cast(Sequence[object], drawing['strokes'])) == 2
    assert [(point['index'], point['kind'], point['value']) for point in _stroke_points(observed['tertiary_trends'])] == [
        (75, 'H', 1250), (200, 'H', 1350)]
    _assert_stages(trace, (0, 1, 2, 3, 4, 5, 6))


@pytest.mark.parametrize('enabled', (False, True))
def test_complete_stock_pipeline_keeps_empty_input_and_option_contracts(enabled: bool) -> None:
    observed = trend_pipeline.calculate_trend_hierarchy((), n_target_trend_confirmation_enabled=enabled)
    first = reversal_trends(observed['lecture_drawing'], (), n_target_trend_confirmation_enabled=enabled)
    second = secondary_trends(first, (), n_target_trend_confirmation_enabled=enabled)
    third = tertiary_trends(second, (), n_target_trend_confirmation_enabled=enabled)
    assert observed == dict(lecture_drawing=observed['lecture_drawing'], reversal_trends=first,
                            secondary_trends=second, tertiary_trends=third)
    assert all(level['strokes'] == level['qualified_strokes'] == [] for level in (first, second, third))


def _prefix_result(result: object, end: int, *, chart: bool) -> object:
    history, details = cast(tuple[Mapping[int, object], object], result)
    prefix = {index: value for index, value in history.items() if index < end}
    if chart:
        events = cast(Sequence[Mapping[str, object]], details)
        return prefix, [event for event in events if cast(int, event['bar_index']) < end]
    epochs = cast(Mapping[int, object], details)
    return prefix, {index: value for index, value in epochs.items() if index < end}


@pytest.mark.parametrize('chart', (False, True))
def test_staged_history_keeps_cold_warm_partial_and_appended_prefix_results_identical(chart: bool) -> None:
    bars = _nested_bars()[:40]
    observe = _observe_chart if chart else _observe_hierarchy
    complete, complete_geometry, _ = observe(bars)
    cache: dict[str, object] = {}
    for end in (28, 39, 40):
        prefix = bars[:end]
        fresh, fresh_geometry, _ = observe(prefix)
        assert fresh == _prefix_result(complete, end, chart=chart)
        if not chart:
            assert fresh_geometry == {now: daily for now, daily in complete_geometry.items() if now < end}
        cached, cached_geometry, trace = observe(prefix, prefix_cache=cache)
        assert cached == fresh
        assert cached_geometry == fresh_geometry
        assert [event.level for event in trace] == sorted(event.level for event in trace)
        warm, warm_geometry, warm_trace = observe(prefix, prefix_cache=cache)
        assert warm == fresh
        assert warm_geometry == fresh_geometry
        assert [event.level for event in warm_trace] == sorted(event.level for event in warm_trace)

    checkpoint = deepcopy(cache['checkpoint'])
    partial = [*bars[:-1], replace(bars[-2], timestamp=bars[-1].timestamp)]
    fresh_partial, fresh_partial_geometry, _ = observe(partial)
    cached_partial, cached_partial_geometry, _ = observe(partial, prefix_cache=cache)
    assert cached_partial == fresh_partial
    assert cached_partial_geometry == fresh_partial_geometry
    assert cache['checkpoint'] == checkpoint
    restored, restored_geometry, _ = observe(bars, prefix_cache=cache)
    assert restored == complete
    assert restored_geometry == complete_geometry
    following = _nested_bars()[:41]
    fresh_next, fresh_next_geometry, _ = observe(following)
    cached_next, cached_next_geometry, _ = observe(following, prefix_cache=cache)
    assert cached_next == fresh_next
    assert cached_next_geometry == fresh_next_geometry
    assert _prefix_result(cached_next, len(bars), chart=chart) == complete


def test_level_stages_reset_each_epoch_without_borrowing_another_paths_higher_points_or_cache() -> None:
    bars = _multiple_path_bars(count=40)
    complete, structures, trace = _observe_hierarchy(bars)
    _, epochs = cast(tuple[object, dict[int, int]], complete)
    assert (epochs[39], epochs[40], epochs[41], epochs[80]) == (0, 40, 41, 41)
    # 高、低两组同向证据齐备当天确认；新路径仍须自行形成证据。
    assert structures[21][2] == ()
    assert _geometry(structures[22][2]) == [(12, 0, 'L', 500, 22)]
    assert _geometry(structures[39][2]) == [(12, 0, 'L', 500, 22), (27, 0, 'H', 1200, 36)]
    assert structures[62][2] == ()
    assert _geometry(structures[63][2]) == [(53, 0, 'L', 600, 63)]
    assert _geometry(structures[80][2]) == [(53, 0, 'L', 600, 63), (68, 0, 'H', 1300, 77)]
    assert all(daily[3] == () for daily in structures.values())
    assert all(cast(int, point['index']) >= 41 for now, daily in structures.items() if now >= 41
               for level in (1, 2, 3) for point in daily[level])
    _assert_stages(trace, (0, 1, 2, 3))

    cache: dict[str, object] = {}
    for end in (40, 41, 42, len(bars)):
        observed, cached_structure, _ = _observe_hierarchy(bars[:end], prefix_cache=cache)
        assert observed == _prefix_result(complete, end, chart=False)
        assert cached_structure == {now: daily for now, daily in structures.items() if now < end}


def test_chart_collector_keeps_closed_epoch_snapshots_causal_during_warm_and_partial_cache_replays() -> None:
    bars = _multiple_path_bars(count=40)
    complete, geometry, trace = _observe_chart(bars)
    assert _geometry(geometry[2]) == [(53, 0, 'L', 600, (START + timedelta(days=63)).date().isoformat()),
                                      (68, 0, 'H', 1300, (START + timedelta(days=77)).date().isoformat())]
    assert geometry[3] == ()
    _assert_stages(trace, (0, 1, 2, 3))
    cache: dict[str, object] = {}
    for end in (40, 41, 42, 80, 81):
        cached, _, _ = _observe_chart(bars[:end], prefix_cache=cache)
        assert cached == _prefix_result(complete, end, chart=True)
    warm, _, _ = _observe_chart(bars, prefix_cache=cache)
    assert warm == complete
    checkpoint = deepcopy(cache['checkpoint'])
    partial = [*bars[:-1], replace(bars[-2], timestamp=bars[-1].timestamp)]
    fresh_partial, _, _ = _observe_chart(partial)
    cached_partial, _, _ = _observe_chart(partial, prefix_cache=cache)
    assert cached_partial == fresh_partial
    assert cache['checkpoint'] == checkpoint
    restored, _, _ = _observe_chart(bars, prefix_cache=cache)
    assert restored == complete
    next_source = _nested_bars()[40]
    following = [*bars, replace(next_source, timestamp=START + timedelta(days=81), open=next_source.open + 100,
                               high=next_source.high + 100, low=next_source.low + 100, close=next_source.close + 100)]
    fresh_next, _, _ = _observe_chart(following)
    cached_next, _, _ = _observe_chart(following, prefix_cache=cache)
    assert cached_next == fresh_next
    assert _prefix_result(cached_next, len(bars), chart=True) == complete
