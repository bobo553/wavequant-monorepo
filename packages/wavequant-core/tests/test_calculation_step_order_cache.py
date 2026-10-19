"""Changing backend execution order preserves the identity of saved step data."""

from datetime import datetime
from pathlib import Path

import pytest

from wavequant.domain.market_structure.calculation_plan import resolve_calculation_plan
from wavequant.domain.market_structure.calculation_steps import STEP_TITLES
from wavequant.domain.models.model import Bar
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


GEOMETRY_FIRST = (1, 3, 10, 17, 2, *range(4, 10), *range(11, 17), *range(18, 25))


def test_execution_order_metadata_keeps_frontend_and_persisted_step_ids_stable(tmp_path: Path) -> None:
    recorder = CalculationStepCache(tmp_path, {'engine': 'v1'}, lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    plan = resolve_calculation_plan(GEOMETRY_FIRST)
    recorder.set_plan(plan)

    snapshot = recorder.snapshot()
    assert snapshot['schema_version'] == 1
    rows = snapshot['steps']
    assert isinstance(rows, list)
    assert [row['id'] for row in rows] == list(range(1, 25))
    assert rows[1]['title'] == STEP_TITLES[1]
    assert rows[16]['title'] == f'同日变化联动 · {STEP_TITLES[16]}'
    assert all(row['status'] == 'pending' for row in rows)
    assert '同一日期立即传播' in str(snapshot['note'])
    assert '独立六态计算或读取步骤' in str(snapshot['note'])

    recorder.append(1, {'kind': 'drawing', 'bar_index': 0})
    recorder.complete(1)
    assert recorder.cache is not None
    manifest = recorder.cache.get('calculation-manifest', recorder.inputs)
    assert manifest['requested_order'] == list(GEOMETRY_FIRST)
    assert manifest['execution_order'] == list(plan.execution_order)
    assert [row['id'] for row in manifest['steps']] == list(range(1, 25))
    assert [row['title'] for row in manifest['steps']] == list(STEP_TITLES)


def test_different_execution_order_reuses_artifacts_by_step_id_without_relabeling_data(tmp_path: Path) -> None:
    bars = [Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)]
    identity = {'engine': 'v1', 'symbol': 'sz.000678'}
    original = CalculationStepCache(tmp_path, identity, lambda snapshot: None)
    original.bind(bars)
    original.set_plan(resolve_calculation_plan(GEOMETRY_FIRST))
    original.append(1, {'kind': 'drawing', 'bar_index': 0})
    original.complete(1)
    original.append(3, {'kind': 'level_one', 'bar_index': 0})
    original.complete(3)

    restored = CalculationStepCache(tmp_path, identity, lambda snapshot: None)
    restored.bind(bars)
    restored.set_plan(resolve_calculation_plan(tuple(range(1, 25))))

    assert restored.inputs == original.inputs
    assert restored.steps[0]['cache'] == 'available'
    assert restored.steps[2]['cache'] == 'available'
    assert restored.steps[1]['cache'] == 'none'
    assert restored.steps[2]['status'] == 'pending'
    assert restored.chunks[1] == original.chunks[1]
    assert restored.chunks[3] == original.chunks[3]
    assert restored.cache is not None
    assert restored.cache.get('calculation-chunk', restored.chunks[1][0])[0]['kind'] == 'drawing'
    assert restored.cache.get('calculation-chunk', restored.chunks[3][0])[0]['kind'] == 'level_one'
    rows = restored.snapshot()['steps']
    assert isinstance(rows, list)
    assert rows[2]['title'] == f'同日变化联动 · {STEP_TITLES[2]}'


def test_order_cannot_change_mid_execution_or_mark_pending_steps_completed(tmp_path: Path) -> None:
    recorder = CalculationStepCache(tmp_path, 'a', lambda snapshot: None)
    recorder.bind([Bar(datetime(2026, 1, 2), 'sz.000678', 10, 11, 9, 10, 100)])
    plan = resolve_calculation_plan(GEOMETRY_FIRST)
    recorder.set_plan(plan)
    recorder.begin(1)
    recorder.set_plan(plan)

    with pytest.raises(ValueError, match='cannot change'):
        recorder.set_plan(resolve_calculation_plan(tuple(range(1, 25))))

    recorder.finish(None, ValueError('interrupted'))
    assert recorder.plan == plan
    assert recorder.steps[0]['status'] == 'failed'
    assert recorder.steps[2]['status'] == 'not_recorded'
    assert recorder.steps[23]['status'] == 'not_recorded'
