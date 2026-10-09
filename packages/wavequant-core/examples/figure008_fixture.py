"""Synthetic numeric evidence preserving Figure 008's labelled relative geometry.

The image has no price axis or real candle dates. Prices and daily timestamps
below are engineering inputs, not measurements taken from the image. They do not
prove a 33% countermove, actual intrabar order, volume expansion or profitability.
Every supplied pivot is explicitly confirmed on the following sample bar.
"""

from datetime import datetime, timedelta

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.models.model import Bar


FIGURE008_LABELS = (
    'L1', 'H1', 'L0', 'H2', 'L2', 'H3', 'L3', 'H4', 'L4',
    'H0', 'L5', 'H5', 'L6', 'H6', 'L7', 'H7', 'L8',
)
FIGURE008_PRICES = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)


def build_figure008_sample() -> tuple[tuple[Bar, ...], tuple[ReversalPoint, ...]]:
    """Build matching OHLC bars and causal pivots, plus bar 17 confirming L8.

    High bars have two-unit ranges below their labelled high; low bars have
    two-unit ranges above their labelled low. Fixed sample volume is not chart
    evidence. Confirmation dates are explicitly supplied, not inferred from these
    synthetic candles or a guessed initial polyline direction.
    """

    bars: list[Bar] = []
    points: list[ReversalPoint] = []
    for index, (label, price) in enumerate(zip(FIGURE008_LABELS, FIGURE008_PRICES)):
        is_high = label.startswith('H')
        kind = PointKind.HIGH if is_high else PointKind.LOW
        opening = price - 1 if is_high else price + 1
        high = price if is_high else price + 2
        low = price - 2 if is_high else price
        bars.append(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'FIG008', opening, high, low, opening, 1000))
        points.append(ReversalPoint(LinePoint(index, 0, kind, price), index + 1, 'figure008_synthetic_confirmed'))
    bars.append(Bar(datetime(2026, 1, 18), 'FIG008', 27, 29, 27, 28, 1000))
    return tuple(bars), tuple(points)
