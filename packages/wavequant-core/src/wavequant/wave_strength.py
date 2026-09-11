"""Countermove strength, literal gaps and exact sixths; not trend direction."""
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Sequence

from .model import Bar
from .polyline import PointKind, ReversalPoint
from .price_action import Direction, _index, _positive, _validate_bar, _ordered_pair


class StrengthScale(str, Enum):
    EXACT_FRACTIONS = 'exact_sixths'
    PRINTED_PERCENTAGES = 'printed_16.7_33.3_50_66.7_83.3'


class CounterStrength(str, Enum):
    STRONG = 'strong_countermove'
    MEDIUM = 'medium_countermove'
    WEAK = 'weak_countermove'
    UNSPECIFIED = 'lecture_interval_not_specified'
    BOUNDARY = 'lecture_boundary_unresolved'
    NONE = 'no_countermove'


@dataclass(frozen=True)
class StrengthLevel:
    fraction: str
    price: float


@dataclass(frozen=True)
class WaveStrength:
    impulse_direction: Direction
    scale: StrengthScale
    impulse: float
    countermove: float
    ratio: float
    exact_ratio: str
    counter_strength: CounterStrength
    counter_name: str
    original_trend_reading: str
    sixth_band: int | None
    sixth_boundary: str | None
    extent: str
    height_from_wave_low: float
    levels: tuple[StrengthLevel, ...]
    rule: str = 'literal_gaps_pullback_half_inclusive_engineering_v1'


def measure_strength(origin: float, extreme: float, counter: float, *,
                     impulse_direction: Direction, scale: StrengthScale) -> WaveStrength:
    if not isinstance(impulse_direction, Direction) or not isinstance(scale, StrengthScale):
        raise ValueError('explicit direction and scale required')
    for value in (origin, extreme, counter):
        _positive(value, 'price')
    a, b, c = (Fraction(str(v)) for v in (origin, extreme, counter))
    sign = 1 if impulse_direction == Direction.UP else -1
    impulse, move = sign*(b-a), sign*(b-c)
    if impulse <= 0 or move < 0:
        raise ValueError('positive impulse and nonnegative countermove required')
    r = move/impulse
    ticks = (tuple(Fraction(i, 6) for i in range(1, 6)) if scale == StrengthScale.EXACT_FRACTIONS
             else tuple(Fraction(v) for v in ('0.167', '0.333', '0.5', '0.667', '0.833')))
    third, half, two_thirds = ticks[1:4]
    up = impulse_direction == Direction.UP
    if not move:
        grade = CounterStrength.NONE
    elif up:
        grade = (CounterStrength.WEAK if r < third else
                 CounterStrength.MEDIUM if half <= r < two_thirds else
                 CounterStrength.STRONG if r >= two_thirds else
                 CounterStrength.BOUNDARY if r == third else CounterStrength.UNSPECIFIED)
    else:
        grade = (CounterStrength.STRONG if r > two_thirds else
                 CounterStrength.MEDIUM if half < r < two_thirds else
                 CounterStrength.WEAK if third < r < half else
                 CounterStrength.BOUNDARY if r in (third, half, two_thirds) else CounterStrength.UNSPECIFIED)
    boundary = next((str(t) for t in ticks if r == t), None)
    extent = 'partial' if 0 < r < 1 else 'none' if r == 0 else 'full_retracement' if r == 1 else 'beyond_origin'
    band = None if boundary or r >= 1 else 0 if not r else sum(r > t for t in ticks)+1
    reading = {CounterStrength.WEAK: 'original_direction_relatively_resilient',
               CounterStrength.MEDIUM: 'intermediate_counter_pressure',
               CounterStrength.STRONG: 'original_direction_under_strong_counter_pressure'}.get(grade, 'not_assigned')
    return WaveStrength(impulse_direction, scale, float(impulse), float(move), float(r), str(r),
        grade, '回档' if up else '反弹', reading, band, boundary, extent,
        float(1-r if up else r), tuple(StrengthLevel(str(t), float(b-sign*t*impulse)) for t in ticks))


def validate_prefix(bars: Sequence[Bar], *, symbol: str, end: int):
    _index(end, 'asof_index')
    if end >= len(bars):
        raise ValueError('available asof required')
    from .validated_bars import ValidatedBars
    if isinstance(bars, ValidatedBars):
        if bars[0].symbol != symbol:
            raise ValueError('symbol mismatch')
        return
    for i in range(end+1):
        _validate_bar(bars[i])
        if bars[i].symbol != symbol:
            raise ValueError('symbol mismatch')
        if i:
            _ordered_pair(bars[i-1], bars[i])


def validate_pivot_prices(bars, points, end):
    for p in points:
        if not isinstance(p, ReversalPoint):
            raise ValueError('confirmed reversal points required')
        if p.confirmed_index <= end:
            b = bars[p.point.index]
            if p.point.price != (b.high if p.point.kind == PointKind.HIGH else b.low):
                raise ValueError('pivot differs from source OHLC / adjustment basis')


def observe_wave_strength(bars: Sequence[Bar], points: tuple[ReversalPoint, ReversalPoint, ReversalPoint], *,
                          symbol: str, scale: StrengthScale, asof_index: int) -> WaveStrength | None:
    validate_prefix(bars, symbol=symbol, end=asof_index)
    if len(points) != 3 or not all(isinstance(p, ReversalPoint) for p in points):
        raise ValueError('exactly three confirmed pivot references required')
    validate_pivot_prices(bars, points, asof_index)
    if any(p.confirmed_index > asof_index for p in points):
        return None
    a, b, c = points
    if (not a.point.index < b.point.index < c.point.index or
            not a.confirmed_index <= b.confirmed_index <= c.confirmed_index or
            a.point.kind != c.point.kind or a.point.kind == b.point.kind):
        raise ValueError('ordered alternating distinct-bar pivots required')
    direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
    sign = 1 if direction == Direction.UP else -1
    adverse = lambda bar: bar.low if direction == Direction.UP else bar.high
    forward = lambda bar: bar.high if direction == Direction.UP else bar.low
    if (any(sign*(adverse(bars[i])-a.point.price) < 0 for i in range(a.point.index, b.point.index+1)) or
            any(sign*(forward(bars[i])-b.point.price) > 0 for i in range(a.point.index, c.point.index+1)) or
            any(sign*(adverse(bars[i])-c.point.price) < 0 for i in range(b.point.index+1, c.point.index+1))):
        raise ValueError('supplied pivots are not extrema of the stated wave legs')
    return measure_strength(a.point.price, b.point.price, c.point.price,
                             impulse_direction=direction, scale=scale)
