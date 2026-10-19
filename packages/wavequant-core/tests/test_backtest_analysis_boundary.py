"""A trading day consumes its prepared prefix before the market advances."""

from datetime import datetime, timedelta

from wavequant.domain.market_structure.calculation_steps import observe_steps
from wavequant.domain.models.model import Bar
from wavequant.domain.models.validated_bars import ValidatedBars
from wavequant.domain.strategies import chart_entry_history as chart_module
from wavequant.domain.strategies import integrated_strategy as strategy_module
from wavequant.domain.strategies import lecture_strategy
from wavequant.domain.strategies.backtest_analysis import prepare_backtest_analysis
from wavequant.infrastructure.persistence.calculation_step_cache import CalculationStepCache


def _bars():
    values = (10, 13, 11, 15, 12, 17, 14, 19, 15, 13, 16, 12, 15, 11, 14, 10, 13, 11, 15, 12)
    return ValidatedBars([Bar(datetime(2026, 1, 1) + timedelta(days=i), 'TEST', value,
        value + .2, value - .2, value, 1000 + i * 10) for i, value in enumerate(values)])


def _config():
    return strategy_module.SystemStrategy(pivot_mode='lecture_causal',
        entry_policy='hierarchical_two_buy_points', buy_point_definition='whole_flip_wave_v3')


def _enable_test_interval(monkeypatch):
    import wavequant.domain.strategies.backtest_analysis as analysis_module
    from wavequant.domain.strategies.trading_intervals import TradingInterval

    monkeypatch.setattr(analysis_module.MarketCalculations, 'trading_intervals',
        lambda self, level: (TradingInterval(level, 'test', 1, 0),))


def test_progress_advances_only_after_the_same_days_policy(monkeypatch):
    from wavequant.domain.strategies.market_calculations import MarketDay
    _enable_test_interval(monkeypatch)
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: self.frame.index >= 1)
    percentages, dates = [], []

    def policy(bars, config, **kwargs):
        dates.append(len(bars) - 1)
        assert kwargs['_daily'] is not None
        assert kwargs['_analysis'].frames.read_limit == len(bars)
        assert percentages[-1] == (len(bars) - 1) * 100 // len(_bars())
        return strategy_module.SystemResult([], [], {})

    monkeypatch.setattr(strategy_module, '_generate_system_signals', policy)
    strategy_module.generate_system_signals(_bars(), _config(), progress=percentages.append)
    assert dates == list(range(1, len(_bars())))
    assert percentages == sorted(percentages)
    assert percentages[-1] == 100

def test_signal_progress_keeps_last_detailed_stage():
    from wavequant.interfaces.research_tools.stock_backtest import signal_progress_callbacks

    updates = []
    advance, stage = signal_progress_callbacks(lambda percent, message: updates.append((percent, message)))
    assert advance is not None and stage is not None
    stage('折线 → 一级 → 二级 → 三级（含六态）')
    advance(40)
    advance(80)
    assert updates[-1] == (55, '折线 → 一级 → 二级 → 三级（含六态）')
    assert updates[-2][0] == 45
    stage('第24步：交易信号')
    advance(100)
    assert updates[-1] == (60, '第24步：交易信号')


def test_policy_runs_inside_market_scope_and_all_streams_close(monkeypatch, tmp_path):
    from wavequant.domain.strategies.market_calculations import MarketDay
    _enable_test_interval(monkeypatch)
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: self.frame.index >= 1)
    snapshots, retained = [], []
    recorder = CalculationStepCache(tmp_path, 'daily-boundary', snapshots.append, record_details=True)

    def policy(bars, config, **kwargs):
        analysis = kwargs['_analysis']
        assert analysis.bars == tuple(bars)
        assert not analysis.frames.closed
        assert recorder.steps[23]['status'] == 'running'
        assert recorder.steps[2]['status'] == 'running'
        assert len(list(analysis.frames.iter_rows())) == len(bars)
        assert all(len(list(level.iter_rows())) == len(bars) for level in analysis.levels)
        retained.append(analysis)
        return strategy_module.SystemResult([], [], {})

    monkeypatch.setattr(strategy_module, '_generate_system_signals', policy)
    with observe_steps(recorder):
        strategy_module.generate_system_signals(_bars(), _config())
    assert retained
    assert retained[0].frames.closed
    assert all(level.closed for level in retained[0].levels)
    assert recorder.steps[23]['status'] == 'completed'
    assert recorder.steps[1]['status'] == 'completed'

