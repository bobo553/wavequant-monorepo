"""倍量阳线突破此前K线高点后的次日风险，不等待正式趋势确认。"""

from collections.abc import Sequence
from fractions import Fraction
from math import isfinite

from ..models.model import Bar


DOUBLE_VOLUME_BULLISH_FADE_REDUCE = 'double_volume_bullish_breakout_next_bearish_lower_close_reduce_90'


def _prior_candle_high(bars: Sequence[Bar], attack: int) -> int | None:
    """最近未被突破的K线回落高点：左侧不高于它，右侧已有严格回落。

    只读取攻击前行情；等高平台归属最后一根，随后再创新高的旧高不再有效。
    这是量价形态证据，不生成或修改任何级别的趋势线。
    """
    later_high = bars[attack - 1].high
    for index in range(attack - 2, 0, -1):
        left, key, right = bars[index - 1:index + 2]
        if (key.symbol != bars[attack].symbol or left.symbol != key.symbol or right.symbol != key.symbol
                or not left.timestamp < key.timestamp < right.timestamp
                or any(bar.raw_trading_active is False for bar in (left, key, right))
                or not left.adjustment_factor == key.adjustment_factor == right.adjustment_factor
                or not key.adjustment_factor == bars[attack].adjustment_factor
                or not all(isfinite(bar.high) and bar.high > 0 for bar in (left, key, right))):
            return None
        if key.high >= left.high and key.high > later_high:
            return index
        later_high = max(later_high, key.high)
    return None


def observe_double_volume_fade(bars: Sequence[Bar], index: int) -> dict[str, object] | None:
    """前日倍量阳线突破已有K线高点，今日阴线收低则收盘累计减九成。"""
    if not 4 <= index < len(bars):
        return None
    reference, bullish, current = bars[index - 2:index + 1]
    if (len({reference.symbol, bullish.symbol, current.symbol}) != 1
            or not reference.timestamp < bullish.timestamp < current.timestamp
            or any(bar.raw_trading_active is False for bar in (reference, bullish, current))
            or not reference.adjustment_factor == bullish.adjustment_factor == current.adjustment_factor
            or not all(isfinite(value) and value > 0 for value in (
                reference.volume, bullish.volume, bullish.open, bullish.close, current.open, current.close))
            or bullish.close <= bullish.open or current.close >= current.open
            or current.close >= bullish.close
            or Fraction(str(bullish.volume)) < 2 * Fraction(str(reference.volume))):
        return None
    key_index = _prior_candle_high(bars, index - 1)
    if key_index is None or not bullish.high > bars[key_index].high:
        return None
    key = bars[key_index]
    return dict(
        reason=DOUBLE_VOLUME_BULLISH_FADE_REDUCE, exit_fraction=.9, exit_target_fraction=.9,
        execution_model='same_day_close',
        bullish_index=index - 1, bullish_date=bullish.timestamp.date().isoformat(),
        bullish_open=bullish.open, bullish_close=bullish.close, bullish_volume=bullish.volume,
        breakout_key_source='prior_candle_high', breakout_key_index=key_index,
        breakout_key_date=key.timestamp.date().isoformat(), breakout_key_high=key.high,
        breakout_key_known_index=key_index + 1, bullish_high=bullish.high,
        double_volume_reference_date=reference.timestamp.date().isoformat(),
        double_volume_reference=reference.volume, double_volume_ratio=bullish.volume / reference.volume,
        warning_index=index, warning_date=current.timestamp.date().isoformat(), warning_close=current.close,
        observed_open=current.open, observed_high=current.high, observed_volume=current.volume,
        previous_close=bullish.close,
        decision_timestamp=current.timestamp.replace(hour=15, minute=0).isoformat(),
    )
