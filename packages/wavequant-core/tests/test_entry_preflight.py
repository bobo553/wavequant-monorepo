import csv
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wavequant.model import Bar, Signal
from wavequant.config import StrategyConfig
from wavequant.backtest import run_portfolio
from wavequant.bull_eligibility import BullPermission
from wavequant.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.execution_diagnostics import execution_diagnostics
from wavequant.research import save_result


def bars_and_points():
    rows = [(8.5,9,8,8.5), (10,12,9.5,11), (11,12,10,12),
            (12.6,13,12.5,12.8), (12.85,12.9,12.3,12.5),
            (12.6,13.8,12.4,13.7), (13.7,14.2,13.5,14.2),
            (14.2,14.5,13.8,14.3), (14.3,14.7,14,14.5), (14.5,14.9,14.1,14.7)]
    bars = [Bar(datetime(2020,1,1)+timedelta(days=i),'TEST',*r,1000000) for i,r in enumerate(rows)]
    points = tuple(ReversalPoint(LinePoint(i,0,k,bars[i].low if k==PointKind.LOW else bars[i].high),i,'fixture')
                   for i,k in [(0,PointKind.LOW),(1,PointKind.HIGH),(2,PointKind.LOW)])
    return bars,points


def generate(bars, points, enabled=True):
    n=len(bars)
    snapshots={i:tuple(p for p in points if p.confirmed_index<=i) for i in range(n)}
    permission=BullPermission(0,0,9,0,1,2)
    c=SystemStrategy(pivot_mode='confirmed_fractal_proxy',volume_filter=False,
                     preflight_reward_risk=enabled)
    # Isolate event workflow with known anchors. N, six-state, milestones,
    # preflight and trading engine below run their real implementations.
    with patch('wavequant.integrated_strategy.pivot_history',return_value=(snapshots,
               {i:0 for i in range(n)},{i:n-1 for i in range(n)},set())), \
         patch('wavequant.integrated_strategy.bull_permission_history',
               return_value=({i:permission for i in range(n)},[])):
        return generate_system_signals(bars,c)


class EntryPreflightTests(unittest.TestCase):
    def test_infeasible_observation_does_not_consume_n(self):
        bars,points=bars_and_points()
        old=generate(bars,points,False)
        new=generate(bars,points)
        self.assertEqual([s.bar_index for s in old.signals if s.side=='LONG'],[5])
        self.assertEqual([s.bar_index for s in new.signals if s.side=='LONG'],[6])
        self.assertTrue(any(e['event']=='entry_preflight_rejected' and e['bar_index']==5
                            and e['consumed_attack'] is False for e in new.audit))
        self.assertEqual(next(s.target_price for s in old.signals if s.side=='LONG'),14)
        self.assertEqual(next(s.target_price for s in new.signals if s.side=='LONG'),18)
        c=replace(StrategyConfig(),risk_fraction=.005,max_hold_bars=1)
        a=run_portfolio({'TEST':bars},old.signals,c)
        b=run_portfolio({'TEST':bars},new.signals,c)
        self.assertEqual(a.metrics['entry_fills'],0)
        self.assertEqual(b.metrics['entry_fills'],1)
        self.assertEqual(len(b.trades),1)
        self.assertEqual(b.trades[0].entry_time,bars[7].timestamp)
        self.assertTrue(all(o['net_reward_risk']>=1.5 for o in b.orders if o['side']=='BUY' and o['status']=='filled'))

    def test_no_target_skipping_before_actual_milestone(self):
        bars,points=bars_and_points()
        r=generate(bars[:6],points)
        self.assertFalse(any(s.side=='LONG' for s in r.signals))
        rejection=next(e for e in r.audit if e['event']=='entry_preflight_rejected')
        self.assertEqual(rejection['target'],14)

    def test_prefix_invariance_with_real_nonempty_signals(self):
        bars,points=bars_and_points()
        full=generate(bars,points)
        self.assertTrue(any(s.side=='LONG' for s in full.signals))
        for end in range(3,len(bars)):
            short=generate(bars[:end+1],points)
            self.assertEqual(short.signals,[s for s in full.signals if s.bar_index<=end])

    def test_risk_budget_below_one_lot_is_not_cash_shortage(self):
        bars=[Bar(datetime(2025,1,1)+timedelta(days=i),'TEST',1000,1010,990,1000,1000000,
                  adjustment_factor=2) for i in range(2)]
        s=Signal(bars[0].timestamp,'TEST',0,'LONG',1000,800,'test',bars[0].timestamp,0,2,'轧空',1500,1.5)
        r=run_portfolio({'TEST':bars},[s],replace(StrategyConfig(),risk_fraction=.005))
        o=r.orders[0]
        self.assertEqual(o['reason'],'risk_budget_below_one_lot')
        self.assertLess(o['risk_budget_shares'],100)
        self.assertGreater(o['cash_shares'],100)
        self.assertGreater(o['one_lot_price_risk'],o['risk_budget'])
        self.assertEqual(execution_diagnostics(r)['status'],'all_entry_attempts_rejected')
        with tempfile.TemporaryDirectory() as folder:
            save_result(Path(folder),r)
            with (Path(folder)/'orders.csv').open(encoding='utf-8') as handle:
                row=next(csv.DictReader(handle))
            self.assertIn('risk_budget_shares',row)

    def test_net_rr_rejection_contains_numbers_and_no_fill_status(self):
        bars,points=bars_and_points()
        r=run_portfolio({'TEST':bars},generate(bars,points,False).signals,StrategyConfig())
        o=r.orders[0]
        self.assertEqual(o['reason'],'insufficient_net_reward_risk')
        self.assertLess(o['net_reward_risk'],o['required_reward_risk'])
        self.assertEqual(r.metrics['evidence_status'],'no_entry_fills')

    def test_no_signals_and_open_only_distinguished(self):
        bars,points=bars_and_points()
        r=run_portfolio({'TEST':bars},[],StrategyConfig())
        self.assertEqual(execution_diagnostics(r)['status'],'no_entry_signals_reached_execution')
        r=run_portfolio({'TEST':bars[:8]},generate(bars[:8],points).signals,StrategyConfig())
        self.assertEqual(execution_diagnostics(r)['status'],'open_positions_without_closed_trades')


if __name__=='__main__':
    unittest.main()
