"""Close-known two-T resistance shared by entry selection and holding risk."""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from fractions import Fraction
from math import isfinite

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


TWO_T_BODY_VOLUME_CLEAR = "wave_two_t_body_volume_clear"


def _body_volume_reversal(
    bars: Sequence[Bar], index: int, bearish_index: int | None,
) -> dict[str, object] | None:
    """Compare real bodies and the most recent strictly bearish session, skipping dojis."""
    if index < 1 or bearish_index is None:
        return None
    bar, previous, bearish = bars[index], bars[index - 1], bars[bearish_index]
    if (bar.close >= bar.open or previous.close <= previous.open
            or bar.open < previous.close or bar.close > previous.open
            or (bar.open == previous.close and bar.close == previous.open)
            or bearish.volume <= 0 or bar.volume <= bearish.volume):
        return None
    return dict(
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        previous_open=previous.open, previous_high=previous.high,
        previous_low=previous.low, previous_close=previous.close, previous_volume=previous.volume,
        engulfed_date=previous.timestamp.date().isoformat(),
        bearish_reference_date=bearish.timestamp.date().isoformat(),
        bearish_reference_volume=bearish.volume,
    )


def _long_upper_shadow(bar: Bar) -> bool:
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    upper = high - max(opened, closed)
    # Decimal prices that give an equal wick/body must not fail through binary rounding.
    return high > low and 2 * upper >= high - low and upper >= abs(closed - opened)


def _upper_shadow_over_forty_percent(bar: Bar) -> bool:
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    return high > low and 5 * (high - max(opened, closed)) > 2 * (high - low)


def _next_volume_clear(
    bars: Sequence[Bar],
    index: int,
    bearish_index: int | None,
    evidence: dict[str, object],
    reason: str,
) -> dict[str, object] | None:
    previous, bar = bars[index - 1], bars[index]
    if (bar.close >= bar.open or bar.low >= previous.low or bar.close >= previous.close
            or bearish_index is None or bars[bearish_index].volume <= 0
            or bar.volume <= bars[bearish_index].volume):
        return None
    return dict(
        evidence, reason=reason, exit_fraction=1.0,
        bearish_reference_date=bars[bearish_index].timestamp.date().isoformat(),
        bearish_reference_volume=bars[bearish_index].volume,
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        previous_low=previous.low, previous_close=previous.close,
    )


