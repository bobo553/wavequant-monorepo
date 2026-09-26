"""Domain primitives for breaks, key levels, and attack/response/confirmation order.

No N-shape recognition, regime, volume filter, targets or trading decisions live
here. Callers select structural levels and explicitly select the attack basis.
All observations refer to closed bars. Third-bar facts are not an implicit rule
for declaring resistance successful/failed or a trend reversed.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import math
from typing import Sequence

from ..models.model import Bar


class LevelKind(str, Enum):
    RESISTANCE = 'resistance'
    SUPPORT = 'support'


class Direction(str, Enum):
    UP = 'up'
    DOWN = 'down'


class AttackBasis(str, Enum):
    INTRABAR = 'intrabar'
    CLOSE = 'close'


class Phase(str, Enum):
    NO_ATTACK = 'no_attack'
    AWAIT_RESPONSE = 'await_response'
    AWAIT_CONFIRMATION = 'await_confirmation'
    CONFIRMATION_OBSERVED = 'confirmation_observed'


def _positive(value: float, name: str) -> None:
    if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
        raise ValueError(f'{name} must be finite and positive')


def _index(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f'{name} must be a non-negative integer')


@dataclass(frozen=True)
class KeyLevel:
    """A caller-supplied key high / last rising-origin low, known before attack.

    Source and confirmation indices refer to the same ordered bar series. Prices
    must share its adjustment basis. Provenance is explicit, never auto-inferred.
    """
    symbol: str
    timeframe: str
    kind: LevelKind
    price: float
    source_index: int
    confirmed_index: int
    source: str

    def __post_init__(self):
        _positive(self.price, 'key level')
        _index(self.source_index, 'source_index')
        _index(self.confirmed_index, 'confirmed_index')
        if self.source_index > self.confirmed_index:
            raise ValueError('level cannot be confirmed before its source')
        if not isinstance(self.kind, LevelKind):
            raise ValueError('kind must be LevelKind')
        for name in ('symbol', 'timeframe', 'source'):
            if not isinstance(getattr(self,name), str) or not getattr(self,name).strip():
                raise ValueError(f'{name} must be a non-empty string')


@dataclass(frozen=True)
class ShadowPolicy:
    """Explicit engineering threshold; the lecture supplies no default number."""
    minimum_range_fraction: float

    def __post_init__(self):
        _positive(self.minimum_range_fraction, 'shadow threshold')
        if self.minimum_range_fraction > 1:
            raise ValueError('shadow threshold must not exceed 1')


@dataclass(frozen=True)
class AttackEvidence:
    direction: Direction
    level: KeyLevel
    bar_index: int
    observed_at: datetime
    previous_close: float
    extreme_beyond: bool
    close_beyond: bool
    touched: bool
    intrabar_crossed: bool
    close_crossed: bool
    gap_across: bool

    def qualifies(self, basis: AttackBasis) -> bool:
        if not isinstance(basis, AttackBasis):
            raise ValueError('basis must be AttackBasis')
        return self.intrabar_crossed if basis == AttackBasis.INTRABAR else self.close_crossed


@dataclass(frozen=True)
class ResistanceEvidence:
    attack_direction: Direction
    opposing_side: str
    observed_at: datetime
    higher_open: bool
    lower_open: bool
    bullish_body: bool
    bearish_body: bool
    opposing_open_and_body: bool
    direct_opposing_open: bool
    shadow_length: float
    shadow_range_fraction: float | None
    long_shadow: bool | None
    detected: bool | None
    reasons: tuple[str, ...]
    shadow_policy: ShadowPolicy | None


@dataclass(frozen=True)
class ConfirmationEvidence:
    """Direction-normalized third-bar observations, not outcome labels."""
    bar_index: int
    observed_at: datetime
    close_beyond_key: bool
    close_back_across_key: bool
    extreme_beyond_attack: bool
    close_beyond_attack_extreme: bool
    extreme_beyond_response: bool
    close_beyond_response_extreme: bool
    close_progress_from_response: bool
    resistance_outcome: str = 'undefined_rule'


@dataclass(frozen=True)
class AttackSequence:
    phase: Phase
    attack_basis: AttackBasis
    attack: AttackEvidence
    response: ResistanceEvidence | None
    confirmation: ConfirmationEvidence | None


def _validate_bar(bar: Bar) -> None:
    if not isinstance(bar.timestamp, datetime) or not isinstance(bar.symbol, str) or not bar.symbol:
        raise ValueError('bar needs timestamp and symbol')
    for name in ('open','high','low','close'):
        _positive(getattr(bar,name), name)
    if bar.low > min(bar.open,bar.close) or bar.high < max(bar.open,bar.close):
        raise ValueError('inconsistent OHLC')


def _ordered_pair(previous: Bar, current: Bar) -> None:
    _validate_bar(previous)
    _validate_bar(current)
    if previous.symbol != current.symbol:
        raise ValueError('bars must belong to the same symbol')
    if (previous.timestamp.tzinfo is None) != (current.timestamp.tzinfo is None):
        raise ValueError('mixed timezone awareness')
    if current.timestamp <= previous.timestamp:
        raise ValueError('bars must be strictly chronological')


def observe_attack(bars: Sequence[Bar], attack_index: int, level: KeyLevel,
                   *, timeframe: str) -> AttackEvidence:
    """Crossing uses previous close as the known starting side, including gaps.

    Already closing beyond a level is continued occupancy, not a fresh attack.
    A return to the original side permits a later re-attack. Equality is a touch,
    never a strict crossing. Both intrabar and close evidence are always returned.
    """
    _index(attack_index, 'attack_index')
    if attack_index < 1 or attack_index >= len(bars):
        raise ValueError('attack requires a previous bar and an available current bar')
    if not isinstance(level, KeyLevel) or timeframe != level.timeframe:
        raise ValueError('a KeyLevel with matching timeframe is required')
    if level.confirmed_index >= attack_index:
        raise ValueError('key level must be confirmed before the attack bar')
    # ValidatedBars certifies its immutable prefix once. Ordinary sequences
    # still receive the full consumed-prefix check on every standalone call.
    from ..market_state.wave_strength import validate_prefix
    try:
        validate_prefix(bars, symbol=level.symbol, end=attack_index)
    except ValueError as exc:
        if str(exc) == 'symbol mismatch':
            raise ValueError('key level symbol mismatch') from exc
        raise
    prev, current = bars[attack_index-1], bars[attack_index]
    up = level.kind == LevelKind.RESISTANCE
    sign = 1 if up else -1
    extreme = current.high if up else current.low
    starting_side = sign*(prev.close-level.price) <= 0
    extreme_beyond = sign*(extreme-level.price) > 0
    close_beyond = sign*(current.close-level.price) > 0
    return AttackEvidence(Direction.UP if up else Direction.DOWN, level, attack_index,
                          current.timestamp, prev.close, extreme_beyond, close_beyond,
                          current.low <= level.price <= current.high,
                          starting_side and extreme_beyond, starting_side and close_beyond,
                          starting_side and sign*(current.open-level.price) > 0)


def observe_resistance(previous: Bar, current: Bar, *, attack_direction: Direction,
                       shadow_policy: ShadowPolicy | None = None) -> ResistanceEvidence:
    """Recognize the three listed examples, preserving unquantified shadow cases.

    An arbitrary bearish/bullish body alone is not silently expanded into the
    lecture's 'higher-open bearish / lower-open bullish' example. False means no
    listed pattern detected under the policy, not proof of absent market forces.
    """
    _ordered_pair(previous,current)
    if not isinstance(attack_direction, Direction):
        raise ValueError('attack_direction must be Direction')
    if shadow_policy is not None and not isinstance(shadow_policy, ShadowPolicy):
        raise ValueError('invalid shadow policy')
    up = attack_direction == Direction.UP
    higher, lower = current.open > previous.close, current.open < previous.close
    bullish, bearish = current.close > current.open, current.close < current.open
    open_body = higher and bearish if up else lower and bullish
    opposing_open = lower if up else higher
    shadow = current.high-max(current.open,current.close) if up else min(current.open,current.close)-current.low
    span = current.high-current.low
    fraction = shadow/span if span else None
    long_shadow = (False if not shadow else None if shadow_policy is None else
                   fraction >= shadow_policy.minimum_range_fraction)
    reasons = []
    if open_body:
        reasons.append('higher_open_bearish_body' if up else 'lower_open_bullish_body')
    if opposing_open:
        reasons.append('direct_lower_open' if up else 'direct_higher_open')
    if long_shadow:
        reasons.append('long_upper_shadow' if up else 'long_lower_shadow')
    detected = True if reasons else None if long_shadow is None else False
    return ResistanceEvidence(attack_direction, 'bears' if up else 'bulls', current.timestamp,
                              higher, lower, bullish, bearish, open_body, opposing_open,
                              shadow, fraction, long_shadow, detected, tuple(reasons), shadow_policy)


def observe_sequence(bars: Sequence[Bar], attack_index: int, level: KeyLevel, *,
                     timeframe: str, attack_basis: AttackBasis,
                     shadow_policy: ShadowPolicy | None = None) -> AttackSequence:
    """Consume exactly t, t+1, t+2; later bars cannot rewrite this episode.

    Adjacent indices mean adjacent available bars of this symbol/timeframe, not
    consecutive calendar dates. Missing sessions need upstream data-quality checks.
    The key level is frozen in an immutable object for the whole episode.
    """
    attack = observe_attack(bars,attack_index,level,timeframe=timeframe)
    if shadow_policy is not None and not isinstance(shadow_policy, ShadowPolicy):
        raise ValueError('invalid shadow policy')
    if not attack.qualifies(attack_basis):
        return AttackSequence(Phase.NO_ATTACK,attack_basis,attack,None,None)
    if len(bars) <= attack_index+1:
        return AttackSequence(Phase.AWAIT_RESPONSE,attack_basis,attack,None,None)
    response = observe_resistance(bars[attack_index],bars[attack_index+1],
                                  attack_direction=attack.direction,shadow_policy=shadow_policy)
    if len(bars) <= attack_index+2:
        return AttackSequence(Phase.AWAIT_CONFIRMATION,attack_basis,attack,response,None)
    a,r,c = bars[attack_index:attack_index+3]
    _ordered_pair(r,c)
    up = attack.direction == Direction.UP
    sign = 1 if up else -1
    ae,re,ce = (b.high if up else b.low for b in (a,r,c))
    confirmation = ConfirmationEvidence(attack_index+2,c.timestamp,
        sign*(c.close-level.price) > 0, sign*(c.close-level.price) < 0,
        sign*(ce-ae) > 0, sign*(c.close-ae) > 0,
        sign*(ce-re) > 0, sign*(c.close-re) > 0, sign*(c.close-r.close) > 0)
    return AttackSequence(Phase.CONFIRMATION_OBSERVED,attack_basis,attack,response,confirmation)
