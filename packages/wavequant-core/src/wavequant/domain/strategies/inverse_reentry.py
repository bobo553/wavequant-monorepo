"""Causal inverse-N reentry gates and independently confirmed recovery paths."""

from fractions import Fraction

from ..market_structure.alternation_duration import short_shallow_pullback


def fresh_strong_squeeze_recovery(bars, *, now, attack, origin, inverse, strong_squeeze):
    """A wholly new uninterrupted N can retire the preceding inverse episode.

    The caller supplies a confirmed STRONG_BULL frame, with its frozen and
    rolling defenses intact. An ordinary local rebound cannot use this path.
    """
    known = [e for e in inverse if e["known_at"] <= now]
    if not known or not strong_squeeze:
        return None
    last = max(known, key=lambda e: (e["known_at"], e["attack"], e["b_high"]))
    if not 0 <= last["known_at"] < origin < attack < now < len(bars):
        return None
    kill_high = last.get("kill_high")
    if kill_high is None or bars[now].close <= kill_high:
        return None
    return dict(
        inverse_reentry_path="fresh_n_uninterrupted_strong_squeeze",
        recovery_inverse_date=bars[last["attack"]].timestamp.date().isoformat(),
        recovery_kill_high=kill_high,
        recovery_previous_b_high=last["b_high"],
    )


def deep_pullback_recovery(bars, *, now, attack, inverse, alternation, record_break):
    """A newly qualified deep B may recover the selling-high with a fresh squeeze.

    This is not a waiver for old-N bounces: the inverse must belong to the
    completed decline, and both the new N and whole-wave context must be live.
    """
    known = [e for e in inverse if e["known_at"] <= now]
    if not known or not alternation or not record_break:
        return None
    last = max(known, key=lambda e: (e["known_at"], e["attack"], e["b_high"]))
    if alternation.get("definition") != "whole_flip_wave_v3" or alternation.get("trend_level") not in (2, 3):
        return None
    origin, peak, low = (alternation[k] for k in ("origin_index", "flip_high_index", "alternation_low_index"))
    confirmed = alternation["alternation_index"]
    if not (
        0 <= origin < peak < last["attack"] <= low < attack <= now < len(bars)
        and last["known_at"] < attack
        and last["known_at"] <= confirmed <= now
    ):
        return None
    start, high, bottom = (
        Fraction(str(v)) for v in (alternation["origin_price"], alternation["flip_high_price"], bars[low].low)
    )
    if not start < bottom < high or (high - bottom) / (high - start) < Fraction(2, 3):
        return None
    close = min(b.close for b in bars[peak + 1 : low + 1])
    if short_shallow_pullback(float(start), float(high), close, peak - origin, low - peak):
        return None
    if min(b.low for b in bars[low : now + 1]) < float(bottom):
        return None
    kill_high = last.get("kill_high")
    if kill_high is None or bars[now].close <= kill_high:
        return None
    return dict(
        inverse_reentry_path="deep_alternation_kill_high_record_squeeze",
        recovery_inverse_date=bars[last["attack"]].timestamp.date().isoformat(),
        recovery_kill_high=kill_high,
        recovery_previous_b_high=last["b_high"],
        recovery_whole_retracement=float((high - bottom) / (high - start)),
    )


def inverse_reentry_rejection(bars, *, now, attack, inverse, gap=False):
    """Inverse evidence is dated by availability, never by a future pivot."""
    known = [e for e in inverse if e["known_at"] <= now]
    if not known:
        return None
    last = max(known, key=lambda e: (e["known_at"], e["attack"], e["b_high"]))
    if now > last["known_at"] and bars[now].close > last["b_high"] and (attack > last["known_at"] or gap):
        return None
    return dict(
        reason="inverse_n_requires_new_attack_above_b_high",
        inverse_n_date=bars[last["attack"]].timestamp.date().isoformat(),
        inverse_known_date=bars[last["known_at"]].timestamp.date().isoformat(),
        inverse_b_date=bars[last["b_index"]].timestamp.date().isoformat(),
        inverse_b_high=last["b_high"],
        confirmation_close=bars[now].close,
    )
