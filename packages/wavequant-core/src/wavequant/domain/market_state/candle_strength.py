"""Shared candle strength for breakout gates and original-N rebreaks."""

from fractions import Fraction

from ..models.model import Bar
from .volume_candle import volume_bearish_buy_rejection


def medium_bullish_body(bar: Bar) -> bool:
    """共用中大阳门槛：实体至少开盘价 2%，且至少占当日振幅一半。"""
    opening, high, low, close = (Fraction(str(value)) for value in (bar.open, bar.high, bar.low, bar.close))
    body = close - opening
    return body > 0 and body >= opening / 50 and body >= (high - low) / 2


def medium_bearish_body(bar: Bar) -> bool:
    """中大阴沿用中大阳的对称门槛：实体至少开盘价2%，至少占振幅一半。"""
    opening, high, low, close = (Fraction(str(value)) for value in (bar.open, bar.high, bar.low, bar.close))
    body = opening - close
    return body > 0 and body >= opening / 50 and body >= (high - low) / 2


def bullish_breakout_rejection(
    bar: Bar, previous: Bar | None, *, breakout_level: float | None = None,
) -> str | None:
    """低开仅在当棒倍量中大阳并收盘突破时豁免，不借其他候选的量。"""
    if previous is None or previous.timestamp >= bar.timestamp:
        return 'breakout_previous_price_unavailable'
    rejection = volume_bearish_buy_rejection(bar, previous)
    if rejection is not None:
        return rejection
    opening = Fraction(str(bar.open))
    if opening < Fraction(str(previous.close)):
        resistance = Fraction(str(previous.high))
        if breakout_level is not None:
            resistance = max(resistance, Fraction(str(breakout_level)))
        if (previous.volume > 0
                and Fraction(str(bar.volume)) >= 2 * Fraction(str(previous.volume))
                and medium_bullish_body(bar)
                and Fraction(str(bar.close)) > resistance):
            return None
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


QUALIFIED_RISING_CLOSE = 'qualified_volume_high_open_rising_close'


def volume_high_open_rising(bar: Bar, previous: Bar) -> bool:
    """高开相对前收；放量强阳且全天守住开盘，收盘后才能确认。"""
    return (bar.symbol == previous.symbol and previous.timestamp < bar.timestamp
            and previous.volume > 0 and bar.volume > previous.volume
            and bar.open > previous.close and bar.low >= bar.open
            and strong_bullish_candle(bar))


QUALIFIED_VOLUME_BREAKOUT_CLOSE = 'qualified_double_volume_breakout_close'
QUALIFIED_FOLLOW_THROUGH_CLOSE = 'qualified_volume_gap_follow_through_close'
QUALIFIED_CLOSE_ENTRY_REASONS = frozenset((
    QUALIFIED_RISING_CLOSE, QUALIFIED_VOLUME_BREAKOUT_CLOSE, QUALIFIED_FOLLOW_THROUGH_CLOSE,
))
QUALIFIED_GAP_ENTRY = '资格区间跳空买入'
QUALIFIED_INTERVAL_ENTRY_REASONS = QUALIFIED_CLOSE_ENTRY_REASONS | {QUALIFIED_GAP_ENTRY}


def volume_gap_follow_through(bar: Bar, previous: Bar) -> bool:
    """突破次日放量跳空并全天守住前高；平开收的强势守稳也可确认。"""
    if (bar.symbol != previous.symbol or previous.timestamp >= bar.timestamp
            or previous.volume <= 0 or bar.volume <= previous.volume):
        return False
    opening, high, low, close, resistance = (Fraction(str(value)) for value in
        (bar.open, bar.high, bar.low, bar.close, previous.high))
    return (opening > resistance and low > resistance and close >= opening
            and high > low and high - close <= (high - low) / 5)


def double_volume_bullish_breakout(bar: Bar, previous: Bar) -> bool:
    """倍量中大阳收在振幅顶部20%且收盘突破前高；不要求高开或无下影。"""
    if (bar.symbol != previous.symbol or previous.timestamp >= bar.timestamp
            or previous.volume <= 0 or not medium_bullish_body(bar)):
        return False
    high, low, close = (Fraction(str(value)) for value in (bar.high, bar.low, bar.close))
    return (high > low and Fraction(str(bar.volume)) >= 2 * Fraction(str(previous.volume))
            and high - close <= (high - low) / 5 and close > Fraction(str(previous.high)))
