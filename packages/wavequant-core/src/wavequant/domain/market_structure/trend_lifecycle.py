"""Polyline and trend causal lifecycle, separate from geometry and orders.

The caller supplies each date's own geometry. A final chart must never be
trimmed backwards to manufacture this history. Only compact facts and active
cycles are retained; price confirmation advances once per market date.
"""

from collections.abc import Iterable, Mapping, Sequence
from bisect import bisect_left
from heapq import heappop, heappush
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, cast

from .trend_point_version import PointVersion
from .trend_frozen_state import FrozenPrefixList
from .owned_evidence import CertifiedSource
from ..models.model import Bar
from .bull_alternation_confirmation import AlternationPolicy, confirm_bull_alternation
from .confirmation_gap import confirmation_gap
from .trend_market_index import _MarketRangeIndex
from .failed_breakout_exit import FailedBreakoutState, prepare_failed_breakout, STABLE_CLOSE_SESSIONS
from .source_bear_exit import (
    SOURCE_BEAR_EVENT, SourceMarketBreaks, _BearStructureCandidate, _prepare_bearish_structure,
    confirmed_bear_key_break, source_bearish_exits,
)

if TYPE_CHECKING:
    from .trend_lines import _OwnedPolylineTurns


LIFECYCLE_VERSION = 'six_state_polyline_volume_breakout_v33'


class LifecycleState(StrEnum):
    BULL_FLIP = '空翻多'
    BULL_ALTERNATION = '空多交替'
    BULL_CONFIRMED = '转多确认'
    BEAR_FLIP = '多翻空'
    BEAR_ALTERNATION = '多空交替'
    BEAR_CONFIRMED = '转空确认'


_TITLES = ('底部疑虑', '头部疑虑', '翻空为多', '翻多为空', '空多交替', '多空交替')


@dataclass(frozen=True, slots=True)
class _Pivot:
    index: int
    ordinal: int
    kind: str
    price: float
    known: int


@dataclass(frozen=True, slots=True)
class _Fact:
    title: str
    point: _Pivot
    known: int
    origin: _Pivot | None = None
    impulse: _Pivot | None = None
    broken: _Pivot | None = None


@dataclass(frozen=True, slots=True)
class _Cycle:
    state: LifecycleState
    up: bool
    origin: _Pivot
    started: int
    impulse: _Pivot | None = None
    counter: _Pivot | None = None
    alternated: int | None = None
    candidate: tuple[bool, bool, float] | None = None
    evidence: frozenset[_Fact] = frozenset()
    recovery: bool = False
    structure: tuple[_Pivot, ...] = ()
    confirmation_source: tuple[_Pivot, ...] = ()
    confirmation_gap: tuple[int, float, float, str] | None = None
    qualification_closed: int | None = None


@dataclass(frozen=True, slots=True)
class _BullRecovery:
    seed: _Fact
    breach: int | None = None
    consecutive_lower_lows: int = 0
    recovered: int | None = None


@dataclass(frozen=True, slots=True)
class _StructureFacts:
    """当前受控几何版本的解析结果；未来知识到达时必须重新解析。"""

    stroke: Mapping[str, object]
    facts: tuple[_Fact, ...]
    points: tuple[_Pivot, ...]
    point_orders: tuple[tuple[int, int], ...] | None
    fact_set: frozenset[_Fact]
    point_set: frozenset[_Pivot]
    next_known: int | None
    bear_candidate: _BearStructureCandidate | None
    failed_breakout_seed: _Fact | None


def _point_orders(points: tuple[_Pivot, ...]) -> tuple[tuple[int, int], ...] | None:
    """每个结构版本只建一次位置索引；非标准顺序保留公开输入的原遍历语义。"""
    orders = tuple((point.index, point.ordinal) for point in points)
    if any(previous >= current for previous, current in zip(orders, orders[1:])):
        return None
    return orders


def _next_structure_clock(value: object, dates: Mapping[str, int], now: int) -> tuple[bool, int | None]:
    pending = [value]
    visited: set[int] = set()
    following: int | None = None
    while pending:
        current = pending.pop()
        if type(current) in (str, int, float, bool, bytes, type(None)):
            continue
        if type(current) not in (dict, list, tuple, PointVersion, CertifiedSource):
            return False, None
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, dict):
            for field in ('index', 'available_at', 'wave_completed_at'):
                known = _clock(current.get(field), dates)
                if known is not None and known > now:
                    following = known if following is None else min(following, known)
            pending.extend(current.values())
        else:
            pending.extend(cast(Iterable[object], current))
    return True, following


class _StructureClocks:
    """只扫描变化点的证据；旧点的未来时钟到期时再推进到下一个知识日。

    仅在 _owned_structure 契约内使用，当前点版本持有所有未变点，
    不按外部可变字典身份认证，也不保留历史版本链或旧证明对象表。
    """

    def __init__(self) -> None:
        self.points: Sequence[Mapping[str, object]] | None = None
        self.dates: Mapping[str, int] | None = None
        self.future: dict[int, int] = {}
        self.heap: list[tuple[int, int]] = []

    def advance(self, value: object, dates: Mapping[str, int], now: int) -> tuple[bool, int | None]:
        if type(value) not in (list, tuple, PointVersion, CertifiedSource):
            _StructureClocks.__init__(self)
            return _next_structure_clock(value, dates, now)
        points = cast(Sequence[Mapping[str, object]], value)
        previous = self.points if self.dates is dates else None
        if previous is None:
            self.future.clear()
            self.heap.clear()
            start = 0
        elif previous is points:
            start = len(points)
        elif isinstance(points, PointVersion) and isinstance(previous, PointVersion):
            start = points.changed_since(previous)
        else:
            start = 0
            while start < min(len(points), len(previous)) and points[start] is previous[start]:
                start += 1
        if previous is not None:
            for position in range(start, len(previous)):
                self.future.pop(position, None)
        for position in range(start, len(points)):
            standard, following = _next_structure_clock(points[position], dates, now)
            if not standard:
                _StructureClocks.__init__(self)
                return False, None
            if following is not None:
                self.future[position] = following
                heappush(self.heap, (following, position))
        self.points, self.dates = points, dates
        while self.heap:
            following, position = self.heap[0]
            if self.future.get(position) != following:
                heappop(self.heap)
                continue
            if following > now:
                break
            heappop(self.heap)
            self.future.pop(position)
            standard, next_known = _next_structure_clock(points[position], dates, now)
            if not standard:
                _StructureClocks.__init__(self)
                return False, None
            if next_known is not None:
                self.future[position] = next_known
                heappush(self.heap, (next_known, position))
        if len(self.heap) > 2 * len(self.future) + 256:
            self.heap = [(known, position) for position, known in self.future.items()]
            from heapq import heapify
            heapify(self.heap)
        return True, self.heap[0][0] if self.heap else None


def advance_bull_recovery(
    state: _BullRecovery, low: float, close: float, now: int, *, previous_low: float,
) -> _BullRecovery | None:
    """跌破原低点后，最多连续3日最低价低于前日；持平或抬高重置计数。

    收盘严格收回原低点仍是恢复条件；不创新低或收回本身不授予交易资格。
    """
    origin = state.seed.origin
    if origin is None or now <= state.seed.known:
        return state
    if state.breach is None:
        if low >= origin.price:
            return state
        state = replace(state, breach=now)
    lower_lows = state.consecutive_lower_lows + 1 if low < previous_low else 0
    if lower_lows > 3:
        return None
    state = replace(state, consecutive_lower_lows=lower_lows)
    if close < origin.price:
        return replace(state, recovered=None)
    if close > origin.price and state.breach is not None and state.recovered is None:
        return replace(state, recovered=now)
    return state


