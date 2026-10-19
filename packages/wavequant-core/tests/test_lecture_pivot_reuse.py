"""Causal snapshots share only unchanged, exact frozen pivot values."""

from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
import random
from typing import cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.polyline import ReversalPoint
from wavequant.domain.strategies import lecture_strategy


def history(seed: int = 901, n: int = 100) -> list[Bar]:
    rng = random.Random(seed)
    bars = []
    price = 30.
    for index in range(n):
        close = max(10., price + rng.uniform(-3, 3))
        bars.append(Bar(datetime(2020, 1, 1) + timedelta(days=index), 'TEST', price,
                        max(close, price) + rng.uniform(.1, 2),
                        min(close, price) - rng.uniform(.1, 2), close,
                        rng.randint(100000, 2000000)))
        price = close
    return bars


def _points(price: float | int, *, low: bool = False) -> list[dict[str, object]]:
    return [
        dict(index=0, ordinal=0, kind='H' if low else 'L', value=30. if low else 10., state='complete'),
        dict(index=1, ordinal=0, kind='L' if low else 'H', value=price, state='complete'),
        dict(index=2, ordinal=0, kind='H' if low else 'L', value=25. if low else 12., state='complete'),
    ]


def _drawing(
    monkeypatch: pytest.MonkeyPatch,
    frames: Sequence[tuple[int, int, list[dict[str, object]]]],
) -> None:
    def draw(_bars: Sequence[Bar], *, on_step: Callable[..., None]) -> None:
        for index, epoch, points in frames:
            on_step(index, epoch, points)

    monkeypatch.setattr(lecture_strategy, 'lecture_drawing', draw)


@pytest.mark.parametrize('low,price', [(False, 20.), (True, 20.)])
def test_unchanged_frozen_turn_is_shared_without_backdating_or_mutation(
    monkeypatch: pytest.MonkeyPatch, low: bool, price: float,
) -> None:
    points = _points(price, low=low)
    _drawing(monkeypatch, [(2, 0, points), (3, 0, points), (4, 0, points)])
    snapshots, epochs, limits, blocked = lecture_strategy.lecture_pivot_history(history(n=5))
    first = snapshots[2][0]
    assert all(snapshots[index][0] is first for index in (3, 4))
    assert first.confirmed_index == 2 and first.point.index == 1
    assert first.point.price == price and first.point.kind.value == ('L' if low else 'H')
    assert first.source == 'lecture_close_confirmed_convention_not_intrabar_execution'
    points[1]['value'] = 19. if low else 21.
    assert first.point.price == price
    assert epochs == {2: 0, 3: 0, 4: 0} and not blocked
    assert all(limit == 4 for limit in limits.values())


def test_equal_numeric_price_with_a_changed_type_creates_a_new_exact_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drawing(monkeypatch, [(2, 0, _points(20)), (3, 0, _points(20.))])
    snapshots, _, _, _ = lecture_strategy.lecture_pivot_history(history(n=4))
    assert snapshots[2][0] is not snapshots[3][0]
    assert type(snapshots[2][0].point.price) is int
    assert type(snapshots[3][0].point.price) is float
    assert snapshots[2][0].confirmed_index == snapshots[3][0].confirmed_index == 2


def test_revised_price_and_new_epoch_keep_their_own_confirmation_times(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _drawing(monkeypatch, [(2, 0, _points(20.)), (3, 0, _points(21.)),
                          (4, 4, _points(21.))])
    snapshots, epochs, _, _ = lecture_strategy.lecture_pivot_history(history(n=5))
    assert [snapshots[index][0].confirmed_index for index in (2, 3, 4)] == [2, 3, 4]
    assert [snapshots[index][0].point.price for index in (2, 3, 4)] == [20., 21., 21.]
    assert snapshots[3][0] is not snapshots[4][0] and epochs[4] == 4


def test_same_geometry_disappearing_as_developing_is_confirmed_again_when_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    developing = _points(20.)
    developing[1]['state'] = 'developing'
    _drawing(monkeypatch, [(2, 0, _points(20.)), (3, 0, developing), (4, 0, _points(20.))])
    snapshots, _, _, _ = lecture_strategy.lecture_pivot_history(history(n=5))
    assert snapshots[3] == ()
    assert snapshots[2][0].confirmed_index == 2 and snapshots[4][0].confirmed_index == 4
    assert snapshots[2][0] is not snapshots[4][0]


def test_partial_prefix_reuse_preserves_complete_fields_and_outer_snapshot_independence() -> None:
    bars = history(seed=18, n=40)
    prefix_cache: dict[str, object] = {}
    original = lecture_strategy.lecture_pivot_history(bars, prefix_cache=prefix_cache)
    extended = lecture_strategy.lecture_pivot_history(bars, prefix_cache=prefix_cache)
    assert extended == original == lecture_strategy.lecture_pivot_history(bars)
    assert extended[0] is not original[0] and extended[3] is not original[3]
    extended[0].clear()
    assert original[0]


def test_unknown_extra_metadata_in_a_saved_prefix_is_not_carried_into_new_snapshots() -> None:
    bars = history(seed=18, n=40)
    prefix_cache: dict[str, object] = {}
    lecture_strategy.lecture_pivot_history(bars, prefix_cache=prefix_cache)
    checkpoint = cast(tuple[object, ...], prefix_cache['checkpoint'])
    saved = cast(dict[int, tuple[ReversalPoint, ...]], checkpoint[0])
    for points in saved.values():
        for point in points:
            object.__setattr__(point, 'extra_metadata', object())
    resumed = lecture_strategy.lecture_pivot_history(bars, prefix_cache=prefix_cache)
    assert resumed == lecture_strategy.lecture_pivot_history(bars)
    assert all(not hasattr(point, 'extra_metadata') for point in resumed[0][len(bars) - 1])
