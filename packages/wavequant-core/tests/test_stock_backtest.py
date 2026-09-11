from dataclasses import asdict
from datetime import datetime,timedelta
from types import SimpleNamespace
import unittest

from wavequant.config import StrategyConfig
from wavequant.model import Bar,Signal
from wavequant.stock_backtest import single_stock_result


class StockBacktestTests(unittest.TestCase):
    def setUp(self):
        self.bars=[Bar(datetime(2026,1,1)+timedelta(days=i),'TEST',p,p+1,p-1,p,100000)
                   for i,p in enumerate([10,10,11,12,13])]
        self.signals=[Signal(self.bars[i].timestamp,'TEST',i,side,self.bars[i].close,8,'fixture',
                     self.bars[i].timestamp,.1,2,'轧空',20,0) for i,side in [(0,'LONG'),(2,'EXIT')]]
        self.generated=SimpleNamespace(signals=self.signals,counts={'long_signals':1})
        self.config=asdict(StrategyConfig(initial_capital=10000,lot_size=1,max_participation=1,
            risk_fraction=.2,max_position_weight=.8,commission_bps_per_side=0,slippage_bps_per_side=0,
            minimum_commission=0,a_share_taxes=False))

    def test_own_cash_next_open_fills_and_trade_pnl(self):
        result=single_stock_result(self.bars,{},self.config,self.generated)
        fills=[o for o in result['orders'] if o['status']=='filled']
        self.assertEqual([(o['side'],o['timestamp'][:10]) for o in fills],[('BUY','2026-01-02'),('SELL','2026-01-04')])
        self.assertEqual([o['price'] for o in fills],[10,12])
        self.assertEqual(result['metrics']['trades'],1)
        self.assertAlmostEqual(result['metrics']['equity'],10000+result['trades'][0]['pnl'])
        self.assertEqual(result['backtest']['initial_capital'],10000)

    def test_prefix_open_position_has_no_future_sell_or_exit_pnl(self):
        result=single_stock_result(self.bars[:3],{},self.config,self.generated)
        self.assertEqual(result['trades'],[]);self.assertIsNone(result['metrics']['win_rate'])
        self.assertEqual(result['metrics']['open_positions'],1)
        self.assertEqual([o['side'] for o in result['orders'] if o['status']=='filled'],['BUY'])
        self.assertTrue(all(o['timestamp'][:10]<='2026-01-03' for o in result['orders']))
        self.assertNotIn('exit_time',result['backtest']['open_positions'][0])

    def test_no_signals_and_mixed_universe(self):
        result=single_stock_result(self.bars,{},self.config,SimpleNamespace(signals=[],counts={}))
        self.assertEqual(result['metrics']['total_return'],0)
        self.assertEqual(result['backtest']['diagnostics']['entry_attempts'],0)
        self.assertIsNone(result['metrics']['win_rate'])
        with self.assertRaises(ValueError):single_stock_result([],{},self.config)
        with self.assertRaises(ValueError):single_stock_result(self.bars+[Bar(datetime(2026,1,6),'OTHER',10,11,9,10,100)],{},self.config)

    def test_costs_reduce_same_fills_and_rejected_entry_is_not_fill(self):
        base=single_stock_result(self.bars,{},self.config,self.generated)
        costly=single_stock_result(self.bars,{},dict(self.config,commission_bps_per_side=30),self.generated)
        self.assertGreater(costly['metrics']['fees'],0)
        self.assertLess(costly['metrics']['total_return'],base['metrics']['total_return'])
        rejected=single_stock_result(self.bars,{},dict(self.config,lot_size=100000),self.generated)
        self.assertEqual(rejected['metrics']['entry_fills'],0)
        self.assertTrue(rejected['backtest']['diagnostics']['rejection_reasons'])


if __name__=='__main__':unittest.main()
