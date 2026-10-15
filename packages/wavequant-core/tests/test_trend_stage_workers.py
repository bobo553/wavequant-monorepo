"""请求级计算的顺序、回退、前缀一致性和关闭均需真实执行器覆盖。"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from typing import Never, SupportsIndex

import pytest

from wavequant.domain.market_structure.trend_lines import prepare_trend_lines
from wavequant.domain.market_structure.bottom_n_targets import DeclineStart
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import chart_entry_history, hierarchical_entry
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.combined_a_entry import _positive_ns
from wavequant.infrastructure.computation.trend_workers import TrendStageExecutor


def _bars() -> tuple[Bar, ...]:
    values = (20., 30., 10., 20., 14., 26., 18., 35., 28., 45., 38., 42., 32., 36., 24., 29., 26., 50.)
    return tuple(Bar(datetime(2026, 1, 1)+timedelta(days=index), 'WORK', value,
                     value+.5, value-.5, value, 100000.) for index, value in enumerate(values))


def _typed(value: object) -> object:
    if isinstance(value, Mapping):
        return type(value), tuple((_typed(key), _typed(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return type(value), tuple(_typed(item) for item in value)
    if is_dataclass(value) and not isinstance(value, type):
        return type(value), tuple((field.name, _typed(getattr(value, field.name))) for field in fields(value))
    return type(value), value


def test_real_process_rows_match_every_geometry_field_and_keep_submission_order() -> None:
    bars = _bars()
    executor = TrendStageExecutor(bars, workers=2)
    source = [dict(index=index, ordinal=0, time=bar.timestamp.date().isoformat(),
                   kind='L' if index % 2 == 0 else 'H', value=bar.low if index % 2 == 0 else bar.high,
                   available_at=index+1, trend_level=0, state='confirmed') for index, bar in enumerate(bars)]
    try:
        work = ((index, (source[:index+1], bars[0].symbol)) for index in range(len(source)))
        results = list(executor.map('geometry', work))
        assert [index for index, _ in results] == list(range(len(source)))
        for index, result in results:
            assert result is not None
            assert _typed(result) == _typed(prepare_trend_lines(source[:index+1], symbol=bars[0].symbol))
        assert executor.submissions == len(source) and executor.fallbacks == 0
        assert executor.peak_queue_bytes <= 24 * 1024 * 1024
    finally:
        executor.close()
    assert executor.closed and executor.pool is None


def test_unknown_and_nonfinite_data_never_enter_process_serialization_hooks() -> None:
    class Audit:
        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            raise AssertionError('an unknown audit was serialized')

    executor = TrendStageExecutor(_bars(), workers=1)
    try:
        results = list(executor.map('geometry', ((0, ([{'audit': Audit()}], 'WORK')),
                                                (1, ([{'audit': float('nan')}], 'WORK')))))
        assert results == [(0, None), (1, None)]
        assert executor.pool is None and executor.submissions == 0 and executor.fallbacks == 2
    finally:
        executor.close()




def test_combined_a_wire_facts_keep_duplicate_order_knowledge_and_the_full_public_audit() -> None:
    bars = _bars()
    audit = [dict(event='ignored', padding='unrelated audit field '*10000),
             dict(event='n_completed', direction='up', origin=0, neckline=1, pullback=2,
                  bar_index=3, known_at=4, one_p=40., two_t=60., audit=['full proof']),
             dict(event='n_completed', direction='down', origin=0),
             dict(event='n_completed', direction='up', origin=0, neckline=1, pullback=2,
                  bar_index=3, known_at=8, one_p=41., two_t=62.)]
    before = _typed(audit)
    facts = chart_entry_history._combined_a_facts(audit)
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    for now in range(len(bars)):
        assert _positive_ns(bars, {}, dates, now, facts) == _positive_ns(bars, {}, dates, now, audit)
    assert _typed(audit) == before
    assert len(facts) == 2 and all('audit' not in row and 'padding' not in row for row in facts)


@pytest.mark.parametrize('enabled', (False, True))
def test_real_pool_preserves_full_history_sidecars_and_checkpoint_source(
    enabled: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars()
    executor = TrendStageExecutor(bars, workers=2)
    config = SystemStrategy(n_target_trend_confirmation_enabled=enabled)
    expected_declines: dict[int, tuple[DeclineStart, ...]] = {}
    expected_structure: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
    expected = hierarchical_entry.hierarchical_history(bars, decline_sink=expected_declines,
        structure_sink=expected_structure, n_target_trend_confirmation_enabled=enabled)
    with BacktestHistoryReuse(bars, config, stage_runner=executor) as reuse:
        monkeypatch.setattr(reuse, '_stage_runner', lambda supplied, *, rows: executor if executor.matches(supplied) else None)
        declines: dict[int, tuple[DeclineStart, ...]] = {}
        structure: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        actual = hierarchical_entry.hierarchical_history(bars, decline_sink=declines, structure_sink=structure,
            n_target_trend_confirmation_enabled=enabled, _checkpoint_reuse=reuse)
        assert _typed(actual) == _typed(expected)
        assert _typed(declines) == _typed(expected_declines) and _typed(structure) == _typed(expected_structure)
        wanted: list[dict[int, object]] = [{}, {}, {}]
        sinks: list[dict[int, object]] = [{}, {}, {}]
        old = chart_entry_history.chart_entry_history(bars, shallow_candidate_sink=wanted[0],
            combined_candidate_sink=wanted[1], secondary_pullback_sink=wanted[2],
            n_target_trend_confirmation_enabled=enabled)
        new = chart_entry_history.chart_entry_history(bars, shallow_candidate_sink=sinks[0],
            combined_candidate_sink=sinks[1], secondary_pullback_sink=sinks[2], _history_reuse=reuse,
            n_target_trend_confirmation_enabled=enabled)
        assert _typed(new) == _typed(old) and _typed(sinks) == _typed(wanted)
        options = (enabled, True, True, True)
        channel = reuse._minute.channels['chart']
        for index in sorted(channel.sealed):
            read = channel.read(index)
            assert read is not None
            rows, _ = read
            assert [day for level, day, _ in rows] == list(range(index+1))
            assert channel.options == options
    assert executor.closed and executor.pool is None
