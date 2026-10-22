"""索引批量固化保留旧版本和别名，并限制每批构造的持久节点。"""

from dataclasses import dataclass

import pytest

from wavequant.domain.market_structure import trend_frozen_state as frozen
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixMap, _IndexNode


@dataclass(frozen=True)
class _CollisionKey:
    name: str
    hash_value: int = 7

    def __hash__(self) -> int:
        return self.hash_value


class _OpaqueValue:
    def __eq__(self, other: object) -> bool:
        raise AssertionError('index updates must not compare values')


def _node_counts(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    counts = {'leaves': 0, 'branches': 0}
    original_leaf, original_branch = frozen._leaf, frozen._branch

    def leaf(key_hash: int, entries: tuple[tuple[object, object], ...]) -> _IndexNode:
        counts['leaves'] += 1
        return original_leaf(key_hash, entries)

    def branch(bit: int, left: _IndexNode, right: _IndexNode) -> _IndexNode:
        counts['branches'] += 1
        return original_branch(bit, left, right)

    monkeypatch.setattr(frozen, '_leaf', leaf)
    monkeypatch.setattr(frozen, '_branch', branch)
    return counts


def test_dense_freeze_constructs_each_result_node_once(monkeypatch: pytest.MonkeyPatch) -> None:
    original = [_OpaqueValue() for _ in range(1024)]
    current = FrozenPrefixMap(enumerate(original))
    counts = _node_counts(monkeypatch)
    current.freeze()
    assert counts == {'leaves': 1024, 'branches': 1023}
    saved = current.copy()

    changed = [_OpaqueValue() for _ in original]
    counts.update(leaves=0, branches=0)
    for key, value in enumerate(changed):
        current[key] = value
    current.freeze()
    assert counts == {'leaves': 1024, 'branches': 1023}
    assert list(current) == list(range(1024))
    assert all(saved[key] is value for key, value in enumerate(original))
    assert all(current[key] is value for key, value in enumerate(changed))

    archive = current._archive
    counts.update(leaves=0, branches=0)
    for key, value in enumerate(changed):
        current[key] = value
    current.freeze()
    assert counts == {'leaves': 0, 'branches': 0}
    assert current._archive is archive


def test_single_identity_update_preserves_the_entire_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    current = FrozenPrefixMap((key, _OpaqueValue()) for key in range(128))
    current.freeze()
    archive = current._archive
    counts = _node_counts(monkeypatch)
    current[43] = current[43]
    current.freeze()
    assert current._archive is archive
    assert counts == {'leaves': 0, 'branches': 0}


def test_sparse_batch_shares_the_untouched_subtree_and_keeps_old_aliases() -> None:
    shared = _OpaqueValue()
    current = FrozenPrefixMap((key, shared) for key in range(256))
    current.freeze()
    saved = current.copy()
    original_root = saved._archive.root
    assert original_root is not None
    replacements = {0: _OpaqueValue(), 3: _OpaqueValue(), 17: _OpaqueValue()}
    current.update(replacements)
    current.freeze()
    revised_root = current._archive.root
    assert revised_root is not None
    assert revised_root.right is original_root.right
    assert all(saved[key] is shared for key in range(256))
    assert all(current[key] is replacements.get(key, shared) for key in range(256))
    assert current[128] is current[255] is shared


def test_collisions_keep_the_original_keys_and_distinguish_equal_value_objects() -> None:
    first, second, third = (_CollisionKey(name) for name in ('first', 'second', 'third'))
    initial = {'proof': (1, 2, 3)}
    current = FrozenPrefixMap([(first, initial), (second, initial)])
    current.freeze()
    saved = current.copy()
    replacement = dict(initial)
    current[_CollisionKey('first')] = replacement
    current[third] = initial
    current.freeze()
    assert current[first] is replacement and current[second] is current[third] is initial
    assert replacement == initial and replacement is not initial
    assert saved[first] is saved[second] is initial
    assert list(current)[0] is first
    root = current._archive.root
    assert root is not None and root.bit == -1
    assert [key for key, _ in root.entries] == [first, second, third]
    assert root.entries[0][0] is first
    assert list(saved) == [first, second]


@pytest.mark.parametrize('hashes', [
    (0, 1, 2, 3, 4, 5, 6, 7),
    (-1, -2, -3, 0, 1, 2, 3, 4),
    (0, 1 << 30, 1 << 60, (1 << 62) - 1, -(1 << 62), -(1 << 61), 7, 9),
    (7, 7, 7, 7, 7, 7, 7, 7),
])
def test_batch_additions_and_historical_revisions_match_mapping_order(hashes: tuple[int, ...]) -> None:
    keys = [_CollisionKey(str(index), value) for index, value in enumerate(hashes)]
    original = [_OpaqueValue() for _ in keys]
    current = FrozenPrefixMap(zip(keys[:4], original[:4]))
    current.freeze()
    saved = current.copy()
    expected = dict(zip(keys[:4], original[:4]))
    updates = [(keys[6], original[6]), (keys[1], _OpaqueValue()), (keys[4], original[4]),
               (keys[7], original[7]), (keys[0], _OpaqueValue()), (keys[5], original[5])]
    current.update(updates)
    expected.update(updates)
    current.freeze()
    assert list(current) == list(expected)
    assert all(current[key] is value for key, value in expected.items())
    assert all(saved[key] is value for key, value in zip(keys[:4], original[:4]))
    assert len(saved) == 4

    branch = saved.copy()
    branch[keys[2]] = original[7]
    branch[keys[7]] = original[2]
    branch.freeze()
    assert list(branch) == [*keys[:4], keys[7]]
    assert branch[keys[2]] is original[7] and branch[keys[7]] is original[2]
    assert all(current[key] is value for key, value in expected.items())


def test_delete_then_append_keeps_saved_version_and_insertion_order() -> None:
    values = [_OpaqueValue() for _ in range(16)]
    current = FrozenPrefixMap(enumerate(values))
    current.freeze()
    saved = current.copy()
    del current[3]
    current[3] = values[4]
    current[20] = values[0]
    current.freeze()
    assert list(current) == [0, 1, 2, *range(4, 16), 3, 20]
    assert current[3] is values[4] and current[20] is values[0]
    assert list(saved) == list(range(16))
    assert all(saved[key] is value for key, value in enumerate(values))
