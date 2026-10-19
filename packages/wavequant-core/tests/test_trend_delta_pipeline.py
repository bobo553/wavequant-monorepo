"""固化前缀退出重复工作；结果与完整因果入口逐版本比较。"""

from collections.abc import Iterator
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope, owned_signature
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList, FrozenPrefixMap
from wavequant.domain.market_structure.trend_judgement import _FormalKeyBreakState, judge_trend
from wavequant.domain.market_structure.trend_lines import _DefinitionClock, _OwnedSourceInputs, high_low_points_from_strokes
from wavequant.domain.market_structure.trend_source_stream import PromotionSource, SourceExtremes, SourceProjection
from wavequant.domain.market_structure.trend_cascade import _enrich_promotions
from wavequant.domain.market_structure.trend_promotion_scope import PROMOTION_FIELD


def _points(count: int, level: int = 1) -> list[dict[str, object]]:
    return [dict(index=position, ordinal=0, time=str(position), available_at=position + 1,
        value=20 + position + (10 if position % 2 else 0), kind='H' if position % 2 else 'L',
        state='confirmed', trend_level=level, audit={'position': position}) for position in range(count)]


def _keys(points: list[dict[str, object]]) -> tuple[bytes, ...]:
    keys = tuple(owned_signature(point) for point in points)
    assert all(key is not None for key in keys)
    return cast(tuple[bytes, ...], keys)


def test_old_checkpoint_forks_do_not_enumerate_the_frozen_index(monkeypatch: pytest.MonkeyPatch) -> None:
    base = FrozenPrefixMap((position, position) for position in range(4096))
    base.freeze()
    original = base.copy()

    def forbidden(_: FrozenPrefixMap[int, int]) -> Iterator[int]:
        raise AssertionError('tail revisions must not enumerate the frozen index')

    monkeypatch.setattr(FrozenPrefixMap, '__iter__', forbidden)
    for revision in range(100):
        fork = original.copy()
        fork[4095] = -revision
        fork[4096] = revision
        fork.freeze()
        assert original[4095] == 4095 and len(original) == 4096
        assert fork[4095] == -revision and fork[4096] == revision
        assert fork[0] == original[0]


def test_tail_revision_and_truncation_reuse_unchanged_sequence_blocks() -> None:
    base = FrozenPrefixList(range(4096))
    base.freeze(len(base))
    assert base._archive is not None
    for revision in range(100):
        fork = base.copy()
        del fork[4090:]
        fork.extend((-revision, revision))
        fork.freeze(len(fork))
        assert fork._archive is not None
        assert fork._archive.left is base._archive.left
        assert fork[-2:] == [-revision, revision]
        assert base[-1] == 4095 and len(base) == 4096


def test_hash_collisions_and_branch_insertion_order_remain_independent() -> None:
    class Key:
        def __init__(self, value: int) -> None:
            self.value = value

        def __hash__(self) -> int:
            return 7

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Key) and self.value == other.value

    keys = [Key(index) for index in range(20)]
    base = FrozenPrefixMap((key, index) for index, key in enumerate(keys))
    base.freeze()
    fork = base.copy()
    fork[Key(4)] = 40
    fork[Key(20)] = 20
    fork.freeze()
    assert list(base.values()) == list(range(20))
    assert [key.value for key in fork] == list(range(21))
    assert fork[Key(4)] == 40 and base[Key(4)] == 4


def test_owned_source_adapter_does_not_sign_unchanged_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope() as owner:
        points = _points(2048, 0)
        first = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        before = adapter.prepare(first, ())['path-0']
        appended = _points(2049, 0)[-1]
        following = CertifiedSource([*first, appended], (*first.keys, *_keys([appended])),
            owner, first, _changed_from=len(first))
        calls = []
        signature = trend_lines._geometry_signature

        def record(value: object) -> bytes | None:
            calls.append(value)
            return signature(value)

        monkeypatch.setattr(trend_lines, '_geometry_signature', record)
        after = adapter.prepare(following, ())['path-0']
        assert len(calls) == 1
        assert len(after) == 2049 and len(before) == 2048
        assert after[0] is before[0]
        assert type(after) is CertifiedSource and after.changed_from == 2048


def test_incremental_definition_clock_matches_full_sort_for_late_coordinates_and_retraction() -> None:
    clock = _DefinitionClock()
    source: list[dict[str, object]] = []
    with owned_evidence_scope() as owner:
        previous = None
        points = [dict(index=1, time='2020-01-02', available_at='2020-01-10', state='reversal')]
        for index in range(1, 9):
            source = [*source, dict(index=index, time=f'2020-01-{index + 1:02}',
                                   available_at='2020-01-10', state='confirmed')]
            current = CertifiedSource(source, _keys(source), owner, previous,
                _changed_from=len(source) - 1 if previous is not None else 0)
            clock.update(current, points, len(points) if previous is not None else 0)
            assert dict(clock) == trend_lines._definition_dates([*source, *points])
            previous = current
        revised = [*source[:2], dict(source[2], time='2020-01-20', available_at='2020-01-21')]
        current = CertifiedSource(revised, _keys(revised), owner, previous, _changed_from=2)
        clock.update(current, points, len(points))
        assert dict(clock) == trend_lines._definition_dates([*revised, *points])


