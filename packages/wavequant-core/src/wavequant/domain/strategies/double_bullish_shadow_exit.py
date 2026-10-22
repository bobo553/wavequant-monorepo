"""倍量长上影阳线后的收盘破位，逐日保留一个观察位。"""

from collections.abc import Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite

from ..market_state.volume_candle import is_volume_bearish
from ..models.model import Bar


DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR = 'volume_bearish_below_double_bullish_shadow_close_clear'


def _valid_candle(bar: Bar) -> bool:
    return (all(isfinite(value) and value > 0 for value in (bar.open, bar.high, bar.low, bar.close))
            and bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
            and isfinite(bar.volume) and bar.volume >= 0 and bar.raw_trading_active is not False)


def _double_bullish_shadow(bar: Bar, previous: Bar) -> bool:
    """本形态以上影长于阳线实体为准，不修改其他长上影规则的半振幅门槛。"""
    if bar.close <= bar.open or previous.volume <= 0:
        return False
    opened, closed, high = (Fraction(str(value)) for value in (bar.open, bar.close, bar.high))
    return (high - closed > closed - opened
            and Fraction(str(bar.volume)) >= 2 * Fraction(str(previous.volume)))


@dataclass
class DoubleVolumeBullishShadowExit:
    """同一持仓的不可变日线前缀只推进一次；新观察位接替旧位，触发后消费。"""

    index: int = -1
    previous: Bar | None = None
    last_bearish: Bar | None = None
    warning: Bar | None = None
    warning_baseline: Bar | None = None
    decision: dict[str, float | str] | None = None

    def advance(self, bars: Sequence[Bar], index: int) -> dict[str, float | str] | None:
        if not 0 <= index < len(bars):
            return None
        if index < self.index:
            self.index = -1
            self.previous = self.last_bearish = self.warning = self.warning_baseline = None
            self.decision = None
        for current in range(self.index + 1, index + 1):
            self._observe(bars[current])
            self.index = current
        return dict(self.decision) if self.decision is not None else None

    def _observe(self, bar: Bar) -> None:
        self.decision = None
        if not _valid_candle(bar):
            self.previous = self.last_bearish = self.warning = self.warning_baseline = None
            return
        previous = self.previous
        if previous is not None and (previous.symbol != bar.symbol or previous.timestamp >= bar.timestamp
                                     or previous.adjustment_factor != bar.adjustment_factor):
            previous = None
            self.last_bearish = self.warning = self.warning_baseline = None
        warning, baseline = self.warning, self.warning_baseline
        if previous is not None:
            if (warning is not None and baseline is not None and bar.close < warning.close
                    and is_volume_bearish(bar, previous, self.last_bearish)):
                reference = (previous if previous.volume > 0 and bar.volume > previous.volume
                             else self.last_bearish)
                assert reference is not None
                self.decision = dict(
                    reason=DOUBLE_VOLUME_BULLISH_SHADOW_CLEAR, exit_fraction=1.0,
                    execution_model='same_day_close',
                    decision_timestamp=bar.timestamp.replace(hour=15, minute=0, second=0, microsecond=0).isoformat(),
                    warning_date=warning.timestamp.date().isoformat(), warning_open=warning.open,
                    warning_high=warning.high, warning_low=warning.low, warning_close=warning.close,
                    warning_volume=warning.volume, warning_baseline_date=baseline.timestamp.date().isoformat(),
                    warning_baseline_volume=baseline.volume, warning_volume_multiple=warning.volume / baseline.volume,
                    warning_upper_shadow=warning.high - warning.close, warning_body=warning.close - warning.open,
                    volume_reference_date=reference.timestamp.date().isoformat(), volume_reference=reference.volume,
                    volume_reference_basis='previous_session' if reference is previous else 'last_bearish',
                    volume_multiple=bar.volume / reference.volume, previous_volume=previous.volume,
                    observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                    observed_close=bar.close, observed_volume=bar.volume)
                self.warning = self.warning_baseline = None
            if _double_bullish_shadow(bar, previous):
                self.warning, self.warning_baseline = bar, previous
        if bar.close < bar.open:
            self.last_bearish = bar
        self.previous = bar
