"""Step files preserve dated outputs and never manufacture a completed lifecycle."""

from contextlib import closing
from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
import math
import sqlite3
from unittest.mock import patch
import zlib

import pytest

from wavequant.domain.market_structure.calculation_steps import calculation_steps, observe_steps, record_lifecycle
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


def test_completed_step_is_durable_and_bound_to_every_bar_field(tmp_path):
    bars = [Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)]
    seen = []
    recorder = CalculationStepCache(tmp_path, {'engine': 'v1', 'args': 'a'}, seen.append)
    with observe_steps(recorder):
        assert calculation_steps(bars) is recorder
        recorder.append(1, {'bar_index': 0, 'points': [{'value': 10, 'available_at': '2026-01-02'}]})
        recorder.complete(1)
    assert calculation_steps(bars) is None
    assert recorder.cache.get('calculation-chunk', recorder.chunks[1][0])[0]['bar_index'] == 0
    restored = CalculationStepCache(tmp_path, {'engine': 'v1', 'args': 'a'}, seen.append)
    assert restored.bind(bars)
    assert restored.steps[0]['cache'] == 'available'
    assert restored.steps[0]['status'] == 'pending'
    restored.begin(1)
    assert restored.steps[0]['status'] == 'running'
    assert restored.steps[0]['cache'] == 'available'
    restored.complete(1)
    assert restored.steps[0]['status'] == 'completed'
    changed = CalculationStepCache(tmp_path, {'engine': 'v1', 'args': 'a'}, seen.append)
    changed.bind([replace(bars[0], buyable=False)])
    assert changed.steps[0]['status'] == 'pending'
    changed_engine = CalculationStepCache(tmp_path, {'engine': 'v2', 'args': 'a'}, seen.append)
    changed_engine.bind(bars)
    assert changed_engine.steps[0]['status'] == 'pending'


def test_empty_lifecycle_is_completed_but_a_failed_step_keeps_only_partial_cache(tmp_path):
    bars = [Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)]
    recorder = CalculationStepCache(tmp_path, 'a', lambda snapshot: None)
    recorder.bind(bars)
    recorder.begin(4)
    recorder.complete(4)
    assert recorder.steps[3]['rows'] == 0
    assert recorder.steps[3]['cache'] == 'stored'
    recorder.append(10, {'bar_index': 0, 'strokes': []})
    recorder.finish(None, ValueError('interrupted'))
    assert recorder.steps[9]['status'] == 'failed'
    assert recorder.steps[9]['cache'] == 'partial'
    manifest = recorder.cache.get('calculation-manifest', recorder.inputs)
    assert len(manifest['steps'][9]['chunks']) == 1
    assert recorder.cache.get('calculation-chunk', manifest['steps'][9]['chunks'][0])[0]['bar_index'] == 0
    assert recorder.steps[16]['status'] == 'not_recorded'
    assert recorder.steps[3]['status'] == 'completed'


