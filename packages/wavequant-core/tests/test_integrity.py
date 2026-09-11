from __future__ import annotations

import math
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from wavequant.backtest import run_backtest, run_portfolio, transaction_fee
from wavequant.config import StrategyConfig
from wavequant.data import opening_permissions, synthetic_dataset
from wavequant.features import compute_features
from wavequant.io import load_bars
from wavequant.model import Bar, Signal
from wavequant.strategy import generate_signals
from wavequant.tdx import RECORD, adjust_rows, ex_reference, read_day


def fixture(count=6, symbol='TEST'):
    return [Bar(datetime(2025,1,2)+timedelta(days=i), symbol,10,10.2,9.8,10,1_000_000)
            for i in range(count)]


def signal(bars, i=0, side='LONG', stop=9):
    b = bars[i]
    return Signal(b.timestamp,b.symbol,i,side,b.close,stop,'test',b.timestamp,0,1.5,'range')


def config(**kwargs):
    return replace(StrategyConfig(), slippage_bps_per_side=0, commission_bps_per_side=0,
                   minimum_commission=0, a_share_taxes=False, **kwargs)


class AccountingTests(unittest.TestCase):
    def test_terminal_position_is_not_forced_sold(self):
        bars = fixture(2)
        bars[-1] = replace(bars[-1], sellable=False)
        r = run_backtest(bars,[signal(bars)],config())
        self.assertEqual(len(r.trades),0)
        self.assertEqual(len(r.open_positions),1)
        self.assertEqual(r.open_positions[0]['entry_time'],bars[1].timestamp.isoformat())
        self.assertAlmostEqual(r.metrics['final_equity'],1_000_000)

    def test_multiple_symbols_share_cash_and_position_limit(self):
        grouped = {f'S{i}':fixture(symbol=f'S{i}') for i in range(10)}
        r = run_portfolio(grouped,[signal(b) for b in grouped.values()],config(max_positions=3))
        self.assertEqual(len(r.open_positions),3)
        for row in r.equity:
            self.assertGreaterEqual(row['cash'],0)
            self.assertAlmostEqual(row['equity'],row['cash']+row['market_value'])
        self.assertEqual(r.metrics['cancelled_orders'],7)

    def test_gap_through_stop_rejects_entry(self):
        bars = fixture()
        bars[1] = replace(bars[1],open=8,low=7.9,close=8)
        r = run_backtest(bars,[signal(bars)],config())
        self.assertFalse(r.open_positions)
        self.assertEqual(r.orders[0]['reason'],'invalidated_at_open')

    def test_unbuyable_signal_does_not_fill_days_later(self):
        bars = fixture()
        bars[1] = replace(bars[1],buyable=False)
        r = run_backtest(bars,[signal(bars)],config())
        self.assertFalse(r.open_positions)
        self.assertEqual(r.orders[0]['reason'],'not_buyable')

    def test_missing_symbol_order_expires_on_market_calendar(self):
        a,b = fixture(symbol='A'),fixture(symbol='B')
        a = [a[0],a[4],a[5]]
        r = run_portfolio({'A':a,'B':b},[signal(a)],config())
        self.assertEqual(r.orders[0]['reason'],'expired')
        self.assertFalse(r.open_positions)

    def test_new_trailing_stop_is_not_retroactive(self):
        bars = fixture(4)
        bars[2] = replace(bars[2],low=9.4,close=10.1)
        r = run_backtest(bars,[signal(bars),signal(bars,2,stop=9.5)],config())
        self.assertFalse(r.trades)
        self.assertEqual(len(r.open_positions),1)

    def test_sell_limit_deferred_until_tradable(self):
        bars = fixture(6)
        bars[3] = replace(bars[3],sellable=False)
        r = run_backtest(bars,[signal(bars),signal(bars,2,'EXIT',11)],config())
        self.assertEqual(r.trades[0].exit_time,bars[4].timestamp)
        self.assertEqual(r.metrics['deferred_exits'],1)

    def test_cost_pnl_reconciles_to_equity(self):
        bars = fixture()
        c = StrategyConfig()
        r = run_backtest(bars,[signal(bars),signal(bars,2,'EXIT',11)],c)
        self.assertEqual(len(r.trades),1)
        self.assertLess(r.trades[0].pnl,0)
        self.assertAlmostEqual(r.metrics['final_equity']-c.initial_capital,r.trades[0].pnl)
        self.assertAlmostEqual(r.metrics['fees'],r.trades[0].fees)

    def test_adjusted_unit_entry_is_raw_board_lot(self):
        bars = [replace(b,adjustment_factor=1.7) for b in fixture()]
        r = run_backtest(bars,[signal(bars)],config())
        shares = r.open_positions[0]['quantity']*1.7
        self.assertAlmostEqual(shares/100,round(shares/100))

    def test_historical_cost_boundaries(self):
        c = config()
        c = replace(c,a_share_taxes=True)
        self.assertAlmostEqual(transaction_fee(100_000,datetime(2022,4,28),False,c),2)
        self.assertAlmostEqual(transaction_fee(100_000,datetime(2022,4,29),False,c),1)
        self.assertAlmostEqual(transaction_fee(100_000,datetime(2023,8,27),True,c),101)
        self.assertAlmostEqual(transaction_fee(100_000,datetime(2023,8,28),True,c),51)

    def test_misaligned_signal_rejected(self):
        bars = fixture()
        with self.assertRaises(ValueError):
            run_backtest(bars,[replace(signal(bars),bar_index=2)],config())

    def test_capacity_uses_past_volume_not_same_day_volume(self):
        bars = fixture()
        low_volume = list(bars)
        low_volume[1] = replace(bars[1],volume=1)
        a = run_backtest(bars,[signal(bars)],config())
        b = run_backtest(low_volume,[signal(bars)],config())
        self.assertEqual(a.orders[0],b.orders[0])


