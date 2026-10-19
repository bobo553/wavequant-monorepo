"""Backend scheduling changes execution order without changing dated market facts."""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from wavequant.domain.market_structure.calculation_plan import resolve_calculation_plan, using_calculation_order
from wavequant.domain.market_structure.calculation_steps import observe_steps
from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle
from wavequant.domain.market_structure.trend_staging import TrendStageRows, _STAGE_MEMORY_BYTES
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis, prepare_backtest_analysis
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


def _bars() -> ValidatedBars:
    values = (10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10, 13, 11, 15, 12)
    bars = []
    for index, value in enumerate(values):
        rising = index == 0 or value > values[index - 1]
        body = .1 if rising else -.1
        bars.append(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', value - body,
            value + .2, value - .2, value + body, 1000 + index * 10))
    return ValidatedBars(bars)


def _config() -> SystemStrategy:
    return SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3')


def _facts(analysis: BacktestAnalysis) -> dict[str, object]:
    return deepcopy(dict(
        bars=analysis.bars,
        frames=tuple(asdict(frame) for frame in analysis.frames.iter_rows()),
        levels=tuple(tuple(level.iter_changes()) for level in analysis.levels),
        dated_levels=tuple(tuple(level.iter_rows()) for level in analysis.levels),
        pivots=analysis.pivots,
        n_observations=analysis.n_observations,
        regimes=analysis.regimes,
        projections=analysis.projections,
        lifecycle_events=analysis.lifecycle_events,
        trading_intervals=analysis.trading_intervals,
        buy_days=analysis.buy_days,
        scan_days=analysis.scan_days,
    ))


@pytest.mark.parametrize('order', [(17, 2), (23, 9, 16, 2)])
def test_reordered_preparation_preserves_all_facts_and_advances_each_level_once(
    order: tuple[int, ...], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bars = _bars()
    from wavequant.domain.strategies.eligible_polyline_n import EligiblePolylineN
    from wavequant.domain.strategies.market_calculations import MarketDay

    # 调度用例显式给出已确认窗口，避免几何夹具偶然未取得资格而不覆盖 N 目标。
    source_advance = EligiblePolylineN.advance

    def eligible_sources(self, now, points, events):
        opening = (dict(event='trend_lifecycle_state', bar_index=now, trend_level=2,
            source_path='test-window', state='空多交替', flip_confirmed=True,
            flip_index=0, flip_known_index=1, counter_index=0, counter_ordinal=0,
            counter_price=9.8, alternation_index=now),) if now == 2 else ()
        return source_advance(self, now, points, opening)

    monkeypatch.setattr(EligiblePolylineN, 'advance', eligible_sources)
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: self.frame.index >= 2)
    clocks: dict[TrendLifecycle, list[int]] = {}
    advance = TrendLifecycle.advance

    def counted_advance(self: TrendLifecycle, levels: Sequence[Mapping[str, object]], market: Sequence[Bar],
                        now: int, dates: Mapping[str, int], **kwargs) -> list[dict[str, object]]:
        clocks.setdefault(self, []).append(now)
        return advance(self, levels, market, now, dates, **kwargs)

    monkeypatch.setattr(TrendLifecycle, 'advance', counted_advance)
    with using_calculation_order(tuple(range(1, 25))), prepare_backtest_analysis(bars, _config()) as analysis:
        expected = _facts(analysis)
        assert analysis.n_observations, 'The fixture must exercise N measurement, not only empty outputs'
    assert len(clocks) == 4
    assert sum(len(indices) for indices in clocks.values()) == 4 * len(bars)
    with using_calculation_order(order), prepare_backtest_analysis(bars, _config()) as analysis:
        actual = _facts(analysis)
        assert analysis.lifecycle_events == sorted(analysis.lifecycle_events,
            key=lambda event: (event['trend_level'], event['bar_index']))
        assert len(analysis.buy_days) == len(bars)
        assert len(analysis.scan_days) == len(bars)

    assert actual == expected
    assert len(clocks) == 8
    assert all(indices == list(range(len(bars))) for indices in clocks.values())


