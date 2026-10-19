from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.application.analytics.backtest import AccountDay, portfolio_days, run_portfolio
from wavequant.domain.market_structure.trend_staging import TrendStageRows
from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal


@pytest.mark.parametrize('at_close', [False, True])
def test_live_account_matches_existing_fills_and_keeps_pending_orders(at_close):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 10.5, 9.8, 10.2, 1_000_000,
                close_buyable=True) for i in range(5)]
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'B', 14, 0)
    sell = replace(buy, timestamp=bars[3].timestamp, bar_index=3, side='EXIT', reason='risk')
    config = StrategyConfig(entry_at_close=at_close, net_reward_risk_filter=False)
    expected = run_portfolio({'TEST': bars}, [buy, sell], config)
    snapshots = []
    runner = portfolio_days({'TEST': bars}, [], config, live=True,
        on_day=lambda index, orders, trades, curve: snapshots.append((index, deepcopy(orders), deepcopy(curve))))
    next(runner)
    actual = None
    try:
        for index in range(len(bars)):
            try:
                runner.send(AccountDay(index, [signal for signal in (buy, sell) if signal.bar_index == index],
                    {}, [], {}, {}))
            except StopIteration as completed:
                assert index == len(bars) - 1
                actual = completed.value
    finally:
        runner.close()
    assert actual is not None
    actual_orders, expected_orders = deepcopy(actual.orders), deepcopy(expected.orders)
    for row in (*actual_orders, *expected_orders):
        if 'holding_drawdown' in row:
            row['holding_drawdown'].pop('observation_scope')
    assert actual_orders == expected_orders
    assert actual.orders[-1]['holding_drawdown']['observation_scope'] == 'qualified_sessions'
    assert expected.orders[-1]['holding_drawdown']['observation_scope'] == 'full_holding_cycle'
    assert actual.equity == expected.equity
    assert actual.trades == expected.trades
    assert [index for index, _, _ in snapshots] == list(range(len(bars)))
    for index, orders, curve in snapshots:
        assert curve[-1]['timestamp'] == bars[index].timestamp.isoformat()
        assert all(order['timestamp'] <= bars[index].timestamp.isoformat() for order in orders)


def test_live_account_rejects_skipped_day_before_publishing():
    bars = [Bar(datetime(2026, 1, 5), 'TEST', 10, 11, 9, 10, 100)]
    snapshots = []
    runner = portfolio_days({'TEST': bars}, [], StrategyConfig(), live=True,
        on_day=lambda *args: snapshots.append(args))
    next(runner)
    try:
        with pytest.raises(ValueError, match='consecutive'):
            runner.send(AccountDay(1, [], {}, [], {}, {}))
        assert not snapshots
    finally:
        runner.close()


@pytest.mark.parametrize('blocked_close', [False, True])
def test_eligibility_close_liquidates_once_and_never_trades_the_gap(blocked_close):
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(8)]
    bars[3] = replace(bars[3], sellable=not blocked_close)
    bars[4] = replace(bars[4], low=1, close=2)
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'B', 15)
    runner = portfolio_days({'TEST': bars}, [], StrategyConfig(entry_at_close=False,
        net_reward_risk_filter=False), live=True)
    next(runner)
    result = None
    try:
        for index in range(len(bars)):
            try:
                runner.send(AccountDay(index, [buy] if index == 1 else [], {}, [], {}, {},
                    trading_eligible=index in (1, 2, 6, 7), eligibility_closed=index == 3,
                    trading_window_start=6 if index >= 6 else 1))
            except StopIteration as completed:
                result = completed.value
    finally:
        runner.close()
    assert result is not None
    sells = [row for row in result.orders if row['side'] == 'SELL' and row['status'] == 'filled']
    assert len(sells) == 1
    assert sells[0]['reason'] == 'trading_eligibility_closed'
    assert sells[0]['timestamp'] == bars[6 if blocked_close else 3].timestamp.isoformat()
    assert sells[0]['execution_model'] == ('next_open' if blocked_close else 'same_day_close')
    assert not any(row['timestamp'] in {bars[4].timestamp.isoformat(), bars[5].timestamp.isoformat()}
        for row in result.orders)
    assert [row['equity'] for row in result.equity[3:6]] == [result.equity[3]['equity']] * 3


