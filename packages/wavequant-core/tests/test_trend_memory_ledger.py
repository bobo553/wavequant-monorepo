"""Checkpoint accounting must reuse blocks without hiding mutable growth."""

from collections.abc import Sequence

import pytest

from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList, FrozenPrefixMap
from wavequant.domain.market_structure.trend_judgement import _FormalKeyBreakState
from wavequant.domain.market_structure.trend_memory_ledger import _ImmutableBlockLedger


def _ledger(limit: int = 8 * 1024 * 1024) -> _ImmutableBlockLedger:
    return _ImmutableBlockLedger(
        _FormalKeyBreakState._budget_children, _FormalKeyBreakState._budget_block, limit=limit)


def test_unchanged_sealed_blocks_are_not_walked_again(monkeypatch: pytest.MonkeyPatch) -> None:
    records = FrozenPrefixList([dict(index=i, evidence=['x' * 200, i]) for i in range(96)])
    records.freeze(len(records))
    ledger = _ledger()
    initial = ledger.measure([records])
    children = ledger._children

    def reject_old_block(value: object) -> Sequence[object]:
        if _FormalKeyBreakState._budget_block(value):
            raise AssertionError('retained immutable block was traversed again')
        return tuple(children(value))

    monkeypatch.setattr(ledger, '_children', reject_old_block)
    for _ in range(8):
        assert ledger.measure([records]) == initial
    assert initial >= _FormalKeyBreakState._value_bytes([records])


def test_shared_versions_keep_live_blocks_and_release_removed_versions() -> None:
    records = FrozenPrefixList([dict(index=i, evidence=['x' * 200]) for i in range(96)])
    records.freeze(len(records))
    newer = records.copy()
    newer.append(dict(index=96, evidence=['tail']))
    newer.freeze(len(newer))
    ledger = _ledger()
    ledger.measure([records])
    original_blocks = set(ledger._blocks)
    ledger.measure([records, newer])
    both = set(ledger._blocks)
    assert original_blocks <= both
    ledger.measure([newer])
    assert set(ledger._blocks) < both
    assert set(ledger._blocks) & original_blocks
    ledger.measure([])
    assert ledger.memory_used == 0
    assert not ledger._blocks and not ledger._roots


def test_mutable_tail_is_remeasured_and_does_not_enter_block_cache() -> None:
    payload: dict[str, object] = dict(evidence=['small'])
    ledger = _ledger()
    before = ledger.measure([payload])
    payload['evidence'] = ['large' * 10000]
    after = ledger.measure([payload])
    assert after > before
    assert after >= _FormalKeyBreakState._value_bytes([payload])
    assert not ledger._blocks


def test_index_revision_releases_old_proof_and_counts_new_capacity() -> None:
    index = FrozenPrefixMap([(i, dict(evidence=str(i) * 100)) for i in range(64)])
    index.freeze()
    ledger = _ledger()
    old_size = ledger.measure([index])
    revised = index.copy()
    revised[12] = dict(evidence='replacement' * 10000)
    revised.freeze()
    revised_size = ledger.measure([revised])
    assert revised_size > old_size
    assert revised_size >= _FormalKeyBreakState._value_bytes([revised])
    for _ in range(5):
        assert ledger.measure([revised]) == revised_size


def test_budget_rejection_releases_ledger_ownership_and_can_restart() -> None:
    records = FrozenPrefixList([dict(evidence='large' * 10000)])
    records.freeze(len(records))
    ledger = _ledger(8192)
    assert ledger.measure([records]) > 8192
    assert ledger.memory_used == 0
    assert not ledger._blocks and not ledger._roots
    assert ledger.measure([dict(value=1)]) <= 8192


def test_repeated_shared_roots_do_not_accumulate_charges() -> None:
    records = FrozenPrefixList([dict(evidence='shared')])
    records.freeze(len(records))
    ledger = _ledger()
    one = ledger.measure([records])
    assert ledger.measure([records, records]) == one
    for _ in range(20):
        assert ledger.measure([records]) == one


def test_replacement_is_charged_after_obsolete_blocks_are_released() -> None:
    old = FrozenPrefixList([dict(evidence='a' * 60000)])
    new = FrozenPrefixList([dict(evidence='b' * 60000)])
    old.freeze(len(old))
    new.freeze(len(new))
    limit = max(_ledger().measure([old]), _ledger().measure([new])) + 8192
    ledger = _ledger(limit)
    assert ledger.measure([old]) <= limit
    old_blocks = set(ledger._blocks)
    assert ledger.measure([new]) <= limit
    assert ledger._blocks
    assert not old_blocks.intersection(ledger._blocks)
    assert ledger.measure([new]) <= limit

    # Both roots really retained must still be rejected.
    assert ledger.measure([old, new]) > limit
    assert not ledger._blocks and not ledger._roots
    assert ledger.measure([old]) <= limit


def test_final_budget_includes_mutable_roots_and_retained_blocks() -> None:
    block = FrozenPrefixList([dict(evidence='a' * 30000)])
    block.freeze(len(block))
    limit = _ledger().measure([block]) + 8192
    ledger = _ledger(limit)
    assert ledger.measure([block]) <= limit
    assert ledger.measure([block, dict(evidence='b' * 30000)]) > limit
    assert ledger.memory_used == 0