def test_lifecycle_events_are_routed_by_level_and_state_without_turning_candidates_into_confirmations(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'a', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    record_lifecycle(recorder, [
        {'trend_level': 2, 'state': '空多交替', 'event': 'trend_lifecycle_candidate', 'bar_index': 0},
        {'trend_level': 3, 'state': '转空确认', 'event': 'trend_lifecycle_state', 'bar_index': 0},
    ])
    assert recorder.steps[11]['rows'] == 1
    assert recorder.steps[12]['rows'] == 0
    assert recorder.steps[22]['rows'] == 1


@dataclass
class _DatedEvidence:
    known_at: datetime
    values: list[object]


def test_encoded_chunks_match_object_codec_and_isolate_caller_mutations_after_restart(tmp_path):
    evidence = _DatedEvidence(datetime(2026, 1, 2, 15, 30), [True, 1, 1.0, -0.0, None])
    nested = {None: 'empty key', False: 'boolean key', 2: ['tuple', (3, 4)]}
    row = {
        '证据': evidence, '日期': date(2026, 1, 2), '文本': '折线 📈 "\\\n\b',
        'nested': nested,
    }
    expected = {
        '证据': {'known_at': '2026-01-02T15:30:00', 'values': [True, 1, 1.0, -0.0, None]},
        '日期': '2026-01-02', '文本': '折线 📈 "\\\n\b',
        'nested': {'None': 'empty key', 'False': 'boolean key', '2': ['tuple', [3, 4]]},
    }
    recorder = CalculationStepCache(tmp_path, 'snapshot', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(1, row)
    evidence.values.append('later mutation')
    nested.clear()
    recorder.complete(1)
    chunk_key = recorder.chunks[1][0]
    recorder.finish({}, None)
    restarted = ArtifactCache(tmp_path)
    actual = restarted.get('calculation-chunk', chunk_key)
    assert restarted.put('reference-codec', chunk_key, [expected])
    reference = restarted.get('reference-codec', chunk_key)
    assert actual == reference
    assert json.dumps(actual, ensure_ascii=False, allow_nan=False) == json.dumps(
        reference, ensure_ascii=False, allow_nan=False)
    values = actual[0]['证据']['values']
    assert [type(value) for value in values] == [bool, int, float, float, type(None)]
    assert math.copysign(1, values[3]) == -1
    with closing(sqlite3.connect(restarted.path)) as db:
        packed, digest = db.execute('SELECT payload,digest FROM artifacts WHERE key=?',
                                   (restarted.key('calculation-chunk', chunk_key),)).fetchone()
    assert hashlib.sha256(packed).hexdigest() == digest
    envelope = json.loads(zlib.decompress(packed))
    assert envelope == dict(schema=restarted.SCHEMA,
                            key=restarted.key('calculation-chunk', chunk_key), data=reference)
    recorder.append(1, {'late': True})
    assert restarted.get('calculation-chunk', chunk_key) == reference
    assert recorder.steps[0]['rows'] == 1


@pytest.mark.parametrize('first_size', [512 * 1024 - 1, 512 * 1024, 512 * 1024 + 1])
def test_encoded_chunk_boundary_and_oversized_single_row_match_original_size(tmp_path, first_size):
    empty_size = len(json.dumps({'payload': ''}, ensure_ascii=False, allow_nan=False).encode())
    first = {'payload': 'x' * (first_size - empty_size)}
    second = {'tail': '结束'}
    recorder = CalculationStepCache(tmp_path, 'boundary', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(1, first)
    assert len(recorder.chunks.get(1, [])) == (1 if first_size >= 512 * 1024 else 0)
    recorder.append(1, second)
    recorder.complete(1)
    stored = [recorder.cache.get('calculation-chunk', key) for key in recorder.chunks[1]]
    expected = [[first], [second]] if first_size >= 512 * 1024 else [[first, second]]
    assert stored == expected
    assert 1 not in recorder.buffers
    assert 1 not in recorder.sizes


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf'), object()])
def test_invalid_row_retains_prior_buffer_and_failed_stage_evidence(tmp_path, invalid):
    recorder = CalculationStepCache(tmp_path, 'invalid', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(3, {'valid': [1, True]})
    recorder.append(3, {'invalid': invalid})
    assert recorder.steps[2]['cache'] == 'failed'
    assert recorder.steps[2]['rows'] == 1
    recorder.finish(None, ValueError('interrupted'))
    assert recorder.steps[2]['status'] == 'failed'
    assert recorder.steps[2]['cache'] == 'failed'
    assert recorder.cache.get('calculation-chunk', recorder.chunks[3][0]) == [{'valid': [1, True]}]


def test_encoded_chunk_write_failure_preserves_existing_failure_and_flush_lifecycle(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'disk-failure', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(17, {'valid': 'independent result'})
    with patch.object(recorder.cache, '_connect', side_effect=sqlite3.OperationalError('disk full')):
        recorder.complete(17)
    assert recorder.steps[16]['status'] == 'completed'
    assert recorder.steps[16]['cache'] == 'partial'
    assert recorder.chunks[17] == []
    assert 17 not in recorder.buffers
    assert 17 not in recorder.sizes


def test_corrupt_encoded_chunk_is_cache_miss_then_can_be_replaced(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'corruption', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(1, {'value': 1.25})
    recorder.complete(1)
    key = recorder.chunks[1][0]
    with closing(sqlite3.connect(recorder.cache.path)) as db, db:
        db.execute('UPDATE artifacts SET payload=? WHERE key=?',
                   (b'broken', recorder.cache.key('calculation-chunk', key)))
    assert recorder.cache.get('calculation-chunk', key) is None
    assert recorder.cache.put('calculation-chunk', key, [{'value': 1.25}])
    assert recorder.cache.get('calculation-chunk', key) == [{'value': 1.25}]


def test_trend_chunks_reuse_full_evidence_and_restore_independent_versions(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'trend-refs', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    point = {'value': 1.0, 'proof': {'values': [True, 1, -0.0, None], 'text': '证据' * 2000}}
    stroke = {'id': 'same-path', 'points': [point]}
    first = {'bar_index': 0, 'strokes': [stroke], 'history_format': 'trend_changes_v1'}
    expected_first = json.loads(json.dumps(first))
    recorder.append(3, first)
    recorder.append(3, dict(first, bar_index=1))
    stroke['points'].append({'value': 2, 'available_at': '2026-01-03'})
    expected_tail = json.loads(json.dumps(dict(first, bar_index=2)))
    recorder.append(3, dict(first, bar_index=2))
    point['proof']['values'][1] = 7
    expected_revision = json.loads(json.dumps(dict(first, bar_index=3)))
    recorder.append(3, dict(first, bar_index=3))
    recorder.complete(3)
    assert recorder.inputs['schema'] == 4
    restarted = ArtifactCache(tmp_path)
    rows = restarted.get('calculation-chunk', recorder.chunks[3][0])
    assert rows == [expected_first, dict(expected_first, bar_index=1), expected_tail, expected_revision]
    assert recorder.steps[2]['reused_bytes'] > 0
    values = rows[0]['strokes'][0]['points'][0]['proof']['values']
    assert [type(value) for value in values] == [bool, int, float, type(None)]
    assert math.copysign(1, values[2]) == -1
    rows[0]['strokes'][0]['points'][0]['proof']['values'][1] = 99
    assert rows[1]['strokes'][0]['points'][0]['proof']['values'][1] == 1
    assert restarted.get('calculation-chunk', recorder.chunks[3][0])[0] == expected_first


def test_standard_evidence_cache_is_bounded_and_unknown_values_keep_original_codec(tmp_path):
    from wavequant.infrastructure.persistence.calculation_chunk import EvidenceJsonCache
    from wavequant.infrastructure.persistence.calculation_step_cache import _encode_json

    class UnknownMapping(dict):
        def __reduce_ex__(self, protocol):
            raise AssertionError('cache signatures must not call unknown serialization hooks')

    cache = EvidenceJsonCache(max_bytes=4096)
    for index in range(30):
        value = {'points': [{'value': index, 'proof': 'x' * 512}]}
        assert json.loads(cache.encode(value, _encode_json, stroke=True)) == value
        assert cache.memory_bytes <= 4096
    oversized = {'points': [{'value': 1, 'proof': 'x' * 8192}]}
    assert json.loads(cache.encode(oversized, _encode_json, stroke=True)) == oversized
    assert cache.memory_bytes <= 4096
    fallback = UnknownMapping(points=[_DatedEvidence(datetime(2026, 1, 2), [True, 1, 1.0])])
    assert cache.encode(fallback, _encode_json, stroke=True) == _encode_json(fallback)


def test_reference_chunks_bound_expanded_history_despite_high_deduplication(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'bounded-expanded-chunks', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    stroke = {'id': 'closed', 'points': [{'value': 1, 'proof': 'x' * (64 * 1024)}]}
    for index in range(80):
        recorder.append(3, {'bar_index': index, 'strokes': [stroke]})
    recorder.complete(3)
    batches = [recorder.cache.get('calculation-chunk', key) for key in recorder.chunks[3]]
    assert len(batches) >= 2
    assert max(len(batch) for batch in batches) <= 64
    assert [row['bar_index'] for batch in batches for row in batch] == list(range(80))
    assert all(row['strokes'] == [stroke] for batch in batches for row in batch)


def test_cache_write_failure_stops_remaining_steps_without_repeated_lock_waits(tmp_path):
    recorder = CalculationStepCache(tmp_path, 'stop-after-write-failure', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    recorder.append(3, {'strokes': [{'points': [{'value': 1}]}]})
    recorder.complete(3)
    recorder.append(10, {'strokes': [{'points': [{'value': 2}]}]})
    with patch.object(recorder.cache, '_connect', side_effect=sqlite3.OperationalError('database is locked')) as connect:
        recorder.complete(10)
        recorder.append(17, {'strokes': [{'points': [{'value': 3}]}]})
        recorder.complete(17)
        recorder.finish({'signals': []})
    assert connect.call_count == 1
    assert recorder.steps[2]['cache'] == 'stored'
    assert recorder.steps[9]['cache'] == recorder.steps[16]['cache'] == 'partial'
    assert recorder.steps[16]['rows'] == 1
    assert recorder.steps[16]['encoded_bytes'] == 0
    assert not recorder.buffers and not recorder.trend_chunks and not recorder.evidence_json.entries


def test_invalid_stroke_reference_is_a_miss_without_exposing_partial_rows(tmp_path):
    cache = ArtifactCache(tmp_path)
    for reference in (-1, 1, True, '0'):
        value = {'format': 'trend_stroke_refs_v1', 'values': [{'points': []}],
                 'rows': [{'row': {'bar_index': 0}, 'strokes': [reference]}]}
        assert cache.put('calculation-chunk', reference, value)
        assert cache.get('calculation-chunk', reference) is None


def test_cache_metrics_measure_encoding_and_write_separately_from_shared_span(tmp_path):
    from wavequant.infrastructure.persistence.calculation_step_cache import _encode_json

    current = [100.0]
    with patch('wavequant.infrastructure.persistence.calculation_step_cache.monotonic', side_effect=lambda: current[0]):
        recorder = CalculationStepCache(tmp_path, 'measured', lambda snapshot: None)
        recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])

        def encode(value):
            current[0] += 2
            return _encode_json(value)

        put = recorder.cache._put_json

        def write(*args):
            current[0] += 3
            return put(*args)

        with patch('wavequant.infrastructure.persistence.calculation_step_cache._encode_json', side_effect=encode):
            recorder.append(1, {'value': 1})
        current[0] += 20
        with patch.object(recorder.cache, '_put_json', side_effect=write):
            recorder.complete(1)
    row = recorder.snapshot()['steps'][0]
    assert row['encoding_seconds'] == 2
    assert row['write_seconds'] == 3
    assert row['elapsed_seconds'] == 25
    assert row['encoded_bytes'] == len(_encode_json({'value': 1}))


def test_persistent_point_versions_record_as_complete_independent_json_rows(tmp_path):
    from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
    from wavequant.domain.market_structure.trend_point_version import PointVersion

    bars = [Bar(datetime(2026, 1, 2), 'sh.601086', 10, 11, 9, 10, 100)]
    recorder = CalculationStepCache(tmp_path, {'engine': 'point-version'}, lambda _: None)
    assert recorder.bind(bars)
    point = dict(index=0, value=10., available_at=0, proof={'known_at': 0})
    with owned_evidence_scope() as owner:
        points = CertifiedSource([point], [b'one'], owner, None)
        legs = PointVersion([dict(points=[point, point])]).seal()
        row = dict(bar_index=0, strokes=[dict(id='path', points=points, structural_legs=legs)])
        recorder.append(3, row)
        recorder.complete(3)
    assert recorder.steps[2]['cache'] == 'stored'
    restored = recorder.cache.get('calculation-chunk', recorder.chunks[3][0])
    assert restored == [dict(bar_index=0, strokes=[dict(id='path', points=[point],
                                                    structural_legs=[dict(points=[point, point])])])]
    restored[0]['strokes'][0]['points'][0]['proof']['known_at'] = 99
    assert point['proof']['known_at'] == 0


def test_owned_json_encoding_reuses_authenticated_nodes_without_resigning_prefix():
    from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
    from wavequant.infrastructure.persistence.calculation_chunk import EvidenceJsonCache
    from wavequant.infrastructure.persistence.calculation_step_cache import _encode_json
    from wavequant.infrastructure.persistence import calculation_chunk

    cache = EvidenceJsonCache(max_bytes=32 * 1024)
    point = dict(index=0, proof={'detail': 'x' * 2048})
    with owned_evidence_scope() as owner:
        owner.publish(point)
        expected = cache.encode(point, _encode_json)
        with patch.object(calculation_chunk, '_geometry_signature', side_effect=AssertionError('old proof was signed')):
            with patch('wavequant.infrastructure.persistence.calculation_step_cache._json_value',
                       side_effect=AssertionError('old proof was traversed')):
                assert cache.encode(point, _encode_json) == expected
        assert cache.memory_bytes <= cache.max_bytes
    with owned_evidence_scope() as owner:
        revised = dict(index=0, proof={'detail': 'y' * 2048})
        owner.publish(revised)
        assert cache.encode(revised, _encode_json) == _encode_json(revised)
        assert cache.encode(revised, _encode_json) != expected
