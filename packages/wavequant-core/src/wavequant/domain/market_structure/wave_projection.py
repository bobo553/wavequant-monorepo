"""Causal, conditional targets after a confirmed bullish N reaches two-t.

This observer does not count Elliott subwaves or issue orders. Daily close
weakness and a later local close breakout are explicit pullback/reattack proxies.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal

from ..models.model import Bar


def _price(value: float) -> Decimal:
    return Decimal(str(value))


@dataclass(frozen=True)
class WaveProjectionSetup:
    """Frozen anchors from one completed N and its dated squeeze confirmation."""

    origin_index: int
    attack_index: int
    squeeze_index: int
    origin: float
    box_anchor: float
    two_t: float
    defense: float

    def __post_init__(self) -> None:
        indices = (self.origin_index, self.attack_index, self.squeeze_index)
        if any(type(i) is not int or i < 0 for i in indices):
            raise ValueError("projection indices must be nonnegative integers")
        if not self.origin_index < self.attack_index < self.squeeze_index:
            raise ValueError("origin, N attack and squeeze confirmation must be chronological")
        if any(not isfinite(v) or v <= 0 for v in (self.origin, self.box_anchor, self.two_t, self.defense)):
            raise ValueError("projection anchors must be finite positive prices")
        if not self.origin <= self.defense < self.box_anchor < self.two_t:
            raise ValueError("invalid bullish projection anchors")
        if _price(self.two_t) != 3 * _price(self.box_anchor) - 2 * _price(self.origin):
            # Targets are serialized floats in the existing N observer.
            if abs(self.two_t - (3 * self.box_anchor - 2 * self.origin)) > 1e-12 * self.two_t:
                raise ValueError("two-t must use the frozen N box")


@dataclass(frozen=True)
class WaveProjectionEvent:
    bar_index: int
    event: str
    attack: int
    state: str
    target: float | None
    defense: float
    two_t: float
    box_height: float
    a_origin: float
    a_high: float
    a_high_index: int
    b_low: float | None = None
    b_low_index: int | None = None
    reached_target: float | None = None
    crossed_boxes: int = 0
    target_stage: str = "five_top"
    projection_span: float = 0.0
    reached_stage: str | None = None
    rule: str = "five_top_ten_full_v2"


def wave_projection_history(
    bars: Sequence[Bar],
    setup: WaveProjectionSetup,
    *,
    asof_index: int | None = None,
    target_policy: Literal["named_stages", "nearest_box"] = "named_stages",
) -> tuple[WaveProjectionEvent, ...]:
    """Replay one N through ready, stacking, pullback, pushing and invalidation.

    Equality reaches a target and holds support; breaks and reattacks are strict.
    Only a target known before a bar may use its high. New same-bar projections
    use the close. Support loss wins over simultaneous upside observations.
    Time is O(visible bars), including arbitrarily large multi-box price gaps.
    """
    end = len(bars) - 1 if asof_index is None else asof_index
    if target_policy not in ("named_stages", "nearest_box"):
        raise ValueError("explicit supported target policy required")
    named = target_policy == "named_stages"
    if type(end) is not int or not 0 <= end < len(bars):
        raise ValueError("as-of bar is unavailable")
    if setup.attack_index > end:
        return ()
    # Validate only the visible prefix; appending future bars cannot change it.
    from .price_action import _ordered_pair, _validate_bar
    from ..models.validated_bars import ValidatedBars

    if not isinstance(bars, ValidatedBars):
        for i in range(end + 1):
            _validate_bar(bars[i])
            if i:
                _ordered_pair(bars[i - 1], bars[i])

    box = _price(setup.box_anchor) - _price(setup.origin)
    peak, peak_index = bars[setup.attack_index].high, setup.attack_index
    a_high, a_index = peak, peak_index
    b_low: float | None = None
    b_index: int | None = None
    reached = False
    state = "await_two_t"
    target: Decimal | None = None
    stage = "five_top"
    span = 3 * box
    extension = 0
    events: list[WaveProjectionEvent] = []

    def emit(i: int, kind: str, hit: Decimal | None = None, hit_stage: str | None = None) -> None:
        events.append(
            WaveProjectionEvent(
                i,
                kind,
                setup.attack_index,
                state,
                float(target) if target is not None else None,
                setup.defense,
                setup.two_t,
                float(box),
                setup.origin,
                a_high,
                a_index,
                b_low,
                b_index,
                float(hit) if hit is not None else None,
                1 if hit is not None else 0,
                stage,
                float(span),
                hit_stage,
            )
        )

    for i in range(setup.attack_index, end + 1):
        bar = bars[i]
        if i > setup.attack_index and bar.low < setup.defense:
            if state != "await_two_t":
                state, target = "invalidated", None
                emit(i, "wave_projection_invalidated")
            break  # This N cannot revive even if support is regained later.
        previous_peak = peak
        if bar.high > peak:
            peak, peak_index = bar.high, i
        observed = bar.close if i == setup.attack_index else bar.high
        reached |= observed >= setup.two_t
        if state == "await_two_t":
            if not reached or i < setup.squeeze_index:
                continue
            state = "ready"
            a_high, a_index = peak, peak_index
            emit(i, "wave_projection_ready")
            # A weak two-t bar may already begin the correction. Its high/low
            # order is unknown, so a B low on the A-high bar is never assumed.
            if bar.close >= bars[i - 1].close:
                continue

        # A fresh pullback suspends the previous target before considering hits.
        if state != "pullback" and bar.close < bars[i - 1].close:
            state, target = "pullback", None
            same_a = peak_index == a_index
            a_high, a_index = peak, peak_index
            if not same_a or b_low is None:
                b_low, b_index = (bar.low, i) if i > peak_index else (None, None)
            elif bar.low < b_low:
                b_low, b_index = bar.low, i
            if stage != "five_top":
                span = _price(a_high) - _price(setup.origin)
            emit(i, "wave_projection_pullback")
            continue

        if state == "pullback":
            if b_low is None or bar.low < b_low:
                b_low, b_index = bar.low, i
            prev = bars[i - 1]
            if (
                b_index is not None
                and b_index < i
                and bar.low >= prev.low
                and bar.close > prev.high
                and bar.close > bar.open
            ):
                state = "pushing"
                target = _price(b_low) + (span if named else _price(a_high) - _price(setup.origin))
                emit(i, "wave_projection_push")
                # The current high may precede the close-confirmed reattack.
                observed = bar.close
            else:
                continue
        elif state == "ready":
            if bar.close <= previous_peak:
                continue
            # T2 + 3H = X + 5H, with X the original N box high.
            state, target = "stacking", _price(setup.two_t) + (span if named else box)
            emit(i, "wave_projection_stack")
            observed = bar.close

        if target is not None and _price(observed) >= target:
            hit, hit_stage = target, stage
            if not named:
                target += (int((_price(observed) - target) // box) + 1) * box
                state = "stacking"
                emit(i, "wave_projection_target_reached", hit)
                continue
            if stage == "five_top":
                stage = "ten_full"
            else:
                extension += 1
                stage = f"extension_{extension}"
            # Enlarge the next box using only the high observed so far. A later
            # correction freezes its then-known peak for the new B-based route.
            a_high, a_index = peak, peak_index
            span = _price(a_high) - _price(setup.origin)
            target = _price(a_high) + span
            b_low, b_index = None, None
            state = "stacking"
            emit(i, "wave_projection_target_reached", hit, hit_stage)
    return tuple(events)
