"""发展尾部只显示真实下级极值，不借策略许可，也不递归为上级反转。"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_lines import (
    high_low_points_from_strokes, polyline_high_low_points, trend_lines,
)
from wavequant.domain.models.model import Bar


def _source(level: int) -> list[dict[str, object]]:
    values = [20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26]
    start = date(2026, 1, 1)
    return [dict(index=i, ordinal=0, kind='L' if i % 2 == 0 else 'H', value=value,
                 time=(start+timedelta(days=i)).isoformat(),
                 available_at=(start+timedelta(days=i+1)).isoformat(),
                 trend_level=level-1, state='reversal', source_path='figure008')
            for i, value in enumerate(values)]


def _paths(bundle: Mapping[str, object], field: str = 'strokes') -> list[dict[str, object]]:
    paths = bundle[field]
    assert isinstance(paths, list)
    return cast(list[dict[str, object]], paths)


def _points(path: Mapping[str, object]) -> list[dict[str, object]]:
    points = path['points']
    assert isinstance(points, list)
    return cast(list[dict[str, object]], points)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_structural_tail_uses_real_extreme_without_a_strategy_certificate(level: int) -> None:
    result = trend_lines(_source(level), trend_level=level)
    formal = _paths(result)[0]
    tail = _paths(result, 'developing_strokes')[0]
    formal_points, tail_points = _points(formal), _points(tail)
    assert result['qualified_strokes'] == []
    assert [(p['index'], p['value']) for p in formal_points] == [(2, 10), (9, 45)]
    assert [(p['index'], p['value']) for p in tail_points] == [(9, 45), (14, 24)]
    assert tail['display_only'] is True
    assert tail['confirmed_legs'] == []
    assert tail_points[-1]['state'] == 'developing'
    assert tail_points[-1]['available_at'] == '2026-01-16'
    assert tail_points[-1]['source_reversal_available_at'] == '2026-01-16'
    assert high_low_points_from_strokes([formal, tail]) == high_low_points_from_strokes([formal])


@pytest.mark.parametrize('level', [1, 2, 3])
def test_n_option_cannot_filter_geometry_or_its_display_tail(level: int) -> None:
    source = _source(level)
    before = trend_lines(source, trend_level=level)
    after = trend_lines(source, trend_level=level, n_target_trend_confirmation_enabled=True)
    assert _points(_paths(after)[0]) == _points(_paths(before)[0])
    assert after['developing_strokes'] == before['developing_strokes']


@pytest.mark.parametrize('has_right_turns', [False, True])
def test_raw_path_seed_boundary_survives_peak_extraction_and_recursive_adaptation(
    monkeypatch: pytest.MonkeyPatch, has_right_turns: bool,
) -> None:
    left = _source(1)[:15]
    # 两对定义现在于 L12 已齐备。延迟这个必要来源至真实路径断点，继续
    # 验证旧路径不能借新 epoch 的首根行情获得资格，而非依赖旧确认顺序。
    left[12]['available_at'] = left[14]['time']
    left[-1] = dict(left[-1], state='developing')
    right = [dict(left[-1], state='seed')]
    if has_right_turns:
        right += [dict(point, value=value) for point, value in zip(_source(1)[15:], [60, 55])]
    raw = dict(strokes=[dict(id='left', points=left), dict(id='right', points=right)])
    source = polyline_high_low_points(raw)
    assert all(point['source_path_end_index'] == 14 for point in source if point['source_path'] == 'left')
    if has_right_turns:
        assert min(cast(int, point['index']) for point in source if point['source_path'] == 'right') == 15
    else:
        assert not any(point['source_path'] == 'right' for point in source)

    from wavequant.domain.market_structure import trend_lines as module
    original = module.qualify_trend_structure
    cutoffs: list[int] = []

    def observe(candidates: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                bars: Sequence[Bar], *, trend_level: int,
                qualified_source: Sequence[Mapping[str, object]] | None = None,
                n_target_trend_confirmation_enabled: bool = False,
                n_target_sink: list[dict[str, object]] | None = None) -> list[dict[str, object]]:
        cutoffs.append(len(bars))
        return original(candidates, points, bars, trend_level=trend_level, qualified_source=qualified_source,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled, n_target_sink=n_target_sink)

    monkeypatch.setattr(module, 'qualify_trend_structure', observe)
    bars = [Bar(datetime(2026, 1, 1)+timedelta(days=i), 'test', 50, 51, 49, 50, 100) for i in range(20)]
    first = trend_lines(source, bars)
    assert cutoffs[0] == 14
    left_geometry = next(stroke for stroke in _paths(first) if stroke['source_path'] == 'left')
    assert _points(left_geometry)[-1]['available_at'] == left[14]['time']
    # H 的压力可知日正好在断点日，不能借用新路径的首根 K 线进入策略视图。
    assert not any(stroke['source_path'] == 'left' for stroke in _paths(first, 'qualified_strokes'))
    upper_source = high_low_points_from_strokes(first['strokes'])
    assert upper_source and upper_source[0]['source_path_end_index'] == 14
    cutoffs.clear()
    trend_lines(upper_source, bars, trend_level=2)
    assert cutoffs[0] == 14