def test_strategy_consumes_prepared_chart_without_rebuilding_geometry(monkeypatch):
    bars = _bars()
    expected = chart_module.chart_entry_history(bars)
    expected_pivots = lecture_strategy.lecture_pivot_history(bars)
    with prepare_backtest_analysis(bars, _config()) as analysis:
        assert analysis.pivots == expected_pivots

        def forbidden(*args, **kwargs):
            raise AssertionError('strategy rebuilt the prepared chart')

        from wavequant.domain.market_structure.trend_lifecycle import TrendLifecycle

        monkeypatch.setattr(TrendLifecycle, 'advance', forbidden)
        monkeypatch.setattr(chart_module, '_staged_chart_frames', forbidden)
        analysis.buy_days = (True,) * len(bars)
        actual = chart_module.chart_entry_history(bars, _prepared_history=analysis)
        assert actual == expected


def test_closed_schedule_never_requalifies_chart_trends(monkeypatch):
    bars = _bars()
    with prepare_backtest_analysis(bars, _config()) as analysis:
        analysis.buy_days = (False,) * len(bars)

        def forbidden(*args, **kwargs):
            raise AssertionError('closed schedule must not calculate strategy permissions')

        monkeypatch.setattr(chart_module, 'attach_trend_permissions', forbidden)
        shallow, combined, pullback = {}, {}, {}
        history, events = chart_module.chart_entry_history(bars, _prepared_history=analysis,
            shallow_candidate_sink=shallow, combined_candidate_sink=combined, secondary_pullback_sink=pullback)
        assert not any(history.values())
        assert shallow == combined == pullback == {}
        assert sorted(events, key=repr) == sorted(analysis.lifecycle_events, key=repr)


def test_chart_strategy_work_is_limited_to_open_dates_and_previous_day(monkeypatch):
    bars = _bars()
    with prepare_backtest_analysis(bars, _config()) as analysis:
        analysis.buy_days = tuple(5 <= index < 8 or 13 <= index < 15 for index in range(len(bars)))
        dates = []
        original = chart_module.attach_trend_permissions

        def record(prepared, prefix, **kwargs):
            dates.append(len(prefix) - 1)
            return original(prepared, prefix, **kwargs)

        monkeypatch.setattr(chart_module, 'attach_trend_permissions', record)
        chart_module.chart_entry_history(bars, _prepared_history=analysis)
        assert dates
        assert set(dates) <= {4, 5, 6, 7, 12, 13, 14}
        assert {4, 12} <= set(dates)


def test_minute_replay_keeps_checkpoint_route_without_restarting_daily_analysis(monkeypatch):
    import wavequant.domain.strategies.backtest_analysis as analysis_module

    def forbidden(*args, **kwargs):
        raise AssertionError('minute replay restarted full daily preparation')

    def policy(bars, config, **kwargs):
        assert kwargs['chart_history_cache']['source_bars'] == tuple(bars)
        assert '_analysis' not in kwargs
        return strategy_module.SystemResult([], [], {})

    monkeypatch.setattr(analysis_module, 'prepare_backtest_analysis', forbidden)
    monkeypatch.setattr(strategy_module, '_generate_system_signals', policy)
    bars = _bars()
    strategy_module.generate_system_signals(bars, _config(), chart_history_cache={'source_bars': tuple(bars)})


def test_prepared_streams_close_when_policy_fails(monkeypatch):
    import pytest

    _enable_test_interval(monkeypatch)
    from wavequant.domain.strategies.market_calculations import MarketDay
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: True)
    retained = []

    def policy(bars, config, **kwargs):
        retained.append(kwargs['_analysis'])
        raise RuntimeError('policy failed')

    monkeypatch.setattr(strategy_module, '_generate_system_signals', policy)
    with pytest.raises(RuntimeError, match='policy failed'):
        strategy_module.generate_system_signals(_bars(), _config())
    assert retained[0].frames.closed
    assert all(level.closed for level in retained[0].levels)
