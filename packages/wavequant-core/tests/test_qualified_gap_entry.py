"""Qualified session gaps share minute/open execution without a new N prerequisite."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.application.analytics.trade_evidence import enrich_ledger
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.domain.strategies.trading_intervals import TradingInterval
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar


def sample(level=2):
    rows = [(6, 7, 5, 6, 1000000), (18, 20, 17, 19, 1000000),
        (10, 11, 9, 10, 1000000), (10, 10.8, 9.8, 10.5, 1000000),
        (11, 12, 11, 11.8, 1000000)]
    bars = [Bar(datetime(2026, 9, 11) + timedelta(days=i), 'sz.001216', *row) for i, row in enumerate(rows)]
    event = dict(event='trend_lifecycle_state', state='空多交替', bar_index=4,
        timestamp=bars[-1].timestamp.isoformat(), symbol=bars[-1].symbol, trend_level=level,
        source_path='trend', available_index=4, flip_confirmed=True, flip_known_index=1,
        flip_index=1, counter_index=2, alternation_index=4, counter_price=9, breakout_level=20)
    config = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points', buy_point_definition='whole_flip_wave_v3', buy_interval_level=level)
    return bars, SystemResult([], [event], {}), config


def resolve(bars, generated, config, loader=None, **kwargs):
    return resolve_consolidation_entries(bars, generated, config, loader,
        daily_fallback=False, only_index=len(bars) - 1, **kwargs)


@pytest.mark.parametrize('level', [1, 2, 3])
def test_confirmed_session_without_minutes_buys_at_open_and_keeps_account_guards(level):
    bars, generated, strategy = sample(level)
    resolved, executions, fallbacks = resolve(bars, generated, strategy)
    signal = resolved.signals[0]
    entry = executions[signal.symbol, 4]
    assert signal.trigger_timestamp.hour == 9 and entry['price'] == 11
    assert entry['timing']['qualification_model'] == 'confirmation_day_full_session'
    assert fallbacks[0]['execution_model'] == 'same_day_open'
    config = StrategyConfig(entry_at_close=True, slippage_bps_per_side=0)
    account = run_portfolio({signal.symbol: bars}, resolved.signals, config, entry_executions=executions)
    order = account.orders[0]
    assert order['status'] == 'filled' and order['price'] == 11
    assert order['execution_timestamp'] == '2026-09-15T09:30:00'
    enrich_ledger(bars, account, resolved, asdict(strategy))
    assert all(condition['passed'] is not False for condition in order['entry_conditions'])
    expensive = replace(signal, minimum_reward_risk=100)
    refused = run_portfolio({signal.symbol: bars}, [expensive], config,
        entry_executions={(signal.symbol, 4): dict(entry, signal=expensive)})
    assert refused.orders[0]['reason'] == 'insufficient_net_reward_risk'
    refused = run_portfolio({signal.symbol: bars}, resolved.signals, config,
        entry_executions={(signal.symbol, 4): dict(entry, buyable=False)})
    assert refused.orders[0]['status'] == 'cancelled'


def minutes(bars):
    return [MinuteBar(bars[-1].timestamp.replace(hour=9, minute=35), 11, 11.5, 11, 11.4, 50000),
        MinuteBar(bars[-1].timestamp.replace(hour=9, minute=40), 11.4, 12, 11.4, 11.8, 50000),
        MinuteBar(bars[-1].timestamp.replace(hour=9, minute=45), 11.8, 12, 8, 9, 900000)]


def test_minutes_buy_after_rising_confirmation_and_later_decline_cannot_erase_fill():
    bars, generated, strategy = sample()
    minute = minutes(bars)
    result, executions, fallbacks = resolve(bars, generated, strategy, lambda _: minute)
    entry = executions[bars[-1].symbol, 4]
    assert entry['price'] == 11.4
    assert entry['timing']['decision_timestamp'].endswith('09:35:00')
    assert entry['timing']['execution_timestamp'].endswith('09:35:00')
    assert entry['remaining_low'] == 8 and not fallbacks
    account = run_portfolio({bars[0].symbol: bars}, result.signals,
        StrategyConfig(slippage_bps_per_side=0), entry_executions=executions)
    assert account.orders[0]['status'] == 'filled' and account.orders[0]['price'] == 11.4
    assert len(result.signals) == 1


@pytest.mark.parametrize('change', ['low', 'flat'])
def test_available_minutes_without_high_open_rise_do_not_fall_back_to_open(change):
    bars, generated, strategy = sample()
    minute = minutes(bars)
    if change == 'low':
        minute[0] = replace(minute[0], low=10.7)
    else:
        minute = [replace(row, low=11, close=11) for row in minute]
    result, executions, fallbacks = resolve(bars, generated, strategy, lambda _: minute)
    assert not result.signals and not executions and not fallbacks


@pytest.mark.parametrize('change', ['equal', 'no_permission', 'closed', 'wrong_level'])
def test_outside_permission_or_non_strict_gap_does_not_request_minutes(change):
    bars, generated, strategy = sample()
    if change == 'equal': bars[-1] = replace(bars[-1], open=bars[-2].high, low=bars[-2].high)
    elif change == 'no_permission': generated.audit.clear()
    elif change == 'wrong_level': strategy = replace(strategy, buy_interval_level=3)
    else:
        intervals = (TradingInterval(2, 'trend', 3, 1, end_index=4, start_low_price=9),)
        # No remaining eligible day: test the shared resolver without the causal N minute continuation.
        from wavequant.application.analytics.intraday_entry import _qualified_gap_entries
        result = _qualified_gap_entries(bars, generated, strategy,
            lambda _: pytest.fail('closed interval must not load minutes'), only_index=4,
            intervals=intervals)
        assert not result[1] and not result[3]
        return
    result, executions, fallbacks = resolve(bars, generated, strategy,
        lambda _: pytest.fail('ineligible day must not load minutes'))
    assert not result.signals and not executions and not fallbacks


def test_missing_coverage_falls_back_but_invalid_minutes_raise():
    bars, generated, strategy = sample()
    def unavailable(_):
        raise MinuteCoverageError('2026-09-15', None, None, 'test')
    result, executions, fallbacks = resolve(bars, generated, strategy, unavailable)
    assert executions[bars[-1].symbol, 4]['price'] == bars[-1].open and fallbacks
    def invalid(_):
        raise ValueError('mismatched source')
    with pytest.raises(ValueError, match='mismatched source'):
        resolve(bars, generated, strategy, invalid)


def test_huaci_september15_double_volume_without_minutes_gets_close_fill():
    fixtures = Path(__file__).parent / 'fixtures'
    early = json.loads((fixtures / 'huaci_2024_key_break.json').read_text(encoding='utf-8'))
    later = json.loads((fixtures / 'huaci_wave_continuation.json').read_text(encoding='utf-8'))
    rows = {row[0]: row[:7] for row in early['bars']}
    rows.update({row[0]: row[:7] for row in later['bars']})
    bars = [Bar(datetime.fromisoformat(day), later['symbol'], *row[1:])
        for day, row in sorted(rows.items()) if day <= '2026-09-15']
    i = len(bars) - 1
    # Frozen published lifecycle coordinates from the unchanged global daily replay.
    event = dict(event='trend_lifecycle_state', state='空多交替', bar_index=i,
        timestamp=bars[-1].timestamp.isoformat(), symbol=bars[-1].symbol, trend_level=2,
        source_path='secondary-reversal-lecture-0', available_index=i, flip_confirmed=True,
        flip_known_index=996, flip_index=995, counter_index=1157, alternation_index=i,
        counter_price=14.444080740828408, breakout_level=26.835505065126593)
    strategy = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points', buy_point_definition='whole_flip_wave_v3')
    assert isinstance(event['source_path'], str) and isinstance(event['counter_price'], float)
    window = TradingInterval(2, event['source_path'], i, 995,
        start_low_index=1157, start_low_price=event['counter_price'])
    result, executions, _ = resolve(bars, SystemResult([], [event], {}), strategy,
        prepared_analysis=SimpleNamespace(bars=tuple(bars), trading_intervals=(window,)), trading_window_start=i)
    assert len(result.signals) == 1
    account = run_portfolio({bars[-1].symbol: bars}, result.signals,
        StrategyConfig(entry_at_close=True, slippage_bps_per_side=0), entry_executions=executions,
        start='2026-09-15', end='2026-09-15')
    assert account.orders[0]['status'] == 'filled'
    assert not executions
    assert result.signals[0].reason == 'qualified_double_volume_breakout_close'
    assert account.orders[0]['price'] == pytest.approx(bars[-1].close)
    assert account.orders[0]['execution_model'] == 'same_day_close'
    assert account.orders[0]['execution_timestamp'] == '2026-09-15T15:00:00'


@pytest.mark.parametrize('minute_enabled', [False, True])
def test_streaming_coordinator_uses_gap_entry_even_without_old_n_minute_option(monkeypatch, minute_enabled):
    from wavequant.interfaces.research_tools import streaming_backtest as module
    from wavequant.domain.strategies.integrated_strategy import DailySignalState
    bars, confirmation, strategy = sample()
    window = TradingInterval(2, 'trend', 4, 1, start_low_index=2, start_low_price=9)
    def daily(market, config, *, on_day, **options):
        state = DailySignalState(window_start=4)
        generated = SystemResult([], [], {})
        for i in range(len(market)):
            generated.audit = list(confirmation.audit) if i == 4 else []
            analysis = SimpleNamespace(bars=tuple(market[:i + 1]), buy_days=(False,) * i + (i == 4,),
                trading_intervals=(window,) if i == 4 else (), pivots=({}, {}, {}, set()))
            on_day(SimpleNamespace(frame=SimpleNamespace(index=i)), analysis, generated, state)
        return generated
    monkeypatch.setattr(module, 'daily_signals', daily)
    monkeypatch.setattr(module, 'publish_market_preview', lambda *args: None)
    monkeypatch.setattr(module, 'single_stock_result',
        lambda *args, account_result, **kwargs: dict(orders=account_result.orders))
    generated, account = module.streaming_stock_result(bars, asdict(strategy),
        dict(entry_at_close=True, consolidation_entry_intraday=minute_enabled, slippage_bps_per_side=0))
    assert len(generated.signals) == 1
    assert account['orders'][0]['status'] == 'filled' and account['orders'][0]['price'] == 11
    assert account['orders'][0]['execution_timestamp'].endswith('09:30:00')
