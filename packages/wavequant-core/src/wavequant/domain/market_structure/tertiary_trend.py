"""三级适配入口；趋势公式与一级、二级共同使用点序列判别器。"""

from collections.abc import Mapping, Sequence

from ..models.model import Bar


def tertiary_trends(level2: Mapping[str, object], bars: Sequence[Bar], *,
                    n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    """二级趋势线的高低点直接递归，不读取折线、一级或策略资格点替代来源。"""
    from .trend_lines import high_low_points_from_strokes, trend_lines

    points = high_low_points_from_strokes(level2.get('strokes'), include_key_break_attacks=True)
    qualified = high_low_points_from_strokes(level2.get('qualified_strokes', level2.get('strokes')))
    return trend_lines(points, bars, trend_level=3, qualified_source_points=qualified,
                       n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