class DataTests(unittest.TestCase):
    def test_day_binary_units_and_truncation(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'test.day'
            p.write_bytes(RECORD.pack(20250102,1000,1100,900,1050,100000.,10000,0))
            self.assertEqual(read_day(p)[0]['close'],10.5)
            self.assertEqual(read_day(p)[0]['volume'],10000)
            p.write_bytes(b'bad')
            with self.assertRaises(ValueError): read_day(p)

    def test_ex_dividend_removes_mechanical_gap_without_future_rewrite(self):
        raw = [dict(date=date(2025,1,i),open=p,high=p,low=p,close=p,volume=10000)
               for i,p in ((1,10),(2,10),(3,9),(4,9))]
        event = dict(date='2025-01-03',category=1,cash=10,rights=0,rights_price=0,bonus=0)
        all_rows = adjust_rows(raw,[event],date(2025,1,1),date(2025,1,4),'sh.600036')
        prefix = adjust_rows(raw,[event],date(2025,1,1),date(2025,1,2),'sh.600036')
        self.assertEqual(all_rows[:2],prefix)
        self.assertAlmostEqual(all_rows[2]['close'],10)
        self.assertEqual(all_rows[2]['buyable'],1)
        self.assertAlmostEqual(ex_reference(10,event),9)

    def test_unsupported_action_fails_loudly(self):
        with self.assertRaises(ValueError):
            adjust_rows([], [dict(date='2025-01-03',category=11)],date(2025,1,1),date(2025,1,4),'X')

    def test_limit_open_and_suspension(self):
        r = dict(date='2025-01-02',isST='0',tradestatus='1',preclose='10',open='11')
        self.assertEqual(opening_permissions(r),(False,True))
        self.assertEqual(opening_permissions(dict(r,open='9')),(True,False))
        self.assertEqual(opening_permissions(dict(r,tradestatus='0')),(False,False))

    def test_csv_rejects_nonfinite_duplicate_and_bad_ohlc(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'bad.csv'
            header = 'timestamp,symbol,open,high,low,close,volume\n'
            for body in ('2025-01-01,X,10,11,9,nan,100\n',
                         '2025-01-01,X,10,9,8,10,100\n',
                         '2025-01-01,X,10,11,9,10,100\n'*2):
                p.write_text(header+body,encoding='utf-8')
                with self.assertRaises(ValueError): load_bars(p)

    def test_feature_and_signal_prefix_invariance_and_reproducibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'fixture.csv'
            a = synthetic_dataset(p,sessions=250,symbols=2)
            b = synthetic_dataset(p,sessions=250,symbols=2)
            self.assertEqual(a['sha256'],b['sha256'])
            c = StrategyConfig()
            for bars in load_bars(p).values():
                full = compute_features(bars,c)
                signals = generate_signals(full,c)
                for length in (50,100,150,200):
                    prefix = compute_features(bars[:length],c)
                    self.assertEqual(prefix,full[:length])
                    self.assertEqual(generate_signals(prefix,c),[s for s in signals if s.bar_index<length])

    def test_invalid_config(self):
        for values in (dict(lot_size=True),dict(max_positions=0),dict(initial_capital=math.nan),
                       dict(max_participation=2),dict(allow_same_day_exit=1)):
            with self.assertRaises(ValueError): replace(StrategyConfig(),**values).validate()


if __name__ == '__main__':
    unittest.main()
