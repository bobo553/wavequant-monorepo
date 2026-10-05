"""A completed N attack may retain its A/B structure beyond local N lifetimes."""

from collections.abc import Mapping, Sequence
from fractions import Fraction

from ..market_structure.a_wave_rules import a_origin_broken, classify_a_attack
from ..market_structure.wave_projection import WaveProjectionSetup
from ..market_structure.polyline import PointKind, ReversalPoint
from ..models.model import Bar


def wave_pullback_context(
    bars: Sequence[Bar], setup: WaveProjectionSetup, now: int
) -> dict[str, str | int | float] | None:
    """Freeze A and the known B; B may lose squeeze defense while A remains valid."""
    if not setup.squeeze_index < now < len(bars):
        return None
    if any(a_origin_broken(bar.low, setup.origin) for bar in bars[setup.origin_index + 1 : now]):
        return None
    # Exclude today's high: today's attack cannot retroactively create its own A/B.
    peak = max(range(setup.attack_index, now), key=lambda j: bars[j].high)
    one_p = float(2 * Fraction(str(setup.box_anchor)) - Fraction(str(setup.origin)))
    a_class = classify_a_attack(bars[peak].high, one_p, setup.two_t)
    if a_class is None:
        return None
    strong = a_class == "strong"
    milestone = setup.two_t if strong else one_p
    if peak >= now - 1 or bars[peak].high < one_p:
        return None
    bottom = min(range(peak + 1, now), key=lambda j: bars[j].low)
    if not any(bars[j].close < bars[j - 1].close for j in range(peak + 1, now)):
        return None
    amplitude = Fraction(str(bars[peak].high)) - Fraction(str(setup.origin))
    target = Fraction(str(bars[bottom].low)) + amplitude
    reached = next(
        (
            j
            for j in range(setup.attack_index, now)
            if (bars[j].close if j == setup.attack_index else bars[j].high) >= milestone
        ),
        None,
    )
    if reached is None:
        return None
    squeeze_break = next((j for j in range(peak + 1, now) if bars[j].low < setup.defense), None)
    return dict(
        wave_entry_path="two_t_held_defense_gap_attack" if strong else "one_p_held_defense_rebound",
        wave_a_class=a_class,
        wave_entry_one_p=one_p,
        wave_entry_milestone_date=bars[max(reached, setup.squeeze_index)].timestamp.date().isoformat(),
        wave_entry_n_date=bars[setup.attack_index].timestamp.date().isoformat(),
        wave_entry_two_t=setup.two_t,
        wave_entry_two_t_date=bars[max(reached, setup.squeeze_index)].timestamp.date().isoformat() if strong else "",
        wave_a_origin=setup.origin,
        wave_a_origin_date=bars[setup.origin_index].timestamp.date().isoformat(),
        wave_a_high=bars[peak].high,
        wave_a_high_index=peak,
        wave_a_high_date=bars[peak].timestamp.date().isoformat(),
        wave_a_amplitude=float(amplitude),
        wave_b_low=bars[bottom].low,
        wave_b_low_index=bottom,
        wave_b_low_date=bars[bottom].timestamp.date().isoformat(),
        wave_b_broke_squeeze_low=int(squeeze_break is not None),
        wave_b_squeeze_break_date=bars[squeeze_break].timestamp.date().isoformat() if squeeze_break is not None else "",
        wave_b_duration=bottom - peak,
        wave_b_consolidation_duration=now - 1 - bottom,
        wave_b_elapsed_duration=now - 1 - peak,
        wave_duration_unit="trading_bars",
        wave_c_0618_target=float(Fraction(str(bars[bottom].low)) + Fraction("0.618") * amplitude),
        wave_equal_target=float(target),
        **(
            dict(
                wave_c_1618_target=float(Fraction(str(bars[bottom].low)) + Fraction("1.618") * amplitude),
                wave_c_2618_target=float(Fraction(str(bars[bottom].low)) + Fraction("2.618") * amplitude),
            )
            if strong
            else {}
        ),
        wave_defense=setup.defense,
    )


def wave_confirmation_state(proof: Mapping[str, str | int | float]) -> tuple[str, float]:
    """Keep the confirmation phase and the high known when it was emitted."""
    return str(proof["wave_confirmation_phase"]), float(proof["wave_gap_high"])


def wave_confirmation_is_new(proof: Mapping[str, str | int | float], previous: tuple[str, float] | None) -> bool:
    """Only a higher strong-A gap may advance an already confirmed body."""
    if previous is None:
        return True
    return (
        previous[0] == "body"
        and proof["wave_confirmation_phase"] == "gap"
        and float(proof["wave_gap_high"]) > previous[1]
    )