@pytest.mark.parametrize(('order', 'prefix'), [
    ((17, 2), (1, 3, 10, 17, 2)),
    ((23, 9, 16, 2), (1, 3, 10, 17, 2)),
])
def test_cache_reports_real_execution_order_and_finishes_market_steps_before_strategy(
    order: tuple[int, ...], prefix: tuple[int, ...], tmp_path: Path,
) -> None:
    snapshots: list[dict[str, object]] = []
    recorder = CalculationStepCache(tmp_path, 'step-order-boundary', snapshots.append, record_details=True)
    with using_calculation_order(order) as plan, observe_steps(recorder):
        with prepare_backtest_analysis(_bars(), _config()) as analysis:
            assert recorder.plan == plan
            assert plan.execution_order[:len(prefix)] == prefix
            assert [row['status'] for row in recorder.steps[:23]] == ['completed'] * 23
            assert recorder.steps[23]['status'] == 'running'
            assert not analysis.frames.closed
            assert all(not level.closed for level in analysis.levels)
        assert analysis.frames.closed
        assert all(level.closed for level in analysis.levels)

    started: list[int] = []
    for snapshot in snapshots:
        rows = snapshot['steps']
        assert isinstance(rows, list)
        for row in rows:
            if row['status'] == 'running' and row['id'] not in started:
                started.append(row['id'])
    assert set(started) == set(range(1, 25))
    first_completion = next(item for item in snapshots
        if any(row['status'] == 'completed' for row in item['steps']))
    assert all(first_completion['steps'][step - 1]['status'] == 'running' for step in (3, 10, 17))
    assert first_completion['steps'][1]['status'] == 'pending'
    assert recorder.steps[23]['status'] == 'running'
    assert recorder.cache is not None
    manifest = recorder.cache.get('calculation-manifest', recorder.inputs)
    assert manifest['execution_order'] == list(plan.execution_order)
    assert [row['id'] for row in manifest['steps']] == list(range(1, 25))


def test_consumer_failure_closes_prepared_streams_and_restores_order_context() -> None:
    previous = resolve_calculation_plan()
    retained: list[BacktestAnalysis] = []
    with pytest.raises(RuntimeError, match='consumer failed'):
        with using_calculation_order((23, 9, 16, 2)), prepare_backtest_analysis(_bars(), _config()) as analysis:
            retained.append(analysis)
            raise RuntimeError('consumer failed')

    assert len(retained) == 1
    assert retained[0].frames.closed
    assert all(level.closed for level in retained[0].levels)
    assert resolve_calculation_plan() == previous


def test_preparation_failure_after_geometry_closes_every_allocated_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    streams: list[TrendStageRows[object]] = []
    initialize = TrendStageRows.__init__

    def retained_initialize(self: TrendStageRows[object], decode: Callable[[object], object], *,
                            memory_bytes: int = _STAGE_MEMORY_BYTES, **kwargs) -> None:
        initialize(self, decode, memory_bytes=memory_bytes, **kwargs)
        streams.append(self)

    def fail_at_n_measurement(message: str) -> None:
        if 'N 目标' in message:
            raise RuntimeError('N stage interrupted')

    monkeypatch.setattr(TrendStageRows, '__init__', retained_initialize)
    with pytest.raises(RuntimeError, match='N stage interrupted'):
        with using_calculation_order((17, 2)):
            with prepare_backtest_analysis(_bars(), _config(), stage=fail_at_n_measurement):
                raise AssertionError('an interrupted preparation must not yield a strategy input')

    assert streams
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize('order', [(0,), (25,), (True,), (3, 3)])
def test_invalid_backend_order_is_rejected_before_preparation(order: tuple[int, ...]) -> None:
    entered = False
    previous = resolve_calculation_plan()
    with pytest.raises(ValueError):
        with using_calculation_order(order):
            entered = True
            with prepare_backtest_analysis(_bars(), _config()):
                raise AssertionError('invalid execution order reached market preparation')
    assert not entered
    assert resolve_calculation_plan() == previous