def _continued_bull_impulse(cycle: _Cycle, points: tuple[_Pivot, ...],
                            point_orders: tuple[tuple[int, int], ...] | None = None) -> _Cycle | None:
    """同一腿高点向右/向上延伸保留特殊属性；基准或起涨低点修订仍须失效。"""
    if not cycle.recovery or cycle.alternated is not None or len(cycle.structure) != 3 or cycle.impulse is None:
        return None
    broken, origin, previous = cycle.structure
    if point_orders is not None:
        start = bisect_left(point_orders, (broken.index, broken.ordinal))
        points = points[start:start + 3]
    for key, low, high in zip(points, points[1:], points[2:]):
        if (key != broken or low != origin or high.kind != 'H'
                or (high.index, high.ordinal) < (previous.index, previous.ordinal)
                or high.price < previous.price or high.known < previous.known):
            continue
        return replace(cycle, impulse=high, structure=(key, low, high), candidate=None)
    return None


def _clock(value: object, dates: Mapping[str, int]) -> int | None:
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, str):
        return int(value) if value.isdecimal() else dates.get(value)
    return None


def _pivot(value: object, dates: Mapping[str, int], now: int) -> _Pivot | None:
    if not isinstance(value, Mapping) or value.get('state') in ('seed', 'developing') or value.get('display_only'):
        return None
    index, ordinal, price, kind = (value.get('index'), value.get('ordinal', 0),
                                    value.get('value'), value.get('kind'))
    known = _clock(value.get('available_at'), dates)
    if (type(index) is not int or type(ordinal) is not int or not 0 <= index <= now or ordinal < 0
            or type(price) not in (int, float) or not isfinite(cast(float, price)) or kind not in ('H', 'L')
            or known is None or not index <= known <= now):
        return None
    return _Pivot(index, ordinal, kind, cast(float, price), known)


