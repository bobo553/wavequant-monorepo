"""Evaluate the strict figure-009 price template and its mirror."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Sequence

from ..models.model import Bar
from ..market_structure.n_shape import MilestoneBasis, NSetup, NStatus, observe_n
from ..market_structure.price_action import Direction


class WashoutStage(str, Enum):
    AWAIT_TARGET = 'await_open_1p_2t_band'
    AWAIT_PULLBACK = 'await_later_neckline_1p_countermove'
    AWAIT_REATTACK = 'await_independent_new_n'
    CONFIRMED = 'price_template_completed'
    OUTSIDE_TEMPLATE = 'outside_strict_figure_009_template'
    INVALIDATED = 'original_n_origin_breached'
    UNAVAILABLE = 'positive_price_targets_unavailable'


@dataclass(frozen=True)
class WashoutPolicy:
    zone_basis: MilestoneBasis

    def __post_init__(self):
        if not isinstance(self.zone_basis, MilestoneBasis):
            raise ValueError('explicit zone basis required')


@dataclass(frozen=True)
class WashoutFrame:
    bar_index: int
    observed_at: datetime
    stage: WashoutStage
    reason: str
    first_target_band_index: int | None
    first_pullback_band_index: int | None
    reattack_index: int | None
    new_n_defense: float | None


@dataclass(frozen=True)
class WashoutObservation:
    asof_index: int
    n_status: NStatus
    direction: Direction
    policy: WashoutPolicy
    initial_attack_index: int | None = None
    neckline: float | None = None
    one_p: float | None = None
    two_t: float | None = None
    frames: tuple[WashoutFrame, ...] = ()
    rule_version: str = 'figure009_strict_open_bands_separate_bars_v1'
    intent: str = 'unknown_price_pattern_only'
    probability: None = None
    opens_short_position: bool = False

    @property
    def latest(self):
        return self.frames[-1] if self.frames else None

    @property
    def review_role(self):
        return 'long_setup_review_not_order' if self.direction == Direction.UP else 'long_risk_review_not_short_order'


def observe_washout(bars: Sequence[Bar], initial_setup: NSetup, *, timeframe: str,
                    policy: WashoutPolicy, reattack_setup: NSetup | None = None,
                    asof_index: int | None = None) -> WashoutObservation:
    """One fixed initial N + at most one caller-selected independent second N.

    Bands are open intervals. Formation-bar observations use CLOSE only; later
    bands use explicit basis. Failure barriers always use extrema after formation.
    Completion is an historical event, never a permanent entry permission.
    """
    if not isinstance(policy, WashoutPolicy):
        raise ValueError('explicit WashoutPolicy required')
    if reattack_setup is not None:
        if (not isinstance(reattack_setup, NSetup) or
                not isinstance(initial_setup, NSetup) or
                (reattack_setup.symbol, reattack_setup.timeframe, reattack_setup.direction) !=
                (initial_setup.symbol, initial_setup.timeframe, initial_setup.direction)):
            raise ValueError('second N must match initial symbol, timeframe and direction')
        if reattack_setup.origin.index <= initial_setup.pullback.index:
            raise ValueError('independent second N required, not replay of original setup')
    n = observe_n(bars, initial_setup, timeframe=timeframe,
                  milestone_basis=policy.zone_basis, asof_index=asof_index)
    if n.completion is None:
        return WashoutObservation(n.asof_index, n.status, initial_setup.direction, policy)
    t = n.completion.bar_index
    neckline, p, tt = n.anchors.neckline_extreme, n.targets.one_p, n.targets.two_t
    up = initial_setup.direction == Direction.UP
    sign = 1 if up else -1
    stage = WashoutStage.AWAIT_TARGET if p is not None and tt is not None else WashoutStage.UNAVAILABLE
    reason = 'waiting_target_band' if stage == WashoutStage.AWAIT_TARGET else 'nonpositive_price_projection'
    target_index = pullback_index = reattack_index = new_defense = None
    second = None
    frames = []
    terminal = {WashoutStage.OUTSIDE_TEMPLATE, WashoutStage.INVALIDATED,
                WashoutStage.CONFIRMED, WashoutStage.UNAVAILABLE}
    for i in range(t, n.asof_index+1):
        bar = bars[i]
        if stage not in terminal:
            formation = i == t
            forward = bar.close if formation else bar.high if up else bar.low
            adverse = bar.close if formation else bar.low if up else bar.high
            value = bar.close if formation or policy.zone_basis == MilestoneBasis.CLOSE else forward
            countervalue = bar.close if policy.zone_basis == MilestoneBasis.CLOSE else adverse
            if not formation and sign*(adverse-n.anchors.origin) < 0:
                stage, reason = WashoutStage.INVALIDATED, 'original_wave_boundary_breached'
            elif sign*(forward-tt) >= 0:
                stage, reason = WashoutStage.OUTSIDE_TEMPLATE, 'two_t_reached_or_overshot'
            elif target_index is not None and i > target_index and sign*(adverse-neckline) < 0:
                stage, reason = WashoutStage.OUTSIDE_TEMPLATE, 'neckline_lost_after_target_band'
            elif stage == WashoutStage.AWAIT_TARGET:
                if sign*(value-p) > 0 and sign*(tt-value) > 0:
                    if not formation and sign*(adverse-neckline) < 0:
                        stage, reason = WashoutStage.OUTSIDE_TEMPLATE, 'target_and_neckline_breach_order_unknown'
                    else:
                        target_index = i
                        stage, reason = WashoutStage.AWAIT_PULLBACK, 'target_band_observed'
            elif stage == WashoutStage.AWAIT_PULLBACK:
                if i > target_index and sign*(countervalue-neckline) > 0 and sign*(p-countervalue) > 0:
                    pullback_index = i
                    stage, reason = WashoutStage.AWAIT_REATTACK, 'later_countermove_band_observed'
            elif stage == WashoutStage.AWAIT_REATTACK and reattack_setup is not None:
                # Entire second A-B-C must start in this observed pullback, not
                # an old swing relabelled as another attack. Evaluate only once
                # its pivot confirmations are visible; unknown prices stay hidden.
                if reattack_setup.pullback.confirmed_index < i:
                    if reattack_setup.origin.index < pullback_index:
                        raise ValueError('second N origin must belong to the observed pullback or later')
                    if second is None:
                        second = observe_n(bars, reattack_setup, timeframe=timeframe,
                            milestone_basis=MilestoneBasis.CLOSE, asof_index=n.asof_index)
                    if second.completion is not None and second.completion.bar_index == i:
                        reattack_index = i
                        new_defense = second.completion.defense
                        stage, reason = WashoutStage.CONFIRMED, 'independent_same_direction_n_completed'
                    elif second.invalidated_index is not None and second.invalidated_index <= i:
                        reason = 'second_n_' + second.status.value
        frames.append(WashoutFrame(i, bar.timestamp, stage, reason, target_index,
                                   pullback_index, reattack_index, new_defense))
    return WashoutObservation(n.asof_index, n.status, initial_setup.direction, policy,
                              t, neckline, p, tt, tuple(frames))
