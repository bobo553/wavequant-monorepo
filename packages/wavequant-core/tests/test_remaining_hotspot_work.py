"""减少同一图节点、映射尾块和行情区间的重复工作。"""

import builtins
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure import owned_evidence as evidence
from wavequant.domain.market_structure.n_shape import BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixMap
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars


def test_finite_reuses_the_identity_when_leaving_each_container(monkeypatch: pytest.MonkeyPatch) -> None:
    counts: Counter[int] = Counter()

    def identity(value: object) -> int:
        result = builtins.id(value)
        counts[result] += 1
        return result

    graph = {'children': [{'index': i} for i in range(6000)]}
    owner = evidence.OwnedEvidence()
    monkeypatch.setattr(evidence, 'id', identity, raising=False)
    try:
        assert owner.finite(graph)
        assert len(counts) == 6002 and set(counts.values()) == {1}
        cycle: list[object] = []
        cycle.append(cycle)
        assert not owner.finite(cycle)
    finally:
        owner.close()


def test_map_key_tail_keeps_snapshot_order_without_rewriting_partial_blocks() -> None:
    current: FrozenPrefixMap[int, object] = FrozenPrefixMap()
    snapshots: list[FrozenPrefixMap[int, object]] = []
    for i in range(100):
        old_root = current._keys._archive
        current[i] = i
        current.freeze()
        if (i + 1) % 32:
            assert current._keys._archive is old_root
        assert len(current._keys._tail) < 32
        snapshots.append(current.copy())
    current[3] = 'changed'
    current.freeze()
    del current[10]
    for i, saved in enumerate(snapshots):
        assert list(saved.items()) == [(key, key) for key in range(i + 1)]
    assert current[3] == 'changed' and 10 not in current


def _structure(down: bool) -> tuple[list[Bar], NSetup]:
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=i), 'TEST', 10.5, 11, 10, 10.5, 100)
            for i in range(100)]
    bars[0] = replace(bars[0], open=8.5, high=9, low=8, close=8.5)
    bars[50] = replace(bars[50], high=12, close=11)
    if down:
        bars = [replace(bar, open=40-bar.open, high=40-bar.low, low=40-bar.high, close=40-bar.close)
                for bar in bars]
    setup = NSetup('TEST', '1d', Direction.DOWN if down else Direction.UP,
                   PivotRef(0, 0), PivotRef(50, 50), PivotRef(99, 99),
                   'test_confirmed_pivots', BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)
    return bars, setup


@pytest.mark.parametrize('down', [False, True])
def test_n_constraints_read_each_non_anchor_bar_once(down: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    bars, setup = _structure(down)
    expected = observe_n(bars, setup, timeframe='1d', milestone_basis=MilestoneBasis.CLOSE)
    validated = ValidatedBars(bars)
    original = ValidatedBars.__getitem__
    reads: Counter[int] = Counter()

    def read(self: ValidatedBars, index: int | slice) -> object:
        if isinstance(index, int):
            reads[index] += 1
        return original(self, index)

    monkeypatch.setattr(ValidatedBars, '__getitem__', read)
    assert observe_n(validated, setup, timeframe='1d', milestone_basis=MilestoneBasis.CLOSE) == expected
    assert all(reads[i] == 1 for i in range(1, 99) if i != 50)


@pytest.mark.parametrize('origin_bad,neck_bad,expected', [
    (True, True, 'origin is not'), (False, True, 'neckline is not'), (False, False, 'C is not'),
])
def test_combined_constraint_scan_preserves_rejection_priority(
    origin_bad: bool, neck_bad: bool, expected: str,
) -> None:
    bars, setup = _structure(False)
    bars[75] = replace(bars[75], low=9)
    if origin_bad:
        bars[25] = replace(bars[25], low=7)
    if neck_bad:
        bars[25] = replace(bars[25], high=13)
    with pytest.raises(ValueError, match=expected):
        observe_n(ValidatedBars(bars), setup, timeframe='1d', milestone_basis=MilestoneBasis.CLOSE)
