"""Close-confirmed weakness after shrinking sealed rises and heavy turnover."""

from collections.abc import Sequence
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import TypedDict

from ..models.a_share_security import a_share_security_spec
from ..models.model import Bar


LIMIT_UP_DISTRIBUTION_CLEAR = 'limit_up_distribution_midpoint_clear'
RALLY_LOOKBACK = 20
MINIMUM_RALLY = Fraction(3, 10)
TURNOVER_VOLUME_MULTIPLE = 2
OPEN_BODY_TOLERANCE = Fraction(2, 100)


class DistributionExit(TypedDict):
    reason: str
    exit_fraction: float
    execution_model: str
    distribution_quiet_dates: list[str]
    distribution_quiet_volumes: list[float]
    distribution_volume_mean: float
    distribution_turnover_dates: list[str]
    distribution_turnover_volumes: list[float]
    distribution_volume_multiple: int
    distribution_rally_start: str
    distribution_rally_gain: float
    distribution_rally_minimum: float
    distribution_previous_open: float
    distribution_previous_close: float
    distribution_body_midpoint: float
    distribution_open_ceiling: float
    distribution_open_tolerance: float
    observed_open: float
    observed_close: float
    observed_volume: float


def _shrinking_sealed_rise(previous: Bar, current: Bar) -> bool:
    # 缺少 ST 专属涨停依据时不扩展到 ST；复权切换日不拿昨日原价猜除权基准。
    if (current.raw_is_st is True or current.raw_trading_active is False
            or current.adjustment_factor != previous.adjustment_factor
            or current.close <= previous.close or current.close < current.open
            or not 0 < current.volume < previous.volume or current.high != current.close):
        return False
    try:
        rate = a_share_security_spec(current.symbol, current.timestamp.date()).price_limit_rate
    except ValueError:
        return False
    factor = Decimal(str(current.adjustment_factor))
    if factor <= 0:
        return False
    previous_raw = (Decimal(str(previous.close)) / factor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    close_raw = (Decimal(str(current.close)) / factor).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    upper = (previous_raw * (1 + rate)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    return close_raw == upper


def observe_limit_up_distribution_exit(bars: Sequence[Bar], index: int) -> DistributionExit | None:
    """Use at most twenty past sessions; the weakness day cannot supply its own setup."""
    if index < 4 or index >= len(bars):
        return None
    current, previous = bars[index], bars[index - 1]
    body_open, body_close = Fraction(str(previous.open)), Fraction(str(previous.close))
    body = body_close - body_open
    midpoint = (body_open + body_close) / 2
    open_ceiling = midpoint + body * OPEN_BODY_TOLERANCE
    if (body <= 0 or current.close >= current.open or current.open >= previous.close
            or Fraction(str(current.open)) > open_ceiling
            or Fraction(str(current.close)) >= midpoint):
        return None
    start = max(0, index - RALLY_LOOKBACK)
    history = bars[start:index + 1]
    if any(bar.symbol != current.symbol or bar.adjustment_factor != current.adjustment_factor
           or bar.raw_trading_active is False for bar in history):
        return None
    # 最近的缩量封板对提供固定量基准；之后允许普通交易日，不要求连续放量。
    quiet_end = next((j for j in range(index - 2, max(start + 1, 1), -1)
                      if _shrinking_sealed_rise(bars[j - 2], bars[j - 1])
                      and _shrinking_sealed_rise(bars[j - 1], bars[j])), None)
    if quiet_end is None:
        return None
    quiet = [bars[quiet_end - 1], bars[quiet_end]]
    baseline = sum(Fraction(str(bar.volume)) for bar in quiet) / len(quiet)
    turnover = [previous]
    if (previous.close <= bars[index - 2].close
            or Fraction(str(previous.volume)) < baseline * TURNOVER_VOLUME_MULTIPLE):
        return None
    if any(bar.low < min(item.low for item in quiet) for bar in bars[quiet_end + 1:index]):
        return None
    # 起点只从缩量封板之前选，避免放量日的下影把自身算成巨大涨幅。
    origin = min(bars[start:quiet_end], key=lambda bar: bar.low)
    if origin.low <= 0:
        return None
    gain = Fraction(str(previous.close)) / Fraction(str(origin.low)) - 1
    if gain < MINIMUM_RALLY:
        return None
    return DistributionExit(
        reason=LIMIT_UP_DISTRIBUTION_CLEAR, exit_fraction=1.0, execution_model='same_day_close',
        distribution_quiet_dates=[bar.timestamp.date().isoformat() for bar in quiet],
        distribution_quiet_volumes=[bar.volume for bar in quiet],
        distribution_volume_mean=float(baseline),
        distribution_turnover_dates=[bar.timestamp.date().isoformat() for bar in turnover],
        distribution_turnover_volumes=[bar.volume for bar in turnover],
        distribution_volume_multiple=TURNOVER_VOLUME_MULTIPLE,
        distribution_rally_start=origin.timestamp.date().isoformat(), distribution_rally_gain=float(gain),
        distribution_rally_minimum=float(MINIMUM_RALLY),
        distribution_previous_open=previous.open, distribution_previous_close=previous.close,
        distribution_body_midpoint=float(midpoint), distribution_open_ceiling=float(open_ceiling),
        distribution_open_tolerance=float(OPEN_BODY_TOLERANCE),
        observed_open=current.open, observed_close=current.close, observed_volume=current.volume,
    )
