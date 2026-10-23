"""证据准入合并遍历、整块封存及行情顺序消费的边界。"""

import builtins
from datetime import datetime, timedelta
import pickle

import pytest

from wavequant.domain.market_structure import owned_evidence as evidence
from wavequant.domain.market_structure.owned_evidence import CertifiedSource, OwnedEvidence
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars
from wavequant.domain.strategies.secondary_reclaim_entry import secondary_reclaim_history


def test_finite_scalar_leaves_need_no_graph_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    def container_identity(value: object) -> int:
        assert type(value) not in (str, int, float, bool, type(None))
        return builtins.id(value)

    owner = OwnedEvidence()
    monkeypatch.setattr(evidence, 'id', container_identity, raising=False)
    try:
        shared = [*range(6000), 0.5, None, True]
        assert owner.finite({'a': shared, 'b': shared})
        shared.append(float('nan'))
        assert not owner.finite(shared)
        shared.pop()
        shared.append(shared)
        assert not owner.finite(shared)
    finally:
        owner.close()


@pytest.mark.parametrize('key', [(1, 2), b'key', float('nan'), float('inf')])
def test_fused_key_validation_rejects_atomically(key: object) -> None:
    owner = OwnedEvidence()
    history = PreparedGraphHistory()
    try:
        old = {'shared': [1, 2, 3]}
        owner.publish(old)
        payload = history.capture(old)
        assert payload is not None
        used, stored, retained = owner.memory_used, history.stored_bytes, history.memory_used
        bad = {'prefix': old, 'bad': {key: 1}}
        assert not owner.finite(bad)
        owner.publish(bad)
        assert owner.reference(bad) is None and owner.memory_used == used
        assert history.capture(bad) is None
        assert (history.stored_bytes, history.memory_used) == (stored, retained)
        assert history.restore(payload) == old
    finally:
        owner.close()
        history.close()


def test_single_point_versions_only_replace_tree_at_full_block_boundaries() -> None:
    version = PointVersion().seal()
    snapshots: list[PointVersion] = []
    for index in range(160):
        following = PointVersion([{'index': index}], previous=version, start=len(version)).seal()
        if (index + 1) % 32:
            assert following._archive is version._archive
        assert len(following._tail) < 32
        assert following.changed_since(version) == index
        snapshots.append(following)
        version = following
    for index, snapshot in enumerate(snapshots):
        assert list(snapshot) == [{'index': i} for i in range(index + 1)]
        with pytest.raises(TypeError, match='immutable'):
            snapshot.append({'index': -1})


@pytest.mark.parametrize('start', [0, 1, 31, 32, 33, 63, 64, 65, 95, 96, 99])
def test_revised_block_tail_keeps_history_and_aliases(start: int) -> None:
    shared = {'proof': [1, 2, 3]}
    old = PointVersion({'index': i, 'shared': shared} for i in range(100)).seal()
    changed = PointVersion([{'index': -1, 'shared': shared}], previous=old, start=start).seal()
    expected = list(old)[:start] + [{'index': -1, 'shared': shared}]
    assert list(changed) == expected
    assert len(old) == 100 and old[-1]['index'] == 99
    history = PreparedGraphHistory()
    try:
        first = history.capture({'points': old})
        second = history.capture({'points': changed})
        assert first is not None and second is not None
        assert history.restore(first) == {'points': list(old)}
        restored = history.restore(second)
        assert restored == {'points': expected}
        assert isinstance(restored, dict)
        points = restored['points']
        assert all(point['shared'] is points[0]['shared'] for point in points)
        assert pickle.loads(pickle.dumps(changed)) == expected
    finally:
        history.close()


def test_certified_tail_revisions_preserve_keys_and_signature_budget() -> None:
    owner = OwnedEvidence()
    try:
        old = CertifiedSource.from_tail([{'index': i} for i in range(99)],
                                        [str(i).encode() for i in range(99)], owner, None, 0)
        for start in (0, 31, 32, 65, 97, 99):
            changed = CertifiedSource.from_tail([{'index': -1}], [b'changed'], owner, old, start)
            assert list(changed.keys) == list(old.keys)[:start] + [b'changed']
            assert len(changed) == len(changed.keys) == start + 1
            assert changed.signature_bytes == sum(evidence.getsizeof(key) for key in changed.keys)
        assert len(old) == len(old.keys) == 99
    finally:
        owner.close()


def test_empty_reclaim_history_consumes_bars_without_repeated_index_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = ValidatedBars([Bar(datetime(2025, 1, 1) + timedelta(days=i), 'TEST', 8, 10, 7, 9, 100)
                          for i in range(6000)])

    def no_index(self: ValidatedBars, index: object) -> object:
        raise AssertionError('a sequential scan must retain its preceding bar')

    monkeypatch.setattr(ValidatedBars, '__getitem__', no_index)
    assert secondary_reclaim_history(bars, {}) == ([], {})
