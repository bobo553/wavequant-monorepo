"""Window-explicit swing context and frozen-key trend-transition evidence."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Sequence

from .model import Bar
from .polyline import PointKind, ReversalPoint
from .price_action import (AttackBasis, AttackEvidence, Direction, KeyLevel, LevelKind,
                           _index, _ordered_pair, _validate_bar, observe_attack)


class StructuralTrend(str, Enum):
    BULL = '多头趋势'
    BEAR = '空头趋势'
    MIXED = '高低点不同向或相等'
    UNKNOWN = '已确认拐点不足'


@dataclass(frozen=True)
class StructureContext:
    symbol: str
    timeframe: str
    window_start: int
    asof_index: int
    points: tuple[ReversalPoint, ...]
    trend: StructuralTrend
    higher_high: bool | None
    higher_low: bool | None
    lower_high: bool | None
    lower_low: bool | None
    window_low: ReversalPoint | None
    window_high: ReversalPoint | None
    last_fall_high: KeyLevel | None
    last_rise_low: KeyLevel | None
    window_trend: StructuralTrend


def _known_points(points, start, end):
    known = []
    for p in points:
        if not isinstance(p, ReversalPoint):
            raise ValueError('ReversalPoint sequence required')
        if p.confirmed_index > end:
            continue
        if p.point.index < start:
            continue
        if known:
            prev = known[-1]
            if ((p.point.index, p.point.ordinal) <= (prev.point.index, prev.point.ordinal)
                    or p.point.kind == prev.point.kind or p.confirmed_index < prev.confirmed_index):
                raise ValueError('ordered alternating causally confirmed points required')
            if (p.point.kind == PointKind.HIGH and p.point.price <= prev.point.price or
                    p.point.kind == PointKind.LOW and p.point.price >= prev.point.price):
                raise ValueError('nonzero alternating high/low legs required')
        known.append(p)
    return tuple(known)


def preceding_turn(points: Sequence[ReversalPoint], target: ReversalPoint) -> ReversalPoint | None:
    """Nearest opposite reversal to the LEFT of this particular target."""
    try:
        index = points.index(target)
    except ValueError as exc:
        raise ValueError('target must belong to the confirmed sequence') from exc
    return next((p for p in reversed(points[:index]) if p.point.kind != target.point.kind), None)


def observe_structure(points: Sequence[ReversalPoint], *, symbol: str, timeframe: str,
                      window_start: int, asof_index: int) -> StructureContext:
    _index(window_start, 'window_start')
    _index(asof_index, 'asof_index')
    if (window_start > asof_index or not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(timeframe, str) or not timeframe.strip()):
        raise ValueError('explicit symbol, timeframe and ordered window required')
    known = _known_points(points, window_start, asof_index)
    highs = [p for p in known if p.point.kind == PointKind.HIGH]
    lows = [p for p in known if p.point.kind == PointKind.LOW]
    hh = highs[-1].point.price > highs[-2].point.price if len(highs) >= 2 else None
    lh = highs[-1].point.price < highs[-2].point.price if len(highs) >= 2 else None
    hl = lows[-1].point.price > lows[-2].point.price if len(lows) >= 2 else None
    ll = lows[-1].point.price < lows[-2].point.price if len(lows) >= 2 else None
    trend = (StructuralTrend.UNKNOWN if hh is None or hl is None else
             StructuralTrend.BULL if hh and hl else StructuralTrend.BEAR if lh and ll else
             StructuralTrend.MIXED)
    all_up = all(b.point.price > a.point.price for seq in (highs, lows) for a, b in zip(seq, seq[1:]))
    all_down = all(b.point.price < a.point.price for seq in (highs, lows) for a, b in zip(seq, seq[1:]))
    window_trend = (StructuralTrend.UNKNOWN if hh is None or hl is None else
                    StructuralTrend.BULL if all_up else StructuralTrend.BEAR if all_down else
                    StructuralTrend.MIXED)
    # Earliest equal extreme wins; a touch does not silently replace the anchor.
    lo = min(lows, key=lambda p: p.point.price) if lows else None
    hi = max(highs, key=lambda p: p.point.price) if highs else None
    def key(target, kind):
        p = preceding_turn(known, target) if target else None
        return None if p is None else KeyLevel(symbol, timeframe, kind, p.point.price,
            p.point.index, asof_index, f'window_{window_start}_through_{asof_index}_preceding_extreme')
    return StructureContext(symbol, timeframe, window_start, asof_index, known, trend,
        hh, hl, lh, ll, lo, hi, key(lo, LevelKind.RESISTANCE), key(hi, LevelKind.SUPPORT), window_trend)


@dataclass(frozen=True)
class RetracementEvidence:
    impulse: float
    countermove: float
    ratio: float
    below_67_percent: bool
    below_33_percent: bool
    partial: bool


def retracement_evidence(origin: float, extreme: float, counter: float, *,
                         direction: Direction) -> RetracementEvidence:
    from .price_action import _positive
    if not isinstance(direction, Direction):
        raise ValueError('Direction required')
    for value in (origin, extreme, counter):
        _positive(value, 'price')
    a, b, c = (Decimal(str(v)) for v in (origin, extreme, counter))
    sign = 1 if direction == Direction.UP else -1
    impulse, move = sign*(b-a), sign*(b-c)
    if impulse <= 0 or move < 0:
        raise ValueError('positive impulse and nonnegative counter move required')
    return RetracementEvidence(float(impulse), float(move), float(move/impulse),
        move < impulse*Decimal('.67'), move < impulse*Decimal('.33'), 0 < move < impulse)


@dataclass(frozen=True)
class ABCEvidence:
    direction: Direction
    first_leg: float
    third_leg: float
    equal_wave_or_more: bool
    observed_at_index: int


def observe_abc(points: Sequence[ReversalPoint], *, direction: Direction,
                asof_index: int) -> ABCEvidence | None:
    """Exactly four confirmed vertices = three countertrend legs, no inferred abc."""
    _index(asof_index, 'asof_index')
    if not isinstance(direction, Direction):
        raise ValueError('Direction required')
    known = _known_points(points, 0, asof_index)
    if len(known) != 4:
        return None
    expected = PointKind.LOW if direction == Direction.UP else PointKind.HIGH
    if known[0].point.kind != expected:
        raise ValueError('ABC starting kind differs from direction')
    a, b, c, d = (Decimal(str(p.point.price)) for p in known)
    sign = 1 if direction == Direction.UP else -1
    first, retrace, third = sign*(b-a), sign*(b-c), sign*(d-c)
    if not 0 < retrace < first or third <= 0:
        return None
    return ABCEvidence(direction, float(first), float(third), third >= first,
                       known[-1].confirmed_index)


class TransitionStage(str, Enum):
    WATCHING = 'watching_frozen_key'
    SUSPICION = 'head_or_bottom_suspicion'
    KEY_BROKEN = 'key_broken_await_confirmed_countermove'
    ALTERNATION = 'countermove_below_67_percent'
    DEEP_COUNTERMOVE = 'countermove_not_below_67_percent'
    INVALIDATED = 'original_extreme_breached'


@dataclass(frozen=True)
class TrendTransition:
    asof_index: int
    direction: Direction
    stage: TransitionStage
    key: KeyLevel
    suspicion_index: int | None = None
    attack: AttackEvidence | None = None
    impulse_origin: ReversalPoint | None = None
    impulse_extreme: ReversalPoint | None = None
    counterturn: ReversalPoint | None = None
    retracement: RetracementEvidence | None = None
    invalidated_index: int | None = None
    alternation_confirmed_index: int | None = None
    rule_version: str = 'frozen_extreme_key_decimal_67_v1'

    @property
    def suspicion_name(self):
        return '底部疑虑' if self.direction == Direction.UP else '头部疑虑'

    @property
    def break_name(self):
        return '翻空为多' if self.direction == Direction.UP else '翻多为空'

    @property
    def formation_name(self):
        return ('底部成形' if self.direction == Direction.UP else '头部成形') if self.attack else None

    @property
    def alternation_name(self):
        return '空多交替' if self.direction == Direction.UP else '多空交替'


def observe_trend_transition(bars: Sequence[Bar], points: Sequence[ReversalPoint], *,
                             context: StructureContext, attack_basis: AttackBasis,
                             asof_index: int | None = None) -> TrendTransition:
    """One frozen bull/bear context -> break -> first confirmed retracement.

    Separate from local HH/HL comparisons. No automatic buy/sell, abc or N.
    Confirmed pivots need their real availability indices, not drawing dates.
    """
    if not isinstance(context, StructureContext) or context.trend not in (StructuralTrend.BULL, StructuralTrend.BEAR):
        raise ValueError('explicit previously bullish or bearish context required')
    if not isinstance(attack_basis, AttackBasis):
        raise ValueError('explicit AttackBasis required')
    end = len(bars)-1 if asof_index is None else asof_index
    _index(end, 'asof_index')
    if end >= len(bars) or end < context.asof_index:
        raise ValueError('available asof at or after frozen context required')
    up = context.trend == StructuralTrend.BEAR
    direction = Direction.UP if up else Direction.DOWN
    sign = 1 if up else -1
    key = context.last_fall_high if up else context.last_rise_low
    anchor = context.window_low if up else context.window_high
    if key is None or anchor is None:
        raise ValueError('known frozen extreme and preceding key required')
    known = _known_points(points, context.window_start, end)
    original = observe_structure(points, symbol=context.symbol, timeframe=context.timeframe,
                                 window_start=context.window_start, asof_index=context.asof_index)
    if original != context:
        raise ValueError('supplied points changed the frozen context')
    for i in range(end+1):
        _validate_bar(bars[i])
        if bars[i].symbol != context.symbol:
            raise ValueError('symbol mismatch')
        if i:
            _ordered_pair(bars[i-1], bars[i])
    for p in known:
        bar = bars[p.point.index]
        price = bar.high if p.point.kind == PointKind.HIGH else bar.low
        if p.point.price != price:
            raise ValueError('pivot price differs from source bar and adjustment basis')
    attack = None
    invalidated = None
    for i in range(context.asof_index+1, end+1):
        bar = bars[i]
        adverse = bar.low if up else bar.high
        if sign*(adverse-anchor.point.price) < 0:
            invalidated = i
            break
        if attack is None:
            # Cheap candidate screen; canonical primitive verifies the crossing
            # once. Avoid repeatedly revalidating every historical bar (O(n^2)).
            value = bar.close if attack_basis == AttackBasis.CLOSE else bar.high if up else bar.low
            if sign*(bars[i-1].close-key.price) <= 0 and sign*(value-key.price) > 0:
                evidence = observe_attack(bars, i, key, timeframe=context.timeframe)
                if evidence.qualifies(attack_basis):
                    attack = evidence
    # Suspicion requires the weak rebound/retest to have itself reversed, i.e.
    # the third pivot is CONFIRMED. Equality at the old extreme is a retest.
    suspicion = None
    start = known.index(anchor)
    for j in range(start+2, len(known)):
        a, b, c = known[j-2:j+1]
        if (a.point.kind == anchor.point.kind and c.point.kind == anchor.point.kind
                and sign*(c.point.price-a.point.price) >= 0
                and sign*(b.point.price-key.price) < 0
                and sign*(c.point.price-anchor.point.price) >= 0
                and c.confirmed_index > context.asof_index
                and (attack is None or c.confirmed_index < attack.bar_index)
                and (invalidated is None or c.confirmed_index < invalidated)):
            suspicion = c.confirmed_index
            break
    stage = TransitionStage.SUSPICION if suspicion is not None else TransitionStage.WATCHING
    origin = extreme = counter = ratio = None
    alternation_index = None
    if attack is not None:
        stage = TransitionStage.KEY_BROKEN
        extreme_kind = PointKind.HIGH if up else PointKind.LOW
        for j, p in enumerate(known):
            if (p.point.kind == extreme_kind and p.point.index >= attack.bar_index
                    and p.confirmed_index >= attack.bar_index and j > 0):
                origin, extreme = known[j-1], p
                # Do not merge sub-bar legs into a fictitious daily impulse.
                if not origin.point.index < attack.bar_index <= extreme.point.index:
                    origin = extreme = None
                    break
                if j+1 < len(known):
                    counter = known[j+1]
                    if counter.point.index <= extreme.point.index:
                        counter = None
                        break
                    ratio = retracement_evidence(origin.point.price, extreme.point.price,
                        counter.point.price, direction=direction)
                    if ratio.partial and ratio.below_67_percent:
                        stage = TransitionStage.ALTERNATION
                        alternation_index = counter.confirmed_index
                    else:
                        stage = TransitionStage.DEEP_COUNTERMOVE
                break
    if invalidated is not None:
        stage = TransitionStage.INVALIDATED
        # Preserve earlier confirmed facts, never manufacture one after failure.
        if alternation_index is not None and alternation_index >= invalidated:
            alternation_index = None
        if extreme is not None and extreme.confirmed_index >= invalidated:
            origin = extreme = counter = ratio = None
        elif counter is not None and counter.confirmed_index >= invalidated:
            counter = ratio = None
    return TrendTransition(end, direction, stage, key, suspicion, attack, origin,
        extreme, counter, ratio, invalidated, alternation_index)
