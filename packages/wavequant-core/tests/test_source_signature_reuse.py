"""Exhausted evidence pools do not invalidate existing source certificates."""

from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_lines, trend_source_stream
from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope, owned_signature
from wavequant.domain.market_structure.trend_body_reversal import with_price_bodies
from wavequant.domain.market_structure.trend_lines import _OwnedSourceInputs, high_low_points_from_strokes
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.trend_source_stream import SourceProjection
from wavequant.domain.models.model import Bar


def _point(index: int, level: int = 1) -> dict[str, object]:
    shared = list(range(128))
    return dict(index=index, ordinal=0, time=str(index), available_at=index + 1,
                value=20 + index + (10 if index % 2 else 0), kind='H' if index % 2 else 'L',
                state='confirmed', trend_level=level, audit=dict(left=shared, right=shared))


def _keys(points: list[dict[str, object]]) -> tuple[bytes, ...]:
    result = tuple(owned_signature(point) for point in points)
    assert all(key is not None for key in result)
    return cast(tuple[bytes, ...], result)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_projection_reuses_boundary_certificate_when_pool_is_full(
    monkeypatch: pytest.MonkeyPatch, level: int,
) -> None:
    with owned_evidence_scope(memory_bytes=0):
        projection = SourceProjection()
        first = PointVersion(_point(index, level) for index in range(8)).seal()
        before = projection.project([dict(id='path', points=first)], {})
        assert before is not None
        snapshot = deepcopy(list(before))
        following = PointVersion([_point(8, level)], previous=first, start=len(first)).seal()
        calls: list[object] = []
        signature = trend_source_stream.owned_signature

        def measured(value: object) -> bytes | None:
            calls.append(value)
            return signature(value)

        monkeypatch.setattr(trend_source_stream, 'owned_signature', measured)
        stroke = dict(id='path', points=following)
        after = projection.project([stroke], {})
        assert after is not None
        assert len(calls) == 1
        assert after.changed_from == len(before)
        assert list(after) == high_low_points_from_strokes([stroke], include_key_break_attacks=True)
        assert after[7] is before[7] and after.keys[7] is before.keys[7]
        assert list(before) == snapshot


@pytest.mark.parametrize('change', ('type', 'aliases', 'clock'))
def test_projection_revision_replaces_changed_certificate(change: str) -> None:
    with owned_evidence_scope(memory_bytes=0):
        projection = SourceProjection()
        first = PointVersion(_point(index) for index in range(6)).seal()
        before = projection.project([dict(id='path', points=first)], {})
        assert before is not None
        changed = dict(first[3])
        if change == 'type':
            changed['value'] = float(cast(int, changed['value']))
        elif change == 'aliases':
            changed['audit'] = dict(left=list(range(128)), right=list(range(128)))
        else:
            changed['available_at'] = 12
        revised = PointVersion([changed, *first[4:]], previous=first, start=3).seal()
        stroke = dict(id='path', points=revised)
        after = projection.project([stroke], {})
        assert after is not None
        assert after.keys[3] != before.keys[3]
        assert after[3] is not before[3]
        assert after[4] is before[4]
        assert list(after) == high_low_points_from_strokes([stroke], include_key_break_attacks=True)


def _bars() -> tuple[Bar, ...]:
    return tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'SOURCE',
                     20., 24., 18., 22., 100.) for index in range(6))


def test_existing_transformation_reuses_validation_and_signature_with_empty_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        points = [_point(index, 0) for index in range(6)]
        source = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        bars = _bars()
        before = adapter.prepare(source, bars)['path-0']
        repeated = CertifiedSource(points, tuple(source.keys), owner, None)

        def forbidden(_: object) -> bytes:
            raise AssertionError('an unchanged certified transformation must not traverse its proof again')

        monkeypatch.setattr(owner, 'finite', forbidden)
        monkeypatch.setattr(trend_lines, '_geometry_signature', forbidden)
        after = adapter.prepare(repeated, bars)['path-0']
        assert after is before
        assert list(after) == with_price_bodies(points, bars)


