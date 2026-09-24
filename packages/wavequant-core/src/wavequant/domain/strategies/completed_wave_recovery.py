"""Retire local A/B obstacles only for an independently confirmed C-wave attack."""

from collections.abc import Mapping, Sequence

from ..models.model import Bar

Evidence = Mapping[str, str | int | float]


def secondary_wave_recovery(
    bars: Sequence[Bar],
    now: int,
    wave: Evidence | None,
    pressure: Evidence | None,
) -> dict[str, str | int | float] | None:
    """An A-wave resistance record is not a mandatory C-wave entry price.

    The caller supplies wave_gap_entry evidence (reached A target, defended B,
    and independently confirmed reattack). Newer or higher pressure stays live.
    """
    if (
        wave is None
        or pressure is None
        or wave.get("wave_entry_path") not in ("two_t_held_defense_gap_attack", "one_p_held_defense_rebound")
    ):
        return None
    if pressure.get("secondary_current_resistance") or pressure.get("secondary_resistance_resolved"):
        return None
    attack_date = str(pressure["secondary_attack_date"])
    if (
        not str(wave["wave_entry_n_date"])
        <= attack_date
        <= str(wave["wave_a_high_date"])
        < str(wave["wave_b_low_date"])
        <= bars[now].timestamp.date().isoformat()
    ):
        return None
    if (
        float(pressure["secondary_resistance_high"]) > float(wave["wave_a_high"])
        or float(pressure["secondary_high"]) > float(wave["wave_a_high"])
        or float(wave["wave_b_low"]) < float(pressure["secondary_defense"])
        or bars[now].close <= float(pressure["secondary_high"])
    ):
        return None
    return dict(
        wave_secondary_recovery="completed_a_defended_b_gap_attack",
        wave_recovered_secondary_attack_date=attack_date,
        wave_recovered_secondary_high=pressure["secondary_high"],
        wave_recovered_secondary_record=pressure["secondary_resistance_high"],
        wave_recovered_secondary_defense=pressure["secondary_defense"],
    )


def inverse_wave_recovery(
    bars: Sequence[Bar],
    now: int,
    wave: Evidence | None,
    inverse: Sequence[Evidence],
) -> dict[str, str | int | float] | None:
    """A fresh C gap can end an inverse N contained wholly in the defended B."""
    if wave is None or wave.get("wave_entry_path") not in (
        "two_t_held_defense_gap_attack",
        "one_p_held_defense_rebound",
    ):
        return None
    known = [e for e in inverse if int(e["known_at"]) <= now]
    if not known:
        return None
    last = max(known, key=lambda e: (int(e["known_at"]), int(e["attack"]), float(e["b_high"])))
    if not (
        int(wave["wave_a_high_index"])
        < int(last["b_index"])
        < int(last["attack"])
        <= int(wave["wave_b_low_index"])
        <= now
        and int(last["attack"]) <= int(last["known_at"]) < now
        and float(last["b_high"]) <= float(wave["wave_a_high"])
    ):
        return None
    if wave.get("wave_a_class") == "ordinary" and bars[now].close <= float(last["b_high"]):
        return None
    return dict(
        inverse_reentry_path="completed_a_defended_b_gap_attack",
        recovery_inverse_date=bars[int(last["attack"])].timestamp.date().isoformat(),
        recovery_inverse_known_date=bars[int(last["known_at"])].timestamp.date().isoformat(),
        recovery_inverse_b_date=bars[int(last["b_index"])].timestamp.date().isoformat(),
        recovery_previous_b_high=last["b_high"],
    )
