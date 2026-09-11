"""Positive/negative market turn: frozen context -> ratios -> fresh minor-line break."""
from dataclasses import dataclass
from enum import Enum
from fractions import Fraction
from typing import Sequence

from .model import Bar
from .polyline import PointKind, ReversalPoint
from .price_action import AttackBasis, Direction, KeyLevel, LevelKind, _index, observe_attack
from .market_regime import MarketRegime, RegimeObservation, RegimePhase
from .trend_structure import _known_points
from .wave_strength import (StrengthScale, WaveStrength, observe_wave_strength,
                            validate_prefix, validate_pivot_prices)


class TurnThreshold(str, Enum):
    TWO_THIRDS = 'exact_2_over_3'
    PERCENT_67 = 'literal_67_percent'

    @property
    def fraction(self):
        return Fraction(2, 3) if self == TurnThreshold.TWO_THIRDS else Fraction(67, 100)


@dataclass(frozen=True)
class TurnPolicy:
    strength_scale: StrengthScale
    first_threshold: TurnThreshold
    second_threshold: TurnThreshold
    key_basis: AttackBasis
    line_basis: AttackBasis

    def __post_init__(self):
        for v, cls in ((self.strength_scale, StrengthScale), (self.first_threshold, TurnThreshold),
                       (self.second_threshold, TurnThreshold), (self.key_basis, AttackBasis),
                       (self.line_basis, AttackBasis)):
            if not isinstance(v, cls):
                raise ValueError('explicit typed turn policies required')


@dataclass(frozen=True)
class RegimeContext:
    symbol: str
    timeframe: str
    regime: MarketRegime
    asof_index: int
    source: str

    def __post_init__(self):
        _index(self.asof_index, 'regime asof')
        if (not isinstance(self.regime, MarketRegime) or
                any(not isinstance(v, str) or not v.strip() for v in (self.symbol, self.timeframe, self.source))):
            raise ValueError('explicit regime and provenance required')


def context_from_regime(observation: RegimeObservation, *, symbol: str, timeframe: str) -> RegimeContext:
    """Explicit snapshot of historical last confirmation, never a live-position signal."""
    if (not isinstance(observation, RegimeObservation) or observation.latest is None or
            observation.latest.phase == RegimePhase.INVALIDATED or observation.latest.last_confirmed_regime is None):
        raise ValueError('non-invalidated regime observation with a confirmed event required')
    return RegimeContext(symbol, timeframe, observation.latest.last_confirmed_regime,
                         observation.asof_index, observation.rule_version + '_explicit_last_confirmed_snapshot')


@dataclass(frozen=True)
class MinorLine:
    symbol: str
    timeframe: str
    direction: Direction
    first: ReversalPoint
    second: ReversalPoint
    selected_at_index: int
    source: str

    def __post_init__(self):
        _index(self.selected_at_index, 'line selection index')
        if (not isinstance(self.direction, Direction) or
                not all(isinstance(p, ReversalPoint) for p in (self.first, self.second)) or
                any(not isinstance(v, str) or not v.strip() for v in (self.symbol, self.timeframe, self.source))):
            raise ValueError('typed line anchors and provenance required')
        kind = PointKind.HIGH if self.direction == Direction.UP else PointKind.LOW
        sign = 1 if self.direction == Direction.UP else -1
        if (self.first.point.kind != kind or self.second.point.kind != kind or
                self.first.point.index >= self.second.point.index or
                not self.first.confirmed_index <= self.second.confirmed_index <= self.selected_at_index or
                sign*(self.second.point.price-self.first.point.price) >= 0):
            raise ValueError('positive turn needs descending highs; negative turn needs ascending lows')

    def exact_value(self, index: int) -> Fraction:
        _index(index, 'line evaluation index')
        a, b = self.first.point, self.second.point
        return Fraction(str(a.price)) + (Fraction(str(b.price))-Fraction(str(a.price))) * Fraction(index-a.index, b.index-a.index)


def freeze_minor_line(points: Sequence[ReversalPoint], *, symbol: str, timeframe: str,
                      direction: Direction, selected_at_index: int, source: str) -> MinorLine | None:
    _index(selected_at_index, 'line selection index')
    if not isinstance(direction, Direction):
        raise ValueError('Direction required')
    known = _known_points(points, 0, selected_at_index)
    kind = PointKind.HIGH if direction == Direction.UP else PointKind.LOW
    matching = [p for p in known if p.point.kind == kind]
    if len(matching) < 2:
        return None
    # Use the LAST two, not an older pair selected for a favourable slope.
    return MinorLine(symbol, timeframe, direction, *matching[-2:], selected_at_index, source)