def test_market_revision_resigns_only_the_changed_body(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        points = [_point(index, 0) for index in range(6)]
        source = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        bars = _bars()
        before = adapter.prepare(source, bars)['path-0']
        replacement = Bar(bars[3].timestamp, 'SOURCE', 20., 24., 18., 23., 100.)
        revised_bars = (*bars[:3], replacement, *bars[4:])
        calls: list[object] = []
        signature = trend_lines._geometry_signature

        def measured(value: object) -> bytes | None:
            calls.append(value)
            return signature(value)

        monkeypatch.setattr(trend_lines, '_geometry_signature', measured)
        after = adapter.prepare(source, revised_bars)['path-0']
        assert len(calls) == 1
        assert type(before) is CertifiedSource and type(after) is CertifiedSource
        assert after.keys[3] != before.keys[3]
        assert after[2] is before[2]
        assert list(after) == with_price_bodies(points, revised_bars)
        assert list(before) == with_price_bodies(points, bars)


def test_first_certified_version_still_validates_finite_evidence() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        points = [dict(_point(0, 0), audit=float('inf'))]
        source = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        result = adapter.prepare(source, ())['path-0']
        assert type(result) is list
        assert not adapter._entries and not adapter._groups


def test_reused_certificate_cannot_skip_validation_of_a_replaced_input() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        points = [_point(0, 0)]
        source = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        assert type(adapter.prepare(source, ())['path-0']) is CertifiedSource
        changed = dict(points[0], audit=float('inf'))
        forged = CertifiedSource([changed], tuple(source.keys), owner, None)
        result = adapter.prepare(forged, ())['path-0']
        assert type(result) is list and result[0]['audit'] == float('inf')
        assert not adapter._entries and not adapter._groups


@pytest.mark.parametrize('change', ('aliases', 'order', 'type', 'clock'))
def test_reused_certificate_cannot_replace_a_different_finite_input(change: str) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        point = _point(0, 0)
        source = CertifiedSource([point], _keys([point]), owner, None)
        adapter = _OwnedSourceInputs()
        bars = _bars()
        before = adapter.prepare(source, bars)['path-0']
        changed = dict(point)
        if change == 'aliases':
            changed['audit'] = dict(left=list(range(128)), right=list(range(128)))
        elif change == 'order':
            changed = dict(reversed(tuple(changed.items())))
        elif change == 'type':
            changed['value'] = float(cast(int, changed['value']))
        else:
            changed['available_at'] = 8
        forged = CertifiedSource([changed], tuple(source.keys), owner, None)
        after = adapter.prepare(forged, bars)['path-0']
        assert type(before) is CertifiedSource and type(after) is CertifiedSource
        assert after is not before and after.keys[0] != before.keys[0]
        assert list(after) == with_price_bodies([changed], bars)
        assert tuple(after[0]) == tuple(with_price_bodies([changed], bars)[0])
        if change == 'aliases':
            old_audit = cast(dict[str, object], before[0]['audit'])
            new_audit = cast(dict[str, object], after[0]['audit'])
            assert old_audit['left'] is old_audit['right']
            assert new_audit['left'] is not new_audit['right']


@pytest.mark.parametrize('field', ('timestamp', 'open', 'close', 'high', 'low'))
def test_price_body_derivation_records_each_actual_field_and_type(field: str) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        points = [_point(0, 0)]
        source = CertifiedSource(points, _keys(points), owner, None)
        adapter = _OwnedSourceInputs()
        bars = _bars()
        before = adapter.prepare(source, bars)['path-0']
        original = bars[0]
        changed = Bar(original.timestamp + timedelta(days=1) if field == 'timestamp' else original.timestamp,
                      original.symbol, int(original.open) if field == 'open' else original.open,
                      int(original.high) if field == 'high' else original.high,
                      int(original.low) if field == 'low' else original.low,
                      int(original.close) if field == 'close' else original.close, original.volume)
        revised = (changed, *bars[1:])
        after = adapter.prepare(source, revised)['path-0']
        assert type(before) is CertifiedSource and type(after) is CertifiedSource
        assert after.keys[0] != before.keys[0]
        assert list(after) == with_price_bodies(points, revised)
        if field != 'timestamp':
            assert type(cast(dict[str, object], after[0]['price_body'])[field]) is int