def test_eligibility_close_cancels_pending_buy_before_matching():
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(4)]
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'B', 15)
    runner = portfolio_days({'TEST': bars}, [], StrategyConfig(entry_at_close=False,
        net_reward_risk_filter=False), live=True)
    next(runner)
    result = None
    try:
        for index in range(len(bars)):
            try:
                runner.send(AccountDay(index, [buy] if index == 1 else [], {}, [], {}, {},
                    trading_eligible=index == 1, eligibility_closed=index == 2, trading_window_start=1))
            except StopIteration as completed:
                result = completed.value
    finally:
        runner.close()
    assert result is not None
    assert not any(row['status'] == 'filled' for row in result.orders)
    assert [(row['side'], row['status'], row['reason']) for row in result.orders] == [
        ('BUY', 'cancelled', 'trading_eligibility_closed')]


def test_authoritative_n_state_does_not_keep_a_departed_source(monkeypatch):
    from wavequant.application.analytics import backtest as module

    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), 'TEST', 10, 11, 9.5, 10.2, 1_000_000)
            for i in range(4)]
    buy = Signal(bars[1].timestamp, 'TEST', 1, 'LONG', 10.2, 9, 'entry', bars[0].timestamp, 0, 2, 'B', 15)
    observations = []

    class PressureCursor:
        start_index = 1

        def advance(self, index, context):
            observations.append((index, context[-1]))
            return {}, {}, {}

        def close(self):
            pass

    monkeypatch.setattr(module, 'PressureExitStream', lambda *args, **kwargs: PressureCursor())
    runner = portfolio_days({'TEST': bars}, [], StrategyConfig(entry_at_close=False,
        net_reward_risk_filter=False, pressure_adverse_exit=True), live=True)
    next(runner)
    try:
        for index in range(len(bars)):
            try:
                runner.send(AccountDay(index, [buy] if index == 1 else [],
                    {1: 1} if index in (1, 2) else {}, [], {}, {},
                    trading_eligible=index > 0, trading_window_start=1,
                    positive_n_authoritative=True))
            except StopIteration:
                assert index == len(bars) - 1
    finally:
        runner.close()
    assert observations == [(1, 1), (2, 1), (3, None)]


def test_live_history_read_restores_writer_and_does_not_seal_it():
    with TrendStageRows(lambda row: row) as rows:
        rows.write({'day': 0})
        assert list(rows.iter_rows(seal=False)) == [{'day': 0}]
        rows.write({'day': 1})
        assert list(rows.iter_rows(seal=False)) == [{'day': 0}, {'day': 1}]
        assert list(rows.iter_rows()) == [{'day': 0}, {'day': 1}]
        with pytest.raises(ValueError, match='additional rows'):
            rows.write({'day': 2})


def test_live_history_rejects_writes_during_a_borrowed_read():
    with TrendStageRows(lambda row: row) as rows:
        rows.write({'day': 0})
        reader = rows.iter_rows(seal=False)
        assert next(reader) == {'day': 0}
        with pytest.raises(ValueError, match='reader is active'):
            rows.write({'day': 1})
        reader.close()
        rows.write({'day': 1})
        assert list(rows.iter_rows(seal=False, start_row=1)) == [{'day': 1}]


def test_live_suffix_seeks_to_changed_state_and_keeps_unchanged_days():
    decoded = []

    def decode(row):
        decoded.append(row)
        return row

    with TrendChangeRows(decode, row_count=8) as rows:
        rows.write_change(0, 'old')
        rows.write_change(2, 'middle')
        rows.write_change(5, 'current')
        rows.read_limit = 7
        assert list(rows.iter_rows(start_position=4)) == ['middle', 'current', 'current']
        assert decoded == ['middle', 'current'], 'unneeded old states must not be decoded'
        rows.write_change(7, 'next')
        rows.read_limit = 8
        assert list(rows.iter_rows(start_position=6)) == ['current', 'next']
