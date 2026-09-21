"""Shared close-depth/time gate for confirmed alternation landmarks."""

from fractions import Fraction


def short_shallow_pullback(origin, high, minimum_close, a_duration, b_duration):
    low, peak, close = (Fraction(str(p)) for p in (origin, high, minimum_close))
    return peak > low and close > peak - (peak - low) * Fraction(2, 3) and b_duration * 2 < a_duration


def filter_duration_landmarks(landmarks, bars):
    if bars is None:
        return landmarks
    result = []
    for item in landmarks:
        origin, high = item["confirmed_bear_low"], item["confirmed_flip_high"]
        a, h, b = origin["index"], high["index"], item["index"]
        if not 0 <= a < h < b < len(bars):
            continue
        close = min(bar.close for bar in bars[h + 1 : b + 1])
        if short_shallow_pullback(origin["value"], high["value"], close, h - a, b - h):
            crossing = next((i for i in range(b + 1, len(bars)) if bars[i].close > high["value"]), None)
            if crossing is None or min(bar.low for bar in bars[b : crossing + 1]) < item["value"]:
                continue
            known = max(item["available_at"], bars[crossing].timestamp.date().isoformat())
            item = dict(
                item,
                available_at=known,
                source_available_at=known,
                duration_confirmation="close_above_a_high_after_short_pullback",
            )
        result.append(item)
    return result
