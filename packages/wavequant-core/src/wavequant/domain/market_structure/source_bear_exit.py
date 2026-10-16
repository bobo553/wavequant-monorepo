"""直接来源确认空头后生成本级资格结束事实，不改写本级趋势状态。"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import cast


SOURCE_BEAR_EVENT = 'trading_source_bear_confirmed'
SOURCE_BEAR_EXIT = 'source_bear_trend_confirmed'


def confirmed_source_bear(event: Mapping[str, object], now: int) -> bool:
    """接受既有六态的完整空头确认，候选、孤立标签和未来证据均不成立。"""
    if (event.get('event') != 'trend_lifecycle_state' or event.get('state') != '转空确认'
            or event.get('direction') != 'down' or event.get('flip_confirmed') is not True
            or event.get('bar_index') != now or event.get('available_index') != now
            or event.get('confirmation_rule') != 'strict_close_cross_after_confirmed_alternation'):
        return False
    origin, flip, known = event.get('origin_index'), event.get('flip_index'), event.get('flip_known_index')
    counter, alternated = event.get('counter_index'), event.get('alternation_index')
    if (type(origin) is not int or type(flip) is not int or type(known) is not int
            or type(counter) is not int or type(alternated) is not int
            or not 0 <= origin < flip <= known <= alternated < now
            or not flip < counter <= alternated):
        return False
    threshold, previous, close = (event.get('breakout_level'), event.get('previous_close'),
                                  event.get('confirmation_close'))
    if (type(threshold) not in (int, float) or type(previous) not in (int, float)
            or type(close) not in (int, float)):
        return False
    threshold, previous, close = (float(cast(float, value)) for value in (threshold, previous, close))
    return all(isfinite(value) for value in (threshold, previous, close)) and 0 < close < threshold <= previous


def source_bearish_exits(
    levels: Sequence[Mapping[str, object]], events: Sequence[Mapping[str, object]], now: int,
) -> list[dict[str, object]]:
    """只沿当天本级几何的直接来源关联；时间复杂度为当日事件数加路径数。"""
    confirmed: dict[tuple[int, str], Mapping[str, object]] = {}
    for event in events:
        level, path = event.get('trend_level'), event.get('source_path')
        if (type(level) is int and level in (0, 1, 2) and isinstance(path, str)
                and confirmed_source_bear(event, now)):
            confirmed[level, path] = event
    if not confirmed:
        return []
    result: list[dict[str, object]] = []
    for row in levels:
        level, strokes = row.get('trend_level'), row.get('strokes')
        if type(level) is not int or level not in (1, 2, 3) or not isinstance(strokes, (tuple, list)):
            continue
        cutoffs = row.get('_path_market_end_indices')
        for stroke in strokes:
            if not isinstance(stroke, Mapping):
                continue
            path, source = stroke.get('id'), stroke.get('source_path')
            if not isinstance(path, str) or not isinstance(source, str):
                continue
            cutoff = cutoffs.get(source) if isinstance(cutoffs, Mapping) else None
            if type(cutoff) is int and cutoff <= now:
                continue
            proof = confirmed.get((level - 1, source))
            if proof is not None:
                result.append(dict(event=SOURCE_BEAR_EVENT, bar_index=now, available_index=now,
                    trend_level=level, source_path=path, direct_source_path=source,
                    reason=SOURCE_BEAR_EXIT, source_confirmation=dict(proof)))
    return result
