"""冻结前缀的版本定位和预算只随变化增长。"""

from dataclasses import replace

import pytest

from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_publication_cache import (
    _PublicationCache, _PublicationCheckpoint, _SequenceBudget,
)


@pytest.mark.parametrize('length', (0, 1, 31, 32, 33, 1023, 1024, 1025, 4096))
def test_shared_prefix_handles_branches_truncation_and_capacity_changes(length, monkeypatch):
    original = PointVersion(dict(index=i) for i in range(length)).seal()
    intermediate = PointVersion([dict(index=length)], previous=original, start=length).seal()
    appended = PointVersion([dict(index=length + 1)], previous=intermediate, start=length + 1).seal()

    def forbidden(*args):
        raise AssertionError('shared frozen prefix was iterated')

    monkeypatch.setattr(PointVersion, '__iter__', forbidden)
    assert appended.changed_since(original) == length
    assert original.changed_since(appended) == length
    if length:
        changed = max(0, length - 3)
        revised = PointVersion([dict(index=changed)], previous=appended, start=changed).seal()
        assert revised.changed_since(original) == changed
        truncated = PointVersion(previous=appended, start=length - 1).seal()
        assert truncated.changed_since(original) == length - 1


def test_shared_prefix_with_independent_blocks_and_mutable_tail():
    points = [dict(index=i) for i in range(100)]
    left, right = FrozenPrefixList(points), FrozenPrefixList(points)
    left.freeze(63)
    right.freeze(96)
    assert left.shared_prefix(right) == 100
    right[78] = dict(points[78])
    assert left.shared_prefix(right) == 78


def test_replacing_suffix_does_not_accumulate_discarded_budget():
    budget = _SequenceBudget()
    version = PointVersion([dict(index=0), dict(index=1)]).seal()
    assert budget.update(version, 0, 100_000)
    maximum = budget.size + 1024
    for i in range(200):
        version = PointVersion([dict(index=i)], previous=version, start=1).seal()
        assert budget.update(version, 1, maximum)
        assert len(budget.costs) == 3
    shortened = PointVersion(previous=version, start=1).seal()
    before = budget.size
    assert budget.update(shortened, 1, maximum)
    assert budget.size < before


def test_checkpoint_save_does_not_size_frozen_prefix_again(monkeypatch):
    import wavequant.domain.market_structure.trend_publication_cache as module

    points = FrozenPrefixList([dict(index=i, evidence={'prices': list(range(20))}) for i in range(100)])
    points.freeze(len(points))
    checkpoint = _PublicationCheckpoint(100, 100, points, None, None, None, None, None, None)
    cache = _PublicationCache()
    cache.save(checkpoint)
    assert cache.checkpoint is checkpoint
    old_ids = {id(point) for point in points}
    size = module._builtin_size

    def checked(value, limit):
        assert id(value) not in old_ids, 'frozen evidence was charged again'
        return size(value, limit)

    monkeypatch.setattr(module, '_builtin_size', checked)
    following = points.copy()
    following.append(dict(index=100))
    following.freeze(len(following))
    updated = replace(checkpoint, offset=101, horizon=101, result=following)
    cache.save(updated)
    assert cache.checkpoint is updated


def test_checkpoint_budget_still_rejects_oversized_evidence():
    points = FrozenPrefixList[dict[str, object]]([dict(evidence='x' * 100_000)])
    points.freeze(1)
    cache = _PublicationCache(memory_bytes=4096)
    cache.save(_PublicationCheckpoint(1, 1, points, None, None, None, None, None, None))
    assert cache.checkpoint is None


def test_shared_evidence_is_charged_to_its_request_pool_only():
    from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
    from wavequant.domain.market_structure.trend_publication_cache import _publication_size

    value = {'proof': list(range(500))}
    assert _publication_size(value, 64) is None
    with owned_evidence_scope() as owner:
        owner.publish(value)
        assert owner.memory_used > 64
        assert _publication_size(value, 64) == 8
    assert _publication_size(value, 64) is None


def test_scalar_evidence_does_not_pay_for_nonexistent_reference_entries():
    from sys import getsizeof
    from wavequant.domain.market_structure.owned_evidence import OwnedEvidence

    values = [float(i) for i in range(100)]
    owner = OwnedEvidence(memory_bytes=8192)
    try:
        owner.publish(values)
        assert owner.reference(values) is not None
        assert len(owner._nodes) == 1
        assert owner.memory_used == getsizeof(values) + sum(getsizeof(value) for value in values) + 384
        assert owner.loads(owner.dumps(values)) == values
    finally:
        owner.close()
