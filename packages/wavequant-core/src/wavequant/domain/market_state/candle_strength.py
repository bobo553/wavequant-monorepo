"""Explicit strong bullish shape for the original-N attack rebreak route."""

from fractions import Fraction

from ..models.model import Bar


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
