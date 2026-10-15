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
    assert actual.orders == expected.orders
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
