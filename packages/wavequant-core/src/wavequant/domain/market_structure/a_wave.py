"""Confirm A on a strict one-P break independently of later B/C or trend levels."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Literal

from ..models.model import Bar
from .a_wave_rules import AWaveClass, a_origin_broken, classify_a_attack

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


def a_wave_history(
    bars: Sequence[Bar], seeds: Sequence[AWaveSeed], turns: Sequence[AWaveTurn] = (),
) -> tuple[AWaveEvent, ...]:
    """Publish immutable dated changes; N retirement only limits initial A creation.

    A confirmed first-level high freezes the A top, without requiring a second-level
    flip. B then follows actual lows; a strict later A-high break starts C. An origin
    loss wins over the same candle's upside and ends the source permanently. Each
    independent N has its own identity. Cost is O(bars * seeds + turns).
    """
    by_known: dict[int, set[int]] = {}
    for turn in turns:
        if not 0 <= turn.index <= turn.known_at < len(bars):
            raise ValueError("A turns must be known inside the supplied history")
        by_known.setdefault(turn.known_at, set()).add(turn.index)
    if len({seed.source_id for seed in seeds}) != len(seeds):
        raise ValueError("A source identities must be unique")
    events: list[AWaveEvent] = []
    for seed in seeds:
        if not seed.source_id or not 0 <= seed.origin_index < seed.attack_index <= seed.known_at < len(bars):
            raise ValueError("A seeds must have causal source indices")
        if (any(not isfinite(value) or value <= 0 for value in (seed.one_p, seed.two_t, seed.defense))
                or seed.two_t <= seed.one_p):
            raise ValueError("A targets must be finite, positive and increasing")
        origin = bars[seed.origin_index].low
        if any(a_origin_broken(bar.low, origin) for bar in bars[seed.origin_index + 1:seed.known_at + 1]):
            continue
        phase: AWavePhase = "a"
        a_class: AWaveClass = "ordinary"
        confirmed: int | None = None
        strong: int | None = None
        peak = max(range(seed.attack_index, seed.known_at + 1), key=lambda index: bars[index].high)
        a_top_known: int | None = None
        bottom: int | None = None
        b_known: int | None = None
        c_peak: int | None = None
        c_known: int | None = None

        def emit(now: int, kind: AWaveEventKind) -> None:
            assert confirmed is not None
            events.append(AWaveEvent(
                now, kind, seed.source_id, seed.origin_index, seed.attack_index, seed.one_p, seed.two_t,
                a_class, phase, confirmed, strong, peak, a_top_known, bottom, b_known, c_peak, c_known,
            ))

        for now in range(seed.known_at, len(bars)):
            bar = bars[now]
            if a_origin_broken(bar.low, origin):
                if confirmed is not None:
                    phase = "invalidated"
                    emit(now, "a_wave_invalidated")
                break
            if confirmed is None:
                if ((seed.confirm_before is not None and now >= seed.confirm_before)
                        or (now > seed.attack_index and bar.low < seed.defense)):
                    break
                classification = classify_a_attack(bar.high, seed.one_p, seed.two_t)
                if bar.high > bars[peak].high:
                    peak = now
                if classification is None:
                    continue
                confirmed, a_class = now, classification
                strong = now if a_class == "strong" else None
                emit(now, "a_wave_confirmed")
                continue
            if phase == "completed":
                continue
            if phase == "a":
                extended = bar.high > bars[peak].high
                if extended:
                    peak = now
                if a_class == "ordinary" and bar.high >= seed.two_t:
                    a_class, strong = "strong", now
                    emit(now, "a_wave_upgraded")
                elif extended:
                    emit(now, "a_wave_extended")
                if peak in by_known.get(now, ()) and peak < now:
                    phase, a_top_known = "b", now
                    bottom = min(range(peak + 1, now + 1), key=lambda index: bars[index].low)
                    b_known = now
                    emit(now, "a_wave_top_confirmed")
                continue
            if phase == "b":
                assert bottom is not None
                if bar.low < bars[bottom].low:
                    bottom, b_known = now, now
                    emit(now, "b_wave_updated")
                if bar.high > bars[peak].high:
                    phase, c_peak, c_known = "c", now, now
                    emit(now, "c_wave_started")
                continue
            assert c_peak is not None
            if bar.high > bars[c_peak].high:
                c_peak, c_known = now, now
                emit(now, "c_wave_extended")
            if c_peak in by_known.get(now, ()) and c_peak < now:
                phase, c_known = "completed", now
                emit(now, "c_wave_completed")
    return tuple(sorted(events, key=lambda event: (event.bar_index, event.source_id)))
