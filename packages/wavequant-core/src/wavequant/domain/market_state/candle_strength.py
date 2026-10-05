"""Shared strong bullish shape for V3 squeeze entries and attack rebreak confirmation."""

from fractions import Fraction
from typing import TypedDict

from ..models.model import Bar


class StrongBullishCandleEvidence(TypedDict):
    """Quoted shape measurements and the inclusive entry thresholds."""

    confirmation_strong_bullish: bool
    confirmation_body_open_ratio: float | None
    confirmation_body_range_ratio: float | None
    confirmation_upper_shadow_ratio: float | None
    minimum_body_open_ratio: float
    minimum_body_range_ratio: float
    maximum_upper_shadow_ratio: float


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


def strong_bullish_candle_evidence(bar: Bar) -> StrongBullishCandleEvidence:
    """Expose audit ratios without using rounded ratios for qualification."""
    opening, high, low, close = (Fraction(str(value)) for value in (bar.open, bar.high, bar.low, bar.close))
    body, span = close - opening, high - low
    return dict(
        confirmation_strong_bullish=strong_bullish_candle(bar),
        confirmation_body_open_ratio=float(body / opening) if opening > 0 else None,
        confirmation_body_range_ratio=float(body / span) if span > 0 else None,
        confirmation_upper_shadow_ratio=float((high - close) / span) if span > 0 else None,
        minimum_body_open_ratio=.03, minimum_body_range_ratio=.6, maximum_upper_shadow_ratio=.2,
    )
