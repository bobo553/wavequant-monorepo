"""来源生产时传播原点引用，跨日新增点保留与旧前缀的真实别名。"""

from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope, owned_signature
from wavequant.domain.market_structure.owned_evidence_producer import (
    PointRecord, current_producer_records, producer_record_scope,
)
from wavequant.domain.market_structure.owned_evidence_records import RecordRef
from wavequant.domain.market_structure.trend_lines import high_low_points_from_strokes
from wavequant.domain.market_structure.trend_point_records import PointRecordView
from wavequant.domain.market_structure.trend_point_version import PointVersion, record_point_version
from wavequant.domain.market_structure.trend_promotion_scope import PROMOTION_FIELD
from wavequant.domain.market_structure.trend_source_stream import PromotionSource, SourceProjection


def _points(count: int, level: int) -> list[dict[str, object]]:
    proof = {'history': [{'position': index} for index in range(100)]}
    return [dict(index=index, ordinal=0, time=str(index), available_at=index + 1,
                 value=20 + index, kind='H' if index % 2 else 'L', state='confirmed',
                 trend_level=level, audit=proof) for index in range(count)]


def _keys(points: list[dict[str, object]]) -> tuple[bytes, ...]:
    keys = tuple(owned_signature(point) for point in points)
    assert all(key is not None for key in keys)
    return cast(tuple[bytes, ...], keys)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_projection_binds_actual_input_tail_before_copying_shared_proof(level: int) -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        first = PointVersion(_points(64, level)).seal()
        with producer_record_scope(store) as scope:
            record_point_version(first, scope)
        adapter = SourceProjection()
        old = adapter.project([dict(id='path', points=first)], {})
        assert old is not None and old.evidence_sequence is not None
        assert current_producer_records() is None
        point = dict(first[-1], index=64, kind='L', time='64', available_at=65)
        following = PointVersion([point], previous=first, start=len(first)).seal()
        # 原点生产者已有明确的来源引用；投影生产者随后必须继续传递此引用。
        with producer_record_scope(store, inputs=(first.point_records[0],)) as scope:
            record_point_version(following, scope)
        visits: list[tuple[object, int]] = []
        point_at = PointRecordView._point

        def read(view: PointRecordView, position: int, reference: RecordRef) -> PointRecord:
            visits.append((view._version, position))
            return point_at(view, position, reference)

        with patch.object(PointRecordView, '_point', read), patch.object(
            store, 'write_list', side_effect=AssertionError('old proof list was encoded again'),
        ):
            current = adapter.project([dict(id='path', points=following)], {})
        assert current is not None and current.evidence_sequence is not None
        assert current_producer_records() is None
        assert [position for version, position in visits if version is following] == [63, 64]
        assert all(position >= 63 for _, position in visits)
        actual = cast(list[dict[str, object]], store.materialize(current.evidence_sequence.reference))
        assert actual[0]['audit'] is actual[-1]['audit']
        assert actual == high_low_points_from_strokes(
            [dict(id='path', points=following)], include_key_break_attacks=True)
        assert len(cast(list[object], store.materialize(old.evidence_sequence.reference))) == 64


def test_promotion_source_append_keeps_shared_proof_without_binding_old_prefix() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        points = _points(40, 0)
        first = CertifiedSource(points, _keys(points), owner, None)
        with producer_record_scope(store) as scope:
            record_point_version(first, scope)
        adapter = PromotionSource()
        old = adapter.enrich(first, {}, PROMOTION_FIELD)
        assert old is not None and old.evidence_sequence is not None
        point = dict(first[-1], index=40, kind='L', time='40', available_at=41)
        following = CertifiedSource.from_tail([point], _keys([point]), owner, first, len(first))
        with producer_record_scope(store, inputs=(first.point_records[0],)) as scope:
            record_point_version(following, scope)
        visits: list[int] = []
        point_at = PointRecordView._point

        def read(view: PointRecordView, position: int, reference: RecordRef) -> PointRecord:
            if view._version is following:
                visits.append(position)
            return point_at(view, position, reference)

        with patch.object(PointRecordView, '_point', read), patch.object(
            store, 'write_list', side_effect=AssertionError('old proof list was encoded again'),
        ):
            current = adapter.enrich(following, {}, PROMOTION_FIELD)
        assert visits == [40]
        assert current is not None and current.evidence_sequence is not None
        actual = cast(list[dict[str, object]], store.materialize(current.evidence_sequence.reference))
        assert actual == list(current) and actual[0]['audit'] is actual[-1]['audit']
        assert current_producer_records() is None


def test_promotion_addition_uses_actual_release_root_and_keeps_cross_record_aliases() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        points = _points(5, 0)
        source = CertifiedSource(points, _keys(points), owner, None)
        with producer_record_scope(store) as scope:
            record_point_version(source, scope)
        adapter = PromotionSource()
        old = adapter.enrich(source, {}, PROMOTION_FIELD)
        assert old is not None
        release = dict(source[2], upgrade_evidence={'origin': source[0]},
                       _promotion_target_level=1, _promotion_target_path='path-0')
        with producer_record_scope(store, inputs=(source.point_records[0], source.point_records[2])) as scope:
            record = scope.record_point(release)
        references = {id(release): (record.reference.namespace, record.reference.offset)}
        with patch.object(store, 'materialize', side_effect=AssertionError('producer decoded historical values')):
            current = adapter.enrich(source, {'path-0': {b'release': release}}, PROMOTION_FIELD,
                                     _record_references=references)
        assert current is not None and current.evidence_sequence is not None
        values = cast(list[dict[str, object]], store.materialize(current.evidence_sequence.reference))
        promotion = cast(list[dict[str, object]], values[2][PROMOTION_FIELD])[0]
        assert promotion == release
        assert promotion['_promotion_target_path'] == 'path-0'
        assert promotion['audit'] is values[0]['audit'] is values[-1]['audit']
        assert cast(dict[str, object], promotion['upgrade_evidence'])['origin'] is values[0]
        assert PROMOTION_FIELD not in old[2]


@pytest.mark.parametrize('versioned', (False, True))
def test_recorded_projection_rejects_missing_input_carriers_without_reencoding(versioned: bool) -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        points = _points(3, 1)
        source = PointVersion(points).seal() if versioned else points
        store = owner.record_store()
        before = store.record_count
        with pytest.raises(ValueError, match='complete same-owner'):
            SourceProjection().project([dict(id='path', points=source)], {})
        assert store.record_count == before and current_producer_records() is None


def test_promotion_source_rejects_unknown_release_carrier_and_keeps_previous_root() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        points = _points(3, 0)
        source = CertifiedSource(points, _keys(points), owner, None)
        with producer_record_scope(store) as scope:
            record_point_version(source, scope)
        adapter = PromotionSource()
        old = adapter.enrich(source, {}, PROMOTION_FIELD)
        assert old is not None and old.evidence_sequence is not None
        release = dict(source[2], upgrade_evidence={'origin': source[0]})
        before = store.record_count
        with pytest.raises(ValueError, match='actual recorded dictionary root'):
            adapter.enrich(source, {'path-0': {b'release': release}}, PROMOTION_FIELD)
        assert store.record_count == before
        assert cast(list[object], store.materialize(old.evidence_sequence.reference)) == list(source)
        assert current_producer_records() is None


def test_disabled_record_history_keeps_ordinary_projection_inputs_supported() -> None:
    with owned_evidence_scope():
        points = _points(3, 1)
        result = SourceProjection().project([dict(id='path', points=points)], {})
        assert result is not None and result.evidence_sequence is None
