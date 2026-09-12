"""Observe N attack candles, volume, and frozen defense without intent inference."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math
from typing import Sequence

from ..models.model import Bar
from ..market_structure.n_shape import NCompletion, NSetup, NStatus, MilestoneBasis, observe_n
from ..market_structure.price_action import (Direction, KeyLevel, LevelKind, ResistanceEvidence,
                           ShadowPolicy, observe_resistance)


@dataclass(frozen=True)
class AttackVolume:
    lookback: int
    available_history: int
    attack_volume: float
    previous_volume: float
    increased_from_previous: bool
    trailing_mean: float | None
    relative_volume: float | None
    increased_from_mean: bool | None


@dataclass(frozen=True)
class DefenseFrame:
    bar_index: int
    observed_at: datetime
    intrabar_beyond: bool
    close_beyond: bool
    first_intrabar_breach_index: int | None
    first_close_breach_index: int | None
    recovered_on_close: bool


@dataclass(frozen=True)
class ControlBarObservation:
    asof_index: int
    n_status: NStatus
    direction: Direction
    completion: NCompletion | None = None
    defense_level: KeyLevel | None = None
    volume: AttackVolume | None = None
    next_bar_resistance: ResistanceEvidence | None = None
    defense_frames: tuple[DefenseFrame, ...] = ()
    cost_basis: str = 'not_identifiable_from_ohlcv'
    participant_intent: str = 'unknown_not_inferred'


def observe_control_bar(bars: Sequence[Bar], setup: NSetup, *, timeframe: str,
                        volume_lookback: int, shadow_policy: ShadowPolicy | None,
                        asof_index: int | None = None) -> ControlBarObservation:
    if type(volume_lookback) is not int or volume_lookback < 1:
        raise ValueError('positive integer volume_lookback required')
    if shadow_policy is not None and not isinstance(shadow_policy, ShadowPolicy):
        raise ValueError('explicit ShadowPolicy or None required')
    n = observe_n(bars, setup, timeframe=timeframe, milestone_basis=MilestoneBasis.CLOSE,
                  asof_index=asof_index)
    if n.completion is None:
        return ControlBarObservation(n.asof_index, n.status, setup.direction)
    t = n.completion.bar_index
    history = bars[max(0, t-volume_lookback):t]
    for b in (*history, bars[t]):
        if type(b.volume) not in (int, float) or not math.isfinite(b.volume) or b.volume < 0:
            raise ValueError('finite nonnegative comparable volume required')
    mean = math.fsum(b.volume/volume_lookback for b in history) if len(history) == volume_lookback else None
    ratio = bars[t].volume/mean if mean is not None and mean > 0 else None
    if ratio is not None and not math.isfinite(ratio):
        raise ValueError('relative volume overflow')
    volume = AttackVolume(volume_lookback, len(history), bars[t].volume, bars[t-1].volume,
        bars[t].volume > bars[t-1].volume, mean, ratio,
        bars[t].volume > mean if mean is not None else None)
    up = setup.direction == Direction.UP
    sign = 1 if up else -1
    level = KeyLevel(setup.symbol, timeframe, LevelKind.SUPPORT if up else LevelKind.RESISTANCE,
        n.completion.defense, t, t, 'completed_n_virtual_defense')
    response = (observe_resistance(bars[t], bars[t+1], attack_direction=setup.direction,
                                   shadow_policy=shadow_policy) if t+1 <= n.asof_index else None)
    first_extreme = first_close = None
    frames = []
    for i in range(t+1, n.asof_index+1):
        b = bars[i]
        intrabar = sign*((b.low if up else b.high)-level.price) < 0
        close = sign*(b.close-level.price) < 0
        if intrabar and first_extreme is None:
            first_extreme = i
        if close and first_close is None:
            first_close = i
        recovered = (first_close is not None and i > first_close and
                     sign*(bars[i-1].close-level.price) < 0 and sign*(b.close-level.price) >= 0)
        frames.append(DefenseFrame(i, b.timestamp, intrabar, close, first_extreme, first_close, recovered))
    return ControlBarObservation(n.asof_index, n.status, setup.direction, n.completion, level,
                                  volume, response, tuple(frames))
