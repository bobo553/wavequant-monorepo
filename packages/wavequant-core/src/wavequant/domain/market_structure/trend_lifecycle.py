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
from .bull_alternation_confirmation import AlternationPolicy, confirm_body_gap_alternation, confirm_recovery_alternation


LIFECYCLE_VERSION = 'six_state_frozen_flip_prerequisite_v9'


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


@dataclass(frozen=True, slots=True)
class _BullRecovery:
    seed: _Fact
    breach: int | None = None
    below: int = 0
    recovered: int | None = None


def advance_bull_recovery(
    state: _BullRecovery, low: float, close: float, now: int,
) -> _BullRecovery | None:
    """最低价触发观察，允许0至3根收盘跌破；第四根作废，相等不算收回。

    只保存一个交易日计数，不读取未来行情，也不将收回直接视作交易许可。
    """
    origin = state.seed.origin
    if origin is None or now <= state.seed.known:
        return state
    if state.breach is None:
        if low >= origin.price:
            return state
        state = replace(state, breach=now)
    if close < origin.price:
        below = state.below + 1
        return replace(state, below=below, recovered=None) if below <= 3 else None
    if close > origin.price and state.breach is not None and state.recovered is None:
        return replace(state, below=0, recovered=now)
    return replace(state, below=0)


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
                       own_records: Mapping[tuple[int, int], Mapping[str, object]],
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
    available = max(known, point.known, origin.known, broken.known, confirming.known)
    if broken.known >= available:
        return None
    # 升级证书证明局部腿成立，不能覆盖本级底部疑虑已冻结的末跌高。
    # 特殊翻多由恢复状态机另行证明，不能在这里借局部突破冒充普通翻多。
    origin_record = own_records[(origin.index, origin.ordinal)]
    for observation in _records(origin_record.get('observations')):
        if observation.get('title') != '底部疑虑':
            continue
        observed = _clock(observation.get('available_at'), dates)
        if observed is None or observed > now:
            continue
        frozen = _pivot(observation.get('key'), dates, now)
        if (frozen is None or frozen.kind != 'H' or point.price <= frozen.price
                or (broken.index, broken.ordinal, broken.price) != (frozen.index, frozen.ordinal, frozen.price)):
            return None
    return _Fact('翻空为多', point, available, origin)


