"""Causal, independent positive-N cycles after their frozen squeeze low fails."""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import isfinite

from ..models.model import Bar
from ..models.validated_bars import ValidatedBars
from .n_shape import (
    BoxAnchorMode, MilestoneBasis, NObservation, NSetup, PivotRef, observe_n,
)
from .observation_window import observation_window_end
from .price_action import Direction
from .trend_evidence_reuse import _geometry_signature


@dataclass(frozen=True)
class PositiveNSeed:
    origin: int
    attack: int
    known_at: int
    defense: float
    level: int = 0
    measurement_start: int | None = None


@dataclass(frozen=True)
class ReformedPositiveN:
    setup: NSetup
    observation: NObservation
    previous_attack: int
    root_attack: int
    level: int


def positive_n_identity(origin: int, neckline: int, pullback: int, attack: int) -> str:
    return f"up:{origin}:{neckline}:{pullback}:{attack}"


def _validate_seed(seed: PositiveNSeed, length: int) -> None:
    if not 0 <= seed.origin < seed.attack <= seed.known_at < length:
        raise ValueError("positive N seed indices must be causal")
    if not isfinite(seed.defense) or seed.defense <= 0:
        raise ValueError("positive N defense must be a positive finite price")
    if seed.measurement_start is not None and (type(seed.measurement_start) is not int
            or not 0 <= seed.measurement_start <= seed.known_at):
        raise ValueError('positive N measurement start must be known')


class _PositiveNFamily:
    """批量与逐日入口共用一份防守失败、重组和起点失效规则。"""

    def __init__(self, bars: Sequence[Bar], seed: PositiveNSeed, *, start_index: int = 0) -> None:
        self.bars, self.seed = bars, seed
        self.start_index = max(start_index, seed.measurement_start if seed.measurement_start is not None else 0)
        self.attack = self.peak = seed.attack
        self.index = max(seed.attack, self.start_index - 1)
        self.defense = seed.defense
        self.breach: int | None = None
        self.low: int | None = None
        self.finished = False
        self.processed_days = 0
        self.formed: dict[str, ReformedPositiveN] = {}

    def advance(self, end: int) -> None:
        if self.finished:
            self.index = end
            return
        for now in range(self.index + 1, end + 1):
            self.index = now
            self.processed_days += 1
            self._advance_day(now)
            if self.finished:
                self.index = end
                break

    def _advance_day(self, now: int) -> None:
        bars, seed = self.bars, self.seed
        bar = bars[now]
        adverse = bar.close if now == self.start_index else bar.low
        forward = bar.close if now == self.start_index else bar.high
        if adverse < bars[seed.origin].low:
            self.finished = True
            return
        if self.breach is None:
            if adverse < self.defense:
                self.breach = self.low = now
            else:
                if forward > bars[self.peak].high:
                    self.peak = now
                return
        elif self.low is not None and adverse < bars[self.low].low:
            self.low = now
        if (now < seed.known_at or self.low is None or self.peak >= self.low
                or forward <= bars[self.peak].high or bar.close <= bars[self.peak].close):
            return
        assert self.breach is not None
        setup = NSetup(
            bar.symbol, "1d", Direction.UP,
            PivotRef(seed.origin, seed.known_at), PivotRef(self.peak, max(seed.known_at, self.peak, self.breach)),
            PivotRef(self.low, now), "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
            allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True,
        )
        try:
            observation = observe_n(bars, setup, timeframe="1d",
                milestone_basis=MilestoneBasis.EXTREME, asof_index=now,
                measurement_start=max(self.start_index, seed.known_at) if self.start_index else None)
        except ValueError:
            return
        completion = observation.completion
        if completion is None or completion.bar_index != now:
            return
        self.formed.setdefault(positive_n_identity(seed.origin, self.peak, self.low, now), ReformedPositiveN(
            setup, observation, self.attack, seed.attack, seed.level))
        self.attack = self.peak = now
        self.defense = completion.defense
        self.breach = self.low = None


def _reformation_result(families: Sequence[_PositiveNFamily]) -> tuple[ReformedPositiveN, ...]:
    formed: dict[str, ReformedPositiveN] = {}
    for family in families:
        for identity, value in family.formed.items():
            formed.setdefault(identity, value)
    return tuple(sorted(formed.values(), key=lambda n: (
        n.setup.pullback.confirmed_index, n.setup.origin.index, n.previous_attack)))


class PositiveNReformationHistory:
    """完整 seed 证书只推进窗口内日期；撤销和修订不继承空档中的防守失败。"""

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0, end_index: int | None = None) -> None:
        self.bars = bars if isinstance(bars, ValidatedBars) else tuple(bars)
        self.start_index = start_index
        self.end_index = observation_window_end(len(self.bars), start_index, end_index)
        self.families: dict[bytes, _PositiveNFamily] = {}
        self.index = start_index - 1
        self.processed_days = 0

    def advance(self, index: int, seeds: Sequence[PositiveNSeed]) -> tuple[ReformedPositiveN, ...]:
        if type(index) is not int or not self.start_index <= index <= self.end_index:
            raise ValueError('positive N history requires a date inside its observation window')
        for seed in seeds:
            _validate_seed(seed, index + 1)
        if self.start_index:
            seeds = tuple(replace(seed, known_at=max(seed.known_at, self.start_index)) for seed in seeds)
        signatures = [(_geometry_signature((seed.origin, seed.attack, seed.known_at, seed.defense, seed.level,
                                          seed.measurement_start))
                       if type(seed) is PositiveNSeed else None) for seed in seeds]
        if any(signature is None for signature in signatures):
            self.families.clear()
            self.index = index
            families = [_PositiveNFamily(self.bars, seed, start_index=self.start_index) for seed in seeds]
            for family in families:
                family.advance(index)
                self.processed_days += family.processed_days
            return _reformation_result(families)
        if index < self.index:
            self.families.clear()
        active: dict[bytes, _PositiveNFamily] = {}
        ordered: list[_PositiveNFamily] = []
        for seed, signature in zip(seeds, signatures):
            assert signature is not None
            family = active.get(signature) or self.families.get(signature)
            if family is None:
                family = _PositiveNFamily(self.bars, seed, start_index=self.start_index)
            previous = family.processed_days
            family.advance(index)
            self.processed_days += family.processed_days - previous
            active[signature] = family
            ordered.append(family)
        self.families = active
        self.index = index
        return _reformation_result(ordered)


def positive_n_reformations(bars: Sequence[Bar], seeds: Sequence[PositiveNSeed]) -> tuple[ReformedPositiveN, ...]:
    """Keep each launch's origin; use its actual later top and a fresh pullback.

    A defense breach ends that N, including a breach on a bullish outside candle
    that subsequently confirms a new N at the close. An origin breach cancels the
    entire family. No future pivot confirmation is required or borrowed.
    """
    families: list[_PositiveNFamily] = []
    for seed in seeds:
        _validate_seed(seed, len(bars))
        family = _PositiveNFamily(bars, seed)
        family.advance(len(bars) - 1)
        families.append(family)
    return _reformation_result(families)
