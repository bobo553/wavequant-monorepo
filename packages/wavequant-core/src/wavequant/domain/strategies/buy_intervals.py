"""所选级别交替结束之间的区间；旧策略保留原上下文适配。"""

from collections.abc import Mapping, Sequence

from .hierarchical_entry import EntryContext
from .trading_intervals import confirmed_trading_intervals, interval_days


def confirmed_buy_interval_days(
    history: Mapping[int, Sequence[EntryContext]], size: int, trend_level: int,
    *, events: Sequence[Mapping[str, object]] = (), require_lifecycle: bool = False,
) -> tuple[bool, ...]:
    """六态策略直接使用交替结束区间，不再与旧交易上下文取交集。"""
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('buy interval level must be 1, 2 or 3')
    if type(size) is not int or size < 0:
        raise ValueError('buy interval history size must be a nonnegative integer')
    if require_lifecycle:
        return interval_days(confirmed_trading_intervals(events, size, trend_level), size)
    bear_flips: set[int] = set()
    for event in events:
        if event.get('event') != 'hierarchy_bear_flip' or event.get('trend_level') != trend_level:
            continue
        known = event.get('bar_index')
        if type(known) is int and 0 <= known < size:
            bear_flips.add(known)
    last_bear_flip = -1
    days: list[bool] = []
    for now in range(size):
        if now in bear_flips:
            last_bear_flip = now
        days.append(any(context.trend_level == trend_level
                        and 0 <= context.flip_index <= context.alternation_index <= now
                        and context.alternation_index > last_bear_flip
                        for context in history.get(now, ())))
    return tuple(days)


def require_entry_days(days: Sequence[bool] | None, size: int) -> None:
    """候选扫描的开闭表必须与真实交易日轴完整对应。"""
    if days is not None and (len(days) != size or any(type(day) is not bool for day in days)):
        raise ValueError('entry days must contain one boolean per market bar')