def two_t_resistance_history(
    bars: Sequence[Bar],
    events: Sequence[Mapping[str, object]],
    *,
    reduction_fraction: float = .8,
) -> dict[int, dict[str, object]]:
    """Share two-T resistance and later volume-backed body reversals globally.

    Projection-ready evidence already fixes the original N, first two-T reach,
    and availability date. A newer entry N cannot erase that larger target.
    A first reach with upper shadow strictly over 40% clears on the next-session
    bearish volume double break. Reductions retain the separate 50% threshold.
    Later body reversals need no warning or minimum body ratio; the target must
    have been reached before today and remain valid through today's close.
    """
    if not isfinite(reduction_fraction) or not 0 < reduction_fraction < 1:
        raise ValueError("reduction fraction must be between zero and one")
    invalidated: dict[tuple[int, int | None], int] = {}
    for event in events:
        attack, index, origin = event.get("attack"), event.get("bar_index"), event.get("origin_index")
        if event.get("event") == "wave_projection_invalidated" and type(attack) is int and type(index) is int:
            key = (attack, origin if type(origin) is int else None)
            invalidated[key] = min(index, invalidated.get(key, index))
    previous_bearish: list[int | None] = []
    body_candidates: dict[int, dict[str, object]] = {}
    reference = None
    for index, bar in enumerate(bars):
        previous_bearish.append(reference)
        reversal = _body_volume_reversal(bars, index, reference)
        if reversal is not None:
            body_candidates[index] = reversal
        if bar.close < bar.open:
            reference = index
    body_dates = list(body_candidates)
    body_risks: dict[int, dict[str, object]] = {}
    body_sources: dict[int, tuple[int, int, int]] = {}
    risks: dict[int, dict[str, object]] = {}
    for event in events:
        if event.get("event") != "wave_projection_ready":
            continue
        reached, attack, target = event.get("bar_index"), event.get("attack"), event.get("two_t")
        origin = event.get("origin_index")
        if (type(reached) is not int or type(attack) is not int
                or not 0 <= attack <= reached < len(bars)
                or not isinstance(target, (int, float)) or isinstance(target, bool)
                or not isfinite(target) or target <= 0):
            continue
        observed = bars[reached].close if reached == attack else bars[reached].high
        if observed < target:
            continue
        key = (attack, origin if type(origin) is int else None)
        if invalidated.get(key, len(bars)) <= reached:
            continue
        target_evidence: dict[str, object] = dict(
            attack=attack, wave_n_date=bars[attack].timestamp.date().isoformat(),
            wave_reached_stage="two_t", wave_reached_price=target,
            wave_reached_date=bars[reached].timestamp.date().isoformat(),
            execution_model="same_day_close",
        )
        if type(origin) is int and 0 <= origin <= reached:
            target_evidence.update(wave_n_origin_date=bars[origin].timestamp.date().isoformat(),
                                   wave_n_origin_price=bars[origin].low)
        # Index the few qualifying reversals once instead of rescanning every N's candles.
        start = bisect_right(body_dates, reached)
        stop = bisect_left(body_dates, invalidated.get(key, len(bars)))
        source = (reached, attack, origin if type(origin) is int else -1)
        for body_index in body_dates[start:stop]:
            if source <= body_sources.get(body_index, (-1, -1, -1)):
                continue
            body_sources[body_index] = source
            body_risks[body_index] = dict(
                target_evidence, **body_candidates[body_index],
                reason=TWO_T_BODY_VOLUME_CLEAR, exit_fraction=1.0,
                target_trigger="post_two_t_bearish_body_engulf_volume_gt_last_bearish",
            )
        following = reached + 1
        if following < len(bars) and _upper_shadow_over_forty_percent(bars[reached]):
            confirmation = observe_resistance(bars[reached], bars[following],
                attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5))
            clear = _next_volume_clear(bars, following, previous_bearish[following],
                dict(target_evidence, target_trigger="first_two_t_reach_upper_gt_40pct_next_session",
                     previous_upper_shadow_fraction=(bars[reached].high - max(bars[reached].open, bars[reached].close))
                     / (bars[reached].high - bars[reached].low),
                     target_confirmation_patterns=list(confirmation.reasons)),
                "wave_two_t_next_volume_clear")
            if clear is not None and risks.get(following, {}).get("exit_fraction") != 1.0:
                risks[following] = clear
        for warning_index in range(max(1, reached), min(reached + 2, len(bars))):
            if invalidated.get(key, len(bars)) <= warning_index:
                break
            warning = bars[warning_index]
            if not _long_upper_shadow(warning):
                continue
            resistance = observe_resistance(bars[warning_index - 1], warning,
                attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5))
            patterns = list(resistance.reasons)
            if "long_upper_shadow" not in patterns:
                patterns.append("long_upper_shadow")
            evidence: dict[str, object] = dict(
                target_evidence,
                target_warning_index=warning_index,
                target_warning_date=warning.timestamp.date().isoformat(),
                target_warning_low=warning.low, target_warning_close=warning.close,
                wave_upper_shadow_fraction=(warning.high - max(warning.open, warning.close)) / (warning.high - warning.low),
                target_resistance_patterns=patterns,
                observed_open=warning.open, observed_high=warning.high,
                observed_low=warning.low, observed_close=warning.close,
                observed_volume=warning.volume, previous_close=bars[warning_index - 1].close,
            )
            reduction = dict(evidence, reason="wave_two_t_resistance_reduce",
                             exit_fraction=reduction_fraction, exit_target_fraction=reduction_fraction)
            if risks.get(warning_index, {}).get("exit_fraction") != 1.0:
                risks[warning_index] = reduction
            following = warning_index + 1
            if following >= len(bars):
                continue
            bar = bars[following]
            confirmation = observe_resistance(warning, bar, attack_direction=Direction.UP,
                                              shadow_policy=ShadowPolicy(.5))
            if confirmation.detected is True and risks.get(following, {}).get("exit_fraction") != 1.0:
                risks[following] = dict(reduction, target_confirmation_patterns=list(confirmation.reasons),
                                        observed_open=bar.open, observed_high=bar.high,
                                        observed_low=bar.low, observed_close=bar.close,
                                        observed_volume=bar.volume, previous_close=warning.close)
            clear = _next_volume_clear(bars, following, previous_bearish[following],
                dict(evidence, target_confirmation_patterns=list(confirmation.reasons)),
                "wave_two_t_resistance_volume_clear")
            if clear is not None:
                risks[following] = clear
    # Full body-reversal exits take priority over target-window partial reductions.
    for index, clear in body_risks.items():
        if risks.get(index, {}).get("exit_fraction") != 1.0:
            risks[index] = clear
    return risks
