"""低开放量中大阴线的收盘清仓，不依赖趋势或先前减仓。"""

from collections.abc import Sequence
import math

from ..market_state.candle_strength import medium_bearish_body
from ..market_state.volume_candle import is_volume_bearish
from ..models.model import Bar


GAP_VOLUME_BEARISH_CLEAR = 'volume_gap_medium_bearish_clear'


def observe_gap_bearish_exit(bars: Sequence[Bar], index: int) -> dict[str, float | str] | None:
    """收盘事实仅用于当日收盘决策；阴线量比较复用全局口径。"""
    if not 1 <= index < len(bars):
        return None
    bar, previous = bars[index], bars[index - 1]
    prices = (bar.open, bar.high, bar.low, bar.close, previous.close)
    if (any(not math.isfinite(value) or value <= 0 for value in prices)
            or not math.isfinite(bar.volume) or bar.volume <= 0
            or bar.symbol != previous.symbol or bar.timestamp <= previous.timestamp
            or bar.raw_trading_active is False or previous.raw_trading_active is False
            or bar.adjustment_factor != previous.adjustment_factor
            or not bar.low <= bar.close < bar.open <= bar.high
            or bar.open >= previous.close or not medium_bearish_body(bar)):
        return None
    bearish = next((bars[prior] for prior in range(index - 1, -1, -1)
                    if bars[prior].symbol == bar.symbol and bars[prior].timestamp < bar.timestamp
                    and bars[prior].close < bars[prior].open), None)
    previous_reference = previous if math.isfinite(previous.volume) else None
    bearish_reference = bearish if bearish is not None and math.isfinite(bearish.volume) else None
    if not is_volume_bearish(bar, previous_reference, bearish_reference):
        return None
    reference = previous if (previous_reference is not None
                             and previous.volume > 0 and bar.volume > previous.volume) else bearish_reference
    assert reference is not None
    evidence: dict[str, float | str] = dict(
        reason=GAP_VOLUME_BEARISH_CLEAR, exit_fraction=1.0, execution_model='same_day_close',
        decision_timestamp=bar.timestamp.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(),
        previous_date=previous.timestamp.date().isoformat(), previous_close=previous.close,
        volume_reference_date=reference.timestamp.date().isoformat(), volume_reference=reference.volume,
        volume_reference_basis='previous_session' if reference is previous else 'last_bearish',
        volume_multiple=bar.volume / reference.volume,
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        bearish_body_fraction=(bar.open - bar.close) / bar.open,
        bearish_body_range_fraction=(bar.open - bar.close) / (bar.high - bar.low))
    if previous_reference is not None:
        evidence['previous_volume'] = previous.volume
    return evidence
