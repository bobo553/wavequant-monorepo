"""Advance execution facts without replaying each LONG owner's N history."""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from typing import cast

from wavequant.domain.models.model import Bar


def _index(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise ValueError(f'execution fact {key} must be an integer')
    return value


def _price(row: Mapping[str, object], key: str) -> float | None:
    value = row[key]
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'execution fact {key} must be a price or None')
    return cast(float, value)


_Scope = tuple[int | None, str | None, int | None]


def _scope(row: Mapping[str, object]) -> _Scope:
    window = row.get('window_start')
    path = row.get('source_path')
    qualification = row.get('qualification_window_start')
    if window is not None and type(window) is not int:
        raise ValueError('execution N window must be an integer')
    if path is not None and not isinstance(path, str):
        raise ValueError('execution N source path must be a string')
    if qualification is not None and type(qualification) is not int:
        raise ValueError('execution N qualification window must be an integer')
    return cast(int | None, window), path, cast(int | None, qualification)


def _scope_fields(scope: _Scope) -> dict[str, object]:
    return {name: value for name, value in zip(
        ('window_start', 'source_path', 'qualification_window_start'), scope) if value is not None}


@dataclass(frozen=True)
class _TargetSource:
    origin: int
    attack: int
    known: int
    defense: float
    one_p: float | None
    two_t: float | None
    n_id: str | None
    primary: bool
    window_start: int | None = None
    source_path: str | None = None
    qualification_window_start: int | None = None

    @property
    def scope(self) -> _Scope:
        return self.window_start, self.source_path, self.qualification_window_start


@dataclass
class _TargetState:
    source: _TargetSource
    through: int
    retired_before: int | None
    ended: bool = False
    reached: set[str] = field(default_factory=set)
    events: list[dict[str, object]] = field(default_factory=list)

    def advance(self, bars: Sequence[Bar], index: int) -> None:
        if self.ended:
            self.through = index
            return
        source = self.source
        for now in range(self.through + 1, index + 1):
            self.through = now
            adverse = bars[now].close if now == source.known else bars[now].low
            if ((self.retired_before is not None and now >= self.retired_before)
                    or (now > source.attack and adverse < source.defense)):
                self.events.append(dict(event='wave_projection_invalidated', attack=source.attack,
                    origin_index=source.origin, bar_index=now, n_id=source.n_id,
                    **_scope_fields(source.scope)))
                self.ended = True
                break
            observed = bars[now].close if now == source.known else bars[now].high
            for stage, target in (('one_p', source.one_p), ('two_t', source.two_t)):
                if target is not None and stage not in self.reached and observed >= target:
                    self.events.append(dict(event='wave_n_target_reached', attack=source.attack,
                        origin_index=source.origin, bar_index=now, reached_stage=stage,
                        reached_target=target, n_id=source.n_id,
                        **_scope_fields(source.scope)))
                    self.reached.add(stage)
        self.through = max(self.through, index)