def test_formal_cache_accepts_the_owned_source_certificate() -> None:
    with owned_evidence_scope() as owner:
        points = _points(12, 1)
        source = CertifiedSource(points, _keys(points), owner, None)
        ordinary = judge_trend(points, trend_level=2)
        state = _FormalKeyBreakState(2)
        assert state.calculate(source, ordinary, _certified_keys=source.keys) == ordinary


def test_recursive_projection_preserves_old_versions_and_matches_full_projection() -> None:
    with owned_evidence_scope():
        projection = SourceProjection()
        previous: list[dict[str, object]] | None = None
        saved = []
        for count in range(3, 40):
            points = _points(count)
            if previous is not None:
                points[:len(previous)] = previous
            stroke = dict(id='level1-path', points=points)
            hint = points, previous, len(previous) if previous is not None else 0
            actual = projection.project([stroke], {id(points): hint})
            expected = high_low_points_from_strokes([stroke], include_key_break_attacks=True)
            assert actual is not None and list(actual) == expected
            saved.append((actual, deepcopy(expected)))
            previous = points
        for actual, expected in saved:
            assert list(actual) == expected


@pytest.mark.parametrize('level', (1, 2, 3))
def test_long_developing_tail_and_early_revision_match_full_extreme_selection(level: int) -> None:
    with owned_evidence_scope() as owner:
        index = SourceExtremes()
        previous = None
        for count in range(2, 80):
            points = _points(count, level - 1)
            current = CertifiedSource(points, _keys(points), owner, previous,
                _changed_from=len(previous) if previous is not None else 0)
            actual = trend_lines._structural_tail(current, points[:1], path='p', trend_level=level, _index=index)
            expected = trend_lines._structural_tail(points, points[:1], path='p', trend_level=level)
            assert actual == expected
            previous = current
        assert previous is not None
        revised = [*previous[:3], dict(previous[3], value=999), *previous[4:40]]
        current = CertifiedSource(revised, _keys(revised), owner, previous, _changed_from=3)
        assert trend_lines._structural_tail(current, revised[:1], path='p', trend_level=level, _index=index) == (
            trend_lines._structural_tail(revised, revised[:1], path='p', trend_level=level))


def test_promotion_addition_retraction_and_unchanged_versions_keep_original_semantics() -> None:
    with owned_evidence_scope() as owner:
        points = _points(12, 0)
        source = CertifiedSource(points, _keys(points), owner, None)
        first = dict(points[5], upgrade_evidence={'origin': points[0]})
        second = dict(points[9], upgrade_evidence={'origin': points[4]})
        adapter = PromotionSource()
        saved = []
        for records in ({}, {b'a': first}, {b'a': first, b'b': second}, {b'b': second}, {}):
            allowed = {'path-0': records}
            result = adapter.enrich(source, allowed, PROMOTION_FIELD)
            assert result is not None
            expected = _enrich_promotions(source, allowed)
            assert list(result) == expected
            assert adapter.enrich(source, allowed, PROMOTION_FIELD) is result
            saved.append((result, deepcopy(expected)))
        for result, expected in saved:
            assert list(result) == expected


def test_owned_alias_index_and_leg_blocks_process_only_changed_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope() as owner, trend_lines._ClosedPreparedPaths(_owned_history=True) as paths:
        points = _points(160)
        source_points = list(points)
        legs = trend_lines._structural_legs(points)

        def row(values: list[dict[str, object]], source: list[dict[str, object]],
                edges: list[dict[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {field: [] for field in paths._fields}
            result['source_strokes'] = [dict(id='source', points=source)]
            result['strokes'] = [dict(id='trend', points=values, structural_legs=edges)]
            return result

        for values in (points, source_points, legs):
            paths.trust_points(values, None, 0)
        first = paths.encode(row(points, source_points, legs))
        appended = _points(161)[-1]
        following = [*points, appended]
        following_source = list(following)
        following_legs = [*legs, *trend_lines._structural_legs(following[-2:])]
        paths.trust_points(following, points, len(points))
        paths.trust_points(following_source, source_points, len(source_points))
        paths.trust_points(following_legs, legs, len(legs))
        roots = {id(point) for point in points}
        publish = owner.publish

        def publish_tail(value: object) -> None:
            assert id(value) not in roots, 'unchanged alias roots must not be revisited'
            publish(value)

        monkeypatch.setattr(owner, 'publish', publish_tail)
        second = paths.encode(row(following, following_source, following_legs))
        assert paths.decode(first) == row(points, source_points, legs)
        assert paths.decode(second) == row(following, following_source, following_legs)
        restored = cast(list[dict[str, object]], paths.decode(second)['strokes'])[0]
        assert type(restored['points']) is list and type(restored['structural_legs']) is list
