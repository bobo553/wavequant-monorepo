"""The market cannot advance until that date's account and snapshot are complete."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from wavequant.application.analytics import backtest as account_module
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies import daily_strategy as daily_module
from wavequant.domain.strategies import integrated_strategy as policy
from wavequant.domain.strategies.chart_drawing_history import ChartDrawingFrame
from wavequant.domain.strategies.integrated_strategy import DailySignalState, SystemResult, SystemStrategy
from wavequant.domain.strategies.market_calculations import MarketDay
from wavequant.domain.strategies.market_preview import observe_market_preview
from wavequant.interfaces.research_tools import streaming_backtest as module


def test_supplied_old_signal_result_cannot_bypass_the_whole_wave_daily_account(monkeypatch):
    from wavequant.interfaces.research_tools import stock_backtest

    bars = [Bar(datetime(2026, 1, 5), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    forged = SystemResult([], [dict(event='obsolete_fixture', bar_index=0)], {'trading_interval_count': 99})
    expected = {'origin': 'same_day_eligibility_and_account'}
    calls = []

    def streaming(market, strategy, execution, **options):
        calls.append((market, strategy, execution))
        return SystemResult([], [], {}), expected

    def forbidden(*args, **kwargs):
        raise AssertionError('supplied final signals must not open the batch account bypass')

    monkeypatch.setattr(module, 'streaming_stock_result', streaming)
    monkeypatch.setattr(stock_backtest, 'run_portfolio', forbidden)
    assert stock_backtest.single_stock_result(bars, asdict(config), {}, forged) is expected
    assert calls == [(bars, asdict(config), {})]


@pytest.mark.parametrize('minute_enabled', [False, True])
def test_daily_coordinator_publishes_fills_before_advancing_market(monkeypatch, minute_enabled):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(5)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'bull', 15)
    sell = replace(buy, timestamp=bars[3].timestamp, bar_index=3, side='EXIT', reason='risk')
    previews, minutes, percentages, account_risk = [], [], [], []

    def preview(market, day, result):
        index = day.frame.index
        assert len(result['equity']) == index + 1
        assert result['equity'][-1]['timestamp'] == market[index].timestamp.isoformat()
        previews.append((index, deepcopy(result)))

    def daily(market, strategy, *, on_day, progress, stage):
        state = DailySignalState()
        for index, bar in enumerate(market):
            state.signals.extend(signal for signal in (buy, sell) if signal.bar_index == index)
            generated = SystemResult(list(state.signals), [], {'trading_interval_count': 1})
            frame = ChartDrawingFrame(index, 0, [], {'strokes': []})
            day = MarketDay(frame, ({'strokes': []},) * 3, (), (index in (1, 2, 3),) * 3)
            analysis = SimpleNamespace(buy_days=tuple(i in (1, 2, 3) for i in range(index + 1)), scan_days=(index in (1, 2, 3, 4),), pivots=({}, {}, {}, set()))
            on_day(day, analysis, generated, state)
            account_risk.append(state.account_risk_required)
            assert [day for day, _ in previews] == list(range(index + 1))
            progress((index + 1) * 100 // len(market))
        return generated

    def minute(prefix, generated, strategy, loader, **options):
        assert options['only_index'] == len(prefix) - 1
        minutes.append(options['only_index'])
        return generated, {}, []

    def format_result(market, strategy, execution, generated, *, account_result, entry_fallbacks_override):
        assert account_result is not None, 'formatting must not start a second account replay'
        return dict(orders=account_result.orders, equity=account_result.equity)

    monkeypatch.setattr(module, 'daily_signals', daily)
    monkeypatch.setattr(module, 'resolve_consolidation_entries', minute)
    monkeypatch.setattr(module, 'single_stock_result', format_result)
    with observe_market_preview(preview):
        generated, result = module.streaming_stock_result(bars, asdict(config),
            dict(entry_at_close=False, net_reward_risk_filter=False,
                 consolidation_entry_intraday=minute_enabled),
            progress=lambda percent, stage: percentages.append((percent, previews[-1][0])))
    assert [signal.side for signal in generated.signals] == ['LONG', 'EXIT']
    assert [(row['side'], row['timestamp']) for row in result['orders'] if row['status'] == 'filled'] == [
        ('BUY', bars[2].timestamp.isoformat()), ('SELL', bars[4].timestamp.isoformat())]
    assert not previews[1][1]['orders'], 'next-open entries must remain pending at signal close'
    assert previews[2][1]['orders'][0]['side'] == 'BUY'
    assert len(result['equity']) == len(bars)
    assert [index for _, index in percentages] == list(range(len(bars)))
    assert minutes == ([1, 2, 3] if minute_enabled else [])
    assert account_risk == [False, True, True, True, False], (
        'pending entry, holding and pending EXIT require risk work until the exit actually fills')


def test_consumer_error_stops_before_the_next_market_date(monkeypatch):
    from wavequant.domain.strategies.daily_strategy import daily_signals
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9, 10, 100) for i in range(4)]
    monkeypatch.setattr(MarketDay, 'permits_buy', lambda self, level: False)
    dates, progress = [], []

    def consumer(day, analysis, generated, state):
        dates.append(day.frame.index)
        if day.frame.index == 1:
            raise RuntimeError('account failed')

    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    with pytest.raises(RuntimeError, match='account failed'):
        daily_signals(bars, config, on_day=consumer, progress=progress.append)
    assert dates == [0, 1]
    assert progress == [0, 25], 'an unfinished day must not advance published progress'


def test_preview_consumer_error_closes_account_risk_cursors(monkeypatch):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(3)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    visited, closed = [], []

    class RiskCursor:
        def __init__(self, name):
            self.name = name

        def close(self):
            closed.append(self.name)

    monkeypatch.setattr(account_module, 'PressureExitStream', lambda *args, **kwargs: RiskCursor('pressure'))
    monkeypatch.setattr(account_module, 'TrendFlipExitStream', lambda *args, **kwargs: RiskCursor('trend'))

    def daily(market, strategy, *, on_day, progress, stage):
        state = DailySignalState()
        generated = SystemResult([], [], {})
        for index in range(len(market)):
            visited.append(index)
            frame = ChartDrawingFrame(index, 0, [], {'strokes': []})
            day = MarketDay(frame, ({'strokes': []},) * 3, (), (False,) * 3)
            analysis = SimpleNamespace(buy_days=(False,), scan_days=(False,), pivots=({}, {}, {}, set()))
            on_day(day, analysis, generated, state)
        return generated

    def preview(market, day, result):
        assert day.frame.index == 0
        raise RuntimeError('preview consumer failed')

    monkeypatch.setattr(module, 'daily_signals', daily)
    with observe_market_preview(preview), pytest.raises(RuntimeError, match='preview consumer failed'):
        module.streaming_stock_result(bars, asdict(config),
            dict(pressure_adverse_exit=True, trend_flip_adverse_exit=True))
    assert visited == [0], 'a failed consumer must not advance another market date'
    assert sorted(closed) == ['pressure', 'trend'], 'both account cursors must close exactly once on failure'


def _stub_daily_market(monkeypatch, scan_days, signals):
    generated_days = []

    @contextmanager
    def prepare(market, strategy, *, on_day):
        for index in range(len(market)):
            frame = ChartDrawingFrame(index, 0, [], {'strokes': []})
            day = MarketDay(frame, ({'strokes': []},) * 3, (), (scan_days[index],) * 3)
            analysis = SimpleNamespace(bars=market[:index + 1], buy_days=scan_days[:index + 1],
                scan_days=scan_days[:index + 1], hierarchy=None, trading_intervals=(), calculations=None,
                pivots=({}, {}, {}, set()), structural_n_events=[], one_p_breakout_events=[])
            on_day(day, analysis)
        yield analysis

    def generate(market, strategy, *, _daily, **options):
        index = len(market) - 1
        generated_days.append(index)
        _daily.signals.extend(signal for signal in signals if signal.bar_index == index)
        _daily.has_buy_signal = _daily.has_buy_signal or any(signal.side == 'LONG' for signal in _daily.signals)
        return SystemResult(list(_daily.signals), [], {'rows': index + 1})

    monkeypatch.setattr(daily_module, 'prepare_backtest_analysis', prepare)
    monkeypatch.setattr(policy, '_generate_system_signals', generate)
    return generated_days


def test_ineligible_gap_does_not_retry_a_blocked_qualification_close(monkeypatch):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(7)]
    bars[4] = replace(bars[4], sellable=False)
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'bull', 15)
    sell = replace(buy, timestamp=bars[3].timestamp, bar_index=3, side='EXIT', reason='risk')
    generated_days = _stub_daily_market(monkeypatch, tuple(1 <= i <= 3 for i in range(len(bars))), (buy, sell))
    account_risk = []
    real_daily = module.daily_signals

    def daily(market, strategy, *, on_day, **options):
        def consume(day, analysis, generated, state):
            on_day(day, analysis, generated, state)
            account_risk.append(state.account_risk_required)

        return real_daily(market, strategy, on_day=consume, **options)

    def format_result(market, strategy, execution, generated, *, account_result, entry_fallbacks_override):
        return dict(orders=account_result.orders, equity=account_result.equity)

    monkeypatch.setattr(module, 'daily_signals', daily)
    monkeypatch.setattr(module, 'single_stock_result', format_result)
    generated, result = module.streaming_stock_result(bars, asdict(config),
        dict(entry_at_close=False, net_reward_risk_filter=False))

    assert account_risk == [False, True, True, True, False, False, False]
    assert generated_days == [1, 2, 3]
    assert [signal.side for signal in generated.signals] == ['LONG', 'EXIT']
    assert [row['positions'] for row in result['equity']] == [0, 0, 1, 1, 1, 1, 1]
    assert any(row['side'] == 'SELL' and row['status'] == 'deferred' and row['reason'] == 'not_sellable'
        and row['timestamp'] == bars[4].timestamp.isoformat() for row in result['orders'])
    assert [(row['side'], row['timestamp']) for row in result['orders'] if row['status'] == 'filled'] == [
        ('BUY', bars[2].timestamp.isoformat())]


def test_observer_callback_does_not_claim_an_empty_account(monkeypatch):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(7)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'bull', 15)
    sell = replace(buy, timestamp=bars[3].timestamp, bar_index=3, side='EXIT', reason='risk')
    generated_days = _stub_daily_market(monkeypatch, tuple(i == 1 for i in range(len(bars))), (buy, sell))
    observed = []

    def observer(day, analysis, generated, state):
        observed.append((day.frame.index, state.account_risk_required))

    result = daily_module.daily_signals(bars, config, on_day=observer)
    assert observed == [(index, None) for index in range(len(bars))]
    assert generated_days == [1]
    assert [signal.side for signal in result.signals] == ['LONG']


def test_reopened_eligibility_starts_new_target_streams_without_backfilling_gap(monkeypatch):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 20, 8, 10, 100)
            for i in range(8)]
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
                            buy_point_definition='whole_flip_wave_v3')
    _stub_daily_market(monkeypatch, (False, True, True, False, False, True, True, False), ())
    observations = []

    def generate(market, strategy, *, _daily, **options):
        observations.append((len(market) - 1, _daily.window_start, _daily.a_wave_stream,
                             dict(_daily.bottom_hits)))
        _daily.bottom_hits[1] = {'one_p'}
        return SystemResult([], [], {})

    def observer(day, analysis, generated, state):
        state.account_risk_required = True
        state.has_buy_signal = True

    monkeypatch.setattr(policy, '_generate_system_signals', generate)
    daily_module.daily_signals(bars, config, on_day=observer)
    assert [(index, start) for index, start, _, _ in observations] == [(1, 1), (2, 1), (5, 5), (6, 5)]
    assert observations[0][2] is observations[1][2]
    assert observations[2][2] is observations[3][2]
    assert observations[0][2] is not observations[2][2]
    assert observations[0][3] == observations[2][3] == {}