@dataclass(frozen=True)
class LineBreak:
    bar_index: int
    direction: Direction
    line_price: float
    previous_line_price: float
    observed_price: float
    basis: AttackBasis


def line_break_on_bar(bars: Sequence[Bar], line: MinorLine, index: int, *, basis: AttackBasis) -> LineBreak | None:
    """Both sides use time-varying line values, not a horizontal approximation."""
    if not isinstance(line, MinorLine) or not isinstance(basis, AttackBasis):
        raise ValueError('MinorLine and explicit AttackBasis required')
    validate_prefix(bars, symbol=line.symbol, end=index)
    if index <= line.selected_at_index:
        return None
    validate_pivot_prices(bars, (line.first, line.second), index)
    return _line_break_known(bars, line, index, basis=basis)


def _line_break_known(bars, line, index, *, basis):
    before, now = line.exact_value(index-1), line.exact_value(index)
    if min(before, now) <= 0:
        return None  # An extrapolated nonpositive stock price is not a valid line.
    up = line.direction == Direction.UP
    sign = 1 if up else -1
    bar = bars[index]
    price = bar.close if basis == AttackBasis.CLOSE else bar.high if up else bar.low
    if (sign*(Fraction(str(bars[index-1].close))-before) <= 0 and
            sign*(Fraction(str(price))-now) > 0):
        return LineBreak(index, line.direction, float(now), float(before), price, basis)
    return None


@dataclass(frozen=True)
class TurnSetup:
    symbol: str
    timeframe: str
    direction: Direction
    origin: ReversalPoint
    impulse_end: ReversalPoint
    first_counter: ReversalPoint
    second_counter: ReversalPoint
    background: RegimeContext
    trend_key: KeyLevel

    def __post_init__(self):
        points = (self.origin, self.impulse_end, self.first_counter, self.second_counter)
        if (not isinstance(self.direction, Direction) or not all(isinstance(p, ReversalPoint) for p in points)
                or not isinstance(self.background, RegimeContext) or not isinstance(self.trend_key, KeyLevel)):
            raise ValueError('typed four-pivot turn context required')
        up = self.direction == Direction.UP
        kinds = (PointKind.HIGH, PointKind.LOW, PointKind.HIGH, PointKind.LOW) if up else (
            PointKind.LOW, PointKind.HIGH, PointKind.LOW, PointKind.HIGH)
        if tuple(p.point.kind for p in points) != kinds:
            raise ValueError('four alternating pivot kinds differ from turn direction')
        if any(a.point.index >= b.point.index or a.confirmed_index > b.confirmed_index for a, b in zip(points, points[1:])):
            raise ValueError('strict chronological source bars and ordered confirmations required')
        if self.impulse_end.confirmed_index >= self.first_counter.point.index:
            raise ValueError('original wave must be known before the first counter extreme')
        bearish = {MarketRegime.BEAR, MarketRegime.STRONG_BEAR, MarketRegime.GRIND_DOWN}
        bullish = {MarketRegime.BULL, MarketRegime.STRONG_BULL, MarketRegime.GRIND_UP}
        if self.background.regime not in (bearish if up else bullish):
            raise ValueError('regime background is inconsistent with requested turn')
        for obj in (self.background, self.trend_key):
            if (obj.symbol, obj.timeframe) != (self.symbol, self.timeframe):
                raise ValueError('context symbol/timeframe mismatch')
        if (self.background.asof_index > self.impulse_end.confirmed_index or
                self.trend_key.confirmed_index > self.impulse_end.confirmed_index or
                self.trend_key.kind != (LevelKind.RESISTANCE if up else LevelKind.SUPPORT)):
            raise ValueError('matching last-fall-high / last-rise-low and background must be known before counterwave')


class TurnStage(str, Enum):
    AWAIT_FIRST = 'await_confirmed_first_counterwave'
    NO_SUSPICION = 'first_counter_does_not_trigger_suspicion'
    SUSPICION = 'turn_suspicion'
    WAIT_LINE = 'ratio_combination_known_await_fresh_line_break'
    BOUNDARY = 'second_counter_threshold_equality_unresolved'
    SECOND_TOO_STRONG = 'second_counter_not_below_threshold'
    CONFIRMED = 'market_turn_confirmed'
    COUNTER_EXTENDED = 'second_counter_extended_new_pivot_required'
    MAJOR_FLIP = 'positive_early_turn_preempted_by_last_fall_high_break'


