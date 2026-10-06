"""Causal partial-session candles and prior-target five-top minute exits."""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from wavequant.domain.market_structure.a_wave_rules import a_origin_broken
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.wave_exhaustion_exit import observe_five_top_upper_shadow_clear
from wavequant.infrastructure.market_data.data import opening_permissions
from wavequant.infrastructure.market_data.minute import MinuteBar, verify_minute_day


@dataclass(frozen=True)
class PartialSessionCandle:
    """A completed observation and the immediately following interval's open."""

    bar: Bar
    decision_at: datetime
    execution_at: datetime
    next_open_raw: float


def five_top_intraday_eligible(bars: Sequence[Bar], index: int, events: Sequence[dict]) -> bool:
    """Require only open-known geometry and a live milestone known before today."""
    if index < 1:
        return False
    mother, current = bars[index - 1], bars[index]
    mother_low, mother_high = sorted((mother.open, mother.close))
    body_candidate = mother_low < mother_high and mother_low <= current.open <= mother_high
    if current.open >= mother.close and not body_candidate:
        return False
    invalidated = {(event['attack'], event.get('origin_index')) for event in events
                   if event['bar_index'] < index and event['event'] == 'wave_projection_invalidated'}
    return any(event['bar_index'] < index and event['event'] == 'wave_projection_target_reached'
               and event.get('reached_stage') in ('five_top', 'ten_full')
               and (event['attack'], event.get('origin_index')) not in invalidated for event in events)


def partial_session_candles(daily: Bar, minutes: Sequence[MinuteBar]) -> Iterator[PartialSessionCandle]:
    """Validate source completeness; final daily extrema never enter a decision."""
    rows = [dict(date=daily.timestamp.date().isoformat(), code=daily.symbol, adjustflag='3',
                 time=minute.timestamp.strftime('%Y%m%d%H%M%S') + '000',
                 **{field: str(getattr(minute, field)) for field in ('open', 'high', 'low', 'close', 'volume')})
            for minute in minutes]
    verified = verify_minute_day(rows, daily)
    high, low, volume = 0.0, float('inf'), 0.0
    for offset, observed in enumerate(verified[:-1]):
        high = max(high, observed.high * daily.adjustment_factor)
        low = min(low, observed.low * daily.adjustment_factor)
        volume += observed.volume
        partial = replace(daily, high=high, low=low, close=observed.close * daily.adjustment_factor, volume=volume)
        following = verified[offset + 1]
        yield PartialSessionCandle(partial, observed.timestamp,
                                   following.timestamp - timedelta(minutes=5), following.open)


def observe_intraday_five_top_exit(
    bars: Sequence[Bar], index: int, candle: PartialSessionCandle, events: Sequence[dict]
) -> dict | None:
    """Ignore today's final events and retire known A origins at their observed break."""
    known = [event for event in events if event['bar_index'] < index]
    for event in tuple(known):
        origin = event.get('a_origin', event.get('origin'))
        origin_index = event.get('origin_index')
        if origin is None and type(origin_index) is int and 0 <= origin_index < index:
            origin = bars[origin_index].low
        if isinstance(origin, (int, float)) and not isinstance(origin, bool) and a_origin_broken(candle.bar.low, origin):
            known.append(dict(event, event='wave_projection_invalidated', bar_index=index))
    decision = observe_five_top_upper_shadow_clear([*bars[:index], candle.bar], index, known)
    if decision is None:
        return None
    return dict(decision, execution_model='intraday_5m_next_open',
                decision_source='completed_five_minute_bar', decision_timestamp=candle.decision_at.isoformat())


def minute_sellable(daily: Bar, previous: Bar, price_raw: float) -> bool:
    """Explicit source status permits a timed recalculation; unknown sources retain their flag."""
    is_st = daily.raw_is_st
    active = daily.raw_trading_active
    if active is False:
        return False
    if type(is_st) is not bool or active is not True:
        return daily.sellable
    _, sellable = opening_permissions(dict(date=daily.timestamp.date().isoformat(),
                                           isST='1' if is_st else '0', tradestatus='1',
                                           preclose=str(previous.close / daily.adjustment_factor),
                                           open=str(price_raw)), daily.symbol)
    return sellable


def observed_nonflat_minute_sellable(daily: Bar, previous: Bar, candle: PartialSessionCandle) -> bool:
    """Require source status and an already observed traded price above the lower limit."""
    return (type(daily.raw_is_st) is bool and daily.raw_trading_active is True
            and candle.bar.volume > 0 and candle.bar.high > candle.bar.low
            and minute_sellable(daily, previous, candle.bar.high / daily.adjustment_factor))
