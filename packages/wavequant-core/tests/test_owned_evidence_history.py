"""共享历史证据保持完整输出、可变导出隔离及旧点修订语义。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
import pickle
from typing import Never, cast

import pytest

from wavequant.domain.market_structure import trend_judgement, trend_lines
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.owned_evidence import (
    CertifiedSource, current_evidence, owned_evidence_scope, owned_signature,
)
from wavequant.domain.market_structure.prepared_point_history import PreparedPointHistory, PreparedPointReference
from wavequant.domain.market_structure.trend_cascade import _TrendCascadeCache, prepare_trend_cascade
from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature
from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.market_structure.trend_judgement import _SourceVersionOwner, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_lines import _OwnedSourceInputs, _TrendJudgementCache, polyline_high_low_points
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex, market_prefix
from wavequant.domain.market_structure.trend_prefix_view import _PrefixView
from wavequant.domain.models.model import Bar


def _point(index: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=str(index), available_at=index + 1,
        kind='H' if index % 2 else 'L', value=20. + index if index % 2 else 10. + index,
        state='confirmed', trend_level=0)


def _row(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def test_shared_proof_preserves_types_field_order_aliases_and_independent_reads() -> None:
    shared = dict(values=[1, 2.5, None], pair=('same', 7))
    distinct = deepcopy(shared)
    with owned_evidence_scope() as evidence:
        evidence.publish(shared)
        evidence.publish(distinct)
        original = dict(first=shared, again=shared, distinct=distinct, extra=[shared])
        payload = evidence.dumps(original)
        restored = cast(dict[str, object], evidence.loads(payload))
        assert restored == original
        assert list(restored) == list(original)
        assert restored['first'] is restored['again']
        assert cast(list[object], restored['extra'])[0] is restored['first']
        assert restored['first'] is not restored['distinct']
        assert type(cast(dict[str, object], restored['first'])['pair']) is tuple
        cast(list[object], cast(dict[str, object], restored['first'])['values']).append(-99)
        assert evidence.loads(payload) == original
        assert shared['values'] == [1, 2.5, None]


def test_old_history_is_not_rewritten_by_new_proof_or_a_public_reader() -> None:
    with owned_evidence_scope() as evidence, TrendChangeRows(_row, row_count=5, load_encoded=evidence.loads) as history:
        first = dict(available_at=1, evidence=dict(known_at=1, values=list(range(100))))
        evidence.publish(first)
        history._write_encoded_change(0, evidence.dumps(dict(points=[first])))
        revised = dict(first, available_at=3, evidence=dict(known_at=3, values=[-1]))
        evidence.publish(revised)
        history._write_encoded_change(3, evidence.dumps(dict(points=[revised])))
        history.read_limit = 3
        early = list(history.iter_rows())
        assert all(row['points'] == [first] for row in early)
        cast(list[object], early[0]['points']).clear()
        history.read_limit = None
        actual = list(history.iter_rows())
        assert [row['points'] for row in actual] == [[first]] * 3 + [[revised]] * 2
        assert actual[0]['points'] is not actual[1]['points']


def test_append_payload_work_does_not_reencode_the_shared_proof_prefix() -> None:
    sizes = []
    with owned_evidence_scope() as evidence:
        proof: dict[str, object] = dict(index=0, detail='x' * 200_000)
        evidence.publish(proof)
        for day in range(1, 129):
            proof = dict(index=day, previous=proof, available_at=day)
            evidence.publish(proof)
            payload = evidence.dumps(dict(day=day, proof=proof))
            sizes.append(len(payload))
        assert max(sizes) < 512
        assert sum(sizes[64:]) <= 2 * sum(sizes[:64])
        assert evidence.loads(payload) == dict(day=128, proof=proof)


def test_budget_refusal_keeps_complete_evidence_and_existing_references() -> None:
    with owned_evidence_scope(memory_bytes=12_000) as evidence:
        small = dict(values=[1, 2, 3])
        evidence.publish(small)
        saved = evidence.dumps(small)
        large = dict(detail='x' * 100_000)
        evidence.publish(large)
        assert evidence.reference(small) is not None
        assert evidence.reference(large) is None
        assert evidence.memory_used <= 12_000
        assert evidence.loads(saved) == small
        assert evidence.loads(evidence.dumps(large)) == large
    with owned_evidence_scope(memory_bytes=0) as empty:
        empty.publish(large)
        assert empty.memory_used == 0
        assert empty.loads(empty.dumps(large)) == large


def test_unknown_nonfinite_and_cyclic_values_do_not_enter_owned_pool() -> None:
    class Unknown:
        def __reduce__(self) -> Never:
            raise AssertionError('unknown serialization hook must not run')

    cycle: list[object] = []
    cycle.append(cycle)
    with owned_evidence_scope() as evidence:
        for value in ({'audit': Unknown()}, {'audit': float('nan')}, {'audit': float('inf')}, cycle):
            evidence.publish(value)
            assert evidence.reference(value) is None
            assert not evidence.finite(value)
        assert owned_signature({'audit': Unknown()}) is None


def test_request_namespace_and_nested_scopes_cannot_adopt_foreign_versions() -> None:
    assert current_evidence() is None
    with owned_evidence_scope() as outer:
        value = dict(values=[1, 2])
        outer.publish(value)
        payload = outer.dumps(value)
        with owned_evidence_scope() as inner:
            inner.publish(dict(other=[3]))
            with pytest.raises(ValueError, match='own request|its request'):
                inner.loads(payload)
        assert current_evidence() is outer
        assert outer.loads(payload) == value
    assert current_evidence() is None
    with pytest.raises(ValueError, match='closed'):
        outer.loads(payload)


def test_certified_source_skips_old_point_encoding_but_revises_earlier_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    source = [_point(index) for index in range(80)]
    with owned_evidence_scope():
        inputs = _OwnedSourceInputs()
        owner = _SourceVersionOwner(1, 8 * 1024 * 1024)
        first = inputs.prepare(source[:-1], ())['path-0']
        assert owner.prepare(first) is not None
        appended = inputs.prepare(source, ())['path-0']
        original = trend_judgement._geometry_signature
        encoded = 0

        def counted(value: object) -> bytes | None:
            nonlocal encoded
            encoded += 1
            return original(value)

        monkeypatch.setattr(trend_judgement, '_geometry_signature', counted)
        version = owner.prepare(appended)
        assert version is not None and encoded <= 2
        assert owner.prepare(appended) is version and encoded <= 2
        revised = [dict(point) for point in source]
        revised[4]['available_at'] = 6
        revision = owner.prepare(inputs.prepare(revised, ())['path-0'])
        assert revision is not None and revision.changed_from == 4
        assert revision.source[4]['available_at'] == 6
        shortened = owner.prepare(inputs.prepare(revised[:20], ())['path-0'])
        assert shortened is not None and len(shortened.source) == 20


def test_public_mutable_source_still_authenticates_nested_audit_revisions() -> None:
    source = [dict(_point(index), audit=dict(values=[index])) for index in range(12)]
    with owned_evidence_scope():
        cache = _TrendJudgementCache(tail_reuse=True)
        cache.judge(source, path='public', trend_level=1, _owned_source=True)
        cast(list[object], cast(dict[str, object], source[2]['audit'])['values']).append(99)
        actual = cache.judge(source, path='public', trend_level=1, _owned_source=True)
        observed = mutable_points(actual.points), mutable_points(actual.candidates)
    expected = judge_trend(source, trend_level=1)
    assert observed == (mutable_points(expected.points), mutable_points(expected.candidates))


def _bars() -> tuple[Bar, ...]:
    prices = (20., 30., 10., 20., 14., 26., 18., 35., 28., 45., 38., 42., 32., 36., 24., 29., 26., 50.)
    return tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'OWNED', price,
        price + .25, price - .25, price, 1000) for index, price in enumerate(prices))


def test_cold_and_owned_three_level_results_match_for_every_market_prefix() -> None:
    bars = _bars()
    snapshots: list[list[dict[str, object]]] = []

    def collect(_index: int, epoch: int, raw: list[dict[str, object]]) -> None:
        drawing: dict[str, object] = dict(strokes=[dict(id=f'lecture-{epoch}', points=raw)] if len(raw) > 1 else [])
        snapshots.append(deepcopy(polyline_high_low_points(drawing)))

    lecture_drawing(bars, on_step=collect)
    expected = [prepare_trend_cascade(source, symbol='OWNED', bars=bars[:index + 1])
        for index, source in enumerate(snapshots)]
    caches = [_TrendJudgementCache(tail_reuse=True) for _ in range(3)]
    with owned_evidence_scope(), _TrendCascadeCache(caches) as rounds:
        for index, source in enumerate(snapshots):
            actual = prepare_trend_cascade(deepcopy(source), symbol='OWNED', bars=bars[:index + 1],
                caches=caches, _round_caches=rounds, _owned_source=True)
            assert actual == expected[index], index
            assert all(type(stroke['points']) is CertifiedSource for row in actual
                for stroke in cast(Sequence[Mapping[str, object]], row['source_strokes']))
            exported = deepcopy(actual)
            assert exported == expected[index]
            assert all(type(stroke['points']) is list for row in exported
                for stroke in cast(Sequence[Mapping[str, object]], row['source_strokes']))


def test_market_prefix_shares_only_exact_immutable_source_and_hides_future() -> None:
    bars = _bars()
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    cache = trend_lines._TrendPermissionCache(bars)
    for stop in range(len(bars) + 1):
        prefix = market_prefix(bars, stop)
        assert type(prefix) is _PrefixView and prefix.source is bars
        assert list(prefix) == list(bars[:stop])
        assert cache.covers(prefix)
        index = _MarketPrefixIndex(owner, prefix)
        with pytest.raises(IndexError):
            _ = prefix[stop]
        with pytest.raises(ValueError, match='prefix interval'):
            index.first_extreme(0, stop + 1, 0., high=True)
        if stop:
            assert prefix[-1] is bars[stop - 1]
    with pytest.raises(ValueError, match='original Bar'):
        _MarketPrefixIndex(owner, _PrefixView(bars, range(1, len(bars))))
    mutable = list(bars)
    assert type(market_prefix(mutable, 2)) is list
    mutable[0] = Bar(bars[0].timestamp, 'OTHER', 10., 11., 9., 10., 1000)
    assert not cache.covers(mutable)


def test_plain_pickle_remains_readable_by_evidence_loader() -> None:
    with owned_evidence_scope() as evidence:
        value = dict(points=[_point(0)], tuple=(1, 2))
        assert evidence.loads(pickle.dumps(value, protocol=5)) == value
    assert owned_signature(value) == _geometry_signature(value)


def test_point_blocks_keep_dated_versions_after_early_revision_truncation_and_regrowth() -> None:
    points = [_point(index) for index in range(160)]
    snapshots: list[list[dict[str, object]]] = []
    references: list[PreparedPointReference] = []
    with owned_evidence_scope() as evidence:
        history = PreparedPointHistory(evidence)
        try:
            for current in (points[:90], points, points[:33], points[:80],
                    [dict(point, audit=['changed']) if index == 4 else point for index, point in enumerate(points)]):
                reference = history.pack(('geometry', 'path'), current)
                assert reference is not None
                references.append(reference)
                snapshots.append(deepcopy(current))
            for reference, expected in zip(references, snapshots, strict=True):
                actual = history.unpack(reference)
                assert actual == expected
                actual[0]['value'] = -999
                assert history.unpack(reference) == expected
            with pytest.raises(ValueError, match='its history'):
                history.unpack(PreparedPointReference('other', references[0].root, references[0].capacity, 1))
        finally:
            history.close()
        assert history._rows.closed


def test_point_block_write_volume_stays_bounded_per_append_instead_of_copying_all_points() -> None:
    with owned_evidence_scope() as evidence:
        history = PreparedPointHistory(evidence)
        try:
            points: list[dict[str, object]] = []
            writes = []
            for index in range(240):
                points.append(_point(index))
                previous_bytes = history._rows.raw_bytes
                assert history.pack(('geometry', 'growing'), points) is not None
                writes.append(history._rows.raw_bytes - previous_bytes)
            # 相同追加数量只多出对数级索引节点，不能按历史长度重写整条点数组。
            assert sum(writes[160:240]) < 2 * sum(writes[80:160])
            assert max(writes[160:240]) < 20_000
        finally:
            history.close()


def test_prepared_point_transport_keeps_cross_field_aliases_and_plain_public_arrays() -> None:
    proof = dict(available_at=3, facts=[1, 2, 3])
    points = [dict(_point(index), proof=proof) for index in range(70)]
    source = dict(id='source', points=points)
    candidate = dict(id='candidate', source_path='source', points=list(points))
    prepared = dict(source_strokes=[source], candidate_strokes=[candidate], structure_strokes=[candidate],
        strokes=[dict(id='geometry', points=list(points))], developing_strokes=[])
    with owned_evidence_scope(), trend_lines._ClosedPreparedPaths(_owned_history=True) as paths:
        encoded = paths.encode(prepared)
        payload = trend_lines._prepared_payload(encoded)
        assert payload is not None
        actual = paths.decode(paths.load_encoded(payload))
        assert actual == prepared
        sources = cast(list[dict[str, object]], actual['source_strokes'])
        candidates = cast(list[dict[str, object]], actual['candidate_strokes'])
        structures = cast(list[dict[str, object]], actual['structure_strokes'])
        assert candidates[0] is structures[0]
        assert type(sources[0]['points']) is list
        left = cast(list[dict[str, object]], sources[0]['points'])
        right = cast(list[dict[str, object]], candidates[0]['points'])
        assert left is not right and left[0] is right[0]
        assert left[0]['proof'] is left[-1]['proof']
        cast(list[object], cast(dict[str, object], left[0]['proof'])['facts']).clear()
        assert paths.decode(paths.load_encoded(payload)) == prepared


def test_point_block_budget_refuses_without_discarding_saved_history() -> None:
    with owned_evidence_scope() as evidence:
        history = PreparedPointHistory(evidence, memory_bytes=12_000)
        try:
            small = [_point(index) for index in range(4)]
            reference = history.pack(('geometry', 'path'), small)
            assert reference is not None
            assert history.pack(('geometry', 'path'), [_point(index) for index in range(1000)]) is None
            assert history.unpack(reference) == small
            assert history._used <= 12_000
        finally:
            history.close()


def test_exhausted_evidence_uses_complete_graph_without_python_reference_callbacks(monkeypatch):
    from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload
    from wavequant.domain.market_structure.trend_point_version import PointVersion

    point = dict(index=0, proof={'values': [1, 2]})
    distinct = deepcopy(point)
    version = PointVersion([point, distinct]).seal()
    prepared = dict(strokes=[dict(points=version)], candidate_strokes=[dict(points=version)],
                    proof=point['proof'])
    with owned_evidence_scope(memory_bytes=0) as evidence:
        paths = _ClosedPreparedPaths(_owned_history=True)
        encoded = paths.encode(prepared)
        monkeypatch.setattr(evidence, 'reference', lambda _: pytest.fail('exhausted pool was retried'))
        payload = _prepared_payload(encoded)
        assert payload is not None
        restored = evidence.loads(payload)
        points = restored['strokes'][0]['points']
        assert type(points) is list and points == [point, distinct]
        assert points is restored['candidate_strokes'][0]['points']
        assert points[0] is not points[1]
        assert points[0]['proof'] is restored['proof']
        restored['proof']['values'].append(99)
        assert point['proof']['values'] == [1, 2]


def test_exhausted_evidence_does_not_invoke_unknown_audit_reducers():
    from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload
    from wavequant.domain.market_structure.trend_point_version import PointVersion

    class Unknown:
        def __reduce_ex__(self, protocol):
            pytest.fail('unknown audit reducer was invoked')

    with owned_evidence_scope(memory_bytes=0):
        paths = _ClosedPreparedPaths(_owned_history=True)
        prepared = dict(strokes=[dict(points=PointVersion([dict(audit=Unknown())]).seal())])
        assert _prepared_payload(paths.encode(prepared)) is None


def test_signature_direct_dispatch_keeps_reference_bytes_and_root_exclusion() -> None:
    from wavequant.domain.market_structure.owned_evidence import _EvidenceWriter, _SignatureWriter

    class ReferenceSignatureWriter(_EvidenceWriter):
        def persistent_id(self, value: object) -> int | None:
            if type(value) is str:
                return None
            return super().persistent_id(value)

    proof = {'values': [1, 2, 3]}
    data = {'left': proof, 'right': proof, 'value': 7}
    with owned_evidence_scope() as owner:
        owner.publish(data)
        expected = owner.dumps(data, writer=lambda stream: ReferenceSignatureWriter(stream, owner, root=data))
        actual = owner.dumps(data, writer=lambda stream: _SignatureWriter(stream, owner, root=data))
        assert actual == expected
        restored = owner.loads(actual)
        assert restored == data
        assert restored['left'] is restored['right']
        assert restored['left'] is not proof