def _facts(points: object, dates: Mapping[str, int], now: int) -> tuple[_Fact, ...]:
    result: list[_Fact] = []
    parsed = tuple((raw, point) for raw in _records(points)
                   if (point := _pivot(raw, dates, now)) is not None)
    own_points = {(point.index, point.ordinal): point for _, point in parsed}
    own_records = {(point.index, point.ordinal): raw for raw, point in parsed}
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
            if title in ('翻空为多', '翻多为空') and observation.get('key') is not None:
                frozen = _pivot(observation['key'], dates, now)
                sign = 1 if up else -1
                if (frozen is None or frozen.kind != point.kind or sign * (point.price - frozen.price) <= 0
                        or (frozen.index, frozen.ordinal) >= (point.index, point.ordinal)):
                    continue
            result.append(_Fact(title, point, max(known, point.known,
                origin.known if origin else 0, impulse.known if impulse else 0), origin, impulse))
            ordinary_bull = ordinary_bull or title == '翻空为多'
        # 本级冻结关键位的正式突破优先；没有正式标记才读取升级证明。
        if not ordinary_bull:
            upgrade = _upgrade_bull_fact(raw, point, own_points, own_records, dates, now)
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
            return value.title, pivot(value.point), value.known, pivot(value.origin), pivot(value.impulse)

        rows = []
        for key, seen in sorted(self._seen.items()):
            cycle = self._cycles.get(key)
            saved = (cycle.state.value, cycle.up, pivot(cycle.origin), cycle.started, pivot(cycle.impulse),
                     pivot(cycle.counter), cycle.alternated, cycle.candidate,
                     tuple(sorted((fact(item) for item in cycle.evidence), key=repr)),
                     cycle.recovery, tuple(pivot(item) for item in cycle.structure),
                     tuple(pivot(item) for item in cycle.confirmation_source)) if cycle else None
            recovery = self._bull_recovery.get(key)
            pending = (fact(recovery.seed), recovery.breach, recovery.below, recovery.recovered) if recovery else None
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
                title, point, known, origin, impulse = sequence(row, 5)
                parsed = pivot(point)
                if not isinstance(title, str) or title not in _TITLES or parsed is None:
                    raise ValueError('invalid lifecycle checkpoint fact')
                result.append(_Fact(title, parsed, integer(known), pivot(origin), pivot(impulse)))
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
                seed, breach, below, recovered = sequence(pending, 4)
                parsed_seed = next(iter(facts((seed,))))
                if (parsed_seed.title != '翻空为多' or parsed_seed.origin is None
                        or type(below) is not int or not 0 <= below <= 3):
                    raise ValueError('invalid bull recovery checkpoint')
                restored._bull_recovery[key] = _BullRecovery(parsed_seed,
                    integer(breach) if breach is not None else None, below,
                    integer(recovered) if recovered is not None else None)
            if raw is None:
                continue
            state, up, origin, started, impulse, counter, alternated, candidate, evidence, recovery, structure, source = sequence(raw, 12)
            parsed_origin = pivot(origin)
            if not isinstance(state, str) or type(up) is not bool or parsed_origin is None:
                raise ValueError('invalid lifecycle checkpoint cycle')
            hint: tuple[bool, bool, float] | None = None
            if candidate is not None:
                gap, volume, price = sequence(candidate, 3)
                if type(gap) is not bool or type(volume) is not bool or type(price) not in (int, float) or not isfinite(price):
                    raise ValueError('invalid lifecycle checkpoint candidate')
                hint = gap, volume, price
            if type(recovery) is not bool or not isinstance(structure, tuple) or not isinstance(source, tuple):
                raise ValueError('invalid lifecycle structure checkpoint')
            parsed_structure = tuple(pivot(item) for item in structure)
            parsed_source = tuple(pivot(item) for item in source)
            if any(item is None for item in (*parsed_structure, *parsed_source)):
                raise ValueError('invalid lifecycle structure point')
            restored._cycles[key] = _Cycle(LifecycleState(state), up, parsed_origin, integer(started),
                pivot(impulse), pivot(counter), integer(alternated) if alternated is not None else None,
                hint, facts(evidence), recovery, tuple(item for item in parsed_structure if item is not None),
                tuple(item for item in parsed_source if item is not None))
        return restored

    def advance(self, levels: Sequence[Mapping[str, object]], bars: Sequence[Bar], now: int,
                dates: Mapping[str, int], *, polyline: Sequence[Mapping[str, object]] = (),
                polyline_path: str | None = None,
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
                self._advance_bull_recovery(key, points, bars, now, events, source_points, confirmation_policy)
                self._advance_body_gap(key, points, bars, now, events, source_points)
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
        return events

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
                cycle = replace(cycle, impulse=fact.point, candidate=None,
                                evidence=cycle.evidence.union((fact,)))
                self._cycles[key] = cycle
                events.append(self._event(key, cycle, now, 'trend_lifecycle_anchor',
                                          source_event=fact.title, source_known_index=fact.known))
            return
        if cycle is None or cycle.up != up or cycle.state != flip_state:
            return
        if not cycle.recovery and not any(
                item.title == ('翻空为多' if up else '翻多为空') for item in cycle.evidence):
            return
        impulse = fact.impulse or cycle.impulse
        if impulse is None or impulse.kind != ('H' if up else 'L'):
            return
        # The shared geometry has already certified the retracement. This layer
        # enforces chronology and never promotes a developing point itself.
        if ((fact.point.index, fact.point.ordinal) <= (impulse.index, impulse.ordinal)
                or fact.known < impulse.known):
            return
        cycle = replace(cycle, state=alternation_state, impulse=impulse, counter=fact.point,
                        alternated=now, candidate=None, evidence=cycle.evidence.union((fact,)))
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
                                  source_event=fact.title, source_known_index=fact.known))

    def _advance_bull_recovery(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                               bars: Sequence[Bar], now: int, events: list[dict[str, object]],
                               polyline: tuple[_Pivot, ...], policy: AlternationPolicy) -> None:
        """短暂跌破→收回→同级突破→守低且满足既有交替结束规则，各级共用。"""
        pending = self._bull_recovery.get(key)
        if pending is not None and pending.seed.origin is not None:
            updated = advance_bull_recovery(pending, bars[now].low, bars[now].close, now)
            if updated is None:
                self._bull_recovery.pop(key)
                events.append(dict(event='trend_lifecycle_recovery_expired', bar_index=now,
                    available_index=now, trend_level=key[0], source_path=key[1],
                    reason='four_consecutive_closes_below_original_low', rule_version=LIFECYCLE_VERSION))
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
                        consecutive_below_closes=pending.below,
                        market_low=bars[now].low, confirmation_close=bars[now].close,
                        confirmation_rule='intraday_break_then_strict_close_reclaim_within_three_below_closes',
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
                            confirmation_rule='short_close_break_recovered_then_same_level_break',
                            original_low_index=updated.seed.origin.index,
                            breach_index=updated.breach, recovered_index=updated.recovered,
                            broken_high_index=previous.index, broken_high_price=previous.price))
                        break
        cycle = self._cycles.get(key)
        if cycle is None or not cycle.recovery or cycle.alternated is not None or cycle.impulse is None:
            return
        if min(bar.low for bar in bars[cycle.origin.index + 1:now + 1]) < cycle.origin.price:
            self._cycles.pop(key)
            events.append(self._event(key, cycle, now, 'trend_lifecycle_invalidated',
                                      reason='special_bull_origin_broken'))
            return
        proof = confirm_recovery_alternation(bars, now, origin=cycle.origin, high=cycle.impulse,
            broken=cycle.structure[0], points=points, polyline=polyline, level=key[0], path=key[1], policy=policy)
        if proof is None:
            return
        point = _Pivot(proof.low.index, proof.low.ordinal, proof.low.kind, proof.low.price, proof.low.known)
        sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.source)
        trend_sources = tuple(_Pivot(p.index, p.ordinal, p.kind, p.price, p.known) for p in proof.trend_source)
        cycle = replace(cycle, state=LifecycleState.BULL_ALTERNATION, counter=point, alternated=now,
            structure=(*cycle.structure, *trend_sources), confirmation_source=sources)
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
            source_event='短暂跌破后空多交替结束', confirmation_rule=proof.rule,
            confirmation_evidence=proof.evidence, counter_source_level=0 if sources else key[0]))

    def _advance_body_gap(self, key: tuple[int, str], points: tuple[_Pivot, ...],
                          bars: Sequence[Bar], now: int, events: list[dict[str, object]],
                          polyline: tuple[_Pivot, ...]) -> None:
        cycle = self._cycles.get(key)
        if (cycle is None or cycle.recovery or not cycle.up or cycle.alternated is not None
                or cycle.impulse is None or not any(fact.title == '翻空为多' for fact in cycle.evidence)):
            return
        broken = next((point for point in reversed(points) if point.kind == 'H'
                       and point.index < cycle.origin.index and point.known <= now), None)
        if broken is None:
            return
        proof = confirm_body_gap_alternation(bars, now, origin=cycle.origin, high=cycle.impulse, polyline=polyline)
        if proof is None:
            return
        low = _Pivot(proof.low.index, proof.low.ordinal, proof.low.kind, proof.low.price, proof.low.known)
        cycle = replace(cycle, state=LifecycleState.BULL_ALTERNATION, counter=low, alternated=now,
                        confirmation_source=(low,), structure=(broken,))
        self._cycles[key] = cycle
        events.append(self._event(key, cycle, now, 'trend_lifecycle_state',
            source_event='实体跳空放量突破确认空多交替结束', confirmation_rule=proof.rule,
            confirmation_evidence=dict(proof.evidence or {}, key_index=broken.index, key_price=broken.price),
            counter_source_level=0))

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
                and event.get('event') in ('trend_lifecycle_state', 'trend_lifecycle_invalidated')):
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
            if event.get('event') == 'trend_lifecycle_invalidated' or event.get('state') not in wanted:
                opened.discard(path)
            else:
                opened.add(path)
        days.append(bool(opened))
    return tuple(days)