def _records(value: object) -> Sequence[Mapping[str, object]]:
    if type(value) in (PointVersion, CertifiedSource):
        owned = cast(PointVersion | CertifiedSource, value)
        if owned._sealed:
            return owned
    if not isinstance(value, (tuple, list, PointVersion, CertifiedSource)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _upgrade_flip_fact(raw: Mapping[str, object], point: _Pivot,
                       own_points: Mapping[tuple[int, int], _Pivot],
                       dates: Mapping[str, int], now: int) -> _Fact | None:
    """两种方向共用本级冻结关键位与正式攻击的知识链，不依赖展示标签。

    局部升级不得替换本级冻结关键位；起点、关键位及攻击必须真实属于
    本级当前前缀。直接来源更早的确认不能冒充本级确认。
    """
    proof = raw.get('upgrade_evidence')
    up = point.kind == 'H'
    direction, origin_kind = ('up', 'L') if up else ('down', 'H')
    sign = 1 if up else -1
    if (not isinstance(proof, Mapping) or proof.get('direction') != direction
            or proof.get('role') != 'endpoint'):
        return None
    known = _clock(proof.get('available_at'), dates)
    origin_ref = _pivot(proof.get('origin'), dates, now)
    broken_ref = _pivot(proof.get('broken_key'), dates, now)
    confirming = _pivot(proof.get('confirmed_by'), dates, now)
    if known is None or known > now or origin_ref is None or broken_ref is None or confirming is None:
        return None
    origin = own_points.get((origin_ref.index, origin_ref.ordinal))
    broken = own_points.get((broken_ref.index, broken_ref.ordinal))
    if (origin is None or broken is None or origin.kind != origin_kind or broken.kind != point.kind
            or origin.price != origin_ref.price or broken.price != broken_ref.price
            or confirming.kind != point.kind or confirming.price != point.price
            or (confirming.index, confirming.ordinal) != (point.index, point.ordinal)
            or not (broken.index, broken.ordinal) < (origin.index, origin.ordinal) < (point.index, point.ordinal)
            or sign * (broken.price - origin.price) <= 0 or sign * (point.price - broken.price) <= 0):
        return None
    # 本级关键位状态由几何标注器生成。疑虑可能在更早的低点上，不能只检查
    # 局部上涨起点的 observations；缺失上下文也不能用升级证书推测翻多。
    context = raw.get('lifecycle_context')
    if not isinstance(context, Mapping) or context.get('direction') != direction:
        return None
    context_known = _clock(context.get('available_at'), dates)
    frozen = _pivot(context.get('key'), dates, now)
    background_origin = _pivot(context.get('origin'), dates, now)
    if (context_known is None or context_known > now or frozen is None or background_origin is None
            or frozen.kind != point.kind or background_origin.kind != origin_kind
            or own_points.get((frozen.index, frozen.ordinal)) != frozen
            or own_points.get((background_origin.index, background_origin.ordinal)) != background_origin
            or (broken.index, broken.ordinal, broken.price) != (frozen.index, frozen.ordinal, frozen.price)
            or not (frozen.index, frozen.ordinal) < (background_origin.index, background_origin.ordinal)
                <= (origin.index, origin.ordinal)
            or sign * (frozen.price - background_origin.price) <= 0 or sign * (point.price - frozen.price) <= 0):
        return None
    available = max(known, point.known, origin.known, broken.known, confirming.known,
                    context_known, background_origin.known)
    if broken.known >= point.index:
        return None
    return _Fact('翻空为多' if up else '翻多为空', point, available, background_origin, broken=frozen)


def _point_facts(raw: Mapping[str, object], point: _Pivot, own_points: Mapping[tuple[int, int], _Pivot],
                 dates: Mapping[str, int], now: int) -> tuple[_Fact, ...]:
    result: list[_Fact] = []
    ordinary_flip = False
    for observation in _records(raw.get('observations')):
        title = observation.get('title')
        known = _clock(observation.get('available_at'), dates)
        if not isinstance(title, str) or title not in _TITLES or known is None or known > now:
            continue
        up = title in ('底部疑虑', '翻空为多', '空多交替')
        expected = ('H' if up else 'L') if title in ('翻空为多', '翻多为空') else ('L' if up else 'H')
        if point.kind != expected:
            continue
        origin_raw = observation.get('confirmed_low' if up else 'confirmed_high', observation.get('origin'))
        origin = _pivot(origin_raw, dates, now)
        impulse_raw = observation.get('flip_high' if up else 'flip_low')
        if title in ('底部疑虑', '头部疑虑'):
            impulse_raw = observation.get('key')
        impulse = _pivot(impulse_raw, dates, now)
        if (origin_raw is not None and origin is None) or (impulse_raw is not None and impulse is None):
            continue
        frozen = None
        if title in ('翻空为多', '翻多为空') and observation.get('key') is not None:
            frozen = _pivot(observation['key'], dates, now)
            sign = 1 if up else -1
            if (frozen is None or frozen.kind != point.kind or sign * (point.price - frozen.price) <= 0
                    or (frozen.index, frozen.ordinal) >= (point.index, point.ordinal)):
                continue
        result.append(_Fact(title, point, max(known, point.known,
            origin.known if origin else 0, impulse.known if impulse else 0,
            frozen.known if frozen else 0), origin, impulse, frozen))
        ordinary_flip = ordinary_flip or title == ('翻空为多' if point.kind == 'H' else '翻多为空')
    # 本级冻结关键位的正式突破优先；没有正式标记才读取升级证明。
    if not ordinary_flip:
        upgrade = _upgrade_flip_fact(raw, point, own_points, dates, now)
        if upgrade is not None:
            result.append(upgrade)
    return tuple(result)


def _ordered_facts(values: Iterable[_Fact]) -> tuple[_Fact, ...]:
    return tuple(sorted(dict.fromkeys(values), key=lambda fact: (
        fact.known, fact.point.index, fact.point.ordinal, _TITLES.index(fact.title))))


def _facts(points: object, dates: Mapping[str, int], now: int, *,
           _parsed: Sequence[tuple[Mapping[str, object], _Pivot]] | None = None) -> tuple[_Fact, ...]:
    parsed = (_parsed if _parsed is not None else tuple((raw, point) for raw in _records(points)
              if (point := _pivot(raw, dates, now)) is not None))
    own_points = {(point.index, point.ordinal): point for _, point in parsed}
    return _ordered_facts(fact for raw, point in parsed for fact in _point_facts(raw, point, own_points, dates, now))


class _StructureProjection:
    """受控有序点的事实投影；新尾部不重新读取未变前缀的嵌套证明。

    升级事实只引用该点之前的本级坐标，最早修订位置覆盖所有受影响的
    后继事实。知识时钟到期或非严格有序输入则重新投影，不能借用旧结论。
    """

    def __init__(self) -> None:
        self.points: PointVersion | None = None
        self.dates: Mapping[str, int] | None = None
        self.parsed = FrozenPrefixList[tuple[Mapping[str, object], _Pivot] | None]()
        self.facts = FrozenPrefixList[tuple[_Fact, ...]]()
        self.own: dict[tuple[int, int], _Pivot] = {}

    def advance(self, points: PointVersion, dates: Mapping[str, int], now: int, *, restart: bool) -> tuple[
        tuple[_Fact, ...], tuple[tuple[Mapping[str, object], _Pivot], ...],
    ] | None:
        start = points.changed_since(self.points) if not restart and self.dates is dates else 0
        for entry in self.parsed.iter_range(start):
            if entry is not None:
                self.own.pop((entry[1].index, entry[1].ordinal), None)
        del self.parsed[start:]
        del self.facts[start:]
        previous = next(reversed(self.own.values()), None)
        for raw in points.iter_range(start):
            point = _pivot(raw, dates, now)
            if point is not None:
                if previous is not None and (previous.index, previous.ordinal) >= (point.index, point.ordinal):
                    self.__init__()
                    return None
                self.own[point.index, point.ordinal] = point
                previous = point
            self.parsed.append((raw, point) if point is not None else None)
        for entry in self.parsed.iter_range(start):
            self.facts.append(_point_facts(entry[0], entry[1], self.own, dates, now) if entry is not None else ())
        self.parsed.freeze(len(self.parsed))
        self.facts.freeze(len(self.facts))
        self.points, self.dates = points, dates
        parsed = tuple(entry for entry in self.parsed if entry is not None)
        facts = _ordered_facts(fact for values in self.facts for fact in values)
        return facts, parsed


class TrendLifecycle:
    """Request-owned compact state, copied at checkpoints before a partial day.

    The polyline and three trend levels use identical transitions. The six named states are
    supplemented by explicit invalidation, not by an invented seventh trend.
    Candidate gap/volume evidence cannot confirm an unfinished alternation.
    """

    def __init__(self) -> None:
        self._cycles: dict[tuple[int, str], _Cycle] = {}
        self._seen: dict[tuple[int, str], frozenset[_Fact]] = {}
        self._bull_recovery: dict[tuple[int, str], _BullRecovery] = {}
        self._polyline_known: dict[tuple[int, str], dict[tuple[int, int, str, float], int]] = {}
        self._last_index = -1
        self._bear_breaks: dict[tuple[int, str], frozenset[tuple[int, int, int]]] = {}
        self._failed_breakouts: dict[tuple[int, str], FailedBreakoutState] = {}
        self._structure_facts: dict[tuple[int, str], _StructureFacts] = {}
        self._structure_clocks: dict[tuple[int, str], _StructureClocks] = {}
        self._structure_projections: dict[tuple[int, str], _StructureProjection] = {}
        self._checkpoint_seen: dict[
            tuple[int, str], tuple[frozenset[_Fact], tuple[tuple[object, ...], ...]]
        ] = {}
        self._source_turns: '_OwnedPolylineTurns | None' = None
        self._source_market_breaks = SourceMarketBreaks()

    @staticmethod
    def _needs_bull_confirmation(cycle: _Cycle) -> bool:
        return (cycle.up and cycle.impulse is not None
                and (cycle.recovery or any(fact.title == '翻空为多' for fact in cycle.evidence))
                and (cycle.alternated is None or cycle.confirmation_gap is not None
                     or cycle.qualification_closed is not None))

    def resume_qualification_search(self, level: int, path: str, now: int, bars: Sequence[Bar]) -> None:
        """资格关闭只重启确认搜索，不撤销仍守住原低点的本级翻多结构。"""
        if now != self._last_index or not 0 <= now < len(bars):
            raise ValueError('qualification closure must follow the current lifecycle day')
        key = level, path
        cycle = self._cycles.get(key)
        if (cycle is None or not cycle.up or cycle.impulse is None
                or bars[now].low < cycle.origin.price):
            return
        self._cycles[key] = replace(cycle, qualification_closed=now)

    def pending_bull_paths(self, level: int) -> frozenset[str]:
        """仅本级真实空翻多后的未决交替允许按需申请内部 N 证明。"""
        if type(level) is not int or level not in (0, 1, 2, 3):
            raise ValueError('a lifecycle level between zero and three is required')
        return frozenset(path for (number, path), cycle in self._cycles.items()
                         if number == level and self._needs_bull_confirmation(cycle))

    def copy(self) -> 'TrendLifecycle':
        other = TrendLifecycle()
        other._cycles = self._cycles.copy()
        other._seen = self._seen.copy()
        other._bull_recovery = self._bull_recovery.copy()
        other._polyline_known = {key: values.copy() for key, values in self._polyline_known.items()}
        other._last_index = self._last_index
        other._bear_breaks = dict(self._bear_breaks)
        other._failed_breakouts = dict(self._failed_breakouts)
        return other

    @classmethod
    def combine(cls, states: Sequence['TrendLifecycle']) -> 'TrendLifecycle':
        """合并同日不同级别的状态，保留独立容器及不可变事实。

        请求内部的同日消费者可直接续接这些领域值，不必先编码整个历史
        再逐条解析。几何对象身份缓存不跨引擎复制；重复路径与异日状态拒绝合并。
        """
        combined = cls()
        if not states:
            return combined
        combined._last_index = states[0]._last_index
        occupied: set[tuple[int, str]] = set()
        for state in states:
            keys = (state._cycles.keys() | state._seen.keys() | state._bull_recovery.keys()
                    | state._polyline_known.keys() | state._bear_breaks.keys() | state._failed_breakouts.keys())
            if state._last_index != combined._last_index or occupied.intersection(keys):
                raise ValueError('lifecycle snapshots require the same date and disjoint paths')
            occupied.update(keys)
            combined._cycles.update(state._cycles)
            combined._seen.update(state._seen)
            combined._bull_recovery.update(state._bull_recovery)
            combined._polyline_known.update({key: values.copy() for key, values in state._polyline_known.items()})
            combined._bear_breaks.update(state._bear_breaks)
            combined._failed_breakouts.update(state._failed_breakouts)
        return combined

    def checkpoint(self) -> tuple[object, ...]:
        """Only builtin immutable values enter the bounded checkpoint journal."""
        def pivot(value: _Pivot | None) -> tuple[object, ...] | None:
            return (value.index, value.ordinal, value.kind, value.price, value.known) if value else None

        def fact(value: _Fact) -> tuple[object, ...]:
            return (value.title, pivot(value.point), value.known, pivot(value.origin),
                    pivot(value.impulse), pivot(value.broken))

        rows = []
        for key, seen in sorted(self._seen.items()):
            previous_seen = self._checkpoint_seen.get(key)
            if previous_seen is not None and previous_seen[0] is seen:
                saved_seen = previous_seen[1]
            else:
                saved_seen = tuple(sorted((fact(item) for item in seen), key=repr))
                self._checkpoint_seen[key] = seen, saved_seen
            cycle = self._cycles.get(key)
            saved = (cycle.state.value, cycle.up, pivot(cycle.origin), cycle.started, pivot(cycle.impulse),
                     pivot(cycle.counter), cycle.alternated, cycle.candidate,
                     tuple(sorted((fact(item) for item in cycle.evidence), key=repr)),
                     cycle.recovery, tuple(pivot(item) for item in cycle.structure),
                     tuple(pivot(item) for item in cycle.confirmation_source), cycle.confirmation_gap,
                     cycle.qualification_closed) if cycle else None
            recovery = self._bull_recovery.get(key)
            pending = (fact(recovery.seed), recovery.breach, recovery.consecutive_lower_lows, recovery.recovered) if recovery else None
            source_clocks = tuple((*point, known) for point, known in sorted(self._polyline_known.get(key, {}).items()))
            failed = self._failed_breakouts.get(key)
            saved_failed = (fact(failed.seed), failed.last, failed.attack, failed.volume_mean,
                            failed.consecutive, failed.longest, failed.finished) if failed else None
            rows.append((key, saved_seen, saved, pending,
                         source_clocks, tuple(sorted(self._bear_breaks.get(key, ()))), saved_failed))
        return LIFECYCLE_VERSION, self._last_index, tuple(rows)

    @classmethod
    def from_checkpoint(cls, value: object) -> 'TrendLifecycle':
        """Restore our private format without adding executable pickle types."""
        def sequence(raw: object, size: int) -> tuple[object, ...]:
            if not isinstance(raw, tuple) or len(raw) != size:
                raise ValueError('invalid lifecycle checkpoint shape')
            return raw

        version, last, rows = sequence(value, 3)
        if version != LIFECYCLE_VERSION or type(last) is not int or last < -1 or not isinstance(rows, tuple):
            raise ValueError('incompatible lifecycle checkpoint')

        def integer(raw: object) -> int:
            if type(raw) is not int or not 0 <= raw <= last:
                raise ValueError('invalid lifecycle checkpoint clock')
            return raw

        def pivot(raw: object) -> _Pivot | None:
            if raw is None:
                return None
            index, ordinal, kind, price, known = sequence(raw, 5)
            parsed = _pivot(dict(index=index, ordinal=ordinal, kind=kind, value=price, available_at=known), {}, last)
            if parsed is None:
                raise ValueError('invalid lifecycle checkpoint point')
            return parsed

        def facts(raw: object) -> frozenset[_Fact]:
            if not isinstance(raw, tuple):
                raise ValueError('invalid lifecycle checkpoint facts')
            result = []
            for row in raw:
                title, point, known, origin, impulse, broken = sequence(row, 6)
                parsed = pivot(point)
                if not isinstance(title, str) or title not in _TITLES or parsed is None:
                    raise ValueError('invalid lifecycle checkpoint fact')
                result.append(_Fact(title, parsed, integer(known), pivot(origin), pivot(impulse), pivot(broken)))
            return frozenset(result)

        restored = cls()
        restored._last_index = last
        for row in rows:
            identity, seen, raw, pending, source_clocks, bear_breaks, saved_failed = sequence(row, 7)
            level, path = sequence(identity, 2)
            if type(level) is not int or level not in (0, 1, 2, 3) or not isinstance(path, str):
                raise ValueError('invalid lifecycle checkpoint path')
            key = level, path
            restored._seen[key] = facts(seen)
            if not isinstance(bear_breaks, tuple):
                raise ValueError('invalid bearish structure break ledger')
            breaks = []
            for saved_break in bear_breaks:
                prior, origin, low = sequence(saved_break, 3)
                entry = integer(prior), integer(origin), integer(low)
                if not entry[0] < entry[1] < entry[2]:
                    raise ValueError('invalid bearish structure break order')
                breaks.append(entry)
            restored._bear_breaks[key] = frozenset(breaks)
            if saved_failed is not None:
                seed, observed, attack, mean, consecutive, longest, finished = sequence(saved_failed, 7)
                parsed_seed = next(iter(facts((seed,))))
                observed_index = integer(observed)
                attack_index = integer(attack) if attack is not None else None
                if (parsed_seed.title != '翻多为空' or parsed_seed.origin is None or parsed_seed.broken is None
                        or not parsed_seed.origin.index < parsed_seed.point.index <= parsed_seed.known <= observed_index
                        or (attack_index is not None and not parsed_seed.known < attack_index <= observed_index)
                        or type(mean) not in (float, int) or not isfinite(cast(float, mean)) or cast(float, mean) < 0
                        or type(consecutive) is not int or type(longest) is not int
                        or not 0 <= consecutive <= longest <= STABLE_CLOSE_SESSIONS or type(finished) is not bool
                        or (longest == STABLE_CLOSE_SESSIONS and not finished)):
                    raise ValueError('invalid failed breakout checkpoint')
                restored._failed_breakouts[key] = FailedBreakoutState(parsed_seed, observed_index, attack_index,
                    float(cast(float, mean)), consecutive, longest, finished)
            if not isinstance(source_clocks, tuple):
                raise ValueError('invalid polyline confirmation clocks')
            clocks = {}
            for source_clock in source_clocks:
                point = pivot(source_clock)
                if point is None:
                    raise ValueError('invalid polyline confirmation point')
                clocks[point.index, point.ordinal, point.kind, point.price] = point.known
            restored._polyline_known[key] = clocks
            if pending is not None:
                seed, breach, lower_lows, recovered = sequence(pending, 4)
                parsed_seed = next(iter(facts((seed,))))
                if (parsed_seed.title != '翻空为多' or parsed_seed.origin is None
                        or type(lower_lows) is not int or not 0 <= lower_lows <= 3):
                    raise ValueError('invalid bull recovery checkpoint')
                restored._bull_recovery[key] = _BullRecovery(parsed_seed,
                    integer(breach) if breach is not None else None, lower_lows,
                    integer(recovered) if recovered is not None else None)
            if raw is None:
                continue
            (state, up, origin, started, impulse, counter, alternated, candidate, evidence,
             recovery, structure, source, saved_gap, qualification_closed) = sequence(raw, 14)
            parsed_origin = pivot(origin)
            if not isinstance(state, str) or type(up) is not bool or parsed_origin is None:
                raise ValueError('invalid lifecycle checkpoint cycle')
            hint: tuple[bool, bool, float] | None = None
            if candidate is not None:
                candidate_gap, volume, price = sequence(candidate, 3)
                if type(candidate_gap) is not bool or type(volume) is not bool or type(price) not in (int, float) or not isfinite(cast(float, price)):
                    raise ValueError('invalid lifecycle checkpoint candidate')
                hint = candidate_gap, volume, cast(float, price)
            if type(recovery) is not bool or not isinstance(structure, tuple) or not isinstance(source, tuple):
                raise ValueError('invalid lifecycle structure checkpoint')
            parsed_structure = tuple(pivot(item) for item in structure)
            parsed_source = tuple(pivot(item) for item in source)
            if any(item is None for item in (*parsed_structure, *parsed_source)):
                raise ValueError('invalid lifecycle structure point')
            parsed_gap = None
            if saved_gap is not None:
                gap_index, lower, upper, kind = sequence(saved_gap, 4)
                gap_clock = integer(gap_index)
                if (kind not in ('body', 'full') or gap_index != alternated or up is not True
                        or type(lower) not in (int, float) or type(upper) not in (int, float)
                        or not isfinite(cast(float, lower)) or not isfinite(cast(float, upper))
                        or not 0 < cast(float, lower) < cast(float, upper)):
                    raise ValueError('invalid lifecycle confirmation gap')
                parsed_gap = gap_clock, float(cast(float, lower)), float(cast(float, upper)), kind
            restored._cycles[key] = _Cycle(LifecycleState(state), up, parsed_origin, integer(started),
                pivot(impulse), pivot(counter), integer(alternated) if alternated is not None else None,
                hint, facts(evidence), recovery, tuple(item for item in parsed_structure if item is not None),
                tuple(item for item in parsed_source if item is not None), parsed_gap,
                integer(qualification_closed) if qualification_closed is not None else None)
        return restored

    def advance(self, levels: Sequence[Mapping[str, object]], bars: Sequence[Bar], now: int,
                dates: Mapping[str, int], *, polyline: Sequence[Mapping[str, object]] = (),
                polyline_path: str | None = None,
                source_events: Sequence[Mapping[str, object]] = (),
                confirmation_policy: AlternationPolicy = AlternationPolicy(),
                _owned_structure: bool = False,
                _market_index: _MarketRangeIndex | None = None) -> list[dict[str, object]]:
        """Consume one causal date; repeated/rewound dates require a checkpoint.

        私有结构借用只接收生成后不再修改的版本。价格、候选和源事件仍每日
        消费；公开可变输入不参与身份缓存，未来知识时钟到达时重新解析。
        """
        if type(now) is not int or not 0 <= now < len(bars) or now != self._last_index + 1:
            raise ValueError('lifecycle requires consecutive causal market dates')
        market_index = _market_index if _market_index is not None and bars is _market_index.bars else None
        self._last_index = now
        if not _owned_structure:
            self._structure_facts.clear()
            self._structure_clocks.clear()
            self._structure_projections.clear()
            self._source_turns = None
        events: list[dict[str, object]] = []
        present: set[tuple[int, str]] = set()
        for level in levels:
            number = level.get('trend_level')
            if type(number) is not int or number not in (0, 1, 2, 3):
                raise ValueError('lifecycle level must be 0 (polyline), 1, 2 or 3')
            cutoffs = level.get('_path_market_end_indices', {})
            for stroke in _records(level.get('strokes')):
                path = stroke.get('id')
                if not isinstance(path, str):
                    continue
                cutoff = cutoffs.get(stroke.get('source_path')) if isinstance(cutoffs, Mapping) else None
                if type(cutoff) is int and now >= cutoff:
                    continue
                key = number, path
                present.add(key)
                self._expire_confirmation_gap(key, bars, now, events)
                cached = self._structure_facts.get(key) if _owned_structure else None
                unchanged = (cached is not None
                    and (cached.stroke is stroke or cached.stroke.get('points') is stroke.get('points'))
                    and (cached.next_known is None or now < cached.next_known))
                if unchanged:
                    assert cached is not None
                    facts, parsed_points = cached.facts, cached.points
                    point_orders = cached.point_orders
                    fact_set, parsed_set = cached.fact_set, cached.point_set
                    bear_candidate = cached.bear_candidate
                    failed_seed = cached.failed_breakout_seed
                else:
                    raw_points = stroke.get('points')
                    standard, next_known = False, None
                    if _owned_structure:
                        clocks = self._structure_clocks.setdefault(key, _StructureClocks())
                        standard, next_known = clocks.advance(raw_points, dates, now)
                    projected = None
                    if standard and type(raw_points) in (PointVersion, CertifiedSource) and raw_points._sealed:
                        projection = self._structure_projections.setdefault(key, _StructureProjection())
                        projected = projection.advance(cast(PointVersion, raw_points), dates, now,
                            restart=cached is None or cached.next_known is not None and now >= cached.next_known)
                    else:
                        self._structure_projections.pop(key, None)
                    if projected is None:
                        parsed = tuple((raw, point) for raw in _records(raw_points)
                                       if (point := _pivot(raw, dates, now)) is not None)
                        facts = _facts(raw_points, dates, now, _parsed=parsed)
                    else:
                        facts, parsed = projected
                    parsed_points = tuple(point for _, point in parsed)
                    point_orders = _point_orders(parsed_points)
                    fact_set, parsed_set = frozenset(facts), frozenset(parsed_points)
                    bear_candidate = _prepare_bearish_structure(stroke, now, dates, facts=facts, _parsed=parsed)
                    failed_seed = prepare_failed_breakout(facts, parsed_points)
                    if _owned_structure:
                        if standard:
                            self._structure_facts[key] = _StructureFacts(
                                stroke, facts, parsed_points, point_orders, fact_set, parsed_set, next_known,
                                bear_candidate, failed_seed)
                        else:
                            self._structure_facts.pop(key, None)
                seen = self._seen.get(key, frozenset())
                cycle = self._cycles.get(key)
                needs_points = (key in self._bull_recovery or (cycle is not None and
                                (bool(cycle.structure) or (cycle.up and cycle.impulse is not None)))
                                or not unchanged and any(fact.title == '翻空为多' and fact not in seen for fact in facts))
                points = parsed_points if needs_points else ()
                point_set = parsed_set if needs_points else frozenset()
                source_points: tuple[_Pivot, ...] = ()
                if needs_points and polyline and (polyline_path is None or path.endswith(polyline_path)):
                    from .trend_lines import _OwnedPolylineTurns, polyline_turns

                    start = (cycle.impulse.index if cycle is not None and cycle.up
                             and cycle.impulse is not None else max(0, now - confirmation_policy.structure_window))
                    if _owned_structure:
                        if self._source_turns is None:
                            self._source_turns = _OwnedPolylineTurns()
                        # 仅受控尾部写入借用投影；先按翻向窗口定位，再解析知识时钟。
                        # path 只参与来源匹配，不属于这里消费的价格/确认事实。
                        turns = self._source_turns.project(polyline, source_path=polyline_path or '',
                            _borrow_readonly=True, _start_index=start)
                    else:
                        turns = polyline_turns(polyline, source_path=path)
                    source_points = tuple(point for raw in turns
                        if raw.get('source_kind') == raw.get('kind')
                        and (point := _pivot(raw, dates, now)) is not None
                        and point.index >= start)
                    prior_clocks = self._polyline_known.get(key, {})
                    source_points = tuple(replace(point, known=max(point.known,
                        prior_clocks.get((point.index, point.ordinal, point.kind, point.price), now)))
                        for point in source_points)
                    self._polyline_known[key] = {(p.index, p.ordinal, p.kind, p.price): p.known for p in source_points}
                if (cycle is not None and cycle.evidence.issubset(fact_set)
                        and not set(cycle.structure).issubset(point_set)):
                    continued = _continued_bull_impulse(cycle, points, point_orders)
                    if continued is not None:
                        cycle = continued
                        self._cycles[key] = cycle
                        events.append(self._event(key, cycle, now, 'trend_lifecycle_anchor',
                            source_event='特殊空翻多同腿高点延伸',
                            confirmation_rule='same_origin_and_broken_key_confirmed_high_extension'))
                if cycle is not None and (not cycle.evidence.issubset(fact_set)
                                          or not set(cycle.structure).issubset(point_set)
                                          or not set(cycle.confirmation_source).issubset(source_points)):
                    self._cycles.pop(key)
                    events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated',
                                              reason='source_evidence_revised'))
                recovery = self._bull_recovery.get(key)
                if recovery is not None and recovery.seed not in fact_set:
                    self._bull_recovery.pop(key)
                for fact in (() if unchanged else facts):
                    if fact not in seen:
                        recovery = self._bull_recovery.get(key)
                        active = self._cycles.get(key)
                        if (fact.title == '翻空为多' and fact.origin is not None
                                and (recovery is None or recovery.breach is None)
                                and not (active is not None and active.recovery and active.alternated is None)):
                            self._bull_recovery[key] = _BullRecovery(fact)
                        self._consume(key, fact, now, events)
                if not unchanged:
                    # 新几何未必增加六态事实；保持未变账本身份，检查点复用已有标量编码。
                    additions = fact_set.difference(seen)
                    self._seen[key] = seen.union(additions) if additions else seen
                self._advance_bull_recovery(key, points, bars, now, events, point_orders, market_index)
                self._advance_bull_alternation(key, points, bars, now, events, source_points,
                                               confirmation_policy, point_orders, market_index)
                self._price(key, bars, now, events)
                failed_state = self._failed_breakouts.get(key)
                if failed_seed is None:
                    self._failed_breakouts.pop(key, None)
                else:
                    if failed_state is None or failed_state.seed != failed_seed:
                        failed_state = FailedBreakoutState(failed_seed, failed_seed.known)
                    failed_state, failed_event = failed_state.advance(bars, now, number, path)
                    self._failed_breakouts[key] = failed_state
                    if failed_event is not None:
                        events.append(failed_event)
                for event in (bear_candidate.observe(number, bars, now, dates, _market_index=market_index)
                              if bear_candidate is not None else ()):
                    identity = tuple(cast(int, event[field]) for field in
                                     ('prior_flip_index', 'origin_index', 'flip_index'))
                    break_key = identity[0], identity[1], identity[2]
                    broken = self._bear_breaks.get(key, frozenset())
                    if break_key not in broken:
                        self._bear_breaks[key] = broken.union((break_key,))
                        events.append(event)
                pending = self._bull_recovery.get(key)
                active = self._cycles.get(key)
                if (pending is not None and pending.breach is None and active is not None
                        and active.up and active.alternated is not None):
                    # 已完成普通交替的旧翻多不再作为未来任意下跌的特殊恢复种子。
                    self._bull_recovery.pop(key)
        for key in self._cycles.keys() - present:
            cycle = self._cycles.pop(key)
            events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated', reason='source_path_ended'))
        # A price stop can clear the six-state cycle while a trading interval
        # still waits for the opposite alternation. It must still learn that
        # its source path ended; otherwise it could bridge unrelated history.
        ended = {(event['trend_level'], event['source_path']) for event in events
                 if event.get('reason') == 'source_path_ended'}
        for key in self._seen.keys() - present - ended:
            events.append(dict(event='trend_lifecycle_invalidated', bar_index=now, available_index=now,
                trend_level=key[0], source_path=key[1], reason='source_path_ended', rule_version=LIFECYCLE_VERSION))
        # Closed paths are no longer replayed, so their observation ledgers can go.
        self._seen = {key: facts for key, facts in self._seen.items() if key in present}
        self._bull_recovery = {key: state for key, state in self._bull_recovery.items() if key in present}
        self._polyline_known = {key: clocks for key, clocks in self._polyline_known.items() if key in present}
        self._bear_breaks = {key: breaks for key, breaks in self._bear_breaks.items() if key in present}
        self._failed_breakouts = {key: state for key, state in self._failed_breakouts.items() if key in present}
        self._structure_facts = {key: facts for key, facts in self._structure_facts.items() if key in present}
        self._structure_clocks = {key: clocks for key, clocks in self._structure_clocks.items() if key in present}
        self._structure_projections = {key: value for key, value in self._structure_projections.items() if key in present}
        self._checkpoint_seen = {key: facts for key, facts in self._checkpoint_seen.items() if key in present}
        # 单级联动借用当日下级事件；整层恢复使用同一次 advance 的下级结果。
        market_breaks = self._source_market_breaks.observe(levels, bars, now, dates,
            owned=_owned_structure, market_index=market_index)
        events.extend(source_bearish_exits(levels, (*source_events, *events, *market_breaks), now))
        return events

    def _expire_confirmation_gap(self, key: tuple[int, str], bars: Sequence[Bar], now: int,
                                 events: list[dict[str, object]]) -> None:
        """撤销跳空这项交替确认，保留翻多周期，继续等待其他规则或新的跳空。"""
        cycle = self._cycles.get(key)
        if cycle is None or cycle.confirmation_gap is None:
            return
        index, lower, upper, kind = cycle.confirmation_gap
        if now <= index or bars[now].low > lower:
            return
        events.append(self._event(key, cycle, now, 'trend_lifecycle_confirmation_revoked',
            reason='confirmation_gap_filled', qualification_index=index,
            confirmation_gap_index=index, confirmation_gap_lower=lower,
            confirmation_gap_upper=upper, confirmation_gap_kind=kind, market_low=bars[now].low))
        self._cycles[key] = replace(cycle, state=LifecycleState.BULL_FLIP, counter=None,
            alternated=None, candidate=None, confirmation_source=(), confirmation_gap=None,
            structure=cycle.structure[:3] if cycle.recovery else ())

    @staticmethod
    def _event(key: tuple[int, str], cycle: _Cycle, now: int, event: str,
               **extra: object) -> dict[str, object]:
        return dict(event=event, bar_index=now, available_index=now, trend_level=key[0], source_path=key[1],
                    state=cycle.state.value, direction='up' if cycle.up else 'down',
                    cycle_started_index=cycle.started, origin_index=cycle.origin.index,
                    origin_price=cycle.origin.price, origin_ordinal=cycle.origin.ordinal,
                    breakout_level=cycle.impulse.price if cycle.impulse else None,
                    flip_index=cycle.impulse.index if cycle.impulse else None,
                    flip_known_index=cycle.impulse.known if cycle.impulse else None,
                    flip_ordinal=cycle.impulse.ordinal if cycle.impulse else None,
                    alternation_index=cycle.alternated,
                    counter_index=cycle.counter.index if cycle.counter else None,
                    counter_ordinal=cycle.counter.ordinal if cycle.counter else None,
                    counter_price=cycle.counter.price if cycle.counter else None,
                    recovery_key_index=cycle.structure[0].index if cycle.recovery and cycle.structure else None,
                    recovery_key_price=cycle.structure[0].price if cycle.recovery and cycle.structure else None,
                    flip_confirmed=cycle.recovery or any(
                        fact.title == ('翻空为多' if cycle.up else '翻多为空') for fact in cycle.evidence),
                    special_bull_recovery=cycle.recovery, rule_version=LIFECYCLE_VERSION, **extra)

    def _consume(self, key: tuple[int, str], fact: _Fact, now: int,
                 events: list[dict[str, object]]) -> None:
        if fact.title in ('底部疑虑', '头部疑虑'):
            # 疑虑只保留观察记录，不建立翻向周期，也不撤销既有周期。
            events.append(dict(event='trend_lifecycle_suspicion', bar_index=now, available_index=now,
                trend_level=key[0], source_path=key[1], source_event=fact.title,
                source_known_index=fact.known, origin_index=fact.point.index,
                origin_price=fact.point.price, confirmed=False, rule_version=LIFECYCLE_VERSION))
            return
        up = fact.title in ('底部疑虑', '翻空为多', '空多交替')
        cycle = self._cycles.get(key)
        recovery = self._bull_recovery.get(key)
        if ((up and recovery is not None and recovery.breach is not None)
                or (cycle is not None and cycle.recovery and cycle.alternated is None)):
            # 恢复候选不得屏蔽其后正式跌破冻结末升低的反向事实；裸标签仍不放行。
            if fact.title != '翻多为空' or fact.origin is None or fact.broken is None:
                return
            bearish = _Cycle(LifecycleState.BEAR_FLIP, False, fact.origin, now, fact.point,
                             evidence=frozenset((fact,)))
            proof = self._event(key, bearish, now, 'trend_lifecycle_state',
                source_event=fact.title, source_known_index=fact.known,
                broken_key_index=fact.broken.index, broken_key_known_index=fact.broken.known,
                broken_key_price=fact.broken.price)
            if not confirmed_bear_key_break(proof, now):
                return
            self._bull_recovery.pop(key, None)
        flip_state = LifecycleState.BULL_FLIP if up else LifecycleState.BEAR_FLIP
        alternation_state = LifecycleState.BULL_ALTERNATION if up else LifecycleState.BEAR_ALTERNATION
        if fact.title in ('底部疑虑', '头部疑虑', '翻空为多', '翻多为空'):
            is_break = fact.title in ('翻空为多', '翻多为空')
            origin = fact.origin if is_break else fact.point
            if origin is None or origin.kind != ('L' if up else 'H'):
                return
            frozen_proof: dict[str, object] = {}
            if is_break and fact.broken is not None:
                frozen_proof = dict(broken_key_index=fact.broken.index,
                    broken_key_known_index=fact.broken.known, broken_key_price=fact.broken.price)
            if cycle is None or cycle.up != up:
                cycle = _Cycle(flip_state, up, origin, now, fact.point if is_break else fact.impulse,
                               evidence=frozenset((fact,)))
                self._cycles[key] = cycle
                events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                          source_event=fact.title, source_known_index=fact.known, **frozen_proof))
            elif is_break and cycle.state == flip_state:
                # 同一未结束周期只能延伸攻击极值；局部反弹不能降低原确认门槛。
                sign = 1 if up else -1
                if cycle.impulse is not None and (sign * (fact.point.price - cycle.impulse.price) <= 0
                        or (fact.point.index, fact.point.ordinal) <= (cycle.impulse.index, cycle.impulse.ordinal)):
                    return
                cycle = replace(cycle, impulse=fact.point, candidate=None,
                                evidence=cycle.evidence.union((fact,)))
                self._cycles[key] = cycle
                events.append(self._event(key, cycle, now, 'trend_lifecycle_anchor',
                                          source_event=fact.title, source_known_index=fact.known, **frozen_proof))
            return
        if (cycle is None or cycle.up != up
                or (cycle.state != flip_state and not (up and cycle.confirmation_gap is not None))):
            return
        if not cycle.recovery and not any(
                item.title == ('翻空为多' if up else '翻多为空') for item in cycle.evidence):
            return
        impulse = fact.impulse or cycle.impulse
        if impulse is None or impulse.kind != ('H' if up else 'L'):
            return
        if cycle.impulse is not None and (
                impulse.index, impulse.ordinal, impulse.price) != (
                cycle.impulse.index, cycle.impulse.ordinal, cycle.impulse.price):
            sign = 1 if up else -1
            if (sign * (impulse.price - cycle.impulse.price) <= 0
                    or (impulse.index, impulse.ordinal) <= (cycle.impulse.index, cycle.impulse.ordinal)
                    or impulse.known < cycle.impulse.known):
                # 交替标签也不能绕过同周期攻击基准，另拼一段更低的反弹。
                return
        # The shared geometry has already certified the retracement. This layer
        # enforces chronology and never promotes a developing point itself.
        if ((fact.point.index, fact.point.ordinal) <= (impulse.index, impulse.ordinal)
                or fact.known < impulse.known):
            return
        if cycle.qualification_closed is not None and fact.known <= cycle.qualification_closed:
            return
        prior_gap = cycle.confirmation_gap
        cycle = replace(cycle, state=alternation_state, impulse=impulse, counter=fact.point,
                        alternated=now, candidate=None, evidence=cycle.evidence.union((fact,)),
                        confirmation_gap=None, confirmation_source=(), qualification_closed=None)
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                  source_event=fact.title, source_known_index=fact.known,
                                  replaces_gap_confirmation_index=prior_gap[0] if prior_gap else None))

    def _advance_bull_recovery(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                               bars: Sequence[Bar], now: int, events: list[dict[str, object]],
                               point_orders: tuple[tuple[int, int], ...] | None = None,
                               market_index: _MarketRangeIndex | None = None) -> None:
        """短暂跌破→收回→同级突破→守低且满足既有交替结束规则，各级共用。"""
        pending = self._bull_recovery.get(key)
        if pending is not None and pending.seed.origin is not None:
            recovery_origin = pending.seed.origin
            updated = advance_bull_recovery(pending, bars[now].low, bars[now].close, now,
                                           previous_low=bars[now - 1].low if now else bars[now].low)
            if updated is None:
                self._bull_recovery.pop(key)
                events.append(dict(event='trend_lifecycle_recovery_expired', bar_index=now,
                    available_index=now, trend_level=key[0], source_path=key[1],
                    reason='four_consecutive_lower_lows', consecutive_lower_lows=4,
                    market_low=bars[now].low, previous_low=bars[now - 1].low,
                    rule_version=LIFECYCLE_VERSION))
            else:
                self._bull_recovery[key] = updated
                if pending.breach is None and updated.breach is not None:
                    active = self._cycles.get(key)
                    if active is not None and active.up:
                        self._cycles.pop(key)
                    events.append(dict(event='trend_lifecycle_invalidated', bar_index=now,
                        available_index=now, trend_level=key[0], source_path=key[1],
                        reason='bull_recovery_pending', original_low_index=recovery_origin.index,
                        rule_version=LIFECYCLE_VERSION))
                if updated.recovered is not None and pending.recovered is None:
                    events.append(dict(event='trend_lifecycle_recovered', bar_index=now,
                        available_index=now, trend_level=key[0], source_path=key[1],
                        original_low_index=recovery_origin.index,
                        original_low_price=recovery_origin.price,
                        breach_index=updated.breach, recovered_index=updated.recovered,
                        consecutive_lower_lows=updated.consecutive_lower_lows,
                        market_low=bars[now].low, confirmation_close=bars[now].close,
                        confirmation_rule='intraday_break_then_close_reclaim_with_at_most_three_lower_lows',
                        rule_version=LIFECYCLE_VERSION))
                if updated.recovered is not None:
                    # 本级相邻H/L/H，旧高必须早于突破日已知；不能拿最终图回填。
                    if point_orders is not None and updated.breach is not None:
                        start = max(0, bisect_left(point_orders, (updated.breach, 0)) - 1)
                        points = points[start:]
                    for previous, origin, impulse in zip(points, points[1:], points[2:]):
                        if (previous.kind != 'H' or origin.kind != 'L' or impulse.kind != 'H'
                                or updated.breach is None or origin.index < updated.breach
                                or impulse.index < updated.recovered or previous.known >= impulse.index
                                or not previous.index < origin.index < impulse.index
                                or impulse.price <= previous.price or impulse.price <= origin.price
                                or origin.price >= previous.price):
                            continue
                        broken = (market_index.first_extreme(origin.index + 1, now + 1, origin.price, high=False)
                                  is not None if market_index is not None and market_index.supports_price(origin.price)
                                  else any(bar.low < origin.price for bar in bars[origin.index + 1:now + 1]))
                        if broken:
                            continue
                        cycle = _Cycle(LifecycleState.BULL_FLIP, True, origin, now, impulse,
                            evidence=frozenset((updated.seed,)), recovery=True,
                            structure=(previous, origin, impulse))
                        self._cycles[key] = cycle
                        self._bull_recovery.pop(key)
                        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                            source_event='短暂跌破后同级再次空翻多',
                            confirmation_rule='bounded_lower_low_recovery_then_same_level_break',
                            original_low_index=recovery_origin.index,
                            breach_index=updated.breach, recovered_index=updated.recovered,
                            broken_high_index=previous.index, broken_high_price=previous.price))
                        break

    def _advance_bull_alternation(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                                  bars: Sequence[Bar], now: int, events: list[dict[str, object]],
                                  polyline: tuple[_Pivot, ...], policy: AlternationPolicy,
                                  point_orders: tuple[tuple[int, int], ...] | None = None,
                                  market_index: _MarketRangeIndex | None = None) -> None:
        """已成立的本级翻多统一确认交替，不等待交易资格内的策略扫描。"""
        cycle = self._cycles.get(key)
        if cycle is None or not self._needs_bull_confirmation(cycle):
            return
        assert cycle.impulse is not None
        broken_origin = (market_index.first_extreme(cycle.origin.index + 1, now + 1, cycle.origin.price, high=False)
                         is not None if market_index is not None and market_index.supports_price(cycle.origin.price)
                         else min(bar.low for bar in bars[cycle.origin.index + 1:now + 1]) < cycle.origin.price
                         ) if cycle.recovery else False
        if broken_origin:
            self._cycles.pop(key)
            events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated',
                                      reason='special_bull_origin_broken'))
            return
        broken: _Pivot | None
        if cycle.recovery:
            broken = cycle.structure[0]
        else:
            # 关键位随正式翻多事实保存；禁止用此刻最近的局部高点替换末跌高。
            seed = min((fact for fact in cycle.evidence if fact.title == '翻空为多'
                        and fact.origin == cycle.origin and fact.broken is not None),
                       key=lambda fact: (fact.known, fact.point.index, fact.point.ordinal), default=None)
            broken = seed.broken if seed is not None else None
        if (broken is None
                or not (broken.index, broken.ordinal) < (cycle.origin.index, cycle.origin.ordinal)
                    < (cycle.impulse.index, cycle.impulse.ordinal)):
            return
        if point_orders is None:
            if not {broken, cycle.origin, cycle.impulse}.issubset(points):
                return
        else:
            for point in (broken, cycle.origin, cycle.impulse):
                position = bisect_left(point_orders, (point.index, point.ordinal))
                if position == len(points) or points[position] != point:
                    return
            # 冻结关键位与其后续点参与确认；索引核验仍比较完整点证据，不能漏掉同坐标修订。
            points = points[bisect_left(point_orders, (broken.index, broken.ordinal)):]
        proof = confirm_bull_alternation(bars, now, origin=cycle.origin, high=cycle.impulse,
            broken=broken, points=points, polyline=polyline, level=key[0], path=key[1], policy=policy,
            allow_gap_confirmation=cycle.confirmation_gap is None or cycle.qualification_closed is not None,
            after_index=cycle.qualification_closed,
            flip_confirmed_at=(cycle.started if cycle.recovery else
                               min(fact.known for fact in cycle.evidence if fact.title == '翻空为多')),
            _market_index=market_index)
        if proof is None:
            return
        point = _Pivot(proof.low.index, proof.low.ordinal, proof.low.kind, proof.low.price, proof.low.known)
        sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.source)
        trend_sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.trend_source)
        prior_gap = cycle.confirmation_gap
        confirmed_impulse = cycle.impulse
        cycle = replace(cycle, state=LifecycleState.BULL_ALTERNATION, counter=point, alternated=now,
            structure=(broken, cycle.origin, cycle.impulse, *trend_sources), confirmation_source=sources,
            confirmation_gap=confirmation_gap(proof.rule, proof.evidence, now), qualification_closed=None)
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
            source_event='短暂跌破后空多交替结束' if cycle.recovery else '空翻多后空多交替结束',
            confirmation_rule=proof.rule,
            confirmation_evidence=dict(proof.evidence or {}, key_index=broken.index, key_price=broken.price,
                a_origin_index=cycle.origin.index, a_origin_price=cycle.origin.price,
                a_high_index=confirmed_impulse.index, a_high_price=confirmed_impulse.price),
            counter_source_level=0 if sources else key[0],
            replaces_gap_confirmation_index=prior_gap[0] if prior_gap else None))

    def _price(self, key: tuple[int, str], bars: Sequence[Bar], now: int,
               events: list[dict[str, object]]) -> None:
        cycle = self._cycles.get(key)
        if cycle is None:
            return
        bar = bars[now]
        sign = 1 if cycle.up else -1
        adverse = bar.low if cycle.up else bar.high
        defense = cycle.counter or cycle.origin
        if sign * (adverse - defense.price) < 0:
            self._cycles.pop(key)
            events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated', reason='defense_broken'))
            # 交替回调可以继续延伸，但不等于原翻向已失败。保留尚未被
            # 行情突破的冻结起点和翻向证明，让新的回调重新接受统一确认。
            # 资格是否结束仍由独立账本处理，不能倒改既有区间起点。
            flip_title = '翻空为多' if cycle.up else '翻多为空'
            flips = frozenset(fact for fact in cycle.evidence if fact.title == flip_title)
            if (cycle.counter is not None and sign * (adverse - cycle.origin.price) >= 0
                    and (flips or cycle.recovery)):
                pending = replace(cycle,
                    state=LifecycleState.BULL_FLIP if cycle.up else LifecycleState.BEAR_FLIP,
                    counter=None, alternated=None, candidate=None,
                    evidence=cycle.evidence if cycle.recovery else flips,
                    structure=cycle.structure[:3] if cycle.recovery else (),
                    confirmation_source=(), confirmation_gap=None)
                self._cycles[key] = pending
                events.append(self._event(key, pending, now, 'trend_lifecycle_anchor',
                    confirmation_rule='countermove_extended_with_frozen_origin_defended',
                    previous_counter_index=defense.index, previous_counter_price=defense.price))
            return
        confirmed = LifecycleState.BULL_CONFIRMED if cycle.up else LifecycleState.BEAR_CONFIRMED
        if now == 0 or cycle.state == confirmed or cycle.impulse is None:
            return
        previous = bars[now - 1]
        threshold = cycle.impulse.price
        if (cycle.alternated is not None and now > cycle.alternated
                and sign * (previous.close - threshold) <= 0 < sign * (bar.close - threshold)):
            cycle = replace(cycle, state=confirmed, candidate=None)
            self._cycles[key] = cycle
            events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                      confirmation_rule='strict_close_cross_after_confirmed_alternation',
                                      confirmation_close=bar.close, previous_close=previous.close))
            return
        gap = bar.low > previous.high if cycle.up else bar.high < previous.low
        volume_attack = (bar.volume > previous.volume and sign * (bar.close - bar.open) > 0
                         and sign * (bar.close - previous.close) > 0)
        candidate = (gap, volume_attack, threshold) if gap or volume_attack else None
        if candidate != cycle.candidate:
            self._cycles[key] = replace(cycle, candidate=candidate)
            events.append(self._event(key, cycle, now,
                'trend_lifecycle_candidate' if candidate else 'trend_lifecycle_candidate_expired',
                gap=gap, volume_attack=volume_attack, confirmed=False,
                volume=bar.volume, previous_volume=previous.volume,
                pending_condition=('confirmed_alternation_then_strict_close_cross'
                                   if cycle.alternated is None else 'strict_close_cross')))


