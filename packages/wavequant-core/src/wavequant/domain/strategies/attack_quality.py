"""V3-specific positive N breakout quality, independent of optional RVOL."""

from fractions import Fraction

from ..market_state.control_bar import AttackVolume
from ..models.model import Bar


def _price(value: float) -> Fraction:
    return Fraction(str(value))


def medium_bullish_body(bar: Bar) -> bool:
    """共用中大阳门槛：实体至少开盘价 2%，且至少占当日振幅一半。"""
    body = _price(bar.close) - _price(bar.open)
    return body > 0 and body >= _price(bar.open) / 50 and body >= (_price(bar.high) - _price(bar.low)) / 2


def v3_positive_n_attack_rejection(bar: Bar, volume: AttackVolume | None) -> str | None:
    """Return a stable reason when a geometrical N cannot qualify as V3 evidence."""
    if volume is None or not volume.increased_from_previous:
        return 'v3_attack_volume_not_above_previous'
    opening, closing = _price(bar.open), _price(bar.close)
    body = closing - opening
    if body <= 0:
        return 'v3_attack_not_bullish'
    if not medium_bullish_body(bar):
        return 'v3_attack_body_too_small'
    return None
