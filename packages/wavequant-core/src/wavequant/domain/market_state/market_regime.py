"""Classify six regimes for one causally completed N episode.

Local resistance success is explicitly proxied by breach of the frozen N
defense; renewed continuation requires a close beyond ALL prior episode extrema.
These are engineering conventions, not fully specified rules in the lecture.
See docs/market_regime_contract.md for timing, uncertainty and limitations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from ..models.model import Bar
from ..market_structure.n_shape import MilestoneBasis, NSetup, NStatus, observe_n
from ..market_structure.price_action import Direction, ResistanceEvidence, ShadowPolicy, observe_resistance


class MarketRegime(str, Enum):
    STRONG_BULL = '强轧空'
    BULL = '轧空'
    GRIND_UP = '盘坚'
    GRIND_DOWN = '盘跌'
    BEAR = '追杀'
    STRONG_BEAR = '强追杀'


class RegimePhase(str, Enum):
    AWAIT_RESPONSE = 'await_response'
    AWAIT_CONFIRMATION = 'await_confirmation'
    PENDING = 'pending'
    CONFIRMED = 'confirmed_on_this_bar'
    INVALIDATED = 'wave_boundary_breached'


class ResistanceOutcome(str, Enum):
    NOT_OBSERVED = 'not_yet_observed'
    UNKNOWN = 'unknown_shadow_rule'
    ABSENT = 'no_listed_resistance_detected'
    PENDING = 'resistance_outcome_pending'
    FAILED = 'failed_to_break_n_defense_and_continuation_confirmed'
    SUCCEEDED_LOCALLY = 'n_defense_breached_wave_boundary_held'
    STRUCTURE_FAILED = 'wave_boundary_breached'


class WaveBoundary(str, Enum):
    ORIGIN = 'n_origin'
    PULLBACK = 'n_pullback'


@dataclass(frozen=True)
class RegimePolicy:
    # Both arguments mandatory: None preserves unquantified shadow evidence.
    shadow_policy: ShadowPolicy | None
    wave_boundary: WaveBoundary

    def __post_init__(self):
        if self.shadow_policy is not None and not isinstance(self.shadow_policy, ShadowPolicy):
            raise ValueError('shadow_policy must be ShadowPolicy or None')
        if not isinstance(self.wave_boundary, WaveBoundary):
            raise ValueError('wave_boundary must be explicitly selected')


@dataclass(frozen=True)
class RegimeFrame:
    bar_index: int
    observed_at: datetime
    phase: RegimePhase
    regime: MarketRegime | None
    resistance: ResistanceEvidence | None
    resistance_outcome: ResistanceOutcome
    first_resistance_index: int | None
    unknown_resistance_seen: bool
    first_defense_breach_index: int | None
    first_wave_breach_index: int | None
    continuation_level: float
    close_continuation: bool
    rolling_virtual_defense: float
    rolling_defense_held: bool | None
    uninterrupted_progress: bool
    last_confirmed_regime: MarketRegime | None
    last_confirmed_index: int | None


@dataclass(frozen=True)
class RegimeObservation:
    asof_index: int
    n_status: NStatus
    policy: RegimePolicy
    attack_index: int | None = None
    n_defense: float | None = None
    wave_boundary: float | None = None
    frames: tuple[RegimeFrame, ...] = ()
    rule_version: str = 'six_regimes_v1_close_record_defense_breach'

    @property
    def latest(self) -> RegimeFrame | None:
        return self.frames[-1] if self.frames else None


def observe_market_regime(bars: Sequence[Bar], setup: NSetup, *, timeframe: str,
                          policy: RegimePolicy,
                          asof_index: int | None = None) -> RegimeObservation:
    """Return immutable attack-to-asof frames using closed bars only.

    A confirmation is an event, not a label carried blindly onto later bars.
    The last confirmed event is returned separately. Once the wave boundary
    fails this episode cannot revive; the caller must supply a new N setup.
    """
    if not isinstance(policy, RegimePolicy):
        raise ValueError('explicit RegimePolicy required')
    n = observe_n(bars, setup, timeframe=timeframe,
                  milestone_basis=MilestoneBasis.CLOSE, asof_index=asof_index)
    if n.completion is None:
        return RegimeObservation(n.asof_index, n.status, policy)
    attack = n.completion.bar_index
    defense = n.completion.defense
    boundary = (n.anchors.origin if policy.wave_boundary == WaveBoundary.ORIGIN
                else n.anchors.pullback)
    up = setup.direction == Direction.UP
    sign = 1 if up else -1
    forward = lambda bar: bar.high if up else bar.low
    adverse = lambda bar: bar.low if up else bar.high
    virtual = lambda bar, prev: min(bar.low, prev.close) if up else max(bar.high, prev.close)
    record = forward(bars[attack])
    first_resistance = first_defense = first_wave = None
    unknown = False
    uninterrupted = True
    rolling_all_held = True
    last_regime = last_index = None
    frames = [RegimeFrame(
        attack, bars[attack].timestamp, RegimePhase.AWAIT_RESPONSE, None, None,
        ResistanceOutcome.NOT_OBSERVED, None, False, None, None,
        record, False, defense, None, True, None, None)]
    for i in range(attack + 1, n.asof_index + 1):
        bar, prev = bars[i], bars[i - 1]
        resistance = observe_resistance(prev, bar, attack_direction=setup.direction,
                                         shadow_policy=policy.shadow_policy)
        if resistance.detected is True and first_resistance is None:
            first_resistance = i
        unknown |= resistance.detected is None
        if first_defense is None and sign * (adverse(bar) - defense) < 0:
            first_defense = i
        if first_wave is None and sign * (adverse(bar) - boundary) < 0:
            first_wave = i
        rolling = virtual(prev, bars[i - 2])
        rolling_held = sign * (adverse(bar) - rolling) >= 0
        rolling_all_held &= rolling_held
        uninterrupted &= sign * (bar.close - prev.close) > 0
        continuation = sign * (bar.close - record) > 0
        phase = RegimePhase.AWAIT_CONFIRMATION if i == attack + 1 else RegimePhase.PENDING
        regime = None
        outcome = (ResistanceOutcome.PENDING if first_resistance is not None else
                   ResistanceOutcome.UNKNOWN if unknown else ResistanceOutcome.ABSENT)
        if first_wave is not None:
            phase = RegimePhase.INVALIDATED
            outcome = ResistanceOutcome.STRUCTURE_FAILED
        else:
            if first_resistance is not None and first_defense is not None:
                outcome = ResistanceOutcome.SUCCEEDED_LOCALLY
            if i >= attack + 2 and continuation:
                if (first_resistance is None and not unknown and first_defense is None
                        and uninterrupted and rolling_all_held):
                    regime = MarketRegime.STRONG_BULL if up else MarketRegime.STRONG_BEAR
                # Same-bar resistance + continuation cannot establish their order.
                elif first_resistance is not None and first_resistance < i:
                    if first_defense is None:
                        regime = MarketRegime.BULL if up else MarketRegime.BEAR
                        outcome = ResistanceOutcome.FAILED
                    elif first_defense < i:
                        regime = MarketRegime.GRIND_UP if up else MarketRegime.GRIND_DOWN
                if regime is not None:
                    phase = RegimePhase.CONFIRMED
                    last_regime, last_index = regime, i
        frames.append(RegimeFrame(
            i, bar.timestamp, phase, regime, resistance, outcome, first_resistance,
            unknown, first_defense, first_wave, record, continuation, rolling,
            rolling_held, uninterrupted, last_regime, last_index))
        if sign * (forward(bar) - record) > 0:
            record = forward(bar)
    return RegimeObservation(n.asof_index, n.status, policy, attack, defense,
                             boundary, tuple(frames))
