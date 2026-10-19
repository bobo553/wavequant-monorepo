"""Qualified gap entries use the account's existing observed nonflat limit policy."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from wavequant.application.analytics.backtest import BacktestResult, run_portfolio
from wavequant.application.analytics.intraday_entry import EntryExecution, resolve_consolidation_entries
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.infrastructure.market_data.minute import MinuteBar


FACTOR = 1.1153730301798
LIMIT_PRICE = 18.325578885854114
STOP = 14.444080740828408
TARGET = 26.835505065126593


def gap_session() -> tuple[list[Bar], SystemResult, SystemStrategy, list[MinuteBar]]:
    # The final two daily bars and two minute bars are from the saved 2026-09-15
    # AKShare result. Earlier bars only provide minimal qualification coordinates.
    rows = [(8, 9, 7, 8, 1000000), (24, TARGET, 23, 25, 1000000),
        (15, 15.5, STOP, 15, 1000000),
        (16.23983131941789, 16.819825295111382, 16.016756713381927, 16.66367307088621, 2029500),
        (16.987131249638352, LIMIT_PRICE, 16.987131249638352, LIMIT_PRICE, 4584900)]
    bars = [Bar(datetime(2026, 9, 11) + timedelta(days=i), 'sz.001216', *row,
        adjustment_factor=FACTOR, buyable=True, close_buyable=i != 4,
        nonflat_close_buyable=True, raw_is_st=False, raw_trading_active=True)
        for i, row in enumerate(rows)]
    event = dict(event='trend_lifecycle_state', state='空多交替', bar_index=4,
        timestamp=bars[-1].timestamp.isoformat(), symbol=bars[-1].symbol, trend_level=2,
        source_path='secondary-reversal-lecture-0', available_index=4, flip_confirmed=True,
        flip_known_index=1, flip_index=1, counter_index=2, alternation_index=4,
        counter_price=STOP, breakout_level=TARGET)
    strategy = SystemStrategy(pivot_mode='lecture_causal', entry_policy='hierarchical_two_buy_points',
        buy_point_definition='whole_flip_wave_v3', buy_interval_level=2)
    stamp = datetime(2026, 9, 15, 9, 35, tzinfo=timezone(timedelta(hours=8)))
    minute = [MinuteBar(stamp, 15.23, 16.43, 15.23, 16.43, 1938300),
        MinuteBar(stamp + timedelta(minutes=5), 16.43, 16.43, 16.43, 16.43, 298400)]
    return bars, SystemResult([], [event], {}), strategy, minute


def execute(
    bars: list[Bar], generated: SystemResult, strategy: SystemStrategy, minute: list[MinuteBar], *,
    allow_nonflat: bool = True,
) -> tuple[BacktestResult, dict[tuple[str, int], EntryExecution]]:
    resolved, executions, fallbacks = resolve_consolidation_entries(
        bars, generated, strategy, lambda _: minute, daily_fallback=False,
        qualified_gap_only=True, only_index=4)
    # Keep the actual sizing and costs; a large synthetic budget must not hide
    # rejection below one raw A-share lot.
    config = StrategyConfig(initial_capital=100000, risk_fraction=0.005, max_position_weight=1,
        lot_size=100, minimum_entry_shares=100, max_participation=0.01,
        commission_bps_per_side=3, minimum_commission=5, slippage_bps_per_side=5,
        entry_at_close=True, nonflat_limit_close_fill=allow_nonflat, net_reward_risk_filter=False)
    account = run_portfolio({bars[0].symbol: bars}, resolved.signals, config,
        entry_executions=executions, start='2026-09-15', end='2026-09-15')
    assert not fallbacks
    return account, executions


@pytest.mark.parametrize('allow_nonflat', [True, False])
def test_huaci_first_minute_limit_uses_the_selected_account_policy(allow_nonflat: bool) -> None:
    bars, generated, strategy, minute = gap_session()
    account, executions = execute(bars, generated, strategy, minute, allow_nonflat=allow_nonflat)
    entry = executions[bars[-1].symbol, 4]
    assert entry['buyable'] is False
    assert entry['timing']['observed_nonflat_limit_buyable'] is True
    assert len(account.orders) == 1
    order = account.orders[0]
    assert order['execution_timestamp'] == '2026-09-15T09:35:00+08:00'
    if allow_nonflat:
        assert order['status'] == 'filled', order
        assert order['price'] == LIMIT_PRICE
        assert order['applied_slippage_bps'] == 0
        assert order['fill_assumption'] == 'observed_nonflat_limit_intraday_without_queue_verification'
    else:
        assert order['status'] == 'cancelled'
        assert order['reason'] == 'not_buyable_intraday'


@pytest.mark.parametrize('restriction', ['st', 'suspended', 'no_observed_volume', 'no_next_volume', 'above_limit'])
def test_nonflat_gap_policy_does_not_bypass_market_constraints(restriction: str) -> None:
    bars, generated, strategy, minute = gap_session()
    if restriction == 'st':
        bars[-1] = replace(bars[-1], raw_is_st=True, buyable=False, nonflat_close_buyable=False)
    elif restriction == 'suspended':
        bars[-1] = replace(bars[-1], raw_trading_active=False, buyable=False, nonflat_close_buyable=False)
    elif restriction == 'no_observed_volume':
        minute[0] = replace(minute[0], volume=0)
    elif restriction == 'no_next_volume':
        minute[1] = replace(minute[1], volume=0)
    else:
        minute[1] = replace(minute[1], open=16.44, high=16.44, low=16.44, close=16.44)
    account, executions = execute(bars, generated, strategy, minute)
    assert executions[bars[-1].symbol, 4]['timing']['observed_nonflat_limit_buyable'] is False
    assert account.orders[0]['status'] == 'cancelled'
    assert account.orders[0]['reason'] == 'not_buyable_intraday'


def test_one_price_limit_session_never_creates_a_gap_fill() -> None:
    bars, generated, strategy, minute = gap_session()
    bars[-1] = replace(bars[-1], open=LIMIT_PRICE, low=LIMIT_PRICE,
        buyable=False, nonflat_close_buyable=False)
    minute = [replace(row, open=16.43, high=16.43, low=16.43, close=16.43) for row in minute]
    account, executions = execute(bars, generated, strategy, minute)
    assert not executions and not account.orders


def test_later_volume_and_range_cannot_authorize_the_earlier_minute_fill() -> None:
    bars, generated, strategy, minute = gap_session()
    minute[0] = replace(minute[0], volume=0)
    # A later traded range is unavailable at the first 09:35 decision boundary.
    minute.append(MinuteBar(minute[-1].timestamp + timedelta(minutes=5),
        16.43, 16.43, 15.23, 16.43, 1000000))
    account, executions = execute(bars, generated, strategy, minute)
    timing = executions[bars[-1].symbol, 4]['timing']
    assert timing['decision_timestamp'] == '2026-09-15T09:35:00+08:00'
    assert timing['observed_nonflat_limit_buyable'] is False
    assert account.orders[0]['status'] == 'cancelled'
    assert account.orders[0]['reason'] == 'not_buyable_intraday'
