"""Executable synthetic examples for the reusable trend definition API."""
from datetime import datetime, timedelta

from wavequant.domain.market_structure.candle_primitives import observe_bar_relations, virtual_low
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.trend_primitives import (
    is_bull_trend, last_fall_high, last_rise_low, positive_reversal,
)
from wavequant.domain.market_structure.trend_structure import observe_structure
from wavequant.domain.models.model import Bar


def main() -> None:
    rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (13, 14, 12.5, 13.5), (12.5, 13.2, 12, 12.8)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), "EXAMPLE", *row, 100)
            for index, row in enumerate(rows)]
    # Explicit synthetic confirmation dates demonstrate the observer contract;
    # a production caller supplies the existing polyline's confirmed points.
    points = tuple(ReversalPoint(LinePoint(index, 0, kind, price), index + 1, "synthetic_confirmed")
                   for index, kind, price in (
                       (0, PointKind.LOW, 8), (1, PointKind.HIGH, 12),
                       (2, PointKind.LOW, 10), (3, PointKind.HIGH, 14),
                   ))
    context = observe_structure(points, symbol="EXAMPLE", timeframe="1d", window_start=0, asof_index=4)
    print("日出:", observe_bar_relations(bars[2], bars[3]).sunrise)
    print("攻击棒虚低:", virtual_low(bars[2], bars[3]))
    print("全窗口多头趋势:", is_bull_trend(context))
    print("低点正反转可知:", positive_reversal(points[2], asof_index=3))
    print("窗口最低点没有左侧末跌高:", last_fall_high(context))
    print("指定低点的末跌高:", last_fall_high(context, low=points[2]))
    print("窗口最高点的末升低:", last_rise_low(context))


if __name__ == "__main__":
    main()
