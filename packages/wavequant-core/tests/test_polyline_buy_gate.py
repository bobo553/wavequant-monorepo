"""已确认折线末跌高阻止局部反弹买入，强势当棒影线严格突破才解除。"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.models.model import Bar


def point(index: int, kind: PointKind, price: float, known: int | None = None) -> ReversalPoint:
    return ReversalPoint(LinePoint(index, 0, kind, price), index if known is None else known, 'fixture')


def bar(index: int, high: float = 8.8) -> Bar:
    return Bar(datetime(2025, 1, 1) + timedelta(days=index), 'sh.601086', 8, high, 7, 8.2, 100)


def breakout_bar(index: int, high: float) -> Bar:
    return replace(bar(index, high), open=8.2, low=8.1, close=min(high, 8.9))


def bearish_points() -> list[ReversalPoint]:
    return [point(0, PointKind.HIGH, 10), point(1, PointKind.LOW, 8),
            point(2, PointKind.HIGH, 9), point(3, PointKind.LOW, 7)]


@pytest.mark.parametrize('high,blocked', [(8.99, True), (9, True), (9.01, False)])
def test_shadow_break_is_strict_and_does_not_require_close_above_key(high: float, blocked: bool) -> None:
    gate = PolylineBuyGate()
    points = bearish_points()
    for index in range(4):
        gate.advance(index, bar(index), points, 0)
    gate.advance(4, breakout_bar(4, high), points, 0)
    assert (4 in gate.restrictions) is blocked
    assert gate.restrictions[3]['polyline_key_index'] == 2
    if not blocked:
        gate.advance(5, bar(5, 8.8), points, 0)
        assert 5 not in gate.restrictions


def test_local_higher_low_does_not_lower_the_frozen_key() -> None:
    gate = PolylineBuyGate()
    points = bearish_points() + [point(4, PointKind.HIGH, 8.5), point(5, PointKind.LOW, 7.5)]
    for index in range(7):
        gate.advance(index, bar(index), points, 0)
    assert gate.restrictions[6]['polyline_key_price'] == 9
    assert gate.restrictions[6]['polyline_key_index'] == 2


def test_context_must_be_known_before_a_break_and_old_days_are_not_rewritten() -> None:
    gate = PolylineBuyGate()
    points = bearish_points()[:3] + [point(3, PointKind.LOW, 7, known=5)]
    for index in range(6):
        gate.advance(index, bar(index, 9.1 if index == 5 else 8.8), points, 0)
    assert 4 not in gate.restrictions
    assert gate.restrictions[5]['polyline_key_known_index'] == 5
    gate.advance(6, breakout_bar(6, 9.1), points, 0)
    assert 6 not in gate.restrictions
    assert 5 in gate.restrictions


def test_source_revision_rebuilds_current_context_and_epoch_does_not_reuse_release() -> None:
    gate = PolylineBuyGate()
    points = bearish_points()
    for index in range(5):
        gate.advance(index, breakout_bar(index, 9.1) if index == 4 else bar(index), points, 0)
    revised = points[:2] + [point(2, PointKind.HIGH, 9.5, known=5), point(3, PointKind.LOW, 7, known=5)]
    gate.advance(5, bar(5, 9.1), revised, 0)
    assert gate.restrictions[5]['polyline_key_price'] == 9.5
    gate.advance(6, bar(6), (), 6)
    assert 6 not in gate.restrictions
    assert 3 in gate.restrictions
