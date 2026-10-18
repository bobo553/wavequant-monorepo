"""Shared candle strength for breakout gates and original-N rebreaks."""

from fractions import Fraction

from ..models.model import Bar


def medium_bullish_body(bar: Bar) -> bool:
    """共用中大阳门槛：实体至少开盘价 2%，且至少占当日振幅一半。"""
    opening, high, low, close = (Fraction(str(value)) for value in (bar.open, bar.high, bar.low, bar.close))
    body = close - opening
    return body > 0 and body >= opening / 50 and body >= (high - low) / 2


def bullish_breakout_rejection(bar: Bar, previous: Bar | None) -> str | None:
    """突破当棒共用强势门槛；低开不能借随后涨幅或较早强棒补证。"""
    if previous is None or previous.timestamp >= bar.timestamp:
        return 'breakout_previous_price_unavailable'
    opening = Fraction(str(bar.open))
    if opening < Fraction(str(previous.close)):
        return 'breakout_low_open'
    if opening > Fraction(str(previous.high)) or medium_bullish_body(bar):
        return None
    return 'breakout_requires_gap_or_medium_bullish_body'


def strong_bullish_candle(bar: Bar) -> bool:
    """Require a 3% body, 60% body/range and at most 20% upper shadow.

    Threshold equality qualifies. Quoted-price fractions avoid binary rounding
    changing boundary decisions; volume is checked separately by the strategy.
    """
    opening, high, low, close = (Fraction(str(value)) for value in (bar.open, bar.high, bar.low, bar.close))
    body, span = close - opening, high - low
    return bool(opening > 0 and span > 0 and body > 0
                and body >= opening * Fraction(3, 100)
                and body >= span * Fraction(3, 5)
                and high - close <= span * Fraction(1, 5))