def wave_gap_entry(
    bars: Sequence[Bar],
    setup: WaveProjectionSetup,
    now: int,
    *,
    pivots: Sequence[ReversalPoint] = (),
) -> dict[str, str | int | float] | None:
    """Confirm an ordinary-A close breakout or a strong-A gap/body continuation.

    A partial daily bar may contain only completed minute observations. Never
    require a future closing candle to validate an already observed trigger.
    """
    if not setup.squeeze_index < now < len(bars):
        return None
    bar, prev = bars[now], bars[now - 1]
    gap = bar.open > prev.high and bar.low > prev.high
    body = bar.close - bar.open
    strong_body = body >= bar.open * 0.03 and body >= (bar.high - bar.low) * 0.6
    volume_up = bar.volume > prev.volume
    if not ((gap or body > 0) and bar.low >= setup.defense and bar.close > bars[setup.attack_index].high):
        return None
    # Freeze A before examining the current attack candle.
    context = wave_pullback_context(bars, setup, now)
    if context is None:
        return None
    ordinary = context["wave_a_class"] == "ordinary"
    prior_b_low = float(context["wave_b_low"])
    if not gap and bar.low < prior_b_low:
        # Only the already observed portion of today's candle may update B.
        context.update(wave_b_low=bar.low, wave_b_low_index=now, wave_b_low_date=bar.timestamp.date().isoformat())
        context.update(wave_b_duration=now - int(context["wave_a_high_index"]),
                       wave_b_consolidation_duration=0, wave_b_elapsed_duration=now - int(context["wave_a_high_index"]))
        amplitude = Fraction(str(context["wave_a_amplitude"]))
        for key, multiple in (
            ("wave_c_0618_target", "0.618"),
            ("wave_equal_target", "1"),
            ("wave_c_1618_target", "1.618"),
            ("wave_c_2618_target", "2.618"),
        ):
            if key not in context:
                continue
            context[key] = float(Fraction(str(bar.low)) + Fraction(multiple) * amplitude)
    if bar.close >= float(context["wave_equal_target"]):
        return None
    peak = int(context["wave_a_high_index"])
    two_t_break = next(
        (
            j for j in range(max(setup.attack_index, setup.squeeze_index), peak)
            if bars[j - 1].close <= setup.two_t < bars[j].close
            and bars[j].close > bars[j].open
        ),
        None,
    )
    strong_a_rebreak = False
    midpoint = 0.0
    if not ordinary and two_t_break is not None:
        breakthrough = bars[two_t_break]
        midpoint = (breakthrough.open + breakthrough.close) / 2
        resistance_bar = bars[peak]
        strong_a_rebreak = (
            peak > two_t_break
            and resistance_bar.high > bars[peak - 1].high
            and resistance_bar.close < resistance_bar.open
            and resistance_bar.close < bars[peak - 1].close
            and bar.low >= prior_b_low
            and all(bars[j].close >= midpoint for j in range(two_t_break + 1, now))
            and bar.close >= midpoint
            and bar.close > float(context["wave_a_high"])
            and body > 0
            and volume_up
        )
    if not ordinary and not (strong_a_rebreak or gap or (strong_body and volume_up)):
        return None
    highs = [
        p
        for p in pivots
        if p.point.kind == PointKind.HIGH
        and int(context["wave_a_high_index"]) < p.point.index < now
        and p.confirmed_index < now
    ]
    resistance = max(highs, key=lambda p: (p.point.index, p.confirmed_index), default=None)
    breakout = resistance is not None and bar.high > resistance.point.price
    body_breakout = (
        not gap and resistance is not None and bar.close > resistance.point.price and strong_body and volume_up
    )
    rebound = ordinary and body > 0 and resistance is not None and bar.close > resistance.point.price
    if not (rebound if ordinary else (strong_a_rebreak or (gap and (breakout or volume_up)) or body_breakout)):
        return None
    if strong_a_rebreak:
        context.update(
            wave_entry_path="two_t_strong_a_resistance_rebreak",
            wave_resistance_date=resistance_bar.timestamp.date().isoformat(),
            wave_resistance_high=resistance_bar.high,
            wave_two_t_break_date=breakthrough.timestamp.date().isoformat(),
            wave_two_t_body_midpoint=midpoint,
            wave_five_top_target=float(
                Fraction(str(context["wave_b_low"]))
                + Fraction(str(setup.two_t)) - Fraction(str(setup.origin))
            ),
        )
    return dict(
        context,
        wave_confirmation_phase="rebound" if ordinary else "gap" if gap else "body",
        wave_gap_trigger="rebound_close_breakout"
        if rebound
        else "strong_a_volume_close_breakout"
        if strong_a_rebreak
        else "volume_body_breakout"
        if body_breakout
        else "breakout_and_volume"
        if breakout and volume_up
        else "breakout"
        if breakout
        else "volume",
        wave_breakout_close=bar.close,
        wave_body_fraction=body / bar.open,
        wave_body_range_fraction=body / (bar.high - bar.low) if bar.high > bar.low else 0.0,
        wave_breakout_high=float(context["wave_a_high"]) if strong_a_rebreak else resistance.point.price if resistance else 0.0,
        wave_breakout_date=str(context["wave_a_high_date"]) if strong_a_rebreak else bars[resistance.point.index].timestamp.date().isoformat() if resistance else "",
        wave_gap_previous_high=prev.high,
        wave_gap_high=bar.high,
        wave_gap_low=bar.low,
        wave_gap_volume=bar.volume,
        wave_gap_previous_volume=prev.volume,
    )
