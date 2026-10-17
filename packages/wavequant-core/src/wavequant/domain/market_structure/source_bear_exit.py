"""直接来源确认空头后生成本级资格结束事实，不改写本级趋势状态。"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import TYPE_CHECKING, cast

from ..models.model import Bar

if TYPE_CHECKING:
    from .trend_lifecycle import _Fact


SOURCE_BEAR_EVENT = 'trading_source_bear_confirmed'
SOURCE_BEAR_EXIT = 'source_bear_trend_confirmed'
STRUCTURE_BEAR_EVENT = 'trend_bear_structure_low_broken'
STRUCTURE_BEAR_RULE = 'confirmed_bear_flip_then_bear_structure_low_break'


def _confirmed_structure_break(event: Mapping[str, object], now: int) -> bool:
    if (event.get('event') != STRUCTURE_BEAR_EVENT or event.get('direction') != 'down'
            or event.get('confirmation_rule') != STRUCTURE_BEAR_RULE
            or event.get('flip_confirmed') is not True
            or event.get('bar_index') != now or event.get('available_index') != now):
        return False
    fields = ('prior_flip_origin_index', 'prior_flip_index', 'prior_flip_known_index',
              'origin_index', 'origin_known_index', 'flip_index', 'flip_known_index')
    indices = [event.get(field) for field in fields]
    if any(type(value) is not int for value in indices):
        return False
    prior_origin, prior_flip, prior_known, origin, origin_known, key, known = cast(list[int], indices)
    if not (0 <= prior_origin < prior_flip <= prior_known < origin < key <= known < now
            and origin <= origin_known <= now):
        return False
    values = [event.get(field) for field in ('breakout_level', 'previous_low', 'market_low')]
    if any(type(value) not in (int, float) or not isfinite(cast(float, value)) for value in values):
        return False
    threshold, previous, low = cast(list[float], values)
    return 0 < low < threshold <= previous


def bearish_structure_breaks(
    stroke: Mapping[str, object], level: int, bars: Sequence[Bar], now: int, dates: Mapping[str, int],
    *, facts: Sequence['_Fact'] | None = None,
) -> list[dict[str, object]]:
    """已翻空后的下一下降结构破自身前低；六态防守失效不抹掉结构事实。

    仅消费本级正式节点与共用整波完成证明。高点影线越过原翻空高点时，
    复用既有实体拒绝规则；后续更高的反弹不能把旧翻空无限沿用。
    """
    from .trend_lifecycle import _facts, _pivot, _records
    from .trend_levels import _completed_source_point
    from .trend_body_reversal import rejects_wick_extension

    path = stroke.get('id')
    if now == 0 or not isinstance(path, str):
        return []
    raw_points = _records(stroke.get('points'))
    known_facts = _facts(raw_points, dates, now) if facts is None else facts
    flip = next((fact for fact in reversed(known_facts) if fact.title == '翻多为空'), None)
    if flip is None or flip.origin is None or flip.broken is None:
        return []
    points = [(raw, point) for raw in raw_points if (point := _pivot(raw, dates, now)) is not None]
    own = {(p.index, p.ordinal): (raw, p) for raw, p in points}
    # 最近的正式翻空决定当前上下文；不从更早周期中挑选一个可用的标签。
    anchor = own.get((flip.origin.index, flip.origin.ordinal))
    counter = own.get((flip.point.index, flip.point.ordinal))
    if (anchor is None or counter is None or anchor[1] != flip.origin or counter[1] != flip.point
            or not flip.origin.index < flip.point.index or flip.known >= now):
        return []
    following = [(raw, p) for raw, p in points
                 if (p.index, p.ordinal) > (flip.point.index, flip.point.ordinal)]
    if len(following) < 2:
        return []
    rebound_raw, rebound = following[0]
    _, key = following[1]
    if (rebound.kind != 'H' or key.kind != 'L' or not flip.known < rebound.index < key.index
            or key.known >= now or not bars[now].low < key.price <= bars[now - 1].low):
        return []
    if rebound.price > flip.origin.price and not rejects_wick_extension(anchor[0], counter[0], rebound_raw):
        return []
    if any(bar.high > rebound.price for bar in bars[rebound.index + 1:now + 1]):
        return []
    completion = rebound_raw.get('wave_completion_evidence')
    if not isinstance(completion, Mapping) or completion.get('wave_direction_after') != 'down':
        return []
    try:
        completed = _completed_source_point(rebound_raw)
    except ValueError:
        return []
    origin = _pivot(completed, dates, now)
    if origin is None:
        return []
    return [dict(event=STRUCTURE_BEAR_EVENT, bar_index=now, available_index=now,
        trend_level=level, source_path=path, direction='down', flip_confirmed=True,
        confirmation_rule=STRUCTURE_BEAR_RULE, prior_flip_origin_index=flip.origin.index,
        prior_flip_index=flip.point.index, prior_flip_known_index=flip.known,
        origin_index=rebound.index, origin_known_index=origin.known,
        flip_index=key.index, flip_known_index=key.known, breakout_level=key.price,
        previous_low=bars[now - 1].low, market_low=bars[now].low)]


def confirmed_source_bear(event: Mapping[str, object], now: int) -> bool:
    """接受完整六态确认或已翻空的后续下降破位，拒绝候选及未来证据。"""
    if event.get('event') == STRUCTURE_BEAR_EVENT:
        return _confirmed_structure_break(event, now)
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
