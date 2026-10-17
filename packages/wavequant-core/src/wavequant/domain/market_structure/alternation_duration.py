"""Shared close-depth/time gate for confirmed alternation landmarks."""

from fractions import Fraction

from .trend_market_index import _MarketPrefixIndex


def short_shallow_pullback(origin, high, minimum_close, a_duration, b_duration):
    low, peak, close = (Fraction(str(p)) for p in (origin, high, minimum_close))
    return peak > low and close > peak - (peak - low) * Fraction(2, 3) and b_duration * 2 < a_duration


def filter_duration_landmarks(landmarks, bars, *, _market_index: _MarketPrefixIndex | None = None):
    if bars is None:
        return landmarks
    if _market_index is not None and not _market_index.matches(bars):
        raise ValueError('duration market index must match its immutable Bar prefix')
    result = []
    for item in landmarks:
        origin, high = item["confirmed_bear_low"], item["confirmed_flip_high"]
        a, h, b = origin["index"], high["index"], item["index"]
        if not 0 <= a < h < b < len(bars):
            continue
        indexed = (_market_index is not None and _market_index.owner.supports_price(high['value'])
                   and _market_index.owner.supports_price(item['value']))
        market = _market_index if indexed else None
        close = (bars[market.owner.lowest_close_position(h + 1, b + 1)].close if market is not None else
                 min(bar.close for bar in bars[h + 1 : b + 1]))
        if short_shallow_pullback(origin["value"], high["value"], close, h - a, b - h):
            crossing = (market.first_close_extreme(b + 1, len(bars), high['value'], high=True)
                        if market is not None else
                        next((i for i in range(b + 1, len(bars)) if bars[i].close > high["value"]), None))
            if crossing is None:
                continue
            broken = (market.first_extreme(b, crossing + 1, item['value'], high=False) is not None
                      if market is not None else min(bar.low for bar in bars[b : crossing + 1]) < item['value'])
            if broken:
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
