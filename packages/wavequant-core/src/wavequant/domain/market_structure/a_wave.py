"""Confirm A on a strict one-P break independently of later B/C or trend levels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from math import isfinite
from typing import Literal

from ..models.model import Bar
from .a_wave_rules import AWaveClass, a_origin_broken, classify_a_attack
from .observation_window import observation_window_end

A_WAVE_POLICY = "one_p_break_confirmed_a_origin_lifetime_v1"
AWavePhase = Literal["a", "b", "c", "completed", "invalidated"]
AWaveEventKind = Literal[
    "a_wave_confirmed", "a_wave_upgraded", "a_wave_extended", "a_wave_top_confirmed",
    "b_wave_updated", "c_wave_started", "c_wave_extended", "c_wave_completed", "a_wave_invalidated",
]


@dataclass(frozen=True)
class AWaveSeed:
    source_id: str
    origin_index: int
    attack_index: int
    known_at: int
    one_p: float
    two_t: float
    defense: float
    confirm_before: int | None = None


@dataclass(frozen=True)
class AWaveTurn:
    index: int
    known_at: int


@dataclass(frozen=True)
class AWaveEvent:
    bar_index: int
    event: AWaveEventKind
    source_id: str
    origin_index: int
    attack_index: int
    one_p: float
    two_t: float
    a_class: AWaveClass
    phase: AWavePhase
    confirmed_index: int
    strong_index: int | None
    a_high_index: int
    a_top_known_at: int | None
    b_low_index: int | None
    b_known_at: int | None
    c_high_index: int | None
    c_known_at: int | None


@dataclass
class _AWaveState:
    seed: AWaveSeed
    through: int
    peak: int
    observation_start: int = 0
    ended: bool = False
    phase: AWavePhase = 'a'
    a_class: AWaveClass = 'ordinary'
    confirmed: int | None = None
    strong: int | None = None
    a_top_known: int | None = None
    bottom: int | None = None
    b_known: int | None = None
    c_peak: int | None = None
    c_known: int | None = None
    events: list[AWaveEvent] = field(default_factory=list)

    def emit(self, now: int, kind: AWaveEventKind) -> None:
        assert self.confirmed is not None
        seed = self.seed
        self.events.append(AWaveEvent(
            now, kind, seed.source_id, seed.origin_index, seed.attack_index, seed.one_p, seed.two_t,
            self.a_class, self.phase, self.confirmed, self.strong, self.peak, self.a_top_known,
            self.bottom, self.b_known, self.c_peak, self.c_known,
        ))

    def advance(self, bars: Sequence[Bar], now: int, turns: set[int]) -> None:
        self.through = now
        if self.ended:
            return
        seed, bar = self.seed, bars[now]
        if a_origin_broken(bar.low, bars[seed.origin_index].low):
            if self.confirmed is not None:
                self.phase = 'invalidated'
                self.emit(now, 'a_wave_invalidated')
            self.ended = True
            return
        if self.confirmed is None:
            if ((seed.confirm_before is not None and now >= seed.confirm_before)
                    or (now > seed.attack_index and bar.low < seed.defense)):
                self.ended = True
                return
            observed = bar.close if now == self.observation_start else bar.high
            classification = classify_a_attack(observed, seed.one_p, seed.two_t)
            if bar.high > bars[self.peak].high:
                self.peak = now
            if classification is None:
                return
            self.confirmed, self.a_class = now, classification
            self.strong = now if self.a_class == 'strong' else None
            self.emit(now, 'a_wave_confirmed')
            return
        if self.phase == 'completed':
            return
        if self.phase == 'a':
            extended = bar.high > bars[self.peak].high
            if extended:
                self.peak = now
            if self.a_class == 'ordinary' and bar.high >= seed.two_t:
                self.a_class, self.strong = 'strong', now
                self.emit(now, 'a_wave_upgraded')
            elif extended:
                self.emit(now, 'a_wave_extended')
            if self.peak in turns and self.peak < now:
                self.phase, self.a_top_known = 'b', now
                self.bottom = min(range(max(self.peak + 1, self.observation_start), now + 1),
                                  key=lambda index: bars[index].low)
                self.b_known = now
                self.emit(now, 'a_wave_top_confirmed')
            return
        if self.phase == 'b':
            assert self.bottom is not None
            if bar.low < bars[self.bottom].low:
                self.bottom, self.b_known = now, now
                self.emit(now, 'b_wave_updated')
            if bar.high > bars[self.peak].high:
                self.phase, self.c_peak, self.c_known = 'c', now, now
                self.emit(now, 'c_wave_started')
            return
        assert self.c_peak is not None
        if bar.high > bars[self.c_peak].high:
            self.c_peak, self.c_known = now, now
            self.emit(now, 'c_wave_extended')
        if self.c_peak in turns and self.c_peak < now:
            self.phase, self.c_known = 'completed', now
            self.emit(now, 'c_wave_completed')


class AWaveHistoryStream:
    """Own A/B/C lifetimes and preserve late or revised source semantics.

    Stable sources consume only newly closed candles. A changed source replays
    only that source, while a changed already-known turn invalidates affected
    history. Growing provisional confirm-before horizons are not revisions.
    The frozen market belongs to this request; replacing prices requires a new
    stream, so equal lengths cannot certify a different source. Window bounds
    are inclusive; a new window observes fresh A/B/C lifetimes from its start.
    """

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0, end_index: int | None = None) -> None:
        self.bars = tuple(bars)
        if any(type(bar) is not Bar for bar in self.bars):
            raise ValueError('A wave history requires immutable Bar records')
        self.start_index = start_index
        self.end_index = observation_window_end(len(self.bars), start_index, end_index)
        self.index = start_index - 1
        self.processed_observations = 0
        self.states: dict[str, _AWaveState] = {}
        self.turns: dict[int, set[int]] = {}

    def _state(self, seed: AWaveSeed, index: int) -> _AWaveState:
        if not seed.source_id or not 0 <= seed.origin_index < seed.attack_index <= seed.known_at <= index:
            raise ValueError('A seeds must have causal source indices')
        if (any(not isfinite(value) or value <= 0 for value in (seed.one_p, seed.two_t, seed.defense))
                or seed.two_t <= seed.one_p):
            raise ValueError('A targets must be finite, positive and increasing')
        known = max(seed.known_at, self.start_index)
        origin = self.bars[seed.origin_index].low
        ended = any(a_origin_broken(bar.low, origin)
                    for bar in self.bars[max(seed.origin_index + 1, self.start_index):known + 1])
        # 原攻击高点是几何锚点；空档中的高点不能成为新窗口的 A 高点。
        peak = max((seed.attack_index, *range(max(seed.attack_index, self.start_index), known + 1)),
                   key=lambda now: self.bars[now].high)
        return _AWaveState(seed, known - 1, peak, observation_start=self.start_index, ended=ended)

    def advance(
        self, index: int, seeds: Sequence[AWaveSeed], turns: Sequence[AWaveTurn] = (),
    ) -> tuple[AWaveEvent, ...]:
        empty_default = not self.bars and self.start_index == 0 and index == -1
        if (type(index) is not int or index < self.index
                or not empty_default and not self.start_index <= index <= self.end_index):
            raise ValueError('A wave history requires a nondecreasing date inside its observation window')
        if len({seed.source_id for seed in seeds}) != len(seeds):
            raise ValueError('A source identities must be unique')
        by_known: dict[int, set[int]] = {}
        for turn in turns:
            if not 0 <= turn.index <= turn.known_at <= index:
                raise ValueError('A turns must be known inside the supplied history')
            by_known.setdefault(max(turn.known_at, self.start_index), set()).add(turn.index)
        old_turns = {known: points for known, points in self.turns.items() if known <= self.index}
        revised_turns = {known: points for known, points in by_known.items() if known <= self.index}
        if old_turns != revised_turns:
            self.states.clear()
        self.turns = by_known
        sources = {seed.source_id for seed in seeds}
        for removed in self.states.keys() - sources:
            del self.states[removed]
        for original in seeds:
            if original.known_at < self.start_index:
                if not 0 <= original.origin_index < original.attack_index <= original.known_at:
                    raise ValueError('A seeds must have causal source indices')
                original = replace(original, known_at=self.start_index)
            # The caller's len(prefix) sentinel means "not retired yet".
            seed = replace(original, confirm_before=None) if (
                original.confirm_before is not None and original.confirm_before > index) else original
            state = self.states.get(seed.source_id)
            if state is not None and state.seed != seed:
                structural_change = replace(state.seed, confirm_before=seed.confirm_before) != seed
                limits = [limit for limit in (state.seed.confirm_before, seed.confirm_before) if limit is not None]
                historical_limit = bool(limits) and min(limits) <= state.through
                if structural_change or historical_limit:
                    state = None
                else:
                    state.seed = seed
            if state is None:
                state = self._state(seed, index)
                self.states[seed.source_id] = state
            if state.ended:
                state.through = index
                continue
            for now in range(state.through + 1, index + 1):
                state.advance(self.bars, now, by_known.get(now, set()))
                self.processed_observations += 1
                if state.ended:
                    state.through = index
                    break
        self.index = index
        return tuple(sorted((event for state in self.states.values() for event in state.events),
                            key=lambda event: (event.bar_index, event.source_id)))


def a_wave_history(
    bars: Sequence[Bar], seeds: Sequence[AWaveSeed], turns: Sequence[AWaveTurn] = (),
) -> tuple[AWaveEvent, ...]:
    """Batch compatibility uses the same source-lifetime reducer as daily analysis."""
    return AWaveHistoryStream(bars).advance(len(bars) - 1, seeds, turns)