def lifecycle_interval_days(events: Sequence[Mapping[str, object]], size: int, level: int,
                            *, up: bool) -> tuple[bool, ...]:
    """Buy/sell search windows; an opposite flip closes the previous window."""
    if type(level) is not int or level not in (1, 2, 3) or type(size) is not int or size < 0:
        raise ValueError('valid trend level and market size required')
    dated: dict[int, list[Mapping[str, object]]] = {}
    for event in events:
        index = event.get('bar_index')
        if (event.get('trend_level') == level and type(index) is int and 0 <= index < size
                and (event.get('event') in ('trend_lifecycle_state', 'trend_lifecycle_invalidated',
                                           'trend_lifecycle_confirmation_revoked')
                     or up and event.get('event') == SOURCE_BEAR_EVENT)):
            dated.setdefault(index, []).append(event)
    opened: set[str] = set()
    wanted = {LifecycleState.BULL_ALTERNATION.value, LifecycleState.BULL_CONFIRMED.value} if up else {
        LifecycleState.BEAR_ALTERNATION.value, LifecycleState.BEAR_CONFIRMED.value}
    days = []
    for index in range(size):
        for event in dated.get(index, ()):
            path = event.get('source_path')
            if not isinstance(path, str):
                continue
            if (event.get('event') in ('trend_lifecycle_invalidated', 'trend_lifecycle_confirmation_revoked',
                                       SOURCE_BEAR_EVENT)
                    or event.get('state') not in wanted):
                opened.discard(path)
            else:
                opened.add(path)
        days.append(bool(opened))
    return tuple(days)
