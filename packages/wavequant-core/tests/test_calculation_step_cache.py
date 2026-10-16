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
    recorder.append(10, first)
    assert len(recorder.chunks.get(10, [])) == (1 if first_size >= 512 * 1024 else 0)
    recorder.append(10, second)
    recorder.complete(10)
    stored = [recorder.cache.get('calculation-chunk', key) for key in recorder.chunks[10]]
    expected = [[first], [second]] if first_size >= 512 * 1024 else [[first, second]]
    assert stored == expected
    assert 10 not in recorder.buffers
    assert 10 not in recorder.sizes


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
    assert recorder.steps[16]['cache'] == 'failed'
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
