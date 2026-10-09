from collections.abc import Callable
from dataclasses import FrozenInstanceError, asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from wavequant.domain.market_structure.candle_primitives import (
    BarRelations,
    extending_head,
    falling_tail,
    observe_bar_relations,
    shrinking_foot,
    shrinking_head,
    sunrise,
    sunset,
    virtual_high,
    virtual_low,
)
from wavequant.domain.market_structure.foundations import bar_relations
from wavequant.domain.market_structure.polyline import (
    BarRelations as PolylineBarRelations,
    observe_bar_relations as polyline_bar_relations,
)
from wavequant.domain.models.model import Bar


def candle(index: int, high: float, low: float, close: float) -> Bar:
    return Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', close, high, low, close, 1000)


PREVIOUS = candle(0, 12, 8, 10)
RELATION_OBSERVERS: tuple[Callable[[Bar, Bar], bool], ...] = (
    shrinking_head, shrinking_foot, extending_head, falling_tail, sunrise, sunset,
)
ALL_OBSERVERS: tuple[Callable[[Bar, Bar], object], ...] = (
    observe_bar_relations, *RELATION_OBSERVERS, virtual_low, virtual_high,
)


@pytest.mark.parametrize(
    ('current', 'expected'),
    [
        (candle(1, 13, 9, 12.5), (False, True, True, False, True, False)),
        (candle(1, 11, 7, 7.5), (True, False, False, True, False, True)),
        (candle(1, 11, 9, 10), (True, True, False, False, False, False)),
        (candle(1, 13, 7, 10), (False, False, True, True, False, False)),
        (candle(1, 12, 8, 10), (False, False, False, False, False, False)),
    ],
)
def test_named_relations_follow_strict_high_low_and_close_definitions(
    current: Bar, expected: tuple[bool, bool, bool, bool, bool, bool],
) -> None:
    assert tuple(observer(PREVIOUS, current) for observer in RELATION_OBSERVERS) == expected


@pytest.mark.parametrize('closing', [11.5, 12])
def test_higher_extremes_without_close_above_old_high_are_not_sunrise(closing: float) -> None:
    current = candle(1, 13, 9, closing)
    assert extending_head(PREVIOUS, current)
    assert shrinking_foot(PREVIOUS, current)
    assert not sunrise(PREVIOUS, current)


@pytest.mark.parametrize('closing', [8, 8.5])
def test_lower_extremes_without_close_below_old_low_are_not_sunset(closing: float) -> None:
    current = candle(1, 11, 7, closing)
    assert shrinking_head(PREVIOUS, current)
    assert falling_tail(PREVIOUS, current)
    assert not sunset(PREVIOUS, current)


@pytest.mark.parametrize(
    ('current', 'equal_high', 'equal_low'),
    [(candle(1, 12, 9, 10), True, False), (candle(1, 11, 8, 10), False, True)],
)
def test_one_equal_extreme_does_not_become_strict_containment(
    current: Bar, equal_high: bool, equal_low: bool,
) -> None:
    relation = observe_bar_relations(PREVIOUS, current)
    assert relation.equal_high is equal_high
    assert relation.equal_low is equal_low
    assert not relation.inside
    assert not relation.outside


@pytest.mark.parametrize(
    ('current', 'expected_low', 'expected_high'),
    [
        (candle(1, 15, 13, 14), 10, 15),
        (candle(1, 7, 5, 6), 5, 10),
        (candle(1, 13, 7, 11), 7, 13),
    ],
)
def test_virtual_extremes_include_previous_close_across_gaps(
    current: Bar, expected_low: float, expected_high: float,
) -> None:
    assert virtual_low(PREVIOUS, current) == expected_low
    assert virtual_high(PREVIOUS, current) == expected_high


@pytest.mark.parametrize('observer', ALL_OBSERVERS)
@pytest.mark.parametrize(
    'invalid',
    [
        replace(candle(1, 13, 9, 11), high=10),
        replace(candle(1, 13, 9, 11), low=12),
        replace(candle(1, 13, 9, 11), low=0),
        replace(candle(1, 13, 9, 11), high=float('inf')),
        replace(candle(1, 13, 9, 11), close=float('nan')),
        replace(candle(1, 13, 9, 11), open=True),
    ],
)
def test_all_primitives_reject_invalid_ohlc(
    observer: Callable[[Bar, Bar], object], invalid: Bar,
) -> None:
    with pytest.raises(ValueError):
        observer(PREVIOUS, invalid)
    with pytest.raises(ValueError):
        observer(replace(invalid, timestamp=PREVIOUS.timestamp), candle(1, 13, 9, 11))


@pytest.mark.parametrize('observer', ALL_OBSERVERS)
@pytest.mark.parametrize(
    'invalid',
    [
        candle(0, 13, 9, 11),
        candle(-1, 13, 9, 11),
        replace(candle(1, 13, 9, 11), symbol='OTHER'),
        replace(candle(1, 13, 9, 11), timestamp=datetime(2026, 1, 2, tzinfo=timezone.utc)),
    ],
)
def test_all_primitives_reject_unordered_or_incompatible_pairs(
    observer: Callable[[Bar, Bar], object], invalid: Bar,
) -> None:
    with pytest.raises(ValueError):
        observer(PREVIOUS, invalid)


def test_relations_are_immutable_and_polyline_retains_its_original_public_imports() -> None:
    current = candle(1, 13, 9, 12.5)
    relation = observe_bar_relations(PREVIOUS, current)
    assert PolylineBarRelations is BarRelations
    assert isinstance(polyline_bar_relations(PREVIOUS, current), BarRelations)
    assert polyline_bar_relations(PREVIOUS, current) == relation
    with pytest.raises(FrozenInstanceError):
        setattr(relation, 'sunrise', False)


def test_legacy_adapter_preserves_current_previous_order_and_dictionary_fields() -> None:
    current = candle(1, 13, 9, 12.5)
    relation = observe_bar_relations(PREVIOUS, current)
    expected = asdict(relation)
    expected['lifting_foot'] = expected.pop('shrinking_foot')
    expected.update(virtual_low=9, virtual_high=13, requires_lower_timeframe=False)
    assert bar_relations(current, PREVIOUS) == expected
    assert 'shrinking_foot' not in bar_relations(current, PREVIOUS)
    with pytest.raises(ValueError):
        bar_relations(PREVIOUS, current)
