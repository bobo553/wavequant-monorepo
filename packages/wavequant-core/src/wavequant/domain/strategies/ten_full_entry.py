"""Causal entry pause after a ten-full target is reached."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal

from ..models.model import Bar


TEN_FULL_PULLBACK_PENDING = "wave_ten_full_pullback_pending"
RetracementAnchor = Literal["origin", "b_low"]


def _price(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        return None
    return Decimal(str(value))


@dataclass(frozen=True)
class _Pause:
    attack: int
    origin: int
    reached: int
    peak: Decimal
    anchor: Decimal
    anchor_source: str
    threshold: Decimal


def ten_full_entry_history(
    bars: Sequence[Bar],
    events: Sequence[Mapping[str, object]],
    *,
    breakout_window: int,
    retracement_ratio: float,
    anchor: RetracementAnchor,
) -> dict[int, dict[str, object]]:
    """Pause entries until a prompt new high or the required retracement.

    The target session itself is paused. A completed later session can resolve
    the pause with its low, or with a strict new high inside the trading-day
    window. Release starts on the following session so intraday entry channels
    cannot use a daily high or low that occurred after their intended fill.
    A new high after the window cannot waive the unfinished retracement.
    """
    if type(breakout_window) is not int or breakout_window <= 0:
        raise ValueError("breakout window must be a positive number of sessions")
    if type(retracement_ratio) not in (float, int) or retracement_ratio not in (.5, 2 / 3):
        raise ValueError("retracement ratio must be 1/2 or 2/3")
    if anchor not in ("origin", "b_low"):
        raise ValueError("retracement anchor must be origin or b_low")

    dated = sorted(
        ((index, event) for event in events
         if type(index := event.get("bar_index")) is int and 0 <= index < len(bars)),
        key=lambda row: row[0],
    )
    recent_b: dict[tuple[int, int], Decimal] = {}
    pauses: dict[tuple[int, int], _Pause] = {}
    resolved: set[tuple[int, int]] = set()
    risks: dict[int, dict[str, object]] = {}
    cursor = 0
    ratio = Decimal(str(retracement_ratio))
    for index in range(len(bars)):
        while cursor < len(dated) and dated[cursor][0] <= index:
            known, event = dated[cursor]
            cursor += 1
            attack, origin = event.get("attack"), event.get("origin_index")
            if type(attack) is not int or type(origin) is not int or not 0 <= origin < attack <= known:
                continue
            key = attack, origin
            b_low = _price(event.get("b_low"))
            if b_low is not None:
                recent_b[key] = b_low
            if event.get("event") != "wave_projection_target_reached" or event.get("reached_stage") != "ten_full":
                continue
            if key in pauses or key in resolved:
                continue
            origin_price = _price(event.get("a_origin"))
            previous_b = recent_b.get(key)
            base = origin_price if anchor == "origin" else previous_b or origin_price
            peak = _price(bars[known].high)
            if base is None or peak is None or base >= peak:
                continue
            anchor_source = ("origin" if anchor == "origin" else
                             "b_low" if previous_b is not None else "origin_no_b")
            pauses[key] = _Pause(attack, origin, known, peak, base, anchor_source,
                                 peak - (peak - base) * ratio)

        if index:
            previous = bars[index - 1]
            low, high = Decimal(str(previous.low)), Decimal(str(previous.high))
            for key, pause in list(pauses.items()):
                elapsed = index - 1 - pause.reached
                if elapsed > 0 and (low <= pause.threshold or
                                    (elapsed <= breakout_window and high > pause.peak)):
                    pauses.pop(key)
                    resolved.add(key)
        if not pauses:
            continue
        pause = max(pauses.values(), key=lambda current: (current.reached, current.attack))
        risks[index] = dict(
            reason=TEN_FULL_PULLBACK_PENDING,
            attack=pause.attack,
            wave_n_date=bars[pause.attack].timestamp.date().isoformat(),
            wave_n_origin_date=bars[pause.origin].timestamp.date().isoformat(),
            wave_ten_full_reached_date=bars[pause.reached].timestamp.date().isoformat(),
            wave_ten_full_high=float(pause.peak),
            wave_retracement_anchor=float(pause.anchor),
            wave_retracement_anchor_source=pause.anchor_source,
            wave_retracement_ratio=retracement_ratio,
            wave_retracement_threshold=float(pause.threshold),
            wave_breakout_window=breakout_window,
            wave_breakout_sessions_elapsed=index - pause.reached,
        )
    return risks