@dataclass(frozen=True)
class TurnObservation:
    asof_index: int
    direction: Direction
    policy: TurnPolicy
    stage: TurnStage
    first_strength: WaveStrength | None
    second_strength: WaveStrength | None
    suspicion_index: int | None
    suspicion_reason: str | None
    key_break_index: int | None
    combination_index: int | None
    line_break: LineBreak | None
    invalidated_index: int | None
    opens_short_position: bool = False


def observe_market_turn(bars: Sequence[Bar], setup: TurnSetup, *, policy: TurnPolicy,
                        minor_line: MinorLine | None, asof_index: int | None = None) -> TurnObservation:
    if not isinstance(setup, TurnSetup) or not isinstance(policy, TurnPolicy):
        raise ValueError('TurnSetup and TurnPolicy required')
    if minor_line is not None and (not isinstance(minor_line, MinorLine) or
            (minor_line.symbol, minor_line.timeframe, minor_line.direction) !=
            (setup.symbol, setup.timeframe, setup.direction)):
        raise ValueError('minor line must share indexed bars and direction; map lower timeframe explicitly')
    end = len(bars)-1 if asof_index is None else asof_index
    validate_prefix(bars, symbol=setup.symbol, end=end)
    pts = (setup.origin, setup.impulse_end, setup.first_counter, setup.second_counter)
    validate_pivot_prices(bars, pts, end)
    first = observe_wave_strength(bars, pts[:3], symbol=setup.symbol,
                                  scale=policy.strength_scale, asof_index=end)
    second = observe_wave_strength(bars, pts[1:], symbol=setup.symbol,
                                   scale=policy.strength_scale, asof_index=end)
    if minor_line is not None:
        validate_pivot_prices(bars, (minor_line.first, minor_line.second), end)
    up = setup.direction == Direction.UP
    sign = 1 if up else -1
    if (end >= setup.impulse_end.confirmed_index and
            sign*(bars[setup.impulse_end.confirmed_index].close-setup.trend_key.price) > 0):
        raise ValueError('wave context already beyond major key; provide an earlier context')
    key_break = None
    for i in range(setup.impulse_end.confirmed_index+1, end+1):
        bar = bars[i]
        value = bar.close if policy.key_basis == AttackBasis.CLOSE else bar.high if up else bar.low
        key = setup.trend_key.price
        if sign*(bars[i-1].close-key) <= 0 and sign*(value-key) > 0:
            if observe_attack(bars, i, setup.trend_key, timeframe=setup.timeframe).qualifies(policy.key_basis):
                key_break = i
                break
    suspicion = None
    reason = None
    if first is not None and Fraction(first.exact_ratio) > policy.first_threshold.fraction:
        suspicion, reason = setup.first_counter.confirmed_index, 'first_counter_exceeds_threshold'
    if not up and key_break is not None and (suspicion is None or key_break <= suspicion):
        suspicion, reason = key_break, 'last_rise_low_broken'
    stage = TurnStage.AWAIT_FIRST if first is None else TurnStage.NO_SUSPICION
    combination = event = invalidated = None
    if suspicion is not None:
        stage = TurnStage.SUSPICION
        if second is not None:
            ratio = Fraction(second.exact_ratio)
            if ratio == policy.second_threshold.fraction:
                stage = TurnStage.BOUNDARY
            elif 0 < ratio < policy.second_threshold.fraction:
                combination = max(suspicion, setup.second_counter.confirmed_index)
                stage = TurnStage.WAIT_LINE
            else:
                stage = TurnStage.SECOND_TOO_STRONG
    if combination is not None:
        for i in range(combination+1, end+1):
            b = bars[i]
            if sign*((b.low if up else b.high)-setup.second_counter.point.price) < 0:
                invalidated = i
                stage = TurnStage.COUNTER_EXTENDED
                break
            # The ratio combination and the line must BOTH be known before the
            # crossing bar; already-beyond occupancy is not a fresh attack.
            if event is None and minor_line is not None and i > minor_line.selected_at_index:
                event = _line_break_known(bars, minor_line, i, basis=policy.line_basis)
                if event is not None:
                    stage = TurnStage.CONFIRMED
    # Positive turns are an early warning before major last-fall-high breaks.
    # Negative turns deliberately retain the lecture's explicit key-break OR.
    if up and key_break is not None and (event is None or key_break <= event.bar_index):
        stage = TurnStage.MAJOR_FLIP
        event = None
    return TurnObservation(end, setup.direction, policy, stage, first, second, suspicion,
                           reason, key_break, combination, event, invalidated)
