"""Polyline and trend causal lifecycle, separate from geometry and orders.

The caller supplies each date's own geometry. A final chart must never be
trimmed backwards to manufacture this history. Only compact facts and active
cycles are retained; price confirmation advances once per market date.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite

from ..models.model import Bar
from .bull_alternation_confirmation import AlternationPolicy, confirm_bull_alternation
from .confirmation_gap import confirmation_gap
from .source_bear_exit import SOURCE_BEAR_EVENT, source_bearish_exits


LIFECYCLE_VERSION = 'six_state_direct_source_bear_exit_v19'


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


@dataclass(frozen=True, slots=True)
class _BullRecovery:
    seed: _Fact
    breach: int | None = None
    consecutive_lower_lows: int = 0
    recovered: int | None = None


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


def _continued_bull_impulse(cycle: _Cycle, points: tuple[_Pivot, ...]) -> _Cycle | None:
    """同一腿高点向右/向上延伸保留特殊属性；基准或起涨低点修订仍须失效。"""
    if not cycle.recovery or cycle.alternated is not None or len(cycle.structure) != 3 or cycle.impulse is None:
        return None
    broken, origin, previous = cycle.structure
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
            or type(price) not in (int, float) or not isfinite(price) or kind not in ('H', 'L')
            or known is None or not index <= known <= now):
        return None
    return _Pivot(index, ordinal, kind, price, known)


def _records(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, (tuple, list)):
        return ()
    return tuple(row for row in value if isinstance(row, Mapping))


def _upgrade_bull_fact(raw: Mapping[str, object], point: _Pivot,
                       own_points: Mapping[tuple[int, int], _Pivot],
                       dates: Mapping[str, int], now: int) -> _Fact | None:
    """把本级已发布的末跌高突破证据接入六态，不依赖展示用observations。

    升级证据引用下级坐标，但起点和被突破高点必须实际存在于本级；使用本级
    的确认时钟，不能把下级较早确认时间冒充本级确认。文字标签不是证明。
    """
    proof = raw.get('upgrade_evidence')
    if (point.kind != 'H' or not isinstance(proof, Mapping)
            or proof.get('direction') != 'up' or proof.get('role') != 'endpoint'):
        return None
    known = _clock(proof.get('available_at'), dates)
    origin_ref = _pivot(proof.get('origin'), dates, now)
    broken_ref = _pivot(proof.get('broken_key'), dates, now)
    confirming = _pivot(proof.get('confirmed_by'), dates, now)
    if known is None or known > now or origin_ref is None or broken_ref is None or confirming is None:
        return None
    origin = own_points.get((origin_ref.index, origin_ref.ordinal))
    broken = own_points.get((broken_ref.index, broken_ref.ordinal))
    if (origin is None or broken is None or origin.kind != 'L' or broken.kind != 'H'
            or origin.price != origin_ref.price or broken.price != broken_ref.price
            or confirming.kind != 'H' or confirming.price != point.price
            or (confirming.index, confirming.ordinal) != (point.index, point.ordinal)
            or not (broken.index, broken.ordinal) < (origin.index, origin.ordinal) < (point.index, point.ordinal)
            or not origin.price < broken.price < point.price):
        return None
    # 本级关键位状态由几何标注器生成。疑虑可能在更早的低点上，不能只检查
    # 局部上涨起点的 observations；缺失上下文也不能用升级证书推测翻多。
    context = raw.get('lifecycle_context')
    if not isinstance(context, Mapping) or context.get('direction') != 'up':
        return None
    context_known = _clock(context.get('available_at'), dates)
    frozen = _pivot(context.get('key'), dates, now)
    bearish_origin = _pivot(context.get('origin'), dates, now)
    if (context_known is None or context_known > now or frozen is None or bearish_origin is None
            or frozen.kind != 'H' or bearish_origin.kind != 'L'
            or own_points.get((frozen.index, frozen.ordinal)) != frozen
            or own_points.get((bearish_origin.index, bearish_origin.ordinal)) != bearish_origin
            or (broken.index, broken.ordinal, broken.price) != (frozen.index, frozen.ordinal, frozen.price)
            or not (frozen.index, frozen.ordinal) < (bearish_origin.index, bearish_origin.ordinal)
                <= (origin.index, origin.ordinal)
            or not bearish_origin.price < frozen.price < point.price):
        return None
    available = max(known, point.known, origin.known, broken.known, confirming.known,
                    context_known, bearish_origin.known)
    if broken.known >= available:
        return None
    return _Fact('翻空为多', point, available, bearish_origin, broken=frozen)


def _facts(points: object, dates: Mapping[str, int], now: int) -> tuple[_Fact, ...]:
    result: list[_Fact] = []
    parsed = tuple((raw, point) for raw in _records(points)
                   if (point := _pivot(raw, dates, now)) is not None)
    own_points = {(point.index, point.ordinal): point for _, point in parsed}
    for raw, point in parsed:
        ordinary_bull = False
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
            ordinary_bull = ordinary_bull or title == '翻空为多'
        # 本级冻结关键位的正式突破优先；没有正式标记才读取升级证明。
        if not ordinary_bull:
            upgrade = _upgrade_bull_fact(raw, point, own_points, dates, now)
            if upgrade is not None:
                result.append(upgrade)
    return tuple(sorted(dict.fromkeys(result), key=lambda fact: (
        fact.known, fact.point.index, fact.point.ordinal, _TITLES.index(fact.title))))


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

    def copy(self) -> 'TrendLifecycle':
        other = TrendLifecycle()
        other._cycles = self._cycles.copy()
        other._seen = self._seen.copy()
        other._bull_recovery = self._bull_recovery.copy()
        other._polyline_known = {key: values.copy() for key, values in self._polyline_known.items()}
        other._last_index = self._last_index
        return other

    def checkpoint(self) -> tuple[object, ...]:
        """Only builtin immutable values enter the bounded checkpoint journal."""
        def pivot(value: _Pivot | None) -> tuple[object, ...] | None:
            return (value.index, value.ordinal, value.kind, value.price, value.known) if value else None

        def fact(value: _Fact) -> tuple[object, ...]:
            return (value.title, pivot(value.point), value.known, pivot(value.origin),
                    pivot(value.impulse), pivot(value.broken))

        rows = []
        for key, seen in sorted(self._seen.items()):
            cycle = self._cycles.get(key)
            saved = (cycle.state.value, cycle.up, pivot(cycle.origin), cycle.started, pivot(cycle.impulse),
                     pivot(cycle.counter), cycle.alternated, cycle.candidate,
                     tuple(sorted((fact(item) for item in cycle.evidence), key=repr)),
                     cycle.recovery, tuple(pivot(item) for item in cycle.structure),
                     tuple(pivot(item) for item in cycle.confirmation_source), cycle.confirmation_gap) if cycle else None
            recovery = self._bull_recovery.get(key)
            pending = (fact(recovery.seed), recovery.breach, recovery.consecutive_lower_lows, recovery.recovered) if recovery else None
            source_clocks = tuple((*point, known) for point, known in sorted(self._polyline_known.get(key, {}).items()))
            rows.append((key, tuple(sorted((fact(item) for item in seen), key=repr)), saved, pending, source_clocks))
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
            identity, seen, raw, pending, source_clocks = sequence(row, 5)
            level, path = sequence(identity, 2)
            if type(level) is not int or level not in (0, 1, 2, 3) or not isinstance(path, str):
                raise ValueError('invalid lifecycle checkpoint path')
            key = level, path
            restored._seen[key] = facts(seen)
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
            state, up, origin, started, impulse, counter, alternated, candidate, evidence, recovery, structure, source, saved_gap = sequence(raw, 13)
            parsed_origin = pivot(origin)
            if not isinstance(state, str) or type(up) is not bool or parsed_origin is None:
                raise ValueError('invalid lifecycle checkpoint cycle')
            hint: tuple[bool, bool, float] | None = None
            if candidate is not None:
                candidate_gap, volume, price = sequence(candidate, 3)
                if type(candidate_gap) is not bool or type(volume) is not bool or type(price) not in (int, float) or not isfinite(price):
                    raise ValueError('invalid lifecycle checkpoint candidate')
                hint = candidate_gap, volume, price
            if type(recovery) is not bool or not isinstance(structure, tuple) or not isinstance(source, tuple):
                raise ValueError('invalid lifecycle structure checkpoint')
            parsed_structure = tuple(pivot(item) for item in structure)
            parsed_source = tuple(pivot(item) for item in source)
            if any(item is None for item in (*parsed_structure, *parsed_source)):
                raise ValueError('invalid lifecycle structure point')
            parsed_gap = None
            if saved_gap is not None:
                gap_index, lower, upper, kind = sequence(saved_gap, 4)
                integer(gap_index)
                if (kind not in ('body', 'full') or gap_index != alternated or up is not True
                        or type(lower) not in (int, float) or type(upper) not in (int, float)
                        or not isfinite(lower) or not isfinite(upper) or not 0 < lower < upper):
                    raise ValueError('invalid lifecycle confirmation gap')
                parsed_gap = gap_index, float(lower), float(upper), kind
            restored._cycles[key] = _Cycle(LifecycleState(state), up, parsed_origin, integer(started),
                pivot(impulse), pivot(counter), integer(alternated) if alternated is not None else None,
                hint, facts(evidence), recovery, tuple(item for item in parsed_structure if item is not None),
                tuple(item for item in parsed_source if item is not None), parsed_gap)
        return restored

    def advance(self, levels: Sequence[Mapping[str, object]], bars: Sequence[Bar], now: int,
                dates: Mapping[str, int], *, polyline: Sequence[Mapping[str, object]] = (),
                polyline_path: str | None = None,
                source_events: Sequence[Mapping[str, object]] = (),
                confirmation_policy: AlternationPolicy = AlternationPolicy()) -> list[dict[str, object]]:
        """Consume one causal date; repeated/rewound dates require a checkpoint."""
        if type(now) is not int or not 0 <= now < len(bars) or now != self._last_index + 1:
            raise ValueError('lifecycle requires consecutive causal market dates')
        self._last_index = now
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
                facts = _facts(stroke.get('points'), dates, now)
                seen = self._seen.get(key, frozenset())
                cycle = self._cycles.get(key)
                needs_points = (key in self._bull_recovery or (cycle is not None and
                                (bool(cycle.structure) or (cycle.up and cycle.impulse is not None)))
                                or any(fact.title == '翻空为多' and fact not in seen for fact in facts))
                points = (tuple(point for raw in _records(stroke.get('points'))
                                if (point := _pivot(raw, dates, now)) is not None) if needs_points else ())
                source_points: tuple[_Pivot, ...] = ()
                if needs_points and polyline and (polyline_path is None or path.endswith(polyline_path)):
                    from .trend_lines import polyline_turns
                    source_points = tuple(point for raw in polyline_turns(polyline, source_path=path)
                        if raw.get('source_kind') == raw.get('kind')
                        and (point := _pivot(raw, dates, now)) is not None
                        and point.index >= (cycle.impulse.index if cycle is not None and cycle.up
                            and cycle.impulse is not None else max(0, now - confirmation_policy.structure_window)))
                    prior_clocks = self._polyline_known.get(key, {})
                    source_points = tuple(replace(point, known=max(point.known,
                        prior_clocks.get((point.index, point.ordinal, point.kind, point.price), now)))
                        for point in source_points)
                    self._polyline_known[key] = {(p.index, p.ordinal, p.kind, p.price): p.known for p in source_points}
                if (cycle is not None and cycle.evidence.issubset(facts)
                        and not set(cycle.structure).issubset(points)):
                    continued = _continued_bull_impulse(cycle, points)
                    if continued is not None:
                        cycle = continued
                        self._cycles[key] = cycle
                        events.append(self._event(key, cycle, now, 'trend_lifecycle_anchor',
                            source_event='特殊空翻多同腿高点延伸',
                            confirmation_rule='same_origin_and_broken_key_confirmed_high_extension'))
                if cycle is not None and (not cycle.evidence.issubset(facts)
                                          or not set(cycle.structure).issubset(points)
                                          or not set(cycle.confirmation_source).issubset(source_points)):
                    self._cycles.pop(key)
                    events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated',
                                              reason='source_evidence_revised'))
                recovery = self._bull_recovery.get(key)
                if recovery is not None and recovery.seed not in facts:
                    self._bull_recovery.pop(key)
                for fact in facts:
                    if fact not in seen:
                        recovery = self._bull_recovery.get(key)
                        active = self._cycles.get(key)
                        if (fact.title == '翻空为多' and fact.origin is not None
                                and (recovery is None or recovery.breach is None)
                                and not (active is not None and active.recovery and active.alternated is None)):
                            self._bull_recovery[key] = _BullRecovery(fact)
                        self._consume(key, fact, now, events)
                self._seen[key] = seen.union(facts)
                self._advance_bull_recovery(key, points, bars, now, events)
                self._advance_bull_alternation(key, points, bars, now, events, source_points, confirmation_policy)
                self._price(key, bars, now, events)
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
        # 单级联动借用当日下级事件；整层恢复使用同一次 advance 的下级结果。
        events.extend(source_bearish_exits(levels, (*source_events, *events), now))
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
            return
        flip_state = LifecycleState.BULL_FLIP if up else LifecycleState.BEAR_FLIP
        alternation_state = LifecycleState.BULL_ALTERNATION if up else LifecycleState.BEAR_ALTERNATION
        if fact.title in ('底部疑虑', '头部疑虑', '翻空为多', '翻多为空'):
            is_break = fact.title in ('翻空为多', '翻多为空')
            origin = fact.origin if is_break else fact.point
            if origin is None or origin.kind != ('L' if up else 'H'):
                return
            if cycle is None or cycle.up != up:
                cycle = _Cycle(flip_state, up, origin, now, fact.point if is_break else fact.impulse,
                               evidence=frozenset((fact,)))
                self._cycles[key] = cycle
                events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                          source_event=fact.title, source_known_index=fact.known))
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
                                          source_event=fact.title, source_known_index=fact.known))
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
        prior_gap = cycle.confirmation_gap
        cycle = replace(cycle, state=alternation_state, impulse=impulse, counter=fact.point,
                        alternated=now, candidate=None, evidence=cycle.evidence.union((fact,)),
                        confirmation_gap=None, confirmation_source=())
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                  source_event=fact.title, source_known_index=fact.known,
                                  replaces_gap_confirmation_index=prior_gap[0] if prior_gap else None))

    def _advance_bull_recovery(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                               bars: Sequence[Bar], now: int, events: list[dict[str, object]]) -> None:
        """短暂跌破→收回→同级突破→守低且满足既有交替结束规则，各级共用。"""
        pending = self._bull_recovery.get(key)
        if pending is not None and pending.seed.origin is not None:
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
                        reason='bull_recovery_pending', original_low_index=updated.seed.origin.index,
                        rule_version=LIFECYCLE_VERSION))
                if updated.recovered is not None and pending.recovered is None:
                    events.append(dict(event='trend_lifecycle_recovered', bar_index=now,
                        available_index=now, trend_level=key[0], source_path=key[1],
                        original_low_index=updated.seed.origin.index,
                        original_low_price=updated.seed.origin.price,
                        breach_index=updated.breach, recovered_index=updated.recovered,
                        consecutive_lower_lows=updated.consecutive_lower_lows,
                        market_low=bars[now].low, confirmation_close=bars[now].close,
                        confirmation_rule='intraday_break_then_close_reclaim_with_at_most_three_lower_lows',
                        rule_version=LIFECYCLE_VERSION))
                if updated.recovered is not None:
                    # 本级相邻H/L/H，旧高必须早于突破日已知；不能拿最终图回填。
                    for previous, origin, impulse in zip(points, points[1:], points[2:]):
                        if (previous.kind != 'H' or origin.kind != 'L' or impulse.kind != 'H'
                                or updated.breach is None or origin.index < updated.breach
                                or impulse.index < updated.recovered or previous.known >= impulse.index
                                or not previous.index < origin.index < impulse.index
                                or impulse.price <= previous.price or impulse.price <= origin.price
                                or origin.price >= previous.price):
                            continue
                        if any(bar.low < origin.price for bar in bars[origin.index + 1:now + 1]):
                            continue
                        cycle = _Cycle(LifecycleState.BULL_FLIP, True, origin, now, impulse,
                            evidence=frozenset((updated.seed,)), recovery=True,
                            structure=(previous, origin, impulse))
                        self._cycles[key] = cycle
                        self._bull_recovery.pop(key)
                        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                            source_event='短暂跌破后同级再次空翻多',
                            confirmation_rule='bounded_lower_low_recovery_then_same_level_break',
                            original_low_index=updated.seed.origin.index,
                            breach_index=updated.breach, recovered_index=updated.recovered,
                            broken_high_index=previous.index, broken_high_price=previous.price))
                        break

    def _advance_bull_alternation(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                                  bars: Sequence[Bar], now: int, events: list[dict[str, object]],
                                  polyline: tuple[_Pivot, ...], policy: AlternationPolicy) -> None:
        """已成立的本级翻多统一确认交替，不等待交易资格内的策略扫描。"""
        cycle = self._cycles.get(key)
        if (cycle is None or not cycle.up or cycle.impulse is None
                or (cycle.alternated is not None and cycle.confirmation_gap is None)):
            return
        if cycle.recovery and min(bar.low for bar in bars[cycle.origin.index + 1:now + 1]) < cycle.origin.price:
            self._cycles.pop(key)
            events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated',
                                      reason='special_bull_origin_broken'))
            return
        if cycle.recovery:
            broken = cycle.structure[0]
        else:
            # 关键位随正式翻多事实保存；禁止用此刻最近的局部高点替换末跌高。
            seed = min((fact for fact in cycle.evidence if fact.title == '翻空为多'
                        and fact.origin == cycle.origin and fact.broken is not None),
                       key=lambda fact: (fact.known, fact.point.index, fact.point.ordinal), default=None)
            broken = seed.broken if seed is not None else None
        if (broken is None or not {broken, cycle.origin, cycle.impulse}.issubset(points)
                or not (broken.index, broken.ordinal) < (cycle.origin.index, cycle.origin.ordinal)
                    < (cycle.impulse.index, cycle.impulse.ordinal)):
            return
        proof = confirm_bull_alternation(bars, now, origin=cycle.origin, high=cycle.impulse,
            broken=broken, points=points, polyline=polyline, level=key[0], path=key[1], policy=policy,
            allow_gap_confirmation=cycle.confirmation_gap is None)
        if proof is None:
            return
        point = _Pivot(proof.low.index, proof.low.ordinal, proof.low.kind, proof.low.price, proof.low.known)
        sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.source)
        trend_sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.trend_source)
        prior_gap = cycle.confirmation_gap
        cycle = replace(cycle, state=LifecycleState.BULL_ALTERNATION, counter=point, alternated=now,
            structure=(broken, cycle.origin, cycle.impulse, *trend_sources), confirmation_source=sources,
            confirmation_gap=confirmation_gap(proof.rule, proof.evidence, now))
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
            source_event='短暂跌破后空多交替结束' if cycle.recovery else '空翻多后空多交替结束',
            confirmation_rule=proof.rule,
            confirmation_evidence=dict(proof.evidence or {}, key_index=broken.index, key_price=broken.price,
                a_origin_index=cycle.origin.index, a_origin_price=cycle.origin.price,
                a_high_index=cycle.impulse.index, a_high_price=cycle.impulse.price),
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
