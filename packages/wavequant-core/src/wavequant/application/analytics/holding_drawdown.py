"""Maximum adverse excursion (MAE) from actual entry until full liquidation.

The denominator is the then-current average fill cost, never a floating profit
peak. Partial sells keep that cost; add-ons update it causally. Prices and units
share the engine's adjusted basis. Fees remain in account equity metrics.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any, Callable, Sequence
from zoneinfo import ZoneInfo

from wavequant.domain.models.model import Bar
from wavequant.infrastructure.market_data.minute import MinuteBar

METRIC_VERSION = 'holding_entry_cost_mae_v1'
_SHANGHAI = ZoneInfo('Asia/Shanghai')


def _local(stamp: datetime) -> datetime:
    return stamp.astimezone(_SHANGHAI).replace(tzinfo=None) if stamp.tzinfo else stamp


def holding_fill_time(order: dict[str, Any]) -> datetime:
    explicit = order.get('execution_timestamp')
    stamp = _local(datetime.fromisoformat(explicit or order['timestamp']))
    if explicit or order.get('execution_model') == 'intraday_5m_next_open':
        return stamp
    return datetime.combine(stamp.date(), time(15) if order.get('execution_model') == 'same_day_close' else time(9, 30))


def holding_drawdowns(
    grouped: dict[str, list[Bar]], orders: Sequence[dict[str, Any]], end: str | None = None,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None = None,
) -> list[dict[str, Any]]:
    """Scan disjoint cost/quantity segments in O(B + F log F + F log B).

    Daily input is the engine's validated, sorted daily history. Only boundary
    sessions need verified minutes. Missing coverage returns an explicit lower
    bound; it cannot be reported as the complete maximum loss.
    """
    fills: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for order in orders:
        if order.get('status') == 'filled' and order.get('side') in ('BUY', 'SELL'):
            fills[order['symbol']].append(order)
    episodes: list[dict[str, Any]] = []
    for symbol in sorted(fills):
        bars = grouped.get(symbol, [])
        days = [b.timestamp.date().isoformat() for b in bars]
        minute_cache: dict[str, list[MinuteBar]] = {}
        active: dict[str, Any] | None = None
        segments: list[dict[str, Any]] = []
        quantity = cost = 0.0
        cost_time = ''
        for order in sorted(fills[symbol], key=holding_fill_time):
            when = holding_fill_time(order)
            if end is not None and when.date().isoformat() > end:
                continue
            amount, price = float(order['quantity']), float(order['price'])
            if not math.isfinite(amount) or amount <= 0 or not math.isfinite(price) or price <= 0:
                raise ValueError('invalid filled holding order')
            if active is not None:
                segments[-1].update(end=when, end_price=price)
            if order['side'] == 'BUY':
                if active is None:
                    active = dict(symbol=symbol, entry_time=when.isoformat(),
                                  entry_order_time=order['timestamp'], entry_price=price, exit_time=None)
                    segments = []
                cost = price if quantity == 0 else cost + (price - cost) * (amount / (quantity + amount))
                cost_time = when.isoformat()
                quantity += amount
            else:
                if active is None or amount > quantity + 1e-8:
                    raise ValueError('sell without matching holding')
                quantity = max(0.0, quantity - amount)
                if quantity < 1e-8:
                    active['exit_time'] = when.isoformat()
                    episodes.append(_measure(active, segments, bars, days, end, minute_loader, minute_cache))
                    active = None
                    cost = 0.0
            if active is not None:
                segments.append(dict(start=when, start_price=price, cost=cost, cost_time=cost_time, quantity=quantity))
        if active is not None:
            episodes.append(_measure(active, segments, bars, days, end, minute_loader, minute_cache))
    return episodes


def _measure(
    episode: dict[str, Any], segments: list[dict[str, Any]], bars: list[Bar], days: list[str], end: str | None,
    minute_loader: Callable[[Bar], list[MinuteBar]] | None, minute_cache: dict[str, list[MinuteBar]],
) -> dict[str, Any]:
    entered = datetime.fromisoformat(episode['entry_time'])
    exited = datetime.fromisoformat(episode['exit_time']) if episode['exit_time'] else None
    available = bisect_right(days, end) if end is not None else len(days)
    stop = exited or (datetime.combine(datetime.fromisoformat(days[available - 1]).date(), time(15))
                      if available else entered)
    drawdown = 0.0
    basis, low = float(episode['entry_price']), float(episode['entry_price'])
    basis_time = low_time = episode['entry_time']
    loss_amount = 0.0
    low_source = 'entry_fill'
    missing: set[str] = set()
    resolutions: set[str] = set()

    def observe(price: float, stamp: str, source: str, segment: dict[str, Any]) -> None:
        nonlocal drawdown, basis, low, basis_time, low_time, loss_amount, low_source
        if not math.isfinite(price) or price <= 0:
            raise ValueError('invalid holding drawdown price')
        current = price / segment['cost'] - 1
        # Flat/profitable holdings have zero loss. Equal losses keep the first.
        if current < drawdown:
            drawdown, basis, low = current, segment['cost'], price
            basis_time, low_time = segment['cost_time'], stamp
            loss_amount = (price - basis) * segment['quantity']
            low_source = source

    for segment in segments:
        begin, finish = segment['start'], segment.get('end', stop)
        first = bisect_left(days, begin.date().isoformat())
        last = bisect_right(days, finish.date().isoformat())
        if first == len(days) or days[first] != begin.date().isoformat():
            missing.add(begin.date().isoformat())
        if not days or not last or days[last - 1] != finish.date().isoformat():
            missing.add(finish.date().isoformat())
        observe(segment['start_price'], begin.isoformat(), 'fill', segment)
        for bar in bars[first:last]:
            day = bar.timestamp.date().isoformat()
            opening = datetime.combine(bar.timestamp.date(), time(9, 30))
            closing = datetime.combine(bar.timestamp.date(), time(15))
            lower, upper = max(begin, opening), min(finish, closing)
            if lower >= upper:
                if begin == closing and lower == closing:
                    # A close fill with positive slippage can already mark below
                    # cost at this close, without using earlier daily extrema.
                    observe(bar.close, closing.isoformat(), 'daily_close', segment)
                continue  # No pre-entry extremes or post-liquidation prices.
            if lower == opening and upper == closing:
                observe(bar.low, day, 'daily_low', segment)
                resolutions.add('daily_ohlc')
                continue
            if day not in minute_cache:
                minute: list[MinuteBar] = []
                if minute_loader is not None:
                    try:
                        minute = minute_loader(bar)
                        _validate_minutes(minute, bar)
                    except ValueError:
                        minute = []
                minute_cache[day] = minute
            minute = minute_cache[day]
            if minute:
                resolutions.add('verified_5m')
                for item in minute:
                    ended = _local(item.timestamp)
                    started = ended - timedelta(minutes=5)
                    if started >= lower and ended <= upper:
                        observe(item.low * bar.adjustment_factor, ended.isoformat(), 'verified_5m_low', segment)
                if lower.minute % 5 or upper.minute % 5 or lower.second or upper.second:
                    missing.add(day)
            else:
                missing.add(day)
                if lower == opening:
                    observe(bar.open, opening.isoformat(), 'daily_open', segment)
                if upper == closing:
                    observe(bar.close, closing.isoformat(), 'daily_close', segment)
        if 'end_price' in segment:
            observe(segment['end_price'], finish.isoformat(), 'fill', segment)
    complete = not missing
    return dict(episode, asof=stop.isoformat(), status='closed' if exited else 'open',
                metric_version=METRIC_VERSION, price_basis='causal_adjusted_equivalent', timezone='Asia/Shanghai',
                max_drawdown=drawdown if complete else None, observed_max_drawdown=drawdown,
                cost_price=basis, low_price=low, cost_time=basis_time, low_time=low_time,
                loss_amount=loss_amount, low_source=low_source,
                coverage='complete' if complete else 'incomplete', missing_sessions=sorted(missing),
                resolutions=sorted(resolutions))


def _validate_minutes(minute: list[MinuteBar], daily: Bar) -> None:
    """Require a complete same-day feed matching daily OHLC and volume."""
    expected = [time(9, m) for m in range(35, 60, 5)]
    expected += [time(h, m) for h in (10, 14) for m in range(0, 60, 5)]
    expected += [time(11, m) for m in range(0, 35, 5)]
    expected += [time(13, m) for m in range(5, 60, 5)] + [time(15)]
    stamps = [_local(m.timestamp) for m in minute]
    if len(minute) != 48 or [s.time() for s in stamps] != sorted(expected) or any(s.date() != daily.timestamp.date() for s in stamps):
        raise ValueError('incomplete holding boundary minutes')
    for item in minute:
        prices = (item.open, item.high, item.low, item.close)
        if not all(math.isfinite(p) and p > 0 for p in prices) or item.low > min(item.open, item.close) or item.high < max(item.open, item.close) or not math.isfinite(item.volume) or item.volume < 0:
            raise ValueError('invalid holding boundary OHLC')
    factor = daily.adjustment_factor
    checks = ((minute[0].open, daily.open), (max(m.high for m in minute), daily.high),
              (min(m.low for m in minute), daily.low), (minute[-1].close, daily.close))
    if any(abs(actual * factor - expected_price) > .015 * factor for actual, expected_price in checks):
        raise ValueError('mismatched holding boundary minutes')
    if abs(sum(m.volume for m in minute) - daily.volume) > max(1, daily.volume * .00001):
        raise ValueError('mismatched holding boundary volume')


def drawdown_metrics(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    worst = min(episodes, key=lambda e: (e['observed_max_drawdown'], e['entry_time'], e['symbol'])) if episodes else None
    complete = all(e['coverage'] == 'complete' for e in episodes)
    return dict(holding_drawdown_version=METRIC_VERSION,
                holding_max_drawdown=worst['max_drawdown'] if worst and complete else None,
                holding_drawdown_status='no_entry_fills' if not episodes else 'complete' if complete else 'incomplete',
                holding_drawdown_interval=worst)
