"""一只股票的完整折线来源与三个趋势级别按阶段计算。"""

from collections.abc import Callable, Mapping, Sequence
from typing import TypedDict, cast

from ..models.model import Bar
from .lecture_drawing import lecture_drawing
from .trend_cascade import prepare_trend_cascade
from .trend_lines import (
    attach_trend_permissions, high_low_points_from_strokes,
    polyline_high_low_points,
)


class TrendHierarchy(TypedDict):
    lecture_drawing: Mapping[str, object]
    reversal_trends: dict[str, object]
    secondary_trends: dict[str, object]
    tertiary_trends: dict[str, object]


def calculate_trend_hierarchy(
    bars: Sequence[Bar], *, drawing: Mapping[str, object] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> TrendHierarchy:
    """完整零级来源→完整一级→完整二级→完整三级，随后独立核验策略资格。

    一个级别必须处理完所有路径，才能把全部输出高低点传给下一级；
    不能在某条路径或某根K线内提前跨级。各级仍调用同一个judge_trend，
    prepare只保留来源、结构点和各自可知日，严格高低点突破升级规则不变。

    bars应当已截到请求日期。来源点的当日实体只供翻空后影线反弹判断；
    N和交易交替证书仍在几何之外附加，发展尾部由正式点适配器排除。
    中间结果由本次调用独立拥有，各级只读前级来源，不维护共享全局状态。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    # 旧绘图模块尚未声明类型；在这个边界只使用其单股票bars入口，输出仍由来源适配器校验。
    draw = cast(Callable[[Sequence[Bar]], dict[str, object]], lecture_drawing)
    drawing_result = draw(bars) if drawing is None else drawing
    source = polyline_high_low_points(drawing_result)
    symbol = bars[0].symbol if bars else 'STRUCTURE'
    prepared = dict(enumerate(prepare_trend_cascade(source, symbol=symbol, bars=bars), 1))

    results: dict[int, dict[str, object]] = {}
    qualified_source = None
    for level in (1, 2, 3):
        current = attach_trend_permissions(prepared[level], bars,
            qualified_source_points=qualified_source,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        results[level] = current
        if level < 3:
            qualified_source = high_low_points_from_strokes(current['qualified_strokes'])
    return TrendHierarchy(lecture_drawing=drawing_result, reversal_trends=results[1],
                          secondary_trends=results[2], tertiary_trends=results[3])
