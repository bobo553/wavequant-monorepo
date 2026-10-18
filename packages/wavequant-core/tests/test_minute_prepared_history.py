from dataclasses import replace
import importlib

import pytest
from tests.test_lecture_strategy import history
from wavequant.domain.strategies.backtest_analysis import prepare_backtest_analysis
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.market_structure.bull_alternation_confirmation import AlternationPolicy

chart = importlib.import_module('wavequant.domain.strategies.chart_entry_history')


def test_no_trade_candidate_or_position_never_reads_minutes():
    from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
    from wavequant.application.analytics.backtest import run_portfolio
    from wavequant.domain.models.config import StrategyConfig
    from wavequant.domain.strategies.integrated_strategy import SystemResult

    bars = history(43, 30)

    def forbidden(bar):
        raise AssertionError('ordinary daily analysis must not request minutes')

    generated = SystemResult([], [], {})
    result, executions, fallbacks = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), forbidden, daily_fallback=False)
    assert result.signals == []
    assert result.audit == []
    assert executions == {}
    assert fallbacks == []
    run_portfolio({bars[0].symbol: bars}, [],
        StrategyConfig(staged_exit_intraday=True), minute_loader=forbidden)


def test_full_holding_days_use_daily_prices_without_minutes():
    from tests.test_holding_drawdown import bar, fill, measure

    def forbidden(bar):
        raise AssertionError('a complete holding day must use daily prices')

    episodes = measure([bar(1), bar(2, low=8), bar(3)],
        [fill(1, model='same_day_close'), fill(3, 'SELL')], minute_loader=forbidden)
    assert episodes[0]['coverage'] == 'complete'
    assert episodes[0]['resolutions'] == ['daily_ohlc']


@pytest.mark.parametrize('n_enabled', [False, True])
@pytest.mark.parametrize('window', [0, 70])
def test_borrowed_daily_geometry_matches_cold_minutes(monkeypatch, n_enabled, window):
    bars = history(18, 90)
    config = SystemStrategy(n_target_trend_confirmation_enabled=n_enabled)
    policy = AlternationPolicy(config.structure_window, config.pattern_ttl,
        config.volume_lookback, config.shadow_fraction, config.strict_n_attack_quality,
        config.squeeze_pullback_entries)
    days = tuple(i >= window for i in range(len(bars)))
    cache = {}
    original = chart._staged_chart_frames
    starts = []
    def track(*args, **kwargs):
        starts.append(kwargs['resume_start'])
        return original(*args, **kwargs)
    with prepare_backtest_analysis(bars, config) as analysis:
        for factor in (0.0, 0.5, 1.0):
            bar = bars[-1]
            partial = [*bars[:-1], replace(bar,
                high=bar.open+(bar.high-bar.open)*factor,
                low=bar.open+(bar.low-bar.open)*factor,
                close=bar.open+(bar.close-bar.open)*factor,
                volume=bar.volume*factor)]
            def run(reuse):
                sinks = [{}, {}, {}]
                result = chart.chart_entry_history(partial,
                    shallow_candidate_sink=sinks[0], combined_candidate_sink=sinks[1],
                    secondary_pullback_sink=sinks[2],
                    prefix_cache=cache if reuse else {},
                    n_target_trend_confirmation_enabled=n_enabled,
                    _confirmation_policy=policy, _entry_days=days,
                    _minute_analysis=analysis if reuse else None)
                return result, sinks
            expected = run(False)
            with monkeypatch.context() as patch:
                patch.setattr(chart, '_staged_chart_frames', track)
                actual = run(True)
            assert actual == expected
    assert starts == [len(bars)-1]*3


def test_changed_completed_bar_cannot_borrow_history():
    bars = history(18, 30)
    config = SystemStrategy()
    policy = AlternationPolicy(config.structure_window, config.pattern_ttl,
        config.volume_lookback, config.shadow_fraction, config.strict_n_attack_quality,
        config.squeeze_pullback_entries)
    with prepare_backtest_analysis(bars, config) as analysis:
        changed = [replace(bars[0], close=bars[0].close+.01), *bars[1:]]
        with pytest.raises(ValueError, match='identical completed bars'):
            chart.chart_entry_history(changed, _minute_analysis=analysis, _confirmation_policy=policy)


@pytest.mark.parametrize('n_enabled', [False, True])
def test_minute_cold_stream_and_warm_revisions_match_batch(n_enabled):
    from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
    bars = history(43, 80)
    cache = {}
    for factor in (0.0, 0.5, 1.0):
        bar = bars[-1]
        partial = [*bars[:-1], replace(bar,
            high=bar.open+(bar.high-bar.open)*factor,
            low=bar.open+(bar.low-bar.open)*factor,
            close=bar.open+(bar.close-bar.open)*factor, volume=bar.volume*factor)]
        expected_declines, actual_declines = {}, {}
        expected = hierarchical_history(partial, decline_sink=expected_declines,
            n_target_trend_confirmation_enabled=n_enabled)
        actual = hierarchical_history(partial, decline_sink=actual_declines, prefix_cache=cache,
            n_target_trend_confirmation_enabled=n_enabled, _stream_cold=True)
        assert actual == expected
        assert actual_declines == expected_declines


def test_sparse_minute_days_resume_from_previous_unfinished_day(monkeypatch):
    from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
    from wavequant.domain.strategies.hierarchy_stream import HierarchyHistoryStream
    bars = history(43, 95)
    cache = {}
    hierarchical_history(bars[:60], prefix_cache=cache, decline_sink={}, _stream_cold=True)
    expected_declines, actual_declines = {}, {}
    expected = hierarchical_history(bars, decline_sink=expected_declines)

    def forbidden(*args, **kwargs):
        raise AssertionError('a matching older checkpoint must not cold-start the minute history')

    monkeypatch.setattr(HierarchyHistoryStream, '__init__', forbidden)
    actual = hierarchical_history(bars, prefix_cache=cache, decline_sink=actual_declines, _stream_cold=True)
    assert actual == expected
    assert actual_declines == expected_declines


def test_minute_phase_is_published_before_day_completion(monkeypatch):
    from dataclasses import asdict
    from types import SimpleNamespace
    from wavequant.interfaces.research_tools import streaming_backtest as module
    from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
    from wavequant.domain.strategies.market_calculations import MarketDay
    from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult

    bars = history(43, 2)
    notices = []
    def daily(market, strategy, *, on_day, progress, stage):
        result = SystemResult([], [], {})
        state = DailySignalState()
        for index in range(len(market)):
            frame = ChartDrawingFrame(index, 0, [], {'strokes': []})
            day = MarketDay(frame, ({'strokes': []},)*3, (), (True,)*3)
            analysis = SimpleNamespace(buy_days=(True,)*(index+1), pivots=({}, {}, {}, set()))
            on_day(day, analysis, result, state)
            progress((index+1)*100//len(market))
        return result
    def minute(prefix, generated, strategy, loader, **options):
        options['stage']('核验分钟入场 · 1/47')
        assert notices[-1] == ((len(prefix)-1)*100//len(bars), '核验分钟入场 · 1/47')
        return generated, {}, []
    monkeypatch.setattr(module, 'daily_signals', daily)
    monkeypatch.setattr(module, 'resolve_consolidation_entries', minute)
    monkeypatch.setattr(module, 'single_stock_result', lambda *args, **kwargs: {})
    module.streaming_stock_result(bars, asdict(SystemStrategy()),
        {'consolidation_entry_intraday': True}, progress=lambda percent, message: notices.append((percent, message)))
    assert max(percent for percent, _ in notices) == 99
