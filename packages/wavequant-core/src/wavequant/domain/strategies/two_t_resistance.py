"""Close-known two-T resistance shared by entry selection and holding risk."""

from collections.abc import Mapping, Sequence
from fractions import Fraction
from math import isfinite

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


def _long_upper_shadow(bar: Bar) -> bool:
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    upper = high - max(opened, closed)
    # Decimal prices that give an equal wick/body must not fail through binary rounding.
    return high > low and 3 * upper >= high - low and upper >= abs(closed - opened)


def two_t_resistance_history(
    bars: Sequence[Bar],
    events: Sequence[Mapping[str, object]],
    *,
    reduction_fraction: float = .8,
) -> dict[int, dict[str, object]]:
    """Observe only the reached two-T candle and its next session.

    Projection-ready evidence already fixes the original N, first two-T reach,
    and availability date. A newer entry N cannot erase that larger target.
    A next-session bearish double break requires volume above the most recent
    bearish candle, independently of whether the warning reduction filled.
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
    reference = None
    for index, bar in enumerate(bars):
        previous_bearish.append(reference)
        if bar.close < bar.open:
            reference = index
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
        for warning_index in range(max(1, reached), min(reached + 2, len(bars))):
            if invalidated.get(key, len(bars)) <= warning_index:
                break
            warning = bars[warning_index]
            if not _long_upper_shadow(warning):
                continue
            resistance = observe_resistance(bars[warning_index - 1], warning,
                attack_direction=Direction.UP, shadow_policy=ShadowPolicy(1 / 3))
            patterns = list(resistance.reasons)
            if "long_upper_shadow" not in patterns:
                patterns.append("long_upper_shadow")
            evidence: dict[str, object] = dict(
                attack=attack,
                wave_n_date=bars[attack].timestamp.date().isoformat(),
                wave_reached_stage="two_t", wave_reached_price=target,
                wave_reached_date=bars[reached].timestamp.date().isoformat(),
                target_warning_index=warning_index,
                target_warning_date=warning.timestamp.date().isoformat(),
                target_warning_low=warning.low, target_warning_close=warning.close,
                wave_upper_shadow_fraction=(warning.high - max(warning.open, warning.close)) / (warning.high - warning.low),
                target_resistance_patterns=patterns,
                observed_open=warning.open, observed_high=warning.high,
                observed_low=warning.low, observed_close=warning.close,
                observed_volume=warning.volume, previous_close=bars[warning_index - 1].close,
                execution_model="same_day_close",
            )
            if type(origin) is int and 0 <= origin <= reached:
                evidence.update(wave_n_origin_date=bars[origin].timestamp.date().isoformat(),
                                wave_n_origin_price=bars[origin].low)
            reduction = dict(evidence, reason="wave_two_t_resistance_reduce",
                             exit_fraction=reduction_fraction, exit_target_fraction=reduction_fraction)
            if risks.get(warning_index, {}).get("exit_fraction") != 1.0:
                risks[warning_index] = reduction
            following = warning_index + 1
            if following >= len(bars):
                continue
            bar = bars[following]
            confirmation = observe_resistance(warning, bar, attack_direction=Direction.UP,
                                              shadow_policy=ShadowPolicy(1 / 3))
            if confirmation.detected is True and risks.get(following, {}).get("exit_fraction") != 1.0:
                risks[following] = dict(reduction, target_confirmation_patterns=list(confirmation.reasons),
                                        observed_open=bar.open, observed_high=bar.high,
                                        observed_low=bar.low, observed_close=bar.close,
                                        observed_volume=bar.volume, previous_close=warning.close)
            bearish_index = previous_bearish[following]
            if (bar.close >= bar.open or bar.low >= warning.low or bar.close >= warning.close
                    or bearish_index is None or bars[bearish_index].volume <= 0
                    or bar.volume <= bars[bearish_index].volume):
                continue
            risks[following] = dict(
                evidence, reason="wave_two_t_resistance_volume_clear", exit_fraction=1.0,
                target_confirmation_patterns=list(confirmation.reasons),
                bearish_reference_date=bars[bearish_index].timestamp.date().isoformat(),
                bearish_reference_volume=bars[bearish_index].volume,
                observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                observed_close=bar.close, observed_volume=bar.volume,
                previous_low=warning.low, previous_close=warning.close,
            )
    return risks
