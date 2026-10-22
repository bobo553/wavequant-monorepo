"""折线出生点先封存，来源适配只传播实际变化点的既有记录。"""

from datetime import datetime, timedelta
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope, owned_signature
from wavequant.domain.market_structure.owned_evidence_producer import PointRecord, current_producer_records, producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import RecordRef
from wavequant.domain.market_structure.trend_body_reversal import with_price_bodies
from wavequant.domain.market_structure.trend_lines import _OwnedSourceInputs, polyline_high_low_points
from wavequant.domain.market_structure.trend_point_records import PointRecordView
from wavequant.domain.market_structure.trend_point_version import record_point_version
from wavequant.domain.market_structure.trend_promotion_scope import PROMOTION_FIELD
from wavequant.domain.market_structure.trend_source_stream import PromotionSource
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame, _OwnedDrawingProjection
from wavequant.domain.strategies.chart_entry_history import _closed_drawing_path


def _raw(count: int) -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, time=str(index), value=10. if index % 2 else 20.,
                 kind='L' if index % 2 else 'H', state='confirmed', available_at=index)
            for index in range(count)]


def _frame(raw: list[dict[str, object]], *, epoch: int = 0,
           closed: list[dict[str, object]] | None = None) -> ChartDrawingFrame:
    strokes = [*(closed or []), *([dict(id=f'lecture-{epoch}', points=raw)] if len(raw) > 1 else [])]
    return ChartDrawingFrame(cast(int, raw[-1]['index']) if raw else epoch, epoch, raw, dict(strokes=strokes))


def _keys(points: list[dict[str, object]]) -> tuple[bytes, ...]:
    keys = tuple(owned_signature(point) for point in points)
    assert all(key is not None for key in keys)
    return cast(tuple[bytes, ...], keys)


def test_first_empty_drawing_source_is_recorded_before_promotion_join() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        projection = _OwnedDrawingProjection()
        first = projection.project(_frame(_raw(1)))
        assert type(first) is CertifiedSource and first.evidence_sequence is not None
        assert owner.record_store().materialize(first.evidence_sequence.reference) == []
        enriched = PromotionSource().enrich(first, {}, PROMOTION_FIELD)
        assert enriched is not None and enriched.evidence_sequence is not None
        assert current_producer_records() is None


def test_drawing_append_records_tail_and_keeps_old_proof_alias_and_root() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        proof = {'observations': [{'event': index} for index in range(40)]}
        raw = [dict(point, audit=proof) for point in _raw(64)]
        adapter = _OwnedDrawingProjection()
        old = adapter.project(_frame(raw))
        assert type(old) is CertifiedSource and old.evidence_sequence is not None
        old_values = store.materialize(old.evidence_sequence.reference)
        raw.append(dict(_raw(65)[-1], audit=proof))
        visits: list[int] = []
        point_at = PointRecordView._point

        def read(view: PointRecordView, position: int, reference: RecordRef) -> PointRecord:
            if view._version is old:
                visits.append(position)
            return point_at(view, position, reference)

        with patch.object(PointRecordView, '_point', read), patch.object(
            store, 'write_list', side_effect=AssertionError('drawing copied a historical proof'),
        ):
            current = adapter.project(_frame(raw))
        assert type(current) is CertifiedSource and current.evidence_sequence is not None
        assert visits and min(visits) >= current.changed_from - 1 > 0
        values = cast(list[dict[str, object]], store.materialize(current.evidence_sequence.reference))
        assert values == polyline_high_low_points(_frame(raw).drawing)
        assert values[0]['audit'] is values[-1]['audit']
        assert store.materialize(old.evidence_sequence.reference) == old_values
        assert current_producer_records() is None


def test_drawing_closed_path_and_restart_keep_original_projection() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        projection = _OwnedDrawingProjection()
        raw = _raw(8)
        old = projection.project(_frame(raw))
        assert type(old) is CertifiedSource and old.evidence_sequence is not None
        closed = [_closed_drawing_path(0, raw, 8)]
        following = [dict(point, index=cast(int, point['index']) + 8,
                          available_at=cast(int, point['available_at']) + 8) for point in _raw(5)]
        frame = _frame(following, epoch=8, closed=closed)
        current = projection.project(frame)
        assert type(current) is CertifiedSource and current.evidence_sequence is not None
        assert owner.record_store().materialize(current.evidence_sequence.reference) == polyline_high_low_points(frame.drawing)
        assert all('source_path_end_index' not in point for point in old)


def test_source_input_append_binds_only_new_point_before_body_copy() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        store = owner.record_store()
        proof = {'history': [{'event': index} for index in range(40)]}
        points = [dict(point, source_path='path', audit=proof) for point in _raw(40)]
        source = CertifiedSource(points, _keys(points), owner, None)
        with producer_record_scope(store) as scope:
            record_point_version(source, scope)
        bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'RECORD', 10., 20., 5., 15., 1000)
                     for index in range(41))
        adapter = _OwnedSourceInputs()
        old = adapter.prepare(source, bars)['path']
        point = dict(_raw(41)[-1], source_path='path', audit=proof)
        following = CertifiedSource.from_tail([point], _keys([point]), owner, source, len(source))
        with producer_record_scope(store, inputs=(source.point_records[0],)) as scope:
            record_point_version(following, scope)
        visits: list[int] = []
        point_at = PointRecordView._point

        def read(view: PointRecordView, position: int, reference: RecordRef) -> PointRecord:
            if view._version is following:
                visits.append(position)
            return point_at(view, position, reference)

        with patch.object(PointRecordView, '_point', read), patch.object(
            store, 'write_list', side_effect=AssertionError('body adaptation copied a historical proof'),
        ):
            current = adapter.prepare(following, bars)['path']
        assert visits == [40]
        assert type(current) is CertifiedSource and current.evidence_sequence is not None
        values = cast(list[dict[str, object]], store.materialize(current.evidence_sequence.reference))
        assert values == with_price_bodies(following, bars)
        assert values[0]['audit'] is values[-1]['audit']
        assert current[0] is old[0]
        assert current_producer_records() is None


def test_source_inputs_reject_unrecorded_internal_source_without_full_capture() -> None:
    with owned_evidence_scope() as owner:
        owner.record_history_enabled = True
        points = _raw(5)
        source = CertifiedSource(points, _keys(points), owner, None)
        before = owner.record_store().record_count
        with pytest.raises(ValueError, match='complete same-owner'):
            _OwnedSourceInputs().prepare(source, ())
        assert owner.record_store().record_count == before
        assert current_producer_records() is None
