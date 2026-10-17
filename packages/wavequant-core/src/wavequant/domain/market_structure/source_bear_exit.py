"""直接来源确认空头后生成本级资格结束事实，不改写本级趋势状态。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, cast

from ..models.model import Bar
from .trend_market_index import _MarketRangeIndex
from .failed_breakout_exit import FAILED_BREAKOUT_EVENT, confirmed_failed_breakout

if TYPE_CHECKING:
    from .trend_lifecycle import _Fact, _Pivot


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


@dataclass(frozen=True, slots=True)
class _BearStructureCandidate:
    """一个已解析的结构版本；每日只检查价格，不再次遍历整条趋势。"""

    path: str
    flip: '_Fact'
    rebound: '_Pivot'
    key: '_Pivot'
    rebound_source: Mapping[str, object]

    def observe(self, level: int, bars: Sequence[Bar], now: int,
                dates: Mapping[str, int], *,
                _market_index: _MarketRangeIndex | None = None) -> list[dict[str, object]]:
        flip, rebound, key = self.flip, self.rebound, self.key
        if (now == 0 or flip.known >= now or key.known >= now
                or not bars[now].low < key.price <= bars[now - 1].low):
            return []
        invalidated = (_market_index.first_extreme(rebound.index + 1, now + 1, rebound.price, high=True) is not None
                       if _market_index is not None and bars is _market_index.bars
                       and _market_index.supports_price(rebound.price)
                       else any(bars[index].high > rebound.price for index in range(rebound.index + 1, now + 1)))
        if invalidated:
            return []
        # 完整结束证明只在价格满足破位时核验，不能为缓存把重证据工作提前到每次几何变化。
        from .trend_lifecycle import _pivot
        from .trend_levels import _completed_source_point

        try:
            completed = _completed_source_point(self.rebound_source)
        except ValueError:
            return []
        origin = _pivot(completed, dates, now)
        if origin is None:
            return []
        assert flip.origin is not None
        return [dict(event=STRUCTURE_BEAR_EVENT, bar_index=now, available_index=now,
            trend_level=level, source_path=self.path, direction='down', flip_confirmed=True,
            confirmation_rule=STRUCTURE_BEAR_RULE, prior_flip_origin_index=flip.origin.index,
            prior_flip_index=flip.point.index, prior_flip_known_index=flip.known,
            origin_index=rebound.index, origin_known_index=origin.known,
            flip_index=key.index, flip_known_index=key.known, breakout_level=key.price,
            previous_low=bars[now - 1].low, market_low=bars[now].low)]


def _prepare_bearish_structure(
    stroke: Mapping[str, object], now: int, dates: Mapping[str, int],
    *, facts: Sequence['_Fact'] | None = None,
) -> _BearStructureCandidate | None:
    """结构或知识时钟变化时准备；六态防守失效不抹掉结构事实。

    仅消费本级正式节点与共用整波完成证明。高点影线越过原翻空高点时，
    复用既有实体拒绝规则；后续更高的反弹不能把旧翻空无限沿用。
    """
    from .trend_lifecycle import _facts, _pivot, _records
    from .trend_body_reversal import rejects_wick_extension

    path = stroke.get('id')
    if not isinstance(path, str):
        return None
    raw_points = _records(stroke.get('points'))
    known_facts = _facts(raw_points, dates, now) if facts is None else facts
    flip = next((fact for fact in reversed(known_facts) if fact.title == '翻多为空'), None)
    if flip is None or flip.origin is None or flip.broken is None:
        return None
    points = [(raw, point) for raw in raw_points if (point := _pivot(raw, dates, now)) is not None]
    own = {(p.index, p.ordinal): (raw, p) for raw, p in points}
    # 最近的正式翻空决定当前上下文；不从更早周期中挑选一个可用的标签。
    anchor = own.get((flip.origin.index, flip.origin.ordinal))
    counter = own.get((flip.point.index, flip.point.ordinal))
    if (anchor is None or counter is None or anchor[1] != flip.origin or counter[1] != flip.point
            or not flip.origin.index < flip.point.index):
        return None
    following = [(raw, p) for raw, p in points
                 if (p.index, p.ordinal) > (flip.point.index, flip.point.ordinal)]
    if len(following) < 2:
        return None
    rebound_raw, rebound = following[0]
    _, key = following[1]
    if rebound.kind != 'H' or key.kind != 'L' or not flip.known < rebound.index < key.index:
        return None
    if rebound.price > flip.origin.price and not rejects_wick_extension(anchor[0], counter[0], rebound_raw):
        return None
    completion = rebound_raw.get('wave_completion_evidence')
    if not isinstance(completion, Mapping) or completion.get('wave_direction_after') != 'down':
        return None
    return _BearStructureCandidate(path, flip, rebound, key, rebound_raw)


def bearish_structure_breaks(
    stroke: Mapping[str, object], level: int, bars: Sequence[Bar], now: int, dates: Mapping[str, int],
    *, facts: Sequence['_Fact'] | None = None,
) -> list[dict[str, object]]:
    """公开输入每次解析；请求内受控结构由生命周期按版本复用候选。"""
    if now == 0:
        return []
    candidate = _prepare_bearish_structure(stroke, now, dates, facts=facts)
    return candidate.observe(level, bars, now, dates) if candidate is not None else []


def confirmed_source_bear(event: Mapping[str, object], now: int) -> bool:
    """接受完整六态确认或已翻空的后续下降破位，拒绝候选及未来证据。"""
    if event.get('event') == FAILED_BREAKOUT_EVENT:
        return confirmed_failed_breakout(event, now)
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
