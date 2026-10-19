"""Complete request snapshots share only frozen values and detach public containers."""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.strategies import backtest_history_reuse as reuse_module
from wavequant.domain.strategies.backtest_history_reuse import (
    BacktestHistoryReuse, HierarchyHistory, HierarchyPoint, _exact_value,
)
from wavequant.domain.strategies.integrated_strategy import pivot_history

from .test_backtest_history_reuse import _strategy
from .test_lecture_strategy import history


def test_exact_frozen_pivot_snapshot_preserves_fields_and_detaches_maps_and_sets() -> None:
    bars = history(18, 40)
    strategy = _strategy()
    value = pivot_history(bars, strategy)
    expected = deepcopy(value)
    record = next(point for points in value[0].values() for point in points)
    with BacktestHistoryReuse(bars, strategy) as owner:
        owner.save_pivot(bars, strategy, value)
        value[0].clear()
        value[1].clear()
        value[2].clear()
        value[3].clear()
        first = owner.pivot(bars, strategy)
        assert first == expected and first is not None
        shared = next(point for points in first[0].values() for point in points)
        assert shared is record and shared.point is record.point
        with pytest.raises(FrozenInstanceError):
            setattr(shared, 'source', 'changed')
        with pytest.raises(FrozenInstanceError):
            setattr(shared.point, 'price', 1.)
        for part in first:
            part.clear()
        assert owner.pivot(bars, strategy) == expected


class _Unknown:
    copied = 0
    reduced = 0

    def __deepcopy__(self, memo: dict[int, object]) -> '_Unknown':
        type(self).copied += 1
        return _Unknown()

    def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
        type(self).reduced += 1
        raise AssertionError('unknown serialization hook must not be added by the fast path')


def test_pivot_with_unknown_extra_metadata_keeps_original_deepcopy_count() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    value = pivot_history(bars, strategy)
    record = next(point for points in value[0].values() for point in points)
    object.__setattr__(record, 'unknown_metadata', _Unknown())
    _Unknown.copied = _Unknown.reduced = 0
    with BacktestHistoryReuse(bars, strategy) as owner:
        owner.save_pivot(bars, strategy, value)
        assert _Unknown.copied == 1 and _Unknown.reduced == 0
        first = owner.pivot(bars, strategy)
        assert first is not None
        copied = next(point for points in first[0].values() for point in points)
        assert copied is not record
        assert _Unknown.copied == 2 and _Unknown.reduced == 0


def _hierarchy() -> HierarchyHistory:
    point = dict(index=2, ordinal=0, kind='H', value=-0., available_at=3,
                 nested={'audit': [(1, True, 1.)]})
    reversed_point = dict(reversed(list(point.items())))
    return ({0: {1: (cast(HierarchyPoint, point),)},
             1: {1: (cast(HierarchyPoint, point), cast(HierarchyPoint, reversed_point))}}, {0: 0, 1: 0})


def test_hierarchy_templates_preserve_all_fields_types_order_and_isolate_every_date_and_read() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    value = _hierarchy()
    expected = deepcopy(value)
    with BacktestHistoryReuse(bars, strategy) as owner:
        owner.save_hierarchy(bars, strategy, value)
        cast(dict[str, object], value[0][0][1][0]).clear()
        value[1].clear()
        first = owner.hierarchy(bars, n_target_trend_confirmation_enabled=False)
        assert first is not None and _exact_value(first) == _exact_value(expected)
        assert list(first[0][0][1][0]) == list(expected[0][0][1][0])
        assert list(first[0][1][1][1]) == list(expected[0][1][1][1])
        cast(dict[str, object], first[0][0][1][0]).clear()
        assert first[0][1][1][0] == expected[0][1][1][0]
        assert _exact_value(owner.hierarchy(bars, n_target_trend_confirmation_enabled=False)) == _exact_value(expected)


def test_hierarchy_unknown_metadata_keeps_original_copy_hooks_without_reduce_hooks() -> None:
    bars = history(18, 20)
    strategy = _strategy()
    point = cast(HierarchyPoint, dict(index=2, ordinal=0, kind='H', value=10., available_at=3,
                                     unknown=_Unknown()))
    value: HierarchyHistory = ({0: {1: (point,)}}, {0: 0})
    _Unknown.copied = _Unknown.reduced = 0
    with BacktestHistoryReuse(bars, strategy) as owner:
        owner.save_hierarchy(bars, strategy, value)
        assert _Unknown.copied == 1 and _Unknown.reduced == 0
        assert owner.hierarchy(bars, n_target_trend_confirmation_enabled=False) is not None
        assert _Unknown.copied == 2 and _Unknown.reduced == 0


def test_hierarchy_intern_capacity_fallback_preserves_complete_public_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = history(18, 20)
    strategy = _strategy()
    value = _hierarchy()
    monkeypatch.setattr(reuse_module, '_HIERARCHY_INTERN_BYTES', 64)
    with BacktestHistoryReuse(bars, strategy) as owner:
        owner.save_hierarchy(bars, strategy, value)
        assert _exact_value(owner.hierarchy(bars, n_target_trend_confirmation_enabled=False)) == _exact_value(value)
