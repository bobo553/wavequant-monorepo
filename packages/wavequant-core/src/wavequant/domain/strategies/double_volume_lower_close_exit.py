"""前日倍量后次日继续放量收低的全局收盘清仓。"""

from collections.abc import Sequence
from fractions import Fraction
from math import isfinite

from ..models.model import Bar


DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR = 'volume_after_double_lower_close_clear'


def observe_double_volume_lower_close_exit(bars: Sequence[Bar], index: int) -> dict[str, object] | None:
    """只比较相邻三个交易日；收低指低于前收，不附加阴阳或突破条件。"""
    if not 2 <= index < len(bars):
        return None
    reference, previous, current = bars[index - 2:index + 1]
    if (len({reference.symbol, previous.symbol, current.symbol}) != 1
            or not reference.timestamp < previous.timestamp < current.timestamp
            or any(bar.raw_trading_active is False for bar in (reference, previous, current))
            or not reference.adjustment_factor == previous.adjustment_factor == current.adjustment_factor
            or not all(isfinite(value) and value > 0 for value in (
                reference.adjustment_factor, reference.volume, previous.volume, current.volume,
                previous.close, current.close))
            or Fraction(str(previous.volume)) < 2 * Fraction(str(reference.volume))
            or current.volume <= previous.volume or current.close >= previous.close):
        return None
    return dict(
        reason=DOUBLE_VOLUME_NEXT_LOWER_CLOSE_CLEAR, exit_fraction=1.0,
        execution_model='same_day_close',
        decision_timestamp=current.timestamp.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(),
        double_volume_reference_date=reference.timestamp.date().isoformat(),
        double_volume_reference=reference.volume,
        previous_date=previous.timestamp.date().isoformat(), previous_close=previous.close,
        previous_volume=previous.volume, previous_volume_multiple=previous.volume / reference.volume,
        observed_close=current.close, observed_volume=current.volume,
        response_volume_multiple=current.volume / previous.volume,
    )