class SignalExecutionFactsStream:
    """Own immutable market input and independent N milestone lifetimes.

    A newly learned or changed N catches up its own lifetime once. Retirements
    stop it at their recorded boundary; a revised past boundary rebuilds only
    that N. Owner assignment never repeats candle observations and retains the
    original audit order, including duplicate facts and late owner signals.
    """

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0) -> None:
        self.bars = tuple(bars)
        if any(type(bar) is not Bar for bar in self.bars):
            raise ValueError('execution histories require immutable Bar records')
        if type(start_index) is not int or not 0 <= start_index <= len(self.bars):
            raise ValueError('invalid execution fact window start')
        self.start_index = start_index
        self.index = start_index - 1
        self.processed_observations = 0
        self.states: dict[_TargetSource, _TargetState] = {}
        self.sources: list[_TargetSource] = []
        self.projections: list[dict[str, object]] = []
        self.owners: list[dict[str, object]] = []
        self.n_bars: dict[int, int] = {}
        self.active_positive_n_bars: dict[int, int] = {}
        self.wave_events: list[dict[str, object]] = []

    def _synchronize(
        self, index: int, audit: Sequence[Mapping[str, object]],
        segment_owner: Mapping[str, object] | None = None,
    ) -> None:
        if type(index) is not int or index < self.index or index >= len(self.bars):
            raise ValueError('execution facts require a nondecreasing source date')
        retirements: dict[tuple[_Scope, int], int] = {}
        named_retirements: dict[str, int] = {}
        closures: dict[tuple[_Scope, int], int] = {}
        named_closures: dict[str, int] = {}
        invalidations: dict[str, int] = {}
        completed: list[Mapping[str, object]] = []
        self.projections, self.owners, self.sources = [], [], []
        self.n_bars = {}
        self.active_positive_n_bars = {}
        for row in audit:
            available = max(_index(row, 'bar_index'), _index(row, 'known_at') if 'known_at' in row else 0)
            if not self.start_index <= available <= index:
                continue
            event = row.get('event', '')
            if event == 'n_target_source_retired':
                identity = row.get('n_id')
                is_closed = row.get('reason') == 'trading_eligibility_closed'
                if isinstance(identity, str):
                    (named_closures if is_closed else named_retirements).setdefault(
                        identity, _index(row, 'bar_index'))
                else:
                    (closures if is_closed else retirements).setdefault(
                        (_scope(row), _index(row, 'attack')), _index(row, 'bar_index'))
            elif event == 'n_invalidated':
                identity = row.get('n_id')
                if isinstance(identity, str):
                    invalidations.setdefault(identity, _index(row, 'bar_index'))
            elif event == 'n_completed' and row.get('direction') == 'up':
                attack = _index(row, 'bar_index')
                known = _index(row, 'known_at') if 'known_at' in row else attack
                available = max(attack, known)
                self.n_bars[available] = max(attack, self.n_bars.get(available, -1))
                completed.append(row)
            if isinstance(event, str) and (event.startswith('wave_projection_') or event == 'wave_c_equal_target'):
                self.projections.append(deepcopy(dict(row)))
            if event == 'long_signal':
                self.owners.append(deepcopy(dict(row)))

        def retired_before(row: Mapping[str, object]) -> int | None:
            identity, key = row.get('n_id'), (_scope(row), _index(row, 'bar_index'))
            limits: list[int] = []
            if isinstance(identity, str) and identity in named_closures:
                limits.append(named_closures[identity])
            elif key in closures:
                limits.append(closures[key])
            if row.get('target_primary', True):
                if isinstance(identity, str) and identity in named_retirements:
                    limits.append(named_retirements[identity])
                elif key in retirements:
                    limits.append(retirements[key])
            if isinstance(identity, str) and identity in invalidations:
                limits.append(invalidations[identity])
            return min(limits) if limits else None

        # The account receives an authoritative current set, while the public
        # batch result retains its historical availability map for compatibility.
        for row in completed:
            retired = retired_before(row)
            if retired is None or index < retired:
                attack = _index(row, 'bar_index')
                known = _index(row, 'known_at') if 'known_at' in row else attack
                available = max(attack, known)
                self.active_positive_n_bars[available] = max(
                    attack, self.active_positive_n_bars.get(available, -1))
        owners = [segment_owner] if segment_owner is not None else [row for row in self.owners
            if row.get('channel') == 'wave_push_gap' and isinstance(row.get('wave_b_low_index'), int)]
        minimum_b = min((_index(row, 'wave_b_low_index') for row in owners), default=None)
        present: set[_TargetSource] = set()
        for row in completed:
            if minimum_b is None:
                break
            if row.get('target_eligible') is False:
                continue
            origin, attack = _index(row, 'origin'), _index(row, 'bar_index')
            if origin < minimum_b or attack <= minimum_b:
                continue
            known = max(attack, _index(row, 'known_at') if 'known_at' in row else attack, self.start_index)
            if not 0 <= origin <= index or attack < 0 or known < 0:
                raise ValueError('execution N must have an available origin')
            defense = row.get('defense', self.bars[origin].low)
            if not isinstance(defense, (int, float)) or isinstance(defense, bool):
                raise ValueError('execution N defense must be a price')
            identity = row.get('n_id')
            if identity is not None and not isinstance(identity, str):
                raise ValueError('execution N identity must be a string')
            source = _TargetSource(origin, attack, known, defense, _price(row, 'one_p'), _price(row, 'two_t'),
                                   identity, bool(row.get('target_primary', True)), *_scope(row))
            self.sources.append(source)
            if source in present:
                continue
            present.add(source)
            retired = retired_before(row)
            # Absence and a future boundary are equivalent before that close.
            if retired is not None and retired > index:
                retired = None
            state = self.states.get(source)
            if state is not None and state.retired_before != retired:
                changed = [limit for limit in (state.retired_before, retired) if limit is not None]
                if changed and min(changed) <= state.through:
                    state = None
                else:
                    state.retired_before = retired
            if state is None:
                state = _TargetState(source, known - 1, retired)
                self.states[source] = state
            before = state.through
            was_ended = state.ended
            state.advance(self.bars, index)
            if not was_ended:
                end = cast(int, state.events[-1]['bar_index']) if state.ended else index
                self.processed_observations += max(0, end - before)
        for removed in self.states.keys() - present:
            del self.states[removed]
        self.index = index

    def segment_events(self, signal_row: Mapping[str, object]) -> list[dict[str, object]]:
        b_index, owner = _index(signal_row, 'wave_b_low_index'), _index(signal_row, 'bar_index')
        owner_scope = _scope(signal_row)
        events: list[dict[str, object]] = [dict(event='wave_segment_start', attack=owner, bar_index=owner,
            origin_index=b_index, owner_signal_index=owner, **_scope_fields(owner_scope))]
        for source in self.sources:
            if source.scope != owner_scope:
                continue
            if source.origin < b_index or source.attack <= b_index:
                continue
            events.extend(dict(event, owner_signal_index=owner) for event in self.states[source].events)
        for row in self.projections:
            if not str(row.get('event', '')).startswith('wave_projection_'):
                continue
            if _scope(row) != owner_scope:
                continue
            if (cast(int, row.get('origin_index', -1)) >= b_index and _index(row, 'attack') > b_index):
                events.append(dict(row, owner_signal_index=owner))
            elif (signal_row.get('wave_entry_path') == 'two_t_strong_a_resistance_rebreak'
                  and row['attack'] == signal_row['attack']):
                events.append(dict(row, owner_signal_index=owner))
        return events

    def advance(
        self, index: int, audit: Sequence[Mapping[str, object]],
    ) -> tuple[dict[int, int], list[dict[str, object]]]:
        self._synchronize(index, audit)
        events = list(self.projections)
        for row in self.owners:
            if row.get('channel') == 'wave_push_gap' and isinstance(row.get('wave_b_low_index'), int):
                events.extend(self.segment_events(row))
            if row.get('wave_a_class') == 'ordinary':
                events.append(dict(event='wave_ordinary_entry', attack=row['attack'],
                    bar_index=row['bar_index'], owner_signal_index=row['bar_index'], target=row['wave_equal_target'],
                    target_0618=row['wave_c_0618_target'], one_p=row['wave_entry_one_p'], two_t=row['wave_entry_two_t'],
                    a_origin=row['wave_a_origin'], a_high=row['wave_a_high'], a_high_index=row['wave_a_high_index'],
                    b_low=row['wave_b_low'], b_low_index=row['wave_b_low_index'], **_scope_fields(_scope(row))))
        self.wave_events = events
        return self.n_bars, events


def post_b_wave_exit_events(
    bars: Sequence[Bar], audit: Sequence[Mapping[str, object]], signal_row: Mapping[str, object],
) -> list[dict[str, object]]:
    stream = SignalExecutionFactsStream(bars)
    stream._synchronize(len(bars) - 1, audit, signal_row)
    return stream.segment_events(signal_row)
