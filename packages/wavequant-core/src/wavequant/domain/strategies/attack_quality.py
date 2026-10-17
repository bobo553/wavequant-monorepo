"""V3-specific positive N breakout quality, independent of optional RVOL."""

from fractions import Fraction

from ..market_state.control_bar import AttackVolume
from ..models.model import Bar


def _price(value: float) -> Fraction:
    return Fraction(str(value))


def squeeze_volume_pass(bar: Bar, previous: Bar | None, before_attack: Bar | None = None) -> bool:
    """比较昨天或本候选正 N 突破前一棒，缺失及零量基准不能放行。"""
    return any(reference is not None and reference.timestamp < bar.timestamp
               and reference.volume > 0 and bar.volume > reference.volume
               for reference in (previous, before_attack))


def squeeze_buy_rejection(
    bar: Bar, previous: Bar | None, before_attack: Bar | None = None,
) -> str | None:
    """轧空身份不等于买点；当日量价门槛独立于可选的 N 攻击质量过滤。"""
    if not squeeze_volume_pass(bar, previous, before_attack):
        return 'squeeze_buy_volume_not_above_previous_or_pre_n'
    if previous is None:
        return 'squeeze_buy_previous_price_unavailable'
    opening, closing, high = _price(bar.open), _price(bar.close), _price(bar.high)
    if opening > _price(previous.high):
        return None
    previous_close = _price(previous.close)
    body = closing - opening
    if (previous_close > 0 and closing > previous_close * Fraction(102, 100)
            and body > 0 and body >= high - closing):
        return None
    return 'squeeze_buy_requires_gain_body_or_gap'


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
