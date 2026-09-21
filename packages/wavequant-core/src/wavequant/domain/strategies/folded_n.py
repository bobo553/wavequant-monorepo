"""Retain confirmed swing envelopes when internal pivots fragment an N."""

from ..market_structure.polyline import PointKind


def folded_positive_n_candidates(points, *, earliest):
    return _folded_candidates(points, earliest=earliest, up=True)


def folded_inverse_n_candidates(points, *, earliest):
    return _folded_candidates(points, earliest=earliest, up=False)


def _folded_candidates(points, *, earliest, up):
    high = PointKind.HIGH if up else PointKind.LOW
    low = PointKind.LOW if up else PointKind.HIGH
    sign = 1 if up else -1
    price = lambda p: sign * p.point.price
    points = [p for p in points if p.point.index >= earliest]
    result = []
    for index, b in enumerate(points):
        if b.point.kind != high:
            continue
        left = []
        for p in reversed(points[:index]):
            if p.point.kind == high and price(p) >= price(b):
                break
            if p.point.kind == low and p.point.index < b.point.index:
                left.append(p)
        if not left:
            continue
        a = min(left, key=lambda p: (price(p), -p.point.index))
        c = None
        for p in points[index + 1 :]:
            if p.point.kind == high and price(p) >= price(b):
                break
            if p.point.kind != low or p.point.index <= b.point.index:
                continue
            if price(p) <= price(a):
                c = None
                break
            if c is None or price(p) < price(c):
                c = p
        if c is not None:
            result.append(((a, b, c), 1))
    return result
