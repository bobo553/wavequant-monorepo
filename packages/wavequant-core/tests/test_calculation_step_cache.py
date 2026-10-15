"""Step files preserve dated outputs and never manufacture a completed lifecycle."""

from dataclasses import replace
from datetime import datetime

from wavequant.domain.market_structure.calculation_steps import calculation_steps, observe_steps, record_lifecycle
from wavequant.domain.models.model import Bar
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
