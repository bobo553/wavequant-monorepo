"""Causal, independent positive-N cycles after their frozen squeeze low fails."""

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite

from ..models.model import Bar
from .n_shape import (
    BoxAnchorMode, MilestoneBasis, NObservation, NSetup, PivotRef, observe_n,
)
from .price_action import Direction


@dataclass(frozen=True)
class PositiveNSeed:
    origin: int
    attack: int
    known_at: int
    defense: float
    level: int = 0


@dataclass(frozen=True)
class ReformedPositiveN:
    setup: NSetup
    observation: NObservation
    previous_attack: int
    root_attack: int
    level: int


def positive_n_identity(origin: int, neckline: int, pullback: int, attack: int) -> str:
    return f"up:{origin}:{neckline}:{pullback}:{attack}"


def positive_n_reformations(bars: Sequence[Bar], seeds: Sequence[PositiveNSeed]) -> tuple[ReformedPositiveN, ...]:
    """Keep each launch's origin; use its actual later top and a fresh pullback.

    A defense breach ends that N, including a breach on a bullish outside candle
    that subsequently confirms a new N at the close. An origin breach cancels the
    entire family. No future pivot confirmation is required or borrowed.
    """
    formed: dict[str, ReformedPositiveN] = {}
    for seed in seeds:
        if not 0 <= seed.origin < seed.attack <= seed.known_at < len(bars):
            raise ValueError("positive N seed indices must be causal")
        if not isfinite(seed.defense) or seed.defense <= 0:
            raise ValueError("positive N defense must be a positive finite price")
        attack = seed.attack
        defense = seed.defense
        peak = attack
        breach: int | None = None
        low: int | None = None
        for now in range(seed.attack + 1, len(bars)):
            bar = bars[now]
            if bar.low < bars[seed.origin].low:
                break
            if breach is None:
                if bar.low < defense:
                    breach = now
                    low = now
                else:
                    if bar.high > bars[peak].high:
                        peak = now
                    continue
            elif low is not None and bar.low < bars[low].low:
                low = now
            if (now < seed.known_at or low is None or peak >= low
                    or bar.high <= bars[peak].high or bar.close <= bars[peak].close):
                continue
            setup = NSetup(
                bar.symbol, "1d", Direction.UP,
                PivotRef(seed.origin, seed.known_at), PivotRef(peak, max(seed.known_at, peak, breach)),
                PivotRef(low, now), "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
                allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True,
            )
            try:
                observation = observe_n(bars, setup, timeframe="1d",
                    milestone_basis=MilestoneBasis.EXTREME, asof_index=now)
            except ValueError:
                continue
            completion = observation.completion
            if completion is None or completion.bar_index != now:
                continue
            formed.setdefault(positive_n_identity(seed.origin, peak, low, now), ReformedPositiveN(
                setup, observation, attack, seed.attack, seed.level))
            attack = now
            defense = completion.defense
            peak = now
            breach = None
            low = None
    return tuple(sorted(formed.values(), key=lambda n: (
        n.setup.pullback.confirmed_index, n.setup.origin.index, n.previous_attack)))
