"""来源状态跨生产作用域复用常量引用，深复制保持独立的证据域。"""

from copy import deepcopy
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence import (
    CertifiedSource, owned_evidence_scope, owned_signature,
)
from wavequant.domain.market_structure.owned_evidence_producer import PointRecord, producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords, RecordRef
from wavequant.domain.market_structure.trend_judgement import _SourceVersionOwner, _visible_source
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
from wavequant.domain.market_structure.trend_levels import _SourceTurn, _candidate_from_source, _source_turns
from wavequant.domain.market_structure.trend_point_records import PointRecordView
from wavequant.domain.market_structure.trend_point_version import record_point_version


def _source(
    level: int, prices: tuple[float, ...] = (4.20, 8.23, 4.11, 7.40, 3.83, 6.49, 3.35, 9.20),
) -> list[dict[str, object]]:
    proof = {'observations': list(range(100))}
    return [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=price,
                 time=str(index), available_at=index + 1, trend_level=level - 1, state='confirmed',
                 audit=proof, confirmed_by={'index': index})
            for index, price in enumerate(prices)]


def _reference(turn: _SourceTurn) -> RecordRef:
    assert turn.source_record is not None
    return RecordRef(*turn.source_record)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_old_source_turn_rebinds_only_field_roots_after_its_scope_closes(level: int) -> None:
    source = _source(level)
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        with producer_record_scope(store) as first:
            turn = _source_turns(source[:1], level - 1)[0]
            record = first.lookup(turn.source)
            assert record is not None and _reference(turn) == record.reference
        with producer_record_scope(store) as second:
            # 新 candidate 没有新 list；旧 audit 列表必须止于已知字段根。
            with patch.object(store, 'write_list', side_effect=AssertionError('historical proof was re-encoded')):
                candidate = _candidate_from_source(turn)
                assert second.lookup(candidate) is None
                captured = second.ensure_record(candidate)
            restored = second.lookup(turn.source)
            assert restored is not None and captured is not None
            assert captured.reference != restored.reference
            assert captured.field_reference('audit') == restored.field_reference('audit')
            count = store.record_count
            assert second.ensure_record(candidate) is captured
            assert store.record_count == count
            root = store.write_list([restored.reference, captured.reference])
        values = cast(list[dict[str, object]], store.materialize(root))
        assert values[0]['audit'] is values[1]['audit']
        assert candidate['audit'] is turn.source['audit']
        assert 'source_record' not in candidate


def test_true_deepcopy_drops_the_old_root_and_preserves_an_independent_alias_domain() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        with producer_record_scope(store):
            turn = _source_turns(_source(1)[:1], 0)[0]
        copied = deepcopy(turn)
        assert copied == turn and copied.source is not turn.source
        assert copied.source['audit'] is not turn.source['audit']
        assert copied.source_record is None
        with producer_record_scope(store) as scope:
            original_candidate = _candidate_from_source(turn)
            copied_candidate = _candidate_from_source(copied)
            assert scope.lookup(original_candidate) is None and scope.lookup(copied_candidate) is None
            original_record = scope.ensure_record(original_candidate)
            copied_record = scope.ensure_record(copied_candidate)
            assert original_record is not None and copied_record is not None
            root = store.write_list([_reference(turn), original_record.reference, copied_record.reference])
        values = cast(list[dict[str, object]], store.materialize(root))
        assert values[0]['audit'] is values[1]['audit']
        assert values[1]['audit'] == values[2]['audit']
        assert values[1]['audit'] is not values[2]['audit']


def test_visible_source_has_its_own_dictionary_root_while_sharing_readonly_fields() -> None:
    source = _source(1)[:1]
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        original = scope.record_point(source[0])
        turns: list[_SourceTurn] = []
        visible, positions = _visible_source(source, 0, None, _validated_turns=turns, _owned_source=True)
        assert positions == (0,) and turns[0].source is visible[0]
        assert turns[0].source is not source[0]
        assert _reference(turns[0]) != original.reference
        root = store.write_list([original.reference, _reference(turns[0])])
        values = cast(list[dict[str, object]], store.materialize(root))
        assert values[0] is not values[1]
        assert values[0]['audit'] is values[1]['audit']


