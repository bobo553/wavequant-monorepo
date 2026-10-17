"""当前关键位缩窗保留全部历史、相邻关系与各级真实知识时钟。"""

from collections.abc import Iterator, Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from operator import index as integer_index
from typing import Self, SupportsIndex, cast, overload

import pytest

from wavequant.domain.market_structure import trend_lifecycle as lifecycle_module
from wavequant.domain.market_structure.bull_alternation_confirmation import AlternationPolicy, ConfirmedTurn
from wavequant.domain.market_structure.trend_lifecycle import (
    LifecycleState, TrendLifecycle, _BullRecovery, _Cycle, _Fact, _Pivot,
    _continued_bull_impulse, _point_orders,
)
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex

from .test_bull_recovery import recovery_case, upgrade_recovery_case
from .test_trend_lifecycle import scenario


class _CountedPoints(tuple[_Pivot, ...]):
    """记录原历史中实际读取的位置；切片仍返回普通不可变元组。"""

    read_positions: list[int]

    def __new__(cls, values: Sequence[_Pivot]) -> Self:
        result = super().__new__(cls, values)
        result.read_positions = []
        return result

    @overload
    def __getitem__(self, position: SupportsIndex) -> _Pivot: ...

    @overload
    def __getitem__(self, position: slice) -> tuple[_Pivot, ...]: ...

    def __getitem__(self, position: SupportsIndex | slice) -> _Pivot | tuple[_Pivot, ...]:
        if isinstance(position, slice):
            self.read_positions.extend(range(*position.indices(len(self))))
        else:
            number = integer_index(position)
            self.read_positions.append(number if number >= 0 else len(self) + number)
        return super().__getitem__(position)

    def __iter__(self) -> Iterator[_Pivot]:
        for position, point in enumerate(super().__iter__()):
            self.read_positions.append(position)
            yield point


def _bars(count: int) -> list[Bar]:
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'WINDOW', 11., 16., 10., 11., 100.)
            for index in range(count)]


def _raw(index: int, kind: str, price: float, known: int, *, ordinal: int = 0) -> dict[str, object]:
    return dict(index=index, ordinal=ordinal, kind=kind, value=price, available_at=known, state='confirmed')


def _geometry(points: Sequence[dict[str, object]], level: int = 2) -> dict[str, object]:
    return dict(trend_level=level, strokes=[dict(id='path', points=list(points))])


