"""Shared publication accounting and projections preserve checkpoint versions."""

import pytest

from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList
from wavequant.domain.market_structure.trend_publication_cache import _PublicationCache, _PublicationCheckpoint
from wavequant.domain.market_structure.trend_readonly_cache import _builtin_size


def _point(index):
    return dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=index + 10.,
                available_at=index, evidence={'details': [str(index) * 200]})


def _checkpoint(points):
    points.freeze(len(points))
    return _PublicationCheckpoint(len(points), 200, points.copy(), None, None, None, None, None, None)


def test_new_checkpoint_does_not_rewalk_unchanged_blocks(monkeypatch):
    cache = _PublicationCache()
    records = FrozenPrefixList(_point(i) for i in range(96))
    first = _checkpoint(records)
    cache.save(first)
    old_evidence = {id(point['evidence']) for point in records}
    original = cache._budget._children

    def children(value):
        assert id(value) not in old_evidence, 'old sealed evidence was traversed again'
        return original(value)

    monkeypatch.setattr(cache._budget, '_children', children)
    records.append(_point(96))
    second = _checkpoint(records)
    cache.save(second)
    assert cache.checkpoint is second
    assert cache._checkpoint_bytes >= _builtin_size(list(records), cache.limit)
    assert cache._checkpoint_bytes <= cache.limit
    assert len(first.result) == 96
    cache.clear()
    assert cache._budget.memory_used == 0


def test_unknown_checkpoint_does_not_call_custom_size_hook():
    class Unknown:
        def __sizeof__(self):
            raise AssertionError('unknown hook invoked')

    cache = _PublicationCache()
    records = FrozenPrefixList([dict(_point(0), evidence=Unknown())])
    cache.save(_checkpoint(records))
    assert cache.checkpoint is None
    assert cache._budget.memory_used == 0


def test_budget_rejection_releases_checkpoint_and_projection():
    cache = _PublicationCache(memory_bytes=128)
    cache.save(_checkpoint(FrozenPrefixList([_point(0)])))
    assert cache.checkpoint is None
    assert cache._hierarchy is None
    assert cache._budget.memory_used == 0


def test_projection_reuses_prefix_and_rebuilds_replaced_endpoint():
    cache = _PublicationCache()
    records = FrozenPrefixList(_point(i) for i in range(40))
    saved = _checkpoint(records)
    cache.save(saved)
    first = cache.hierarchy(records, None)
    unchanged = cache.hierarchy(records, saved)
    assert unchanged is first
    newer = records.copy()
    newer[-1] = dict(newer[-1], value=1000.)
    newer.append(_point(40))
    next_saved = _checkpoint(newer)
    cache.save(next_saved)
    second = cache.hierarchy(newer, saved)
    assert second[0] is first[0]
    assert second[38] is first[38]
    assert second[39]['value'] == 1000.
    assert first[39]['value'] == 49.
    assert len(second) == 41 and len(first) == 40
    with pytest.raises(TypeError):
        second.append(_point(41))


def test_unresolved_working_endpoint_never_overwrites_saved_projection():
    cache = _PublicationCache()
    records = FrozenPrefixList(_point(i) for i in range(4))
    saved = _checkpoint(records)
    cache.save(saved)
    original = cache.hierarchy(records, None)
    pending = records.copy()
    pending[-1] = dict(pending[-1], value=100.)
    working = cache.hierarchy(pending, saved)
    assert working[-1]['value'] == 100.
    assert cache._hierarchy[-1]['value'] == original[-1]['value']
    restored = cache.hierarchy(records, saved)
    assert restored == original


def test_projection_is_discarded_when_new_checkpoint_has_no_shared_history():
    cache = _PublicationCache()
    records = FrozenPrefixList(_point(i) for i in range(4))
    cache.save(_checkpoint(records))
    first = cache.hierarchy(records, None)
    cache.clear()
    revised = FrozenPrefixList([dict(_point(0), value=500.)])
    cache.save(_checkpoint(revised))
    result = cache.hierarchy(revised, None)
    assert len(result) == 1 and result[0]['value'] == 500.
    assert first[0]['value'] == 10.