@pytest.mark.parametrize('level', (1, 2, 3))
def test_owned_upgrade_state_records_the_published_shell_instead_of_its_source_root(level: int) -> None:
    source = _source(level)
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        with producer_record_scope(store):
            turns = _source_turns(source, level - 1)
            state = _TrendUpgradeState(turns, [], trend_level=level)
            state.advance()
            assert state.merged
            for point in state.merged.values():
                assert point.source_record is not None
                assert store.materialize(_reference(point)) == point.source
                original = state.source_by_order[(point.index, point.ordinal)]
                assert _reference(point) != _reference(original)
        with producer_record_scope(store) as scope:
            # 旧 merged 状态关闭原 scope 后依旧能成为下次候选来源。
            for point in state.merged.values():
                candidate = _candidate_from_source(point)
                assert scope.lookup(candidate) is None
                record = scope.ensure_record(candidate)
                assert record is not None and store.materialize(record.reference) == candidate


def test_source_version_binds_only_the_reparsed_suffix_and_its_boundary_point() -> None:
    source = _source(1, tuple(float(10 + index) for index in range(40)))
    with owned_evidence_scope() as evidence:
        store = evidence.record_store()
        keys = tuple(owned_signature(point) for point in source)
        assert all(key is not None for key in keys)
        first = CertifiedSource(source, cast(tuple[bytes, ...], keys), evidence, None)
        with producer_record_scope(store) as scope:
            record_point_version(first, scope)
        parser = _SourceVersionOwner(1, 8 * 1024 * 1024)
        with producer_record_scope(store):
            previous = parser.prepare(first)
        assert previous is not None
        added = dict(source[-1], index=40, kind='L', time='40', available_at=41)
        added_key = owned_signature(added)
        assert added_key is not None
        second = CertifiedSource.from_tail([added], [added_key], evidence, first, len(first))
        with producer_record_scope(store, inputs=(first.point_records[-1],)) as scope:
            record_point_version(second, scope)
        visited: list[int] = []
        point_at = PointRecordView._point

        def read_point(view: PointRecordView, position: int, reference: RecordRef) -> PointRecord:
            visited.append(position)
            return point_at(view, position, reference)

        with producer_record_scope(store), patch.object(PointRecordView, '_point', read_point):
            current = parser.prepare(second)
        assert current is not None
        assert visited == [39, 40]
        assert current.turns[0] is previous.turns[0]
        assert current.turns[-1].source_record is not None


def test_source_turn_cannot_use_a_record_from_another_request_store() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as first, OwnedEvidenceRecords(cache_bytes=0) as other:
        with producer_record_scope(first):
            turn = _source_turns(_source(1)[:1], 0)[0]
        before = other.record_count
        with producer_record_scope(other), pytest.raises(ValueError, match='original request owner'):
            _candidate_from_source(turn)
        assert other.record_count == before


def test_cached_source_version_rejects_a_different_record_scope_owner() -> None:
    source = _source(1)[:1]
    with owned_evidence_scope() as evidence, OwnedEvidenceRecords(cache_bytes=0) as other:
        keys = tuple(owned_signature(point) for point in source)
        assert all(key is not None for key in keys)
        certified = CertifiedSource(source, cast(tuple[bytes, ...], keys), evidence, None)
        parser = _SourceVersionOwner(1, 8 * 1024 * 1024)
        with producer_record_scope(evidence.record_store()) as scope:
            record_point_version(certified, scope)
            assert parser.prepare(certified) is not None
        with producer_record_scope(other), pytest.raises(ValueError, match='active request owner'):
            parser.prepare(certified)


def test_no_active_scope_keeps_the_existing_public_source_path() -> None:
    turn = _source_turns(_source(1)[:1], 0)[0]
    assert turn.source_record is None
    assert _candidate_from_source(turn)['audit'] is turn.source['audit']