def _without_orders(_points: tuple[_Pivot, ...]) -> None:
    return None


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('case', ('bull', 'bear', 'recovery', 'upgrade_recovery'))
def test_indexed_daily_lifecycle_matches_full_history_iteration_at_every_level(
    level: int, case: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if case == 'recovery':
        raw_bars, row = recovery_case(level)
    elif case == 'upgrade_recovery':
        raw_bars, row = upgrade_recovery_case(level)
    else:
        raw_bars, row = scenario(level=level, up=case == 'bull')
    bars, geometry = cast(list[Bar], raw_bars), cast(dict[str, object], row)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    indexed, ordinary = TrendLifecycle(), TrendLifecycle()
    all_events: list[dict[str, object]] = []
    for now in range(len(bars)):
        current = indexed.advance((geometry,), bars, now, dates, _owned_structure=True)
        with monkeypatch.context() as fallback:
            fallback.setattr(lifecycle_module, '_point_orders', _without_orders)
            expected = ordinary.advance((geometry,), bars, now, dates)
        assert current == expected, (level, case, now)
        assert indexed.checkpoint() == ordinary.checkpoint(), (level, case, now)
        all_events.extend(current)
    assert any(event['event'] == 'trend_lifecycle_state' for event in all_events)


def test_continuation_reads_only_the_three_points_at_the_frozen_broken_key() -> None:
    prefix = tuple(_Pivot(index, 0, 'H' if index % 2 else 'L', 12. if index % 2 else 10., index)
                   for index in range(40))
    broken, origin = _Pivot(40, 2, 'H', 12., 40), _Pivot(41, 0, 'L', 9., 41)
    previous, extension = _Pivot(42, 0, 'H', 14., 42), _Pivot(43, 0, 'H', 15., 43)
    suffix = tuple(_Pivot(index, 0, 'H' if index % 2 else 'L', 16. if index % 2 else 11., index)
                   for index in range(44, 80))
    points = (*prefix, broken, origin, extension, *suffix)
    cycle = _Cycle(LifecycleState.BULL_FLIP, True, origin, 42, previous,
                   recovery=True, structure=(broken, origin, previous))
    counted = _CountedPoints(points)
    actual = _continued_bull_impulse(cycle, counted, _point_orders(points))
    assert actual == _continued_bull_impulse(cycle, points)
    assert actual is not None and actual.impulse == extension
    assert counted.read_positions == [40, 41, 42]
    assert cycle.impulse == previous


@pytest.mark.parametrize('ordinal', (0, 3))
def test_recovery_window_includes_the_breach_day_and_its_real_predecessor(ordinal: int) -> None:
    prefix = tuple(_Pivot(index, 0, 'H' if index % 2 else 'L', 12. if index % 2 else 10., index)
                   for index in range(39))
    previous = _Pivot(39, 1, 'H', 12., 39)
    origin, impulse = _Pivot(40, ordinal, 'L', 9., 40), _Pivot(41, 0, 'H', 14., 41)
    points = (*prefix, previous, origin, impulse)
    seed = _Fact('翻空为多', _Pivot(1, 0, 'H', 15., 1), 1, _Pivot(0, 0, 'L', 10.5, 0))
    pending = _BullRecovery(seed, breach=40, recovered=40)
    bars, key = _bars(43), (2, 'path')
    indexed, ordinary = TrendLifecycle(), TrendLifecycle()
    indexed._bull_recovery[key] = ordinary._bull_recovery[key] = pending
    actual: list[dict[str, object]] = []
    expected: list[dict[str, object]] = []
    counted = _CountedPoints(points)
    indexed._advance_bull_recovery(key, counted, bars, 42, actual, _point_orders(points))
    ordinary._advance_bull_recovery(key, points, bars, 42, expected)
    assert actual == expected and actual
    assert indexed._cycles == ordinary._cycles
    assert indexed._cycles[key].structure == (previous, origin, impulse)
    assert counted.read_positions == [39, 40, 41]
    assert actual[-1]['origin_index'] == 40 and actual[-1]['broken_high_index'] == 39


def test_alternation_receives_the_suffix_of_its_own_frozen_key(monkeypatch: pytest.MonkeyPatch) -> None:
    prefix = tuple(_Pivot(index, 0, 'H' if index % 2 else 'L', 12. if index % 2 else 10., index)
                   for index in range(40))
    broken, origin, high = _Pivot(40, 1, 'H', 12., 40), _Pivot(41, 0, 'L', 9., 41), _Pivot(42, 0, 'H', 14., 42)
    counter = _Pivot(43, 0, 'L', 11., 43)
    points = (*prefix, broken, origin, high, counter)
    consumed: list[tuple[ConfirmedTurn, ...]] = []

    def capture(
        bars: Sequence[Bar], now: int, *, origin: ConfirmedTurn, high: ConfirmedTurn,
        broken: ConfirmedTurn, points: Sequence[ConfirmedTurn], polyline: Sequence[ConfirmedTurn],
        level: int, path: str, policy: AlternationPolicy, allow_gap_confirmation: bool,
        flip_confirmed_at: int | None,
        _market_index: _MarketRangeIndex | None = None,
    ) -> None:
        consumed.append(tuple(points))

    monkeypatch.setattr(lifecycle_module, 'confirm_bull_alternation', capture)
    for level in (1, 2, 3):
        engine = TrendLifecycle()
        fact = _Fact('翻空为多', high, 42, origin, broken=broken)
        engine._cycles[level, 'path'] = _Cycle(LifecycleState.BULL_FLIP, True, origin, 42, high,
                                              evidence=frozenset((fact,)))
        counted = _CountedPoints(points)
        engine._advance_bull_alternation((level, 'path'), counted, _bars(45), 44, [], (),
                                         AlternationPolicy(), _point_orders(points))
        assert counted.read_positions == [40, 41, 42, 40, 41, 42, 43]
    assert consumed == [(broken, origin, high, counter)] * 3
    assert len(points) == 44


@pytest.mark.parametrize('anchor', (0, 1, 2), ids=('broken', 'origin', 'impulse'))
@pytest.mark.parametrize('revision', ('price', 'known'))
def test_alternation_rejects_revised_frozen_evidence_at_the_same_coordinate(
    anchor: int, revision: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken, origin, high = _Pivot(40, 1, 'H', 12., 40), _Pivot(41, 0, 'L', 9., 41), _Pivot(42, 0, 'H', 14., 42)
    frozen = (broken, origin, high)
    original = frozen[anchor]
    revised = (replace(original, price=original.price + .25) if revision == 'price'
               else replace(original, known=original.known + 1))
    points = tuple(revised if position == anchor else point for position, point in enumerate(frozen))
    fact = _Fact('翻空为多', high, 42, origin, broken=broken)
    cycle = _Cycle(LifecycleState.BULL_FLIP, True, origin, 42, high, evidence=frozenset((fact,)))
    key = (2, 'path')

    def unexpected_confirmation(*args: object, **kwargs: object) -> None:
        raise AssertionError('修订点不能冒充周期已冻结的完整证据')

    monkeypatch.setattr(lifecycle_module, 'confirm_bull_alternation', unexpected_confirmation)
    for orders in (_point_orders(points), None):
        engine = TrendLifecycle()
        engine._cycles[key] = cycle
        events: list[dict[str, object]] = []
        engine._advance_bull_alternation(key, points, _bars(45), 44, events, (), AlternationPolicy(), orders)
        assert engine._cycles[key] == cycle
        assert events == []


def test_future_knowledge_and_revised_geometry_rebuild_the_point_position_index() -> None:
    bars = _bars(7)
    raw = [_raw(0, 'H', 12., 0), _raw(1, 'L', 9., 3), _raw(2, 'H', 13., 2)]
    geometry = _geometry(raw)
    engine = TrendLifecycle()
    for now in range(3):
        engine.advance((geometry,), bars, now, {}, _owned_structure=True)
    before = engine._structure_facts[2, 'path']
    assert before.point_orders == ((0, 0), (2, 0)) and before.next_known == 3
    engine.advance((geometry,), bars, 3, {}, _owned_structure=True)
    available = engine._structure_facts[2, 'path']
    assert available is not before and available.point_orders == ((0, 0), (1, 0), (2, 0))
    engine.advance((geometry,), bars, 4, {}, _owned_structure=True)
    assert engine._structure_facts[2, 'path'] is available
    revised = _geometry([deepcopy(raw[0]), _raw(0, 'L', 11., 5, ordinal=1), *deepcopy(raw[1:])])
    engine.advance((revised,), bars, 5, {}, _owned_structure=True)
    changed = engine._structure_facts[2, 'path']
    assert changed is not available and changed.point_orders == ((0, 0), (0, 1), (1, 0), (2, 0))
    engine.advance((revised,), bars, 6, {}, _owned_structure=True)
    assert engine._structure_facts[2, 'path'] is changed
    assert before.point_orders == ((0, 0), (2, 0))
    assert available.point_orders == ((0, 0), (1, 0), (2, 0))


@pytest.mark.parametrize('irregularity', ('unordered', 'duplicate'))
def test_irregular_point_order_preserves_the_original_iteration_fallback(
    irregularity: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars(5)
    first, second, third = _raw(0, 'H', 12., 0), _raw(1, 'L', 9., 1), _raw(2, 'H', 13., 2)
    raw = [second, first, third] if irregularity == 'unordered' else [first, dict(first), second, third]
    geometry = _geometry(raw)
    indexed, ordinary = TrendLifecycle(), TrendLifecycle()
    for now in range(len(bars)):
        actual = indexed.advance((geometry,), bars, now, {}, _owned_structure=True)
        with monkeypatch.context() as fallback:
            fallback.setattr(lifecycle_module, '_point_orders', _without_orders)
            expected = ordinary.advance((geometry,), bars, now, {})
        assert actual == expected
        assert indexed.checkpoint() == ordinary.checkpoint()
    assert indexed._structure_facts[2, 'path'].point_orders is None
    broken, origin, previous = _Pivot(3, 0, 'H', 12., 3), _Pivot(4, 0, 'L', 9., 4), _Pivot(5, 0, 'H', 14., 5)
    prefix = indexed._structure_facts[2, 'path'].points
    extension = _Pivot(6, 0, 'H', 15., 6)
    points = (*prefix, broken, origin, extension)
    cycle = _Cycle(LifecycleState.BULL_FLIP, True, origin, 5, previous,
                   recovery=True, structure=(broken, origin, previous))
    assert _point_orders(points) is None
    assert _continued_bull_impulse(cycle, points, _point_orders(points)) == _continued_bull_impulse(cycle, points)
