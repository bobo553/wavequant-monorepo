"""大批量证据、持久树分叉和尾部追加保持原有值及预算语义。"""

from sys import getsizeof
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_frozen_state as frozen
from wavequant.domain.market_structure.owned_evidence import OwnedEvidence
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.trend_point_version import PointVersion


def test_large_publication_reads_one_stable_allowance_and_rejects_atomically(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = 0
    original = OwnedEvidence.admission_remaining.fget
    assert original is not None

    def remaining(owner: OwnedEvidence) -> int:
        nonlocal reads
        reads += 1
        return original(owner)

    monkeypatch.setattr(OwnedEvidence, 'admission_remaining', property(remaining))
    value = {'numbers': list(range(6000))}
    owner = OwnedEvidence(memory_bytes=1024 * 1024)
    try:
        owner.publish(value)
        assert reads == 1 and owner.reference(value) is not None
        charged = owner.memory_used
        refused = OwnedEvidence(memory_bytes=charged - 1)
        try:
            refused.publish(value)
            assert refused.memory_used == 0 and refused.reference(value) is None
        finally:
            refused.close()
    finally:
        owner.close()


def test_large_finite_graph_keeps_cycles_and_nonfinite_values_rejected() -> None:
    owner = OwnedEvidence()
    shared: list[object] = [*range(6000), -0., None, True]
    try:
        assert owner.finite({'left': shared, 'right': shared})
        shared.append(float('inf'))
        assert not owner.finite(shared)
        shared.pop()
        shared.append(shared)
        assert not owner.finite(shared)
        owner.publish(shared)
        assert owner.memory_used == 0
    finally:
        owner.close()


@pytest.mark.parametrize('length', (31, 32, 33, 1024, 1025, 6000))
def test_frozen_branch_revisions_preserve_old_versions_and_range_bounds(length: int) -> None:
    old = frozen.FrozenPrefixList(range(length))
    old.freeze(length)
    branch = old.copy()
    plain = list(range(length))
    for index in range(6000):
        position = index % length
        branch[position] = plain[position] = -index
    assert list(old) == list(range(length))
    assert list(branch) == plain
    branch.extend((9001, 9002))
    assert list(branch.iter_range(length)) == [9001, 9002]
    assert list(branch.iter_range(-3)) == [plain[-1], 9001, 9002]
    for position in (len(branch), -len(branch) - 1):
        with pytest.raises(IndexError):
            branch[position] = 0


def test_frozen_assignment_only_reads_the_replaced_leaf_once(monkeypatch: pytest.MonkeyPatch) -> None:
    values = frozen.FrozenPrefixList(range(6000))
    values.freeze(len(values))
    original = frozen._block
    reads = 0

    def block(node: frozen._SequenceNode | None, capacity: int, position: int) -> tuple[object, ...]:
        nonlocal reads
        reads += 1
        return original(node, capacity, position)

    monkeypatch.setattr(frozen, '_block', block)
    for index in range(6000):
        values[index] = -index
    assert reads == 6000
    assert list(values) == [-index for index in range(6000)]


def test_sequence_tree_charge_keeps_exact_empty_tuple_and_child_costs() -> None:
    left = frozen._sequence_node((1, 2, 3))
    right = frozen._sequence_node((4,))
    root = frozen._sequence_node(left=left, right=right)
    assert left.size == 128 + getsizeof((1, 2, 3))
    assert root.size == 128 + getsizeof(()) + left.size + right.size


def test_live_index_tail_uses_one_hash_and_does_not_change_old_branch() -> None:
    class Key:
        calls = 0

        def __hash__(self) -> int:
            self.calls += 1
            return 47

    key = Key()
    previous = frozen.FrozenPrefixMap([(key, 'old')])
    previous.freeze()
    following = previous.copy()
    following[key] = 'new'
    key.calls = 0
    for _ in range(6000):
        assert following[key] == 'new'
    assert key.calls == 6000
    assert previous[key] == 'old'


def test_point_append_keeps_key_validation_sealing_and_historical_prefix() -> None:
    previous = PointVersion({'index': index} for index in range(6000)).seal()
    following = PointVersion(previous=previous, start=len(previous))
    following.append({'index': 6000})
    with pytest.raises(ValueError, match='string keys'):
        following.append(cast(dict[str, object], {1: 'invalid'}))
    following.seal()
    with pytest.raises(TypeError, match='immutable'):
        following.append({'index': 6001})
    assert len(previous) == 6000 and len(following) == 6001
    assert following.changed_since(previous) == 6000


def test_batched_graph_preserves_many_shared_child_references_and_old_payloads() -> None:
    shared = {'numbers': list(range(6000))}
    graph = {'rows': [shared] * 6000, 'again': shared}
    with_history = PreparedGraphHistory()
    try:
        first = with_history.capture(graph)
        assert first is not None
        second = with_history.capture({'rows': [shared], 'again': shared})
        assert second is not None
        restored = cast(dict[str, object], with_history.restore(first))
        assert restored == graph
        assert all(row is restored['again'] for row in cast(list[object], restored['rows']))
        assert with_history.restore(second) == {'rows': [shared], 'again': shared}
    finally:
        with_history.close()
