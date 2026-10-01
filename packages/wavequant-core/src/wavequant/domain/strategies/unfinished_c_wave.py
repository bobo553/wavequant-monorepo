"""Keep frozen, unfinished C-wave pressure across later N and C candidates."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from ..models.model import Bar


@dataclass
class _CWave:
    known: int
    attack: int
    a_index: int
    b_index: int
    origin: float
    a_high: float
    b_low: float
    target_0618: float
    target_equal: float
    peak: float
    confirmation_close: float | None = None
    post_confirmation_high: float | None = None
    post_confirmation_low: float | None = None
    reached: int | None = None
    pullback: int | None = None
    pullback_low: float | None = None
    phase: str = "before_0618"

    @property
    def amplitude(self) -> Fraction:
        return Fraction(str(self.a_high)) - Fraction(str(self.origin))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        return None
    return float(value)


def _wave(event: Mapping[str, object], length: int) -> _CWave | None:
    known, attack = event.get("bar_index"), event.get("attack")
    a_index, b_index = event.get("wave_a_high_index"), event.get("wave_b_low_index")
    if (type(known) is not int or type(attack) is not int or type(a_index) is not int or type(b_index) is not int
            or not 0 <= attack <= a_index < b_index <= known < length):
        return None
    origin = _number(event.get("wave_a_origin"))
    high = _number(event.get("wave_a_high"))
    low = _number(event.get("wave_b_low"))
    target_0618 = _number(event.get("wave_c_0618_target"))
    target_equal = _number(event.get("wave_equal_target"))
    if (origin is None or high is None or low is None or target_0618 is None or target_equal is None
            or not origin < high or not low < target_0618 < target_equal):
        return None
    return _CWave(known, attack, a_index, b_index, origin, high, low, target_0618, target_equal, low,
                  confirmation_close=_number(event.get("wave_breakout_close")),
                  post_confirmation_high=_number(event.get("wave_post_confirmation_high")),
                  post_confirmation_low=_number(event.get("wave_post_confirmation_low")))


def unfinished_c_wave_history(
    bars: Sequence[Bar],
    events: Sequence[Mapping[str, object]],
    *,
    last_bar_complete: bool = True,
    event_sink: list[dict[str, object]] | None = None,
) -> dict[int, dict[str, object]]:
    """Block new entries after 0.618 until price genuinely resolves the old C.

    First confirmation freezes A/B and both targets, including untraded C
    observations. Entries wait until 1x or a strict break of the frozen C origin
    (B low) ends this C cycle. A defended pullback and renewed rise remain part
    of that same unfinished C, even with a strong close above 0.618. The A-sized
    retreat is a risk reference, not a release condition. The initial confirmation does not
    count as its own preceding C; previously frozen waves still apply that day.
    """
    starts: dict[int, list[_CWave]] = {}
    for event in events:
        if event.get("event") != "wave_gap_observed":
            continue
        wave = _wave(event, len(bars))
        if wave is not None:
            starts.setdefault(wave.known, []).append(wave)
    seen: set[tuple[float, int, int]] = set()
    active: list[_CWave] = []
    risks: dict[int, dict[str, object]] = {}

    def transition(wave: _CWave, index: int, phase: str) -> None:
        if phase == wave.phase:
            return
        wave.phase = phase
        if event_sink is not None:
            event_sink.append(dict(
                bar_index=index, event="wave_c_progress_observed", previous_c_phase=phase,
                previous_c_known_date=bars[wave.known].timestamp.date().isoformat(),
                previous_c_b_low=wave.b_low, previous_c_0618_target=wave.target_0618,
                previous_c_equal_target=wave.target_equal,
                observed_close=(wave.confirmation_close if index == wave.known and wave.confirmation_close is not None
                                else bars[index].close),
            ))

    for index, bar in enumerate(bars):
        # If the daily and minute proofs share a date, the minute proof was
        # known first and also records what happened after its confirmation.
        for wave in sorted(starts.get(index, ()),
                           key=lambda w: (w.attack, w.post_confirmation_high is not None), reverse=True):
            key = (wave.origin, wave.a_index, wave.b_index)
            if key not in seen:
                seen.add(key)
                active.append(wave)
        retained: list[_CWave] = []
        blocked: list[_CWave] = []
        for wave in active:
            complete = last_bar_complete or index < len(bars) - 1
            later_minute_break = (index == wave.known and complete and wave.post_confirmation_low is not None
                                  and wave.post_confirmation_low < wave.b_low)
            if later_minute_break or (index > wave.known and bar.low < wave.b_low):
                transition(wave, index, "c_origin_broken")
                continue
            observed = (wave.confirmation_close if wave.confirmation_close is not None else bar.close
                        ) if index == wave.known else bar.high
            if index == wave.known and complete:
                observed = max(observed, bar.close)
                if wave.post_confirmation_high is not None:
                    observed = max(observed, wave.post_confirmation_high)
            if observed >= wave.target_equal:
                transition(wave, index, "equal_completed")
                continue
            wave.peak = max(wave.peak, observed)
            if wave.reached is None and observed >= wave.target_0618:
                wave.reached = index
                transition(wave, index, "reached_0618")
            if wave.reached is not None:
                if index > wave.reached:
                    if wave.pullback_low is not None:
                        wave.pullback_low = min(wave.pullback_low, bar.low)
                    if bar.close < bars[index - 1].close:
                        if wave.pullback is None:
                            wave.pullback = index
                            wave.pullback_low = bar.low
                        transition(wave, index, "pullback_holds_c_origin")
                    elif wave.pullback is not None and bar.close > bars[index - 1].close:
                        transition(wave, index, "resuming_to_equal")
                # This gate concerns a preceding C. A newly observed C must
                # not reject the same confirmation that first defined it.
                if index > wave.known:
                    blocked.append(wave)
            retained.append(wave)
        active = retained
        if not blocked:
            continue
        wave = max(blocked, key=lambda w: (w.reached or 0, w.known, w.target_0618, w.attack))
        evidence: dict[str, object] = dict(
            reason="wave_c_0618_unfinished_pressure",
            previous_c_known_date=bars[wave.known].timestamp.date().isoformat(),
            previous_c_n_date=bars[wave.attack].timestamp.date().isoformat(),
            previous_c_a_high_date=bars[wave.a_index].timestamp.date().isoformat(),
            previous_c_a_origin=wave.origin, previous_c_a_high=wave.a_high,
            previous_c_b_low_date=bars[wave.b_index].timestamp.date().isoformat(),
            previous_c_b_low=wave.b_low, previous_c_a_amplitude=float(wave.amplitude),
            previous_c_0618_target=wave.target_0618, previous_c_equal_target=wave.target_equal,
            previous_c_reached_date=bars[wave.reached if wave.reached is not None else index].timestamp.date().isoformat(),
            previous_c_peak=wave.peak,
            previous_c_phase=wave.phase,
            previous_c_retracement_reference=float(Fraction(str(wave.peak)) - wave.amplitude),
            previous_c_retreat_is_risk_reference=True,
            observed_close=bar.close, daily_close_confirmed=last_bar_complete or index < len(bars) - 1,
        )
        if wave.pullback is not None and wave.pullback_low is not None:
            evidence["previous_c_pullback_date"] = bars[wave.pullback].timestamp.date().isoformat()
            evidence["previous_c_pullback_low"] = wave.pullback_low
            evidence["previous_c_second_rise_reference"] = float(
                Fraction(str(wave.pullback_low)) + Fraction("0.618") * wave.amplitude)
        risks[index] = evidence
    return risks
