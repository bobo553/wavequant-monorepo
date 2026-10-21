"""Minute trading inherits only the current window's already known daily facts."""

from dataclasses import replace
from typing import cast

import pytest

from .test_n_shape import fixture
from .test_qualified_n_windows import source
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
from wavequant.domain.strategies.backtest_history_reuse import MinuteTradingScope
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingHistory
from wavequant.domain.strategies import integrated_strategy
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.domain.strategies.trading_intervals import TradingInterval
from wavequant.domain.strategies.squeeze_n_round import SqueezeN, SqueezeRoundState


def test_minute_scope_retains_consumed_first_n_from_previous_window():
    analysis = prepared()
    checkpoint = SqueezeRoundState(SqueezeN(0, 1, 2, 3, 3, 10.0), selected_at=3, emitted_at=4)
    scope = MinuteTradingScope.from_analysis(analysis, index=6, window_start=4,
        squeeze_checkpoint=checkpoint)
    assert scope is not None
    assert scope.squeeze_checkpoint == checkpoint
    assert scope.sources == (source(4),)


def prepared(start: int = 4) -> BacktestAnalysis:
    bars = tuple(fixture())
    return BacktestAnalysis(bars, cast(ChartDrawingHistory, None), (), ({}, {}, {}, set()),
        trading_intervals=(TradingInterval(2, 'p', 0, 0, end_index=3),
                           TradingInterval(2, 'p', start, start)),
        buy_days=tuple(index < 3 or index >= start for index in range(len(bars))),
        n_sources=[source(0), source(start), replace(source(start), known_at=6)])


def test_minute_scope_excludes_closed_windows_and_today_sources():
    analysis = prepared()
    scope = MinuteTradingScope.from_analysis(analysis, index=6, window_start=4)
    assert scope is not None
    assert scope.active_scopes == frozenset({('p', 4)})
    assert scope.sources == (source(4),)
    assert scope.buy_days == (False, False, False, False, True, True, True)
    assert scope.scan_days == scope.buy_days


def test_close_confirmed_qualification_does_not_load_earlier_minutes():
    analysis = prepared(6)
    assert MinuteTradingScope.from_analysis(analysis, index=6, window_start=6) is None
    result = SystemResult([], [], {})

    def forbidden(_bar):
        raise AssertionError('the current close has not qualified earlier minutes')

    resolved, executions, fallbacks = resolve_consolidation_entries(list(analysis.bars), result,
        SystemStrategy(), forbidden, daily_fallback=False, only_index=6,
        prepared_analysis=analysis, trading_window_start=6)
    assert resolved is result and executions == {} and fallbacks == []


def test_minute_scope_rejects_changed_history_but_allows_partial_last_prices():
    analysis = prepared()
    scope = MinuteTradingScope.from_analysis(analysis, index=6, window_start=4)
    assert scope is not None
    partial = list(analysis.bars)
    partial[-1] = replace(partial[-1], close=partial[-1].open, volume=partial[-1].volume / 2)
    scope.require_input(partial)
    partial[0] = replace(partial[0], high=partial[0].high + 1)
    with pytest.raises(ValueError, match='completed market history'):
        scope.require_input(partial)
    partial = list(analysis.bars)
    partial[-1] = replace(partial[-1], open=partial[-1].open + .01)
    with pytest.raises(ValueError, match='unfinished price'):
        scope.require_input(partial)


def test_minute_wrapper_gives_every_trading_observer_the_window(monkeypatch):
    analysis = prepared()
    scope = MinuteTradingScope.from_analysis(analysis, index=6, window_start=4)
    assert scope is not None
    state_seen = []

    def capture(bars, config, **kwargs):
        assert kwargs['_minute_scope'] is scope
        assert kwargs.get('_analysis') is None
        state = kwargs['_daily']
        state_seen.append(state)
        for observer in (state.a_wave_stream, state.bottom_target_history,
                         state.reformed_target_history, state.reformation_history,
                         state.projection_history, state.secondary_resistance_history,
                         state.risk_histories):
            assert observer is not None and observer.start_index == 4
        return SystemResult([], [], {})

    monkeypatch.setattr(integrated_strategy, '_generate_system_signals', capture)
    integrated_strategy.generate_system_signals(analysis.bars, SystemStrategy(), _minute_scope=scope)
    assert len(state_seen) == 1


def test_minute_does_not_discover_historical_n_outside_inherited_sources(monkeypatch):
    from wavequant.domain.strategies import folded_n, hierarchical_n

    analysis = prepared()
    scope = MinuteTradingScope.from_analysis(analysis, index=6, window_start=4)
    assert scope is not None
    scope = replace(scope, sources=())

    def forbidden(*args, **kwargs):
        raise AssertionError('minute replay must not invent N from an unrestricted historical prefix')

    monkeypatch.setattr(integrated_strategy, 'observe_n', forbidden)
    monkeypatch.setattr(hierarchical_n, 'hierarchical_n_candidates', forbidden)
    monkeypatch.setattr(folded_n, 'folded_positive_n_candidates', forbidden)
    monkeypatch.setattr(folded_n, 'folded_inverse_n_candidates', forbidden)
    strategy = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                              buy_point_definition='whole_flip_wave_v3')
    result = integrated_strategy.generate_system_signals(analysis.bars, strategy,
        _minute_scope=scope, chart_history_cache={'source_bars': analysis.bars})
    assert not any(row['event'] == 'n_completed' for row in result.audit)
