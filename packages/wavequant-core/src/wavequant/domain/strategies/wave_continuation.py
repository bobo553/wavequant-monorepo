"""A completed N attack may retain its A/B structure beyond local N lifetimes."""

from collections.abc import Sequence
from fractions import Fraction

from ..market_structure.wave_projection import WaveProjectionSetup
from ..models.model import Bar


def wave_gap_entry(bars: Sequence[Bar], setup: WaveProjectionSetup, now: int) -> dict[str, str | int | float] | None:
    """Confirm C on a fresh volume gap, using only information known at close."""
    if not setup.squeeze_index < now < len(bars):
        return None
    bar, prev = bars[now], bars[now - 1]
    if not (bar.low > prev.high and bar.close > bar.open and bar.volume > prev.volume):
        return None
    if min(b.low for b in bars[setup.attack_index + 1 : now + 1]) < setup.defense:
        return None
    # Exclude today's high: today's attack cannot retroactively create its own A/B.
    peak = max(range(setup.attack_index, now), key=lambda j: bars[j].high)
    if peak >= now - 1 or bars[peak].high < setup.two_t:
        return None
    bottom = min(range(peak + 1, now + 1), key=lambda j: bars[j].low)
    if bottom == now:
        return None
    if not any(bars[j].close < bars[j - 1].close for j in range(peak + 1, now)):
        return None
    amplitude = Fraction(str(bars[peak].high)) - Fraction(str(setup.origin))
    target = Fraction(str(bars[bottom].low)) + amplitude
    if bar.close <= bars[setup.attack_index].high or target <= Fraction(str(bar.close)):
        return None
    reached = next(
        (
            j
            for j in range(setup.attack_index, now)
            if (bars[j].close if j == setup.attack_index else bars[j].high) >= setup.two_t
        ),
        None,
    )
    if reached is None:
        return None
    return dict(
        wave_entry_path="two_t_held_defense_volume_gap",
        wave_entry_n_date=bars[setup.attack_index].timestamp.date().isoformat(),
        wave_entry_two_t=setup.two_t,
        wave_entry_two_t_date=bars[max(reached, setup.squeeze_index)].timestamp.date().isoformat(),
        wave_a_origin=setup.origin,
        wave_a_high=bars[peak].high,
        wave_a_high_index=peak,
        wave_a_high_date=bars[peak].timestamp.date().isoformat(),
        wave_a_amplitude=float(amplitude),
        wave_b_low=bars[bottom].low,
        wave_b_low_index=bottom,
        wave_b_low_date=bars[bottom].timestamp.date().isoformat(),
        wave_equal_target=float(target),
        wave_c_1618_target=float(Fraction(str(bars[bottom].low)) + Fraction("1.618") * amplitude),
        wave_c_2618_target=float(Fraction(str(bars[bottom].low)) + Fraction("2.618") * amplitude),
        wave_defense=setup.defense,
        wave_gap_previous_high=prev.high,
        wave_gap_low=bar.low,
        wave_gap_volume=bar.volume,
        wave_gap_previous_volume=prev.volume,
    )
