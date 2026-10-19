"""分阶段历史暂存必须有界、独立拥有，并在任意失败阶段关闭资源。"""

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

import pytest

import wavequant.domain.strategies.chart_entry_history as chart_module
import wavequant.domain.strategies.hierarchical_entry as hierarchy_module
import wavequant.domain.market_structure.trend_lines as lines_module
from wavequant.domain.market_structure.trend_staging import TrendStageRows
from wavequant.domain.models.model import Bar


def _bars() -> list[Bar]:
    values = (600, 1080, 960, 1200, 680, 810, 550, 1030, 910, 1150, 630, 760)
    result: list[Bar] = []
    for index, value in enumerate(values):
        low, high = (value, value + .25) if index % 2 == 0 else (value - .25, value)
        middle = (low + high) / 2
        result.append(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'RESOURCE',
                          middle, high, low, middle, 1000))
    return result


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError('integer stage rows required')
    return value


def _identity(value: object) -> object:
    return value


def test_stage_spills_owns_written_rows_and_closes_idempotently() -> None:
    row: dict[str, object] = {'points': [1, 2], 'padding': 'x' * 1000}
    with TrendStageRows(_identity, memory_bytes=16) as stage:
        stage.write(row)
        row['points'] = [99]
        first = list(stage.iter_rows())
        assert first == [{'points': [1, 2], 'padding': 'x' * 1000}]
        assert stage.row_count == 1
        assert not stage.closed
        assert getattr(stage._stream, '_rolled') is True
        assert list(stage.iter_rows()) == first
        with pytest.raises(ValueError, match='cannot accept additional rows'):
            stage.write(row)
    assert stage.closed
    stage.close()
    assert stage.closed


def test_stage_decoder_failure_closes_owned_tempfile() -> None:
    stage: TrendStageRows[object] = TrendStageRows(_integer, memory_bytes=16)
    with pytest.raises(ValueError, match='integer stage rows required'):
        with stage:
            stage.write('incorrect internal row')
            list(stage.iter_rows())
    assert stage.closed


def test_large_stage_compression_preserves_types_aliases_and_independent_reads() -> None:
    shared = [1, True, -0., ('tuple', 3)]
    row = dict(padding='proof field ' * 20000, first=shared, second=shared)
    with TrendStageRows(_identity, memory_bytes=64) as stage:
        stage.write(row)
        assert stage.stored_bytes < stage.raw_bytes / 10
        first = list(stage.iter_rows())[0]
        second = list(stage.iter_rows())[0]
        assert first == second == row
        assert isinstance(first, dict) and isinstance(second, dict)
        assert first['first'] is first['second']
        assert first['first'] is not second['first']
        assert isinstance(first['first'][3], tuple)
        first['first'].clear()
        assert list(stage.iter_rows())[0] == row


def test_stage_rejects_concurrent_readers_without_corrupting_its_cursor() -> None:
    with TrendStageRows(_integer, memory_bytes=16) as stage:
        stage.write(1)
        stage.write(2)
        reader = stage.iter_rows()
        assert next(reader) == 1
        with pytest.raises(ValueError, match='one active sequential reader'):
            next(stage.iter_rows())
        assert next(reader) == 2
        with pytest.raises(StopIteration):
            next(reader)
        assert list(stage.iter_rows()) == [1, 2]


@pytest.mark.parametrize('memory_bytes', [0, -1, True, 1.5])
def test_stage_rejects_invalid_buffer_limits(memory_bytes: object) -> None:
    # 此处主动模拟未经过类型校验的外部配置，仍要求具体值验证而非依赖注解。
    with pytest.raises(ValueError, match='positive integer'):
        TrendStageRows(_identity, memory_bytes=cast(int, memory_bytes))


@pytest.mark.parametrize('failure', [None, 'drawing', 'prepare', 'permissions'])
def test_chart_closes_all_stages_on_success_and_on_every_failure_boundary(
    monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    stages: list[TrendStageRows[object]] = []
    original_init = TrendStageRows.__init__

    def record_init(self: TrendStageRows[object], decode: Callable[[object], object],
                    *, memory_bytes: int = 2 * 1024 * 1024) -> None:
        original_init(self, decode, memory_bytes=16)
        stages.append(self)

    monkeypatch.setattr(TrendStageRows, '__init__', record_init)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError('staging boundary failed')

    target = {'drawing': 'lecture_drawing', 'prepare': 'prepare_trend_lines',
              'permissions': 'attach_trend_permissions'}
    if failure is not None:
        monkeypatch.setattr(chart_module, target[failure], fail)
        with pytest.raises(RuntimeError, match='staging boundary failed'):
            chart_module.chart_entry_history(_bars())
    else:
        chart_module.chart_entry_history(_bars())
    expected = {'drawing': 2, 'prepare': 3}.get(failure, 5) if failure is not None else 5
    assert len(stages) == expected
    assert all(stage.closed for stage in stages)


@pytest.mark.parametrize('failure', [False, True])
def test_hierarchy_closes_owned_stage_files_after_success_or_judgement_failure(
    monkeypatch: pytest.MonkeyPatch, failure: bool,
) -> None:
    stages: list[TrendStageRows[object]] = []
    original_init = TrendStageRows.__init__

    def record_init(self: TrendStageRows[object], decode: Callable[[object], object],
                    *, memory_bytes: int = 2 * 1024 * 1024) -> None:
        original_init(self, decode, memory_bytes=1)
        stages.append(self)

    monkeypatch.setattr(TrendStageRows, '__init__', record_init)

    def fail(*args: object, **kwargs: object) -> object:
        raise RuntimeError('hierarchy stage failed')

    if failure:
        monkeypatch.setattr(lines_module, 'judge_trend', fail)
        with pytest.raises(RuntimeError, match='hierarchy stage failed'):
            hierarchy_module.hierarchical_history(_bars())
    else:
        hierarchy_module.hierarchical_history(_bars())
    assert stages
    assert all(stage.closed for stage in stages)
