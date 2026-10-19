"""Causal entry pause after a ten-full target is reached."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Literal

from ..models.model import Bar
from .event_history import EventHistoryStream


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
    half_threshold: Decimal


class TenFullEntryHistory(EventHistoryStream):
    """Pause entries until a prompt new high or a completed B correction.

    The target session itself is paused. A completed later session can resolve
    the pause with the selected deep low. When timed_half is enabled, a
    half-depth minimum close also resolves it if the time from A high to B low
    strictly exceeds the A rise. A strict new high inside the trading-day
    window releases it too. Release starts on the following session so entry
    channels cannot use later intraday observations.
    A new high after the window cannot waive the unfinished retracement.
    """
    def __init__(
        self, bars: Sequence[Bar], *, breakout_window: int,
        retracement_ratio: float, anchor: RetracementAnchor, timed_half: bool = False, start_index: int = 0,
    ) -> None:
        if type(breakout_window) is not int or breakout_window <= 0:
            raise ValueError("breakout window must be a positive number of sessions")
        if type(retracement_ratio) not in (float, int) or retracement_ratio not in (.5, 2 / 3):
            raise ValueError("retracement ratio must be 1/2 or 2/3")
        if anchor not in ("origin", "b_low"):
            raise ValueError("retracement anchor must be origin or b_low")
        if type(timed_half) is not bool:
            raise ValueError("timed half retracement switch must be boolean")
        if timed_half and (retracement_ratio != 2 / 3 or anchor != "origin"):
            raise ValueError("timed half retracement requires A origin and 2/3 deep threshold")
        self.breakout_window = breakout_window
        self.retracement_ratio = retracement_ratio
        self.anchor = anchor
        self.timed_half = timed_half
        super().__init__(bars, start_index=start_index)

    def _reset_state(self) -> None:
        self.recent_b: dict[tuple[int, int], Decimal] = {}
        self.pauses: dict[tuple[int, int], _Pause] = {}
        self.resolved: set[tuple[int, int]] = set()
        self.minimum_closes: dict[tuple[int, int], Decimal] = {}
        self.corrections: dict[tuple[int, int], tuple[Decimal, int, Decimal]] = {}

    def _advance_day(self, index: int) -> None:
        bars, recent_b, pauses = self.bars, self.recent_b, self.pauses
        resolved, minimum_closes, corrections = self.resolved, self.minimum_closes, self.corrections
        breakout_window, retracement_ratio = self.breakout_window, self.retracement_ratio
        anchor, timed_half = self.anchor, self.timed_half
        ratio = Decimal(str(retracement_ratio))
        known = index
        for event in self.dated.get(index, ()):
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
                                 peak - (peak - base) * ratio,
                                 peak - (peak - base) / 2)

        if index:
            previous = bars[index - 1]
            low, high = Decimal(str(previous.low)), Decimal(str(previous.high))
            close = Decimal(str(previous.close))
            for key, pause in list(pauses.items()):
                elapsed = index - 1 - pause.reached
                if elapsed <= 0:
                    continue
                minimum_closes[key] = min(close, minimum_closes.get(key, close))
                correction = corrections.get(key)
                if correction is None or low < correction[0]:
                    correction = (low, index - 1, minimum_closes[key])
                    corrections[key] = correction
                b_duration = correction[1] - pause.reached
                a_duration = pause.reached - pause.origin
                deep_retracement = correction[0] <= pause.threshold
                timed_half_retracement = (
                    timed_half and b_duration > a_duration
                    and correction[2] <= pause.half_threshold
                )
                if (deep_retracement or timed_half_retracement or
                        (elapsed <= breakout_window and high > pause.peak)):
                    pauses.pop(key)
                    resolved.add(key)
        if not pauses:
            return
        pause = max(pauses.values(), key=lambda current: (current.reached, current.attack))
        correction = corrections.get((pause.attack, pause.origin))
        self.history[index] = dict(
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
            wave_half_retracement_threshold=float(pause.half_threshold),
            wave_timed_half_retracement=timed_half,
            wave_a_duration=pause.reached - pause.origin,
            wave_b_duration=(correction[1] - pause.reached) if correction else 0,
            wave_b_low=float(correction[0]) if correction else None,
            wave_b_low_date=bars[correction[1]].timestamp.date().isoformat() if correction else None,
            wave_b_minimum_close=float(correction[2]) if correction else None,
            wave_breakout_window=breakout_window,
            wave_breakout_sessions_elapsed=index - pause.reached,
        )


def ten_full_entry_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]], *,
    breakout_window: int, retracement_ratio: float, anchor: RetracementAnchor,
    timed_half: bool = False,
) -> dict[int, dict[str, object]]:
    """Batch compatibility uses the same dated reducer as the daily stream."""
    return TenFullEntryHistory(bars, breakout_window=breakout_window,
        retracement_ratio=retracement_ratio, anchor=anchor, timed_half=timed_half).advance(len(bars) - 1, events)
