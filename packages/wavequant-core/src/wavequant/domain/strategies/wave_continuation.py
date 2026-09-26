"""A completed N attack may retain its A/B structure beyond local N lifetimes."""

from collections.abc import Mapping, Sequence
from fractions import Fraction

from ..market_structure.wave_projection import WaveProjectionSetup
from ..market_structure.polyline import PointKind, ReversalPoint
from ..models.model import Bar


def wave_pullback_context(
    bars: Sequence[Bar], setup: WaveProjectionSetup, now: int
) -> dict[str, str | int | float] | None:
    """Freeze the completed A and defended B using previous sessions only."""
    if not setup.squeeze_index < now < len(bars):
        return None
    if min(b.low for b in bars[setup.attack_index + 1 : now]) < setup.defense:
        return None
    # Exclude today's high: today's attack cannot retroactively create its own A/B.
    peak = max(range(setup.attack_index, now), key=lambda j: bars[j].high)
    one_p = float(2 * Fraction(str(setup.box_anchor)) - Fraction(str(setup.origin)))
    strong = bars[peak].high >= setup.two_t
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
    return dict(
        wave_entry_path="two_t_held_defense_gap_attack" if strong else "one_p_held_defense_rebound",
        wave_a_class="strong" if strong else "ordinary",
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
    if not ordinary and not (gap or (strong_body and volume_up)):
        return None
    if not gap and bar.low < float(context["wave_b_low"]):
        # Only the already observed portion of today's candle may update B.
        context.update(wave_b_low=bar.low, wave_b_low_index=now, wave_b_low_date=bar.timestamp.date().isoformat())
        amplitude = Fraction(str(context["wave_a_amplitude"]))
        for key, multiple in (
            ("wave_equal_target", "1"),
            ("wave_c_1618_target", "1.618"),
            ("wave_c_2618_target", "2.618"),
        ):
            if key not in context:
                continue
            context[key] = float(Fraction(str(bar.low)) + Fraction(multiple) * amplitude)
    if bar.close >= float(context["wave_equal_target"]):
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
    if not (rebound if ordinary else ((gap and (breakout or volume_up)) or body_breakout)):
        return None
    return dict(
        context,
        wave_confirmation_phase="rebound" if ordinary else "gap" if gap else "body",
        wave_gap_trigger="rebound_close_breakout"
        if rebound
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
        wave_breakout_high=resistance.point.price if resistance else 0.0,
        wave_breakout_date=bars[resistance.point.index].timestamp.date().isoformat() if resistance else "",
        wave_gap_previous_high=prev.high,
        wave_gap_high=bar.high,
        wave_gap_low=bar.low,
        wave_gap_volume=bar.volume,
        wave_gap_previous_volume=prev.volume,
    )
