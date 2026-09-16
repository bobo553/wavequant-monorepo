from dataclasses import asdict
from datetime import date,timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wavequant.domain.models.config import StrategyConfig
from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.domain.strategies.integrated_strategy import SystemStrategy,SystemResult
from wavequant.domain.models.model import Signal
from wavequant.infrastructure.market_data.tdx import RECORD
from wavequant.interfaces.charts.tdx_browser import TdxBrowser
from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester


class TdxBacktestTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name);self.day=root/'vipdoc/sh/lday/sh600000.day'
        self.day.parent.mkdir(parents=True)
        self.action_path=root/'T0002/hq_cache/gbbq';self.action_path.parent.mkdir(parents=True)
        self.action_path.write_bytes(b'fixture-actions')
        self.days=[date(2020,1,1)+timedelta(days=i) for i in range(45)]
        self.records=[RECORD.pack(int(d.strftime('%Y%m%d')),p,p+10,p-10,p,1000000.,1000000,0)
                      for i,d in enumerate(self.days) for p in [1000 if i<25 else 500]]
        self.day.write_bytes(b''.join(self.records))
        self.events=[dict(symbol='sh.600000',date=self.days[25].isoformat(),category=1,cash=50,
                          bonus=0,rights=0,rights_price=0)]
        self.browser=TdxBrowser(root);self.service=TdxBacktester(self.browser,root/'cache')
        self.strategy=asdict(SystemStrategy(pivot_mode='confirmed_fractal_proxy'))
        self.execution=asdict(StrategyConfig(initial_capital=100000,minimum_commission=0,
            commission_bps_per_side=0,slippage_bps_per_side=0,a_share_taxes=False,max_participation=1))

    def generated(self,bars,config):
        signals=[];audit=[]
        for i,side in ((2,'LONG'),(4,'EXIT')):
            if i>=len(bars): continue
            signals.append(Signal(bars[i].timestamp,bars[i].symbol,i,side,bars[i].close,8,
                'fixture',bars[i].timestamp,.2,2,'轧空',20,0))
            if side=='LONG':
                audit.append(dict(timestamp=bars[i].timestamp.isoformat(),bar_index=i,event='long_transition_evidence',
                    context_index=0,key_source_index=0,flip_index=0,alternation_index=1,
                    bullish_index=1,attack=2))
        return SystemResult(signals,audit,dict(long_signals=sum(s.side=='LONG' for s in signals)))

    def test_engine_fingerprint_includes_domain_trend_reducers(self):
        hashes=TdxBacktester._engine_hashes()
        self.assertIn('domain/market_structure/lecture_trend.py',hashes)
        self.assertIn('domain/market_structure/secondary_trend.py',hashes)

    def run_fixture(self,end=None):
        with patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=(self.events,fingerprint(self.action_path))), \
             patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=self.generated):
            return self.service.run('sh.600000','2020-01-01',end or self.days[-1].isoformat(),self.strategy,self.execution)

    def test_unified_adjusted_prices_and_entry_exit_evidence(self):
        bars,_,view=self.run_fixture()
        self.assertEqual(bars[5].adjustment_factor,2)
        self.assertAlmostEqual(bars[5].close,bars[4].close)
        fills=[m for m in view['markers'] if m['kind']=='fill']
        self.assertEqual([m['side'] for m in fills],['BUY','SELL'])
        self.assertEqual([m['price'] for m in fills],[10,10])
        self.assertEqual([m['raw_price'] for m in fills],[10,5])
        for m in fills:
            b=next(b for b in view['bars'] if b['time']==m['time'])
            self.assertEqual(m['price'],b['open'])
            self.assertLess(m['signal_time'],m['time'])
            self.assertAlmostEqual(m['price']*m['quantity'],m['raw_price']*m['raw_shares'])
        self.assertEqual(fills[0]['trade_id'],fills[1]['trade_id'])
        self.assertTrue(fills[0]['entry_conditions'][0]['passed'])
        self.assertEqual(fills[1]['decision_source'],'strategy_exit_signal')
        self.assertEqual(view['metrics']['total_return'],0)

    def test_prefix_does_not_leak_future_exit_and_factor_is_causal(self):
        _,_,full=self.run_fixture();_,_,prefix=self.run_fixture(self.days[23].isoformat())
        self.assertEqual(prefix['bars'],full['bars'][:4])
        self.assertEqual(prefix['trades'],[])
        self.assertEqual(prefix['orders'],[o for o in full['orders'] if o['timestamp'][:10]<=self.days[23].isoformat()])
        self.assertEqual(prefix['metrics']['open_positions'],1)

    def test_v2_first_buy_ledger_disables_old_ratio_gate_and_signal_has_evidence(self):
        self.strategy.update(pivot_mode='lecture_causal',entry_policy='hierarchical_two_buy_points')
        original=self.generated
        def generated(bars,config):
            from dataclasses import replace
            g=original(bars,config)
            g.signals=[replace(s,retracement=.9) if s.side=='LONG' else s for s in g.signals]
            for e in g.audit:
                e.update(buy_point_type='transition_squeeze',priority=1,trend_level=2,
                         flip_high_price=11,counter_filter_applied=False,counter_ratio=.9)
            return g
        with patch.object(self,'generated',side_effect=generated):
            _,_,view=self.run_fixture()
        buy=next(m for m in view['markers'] if m['kind']=='fill' and m['side']=='BUY')
        self.assertTrue(buy['entry_conditions'][0]['passed'])
        self.assertIsNone(buy['entry_conditions'][2]['passed'])
        self.assertEqual(buy['entry_conditions'][2]['required'],'不启用比例过滤')
        signal=next(m for m in view['markers'] if m['kind']=='signal' and m['side']=='LONG')
        self.assertEqual(signal['decision_evidence'][0]['trend_level'],2)
        self.assertIn('flip_index_date',signal['decision_evidence'][0])

    def test_v2_second_buy_ledger_checks_actual_sequence_and_one_third(self):
        self.strategy.update(pivot_mode='lecture_causal',entry_policy='hierarchical_two_buy_points',mature_shallow_ratio=1/3)
        def generated(bars,config):
            i=5
            signal=Signal(bars[i].timestamp,bars[i].symbol,i,'LONG',bars[i].close,8,
                'system_mature_shallow_squeeze',bars[i].timestamp,.2,2,'轧空',20,0)
            proof=dict(timestamp=signal.timestamp.isoformat(),bar_index=i,event='long_transition_evidence',
                flip_index=0,alternation_index=1,maturity_index=2,impulse_high_index=3,
                pullback_index=4,attack=5,buy_point_type='mature_shallow_squeeze',priority=2,
                trend_level=1,counter_filter_applied=True,counter_ratio=.2,counter_limit=1/3,flip_high_price=11)
            return SystemResult([signal],[proof],{'long_signals':1})
        with patch.object(self,'generated',side_effect=generated):
            _,_,view=self.run_fixture()
        buy=next(m for m in view['markers'] if m['kind']=='fill' and m['side']=='BUY')
        self.assertTrue(buy['entry_conditions'][0]['passed'])
        self.assertTrue(buy['entry_conditions'][2]['passed'])
        self.assertEqual(buy['entry_conditions'][2]['required'],1/3)

    def test_changed_source_invalidates_result_id(self):
        _,_,before=self.run_fixture()
        self.records[-1]=RECORD.pack(int(self.days[-1].strftime('%Y%m%d')),501,511,491,501,1000000.,1000000,0)
        self.day.write_bytes(b''.join(self.records))
        _,_,after=self.run_fixture()
        self.assertNotEqual(before['run_id'],after['run_id'])
        self.assertNotEqual(before['bars'][-1]['close'],after['bars'][-1]['close'])

    def test_missing_actions_unsupported_actions_dates_and_st_blocked(self):
        with self.assertRaises(ValueError):self.service.run('sh.600000','2020-03-01','2020-02-01',self.strategy,self.execution)
        self.events[0]['category']=11
        with self.assertRaisesRegex(ValueError,'unsupported'):self.run_fixture()
        self.action_path.unlink()
        with self.assertRaisesRegex(ValueError,'gbbq'):
            self.service.run('sh.600000','2020-01-01','2020-02-01',self.strategy,self.execution)

    def test_chinext_star_and_bse_use_board_specific_execution(self):
        # Fixture ends before the 2020-08-24 ChiNext reform, so its published
        # as-of spec is the historical 10% rule; row-level adapters switch to
        # 20% on and after the reform date (covered by integrity tests).
        fixtures=(('sz.300750','sz',100,.10),('sh.688001','sh',200,.20),('bj.920001','bj',100,.30))
        for symbol,market,minimum,limit in fixtures:
            target=self.browser.root/f'vipdoc/{market}/lday/{market}{symbol[3:]}.day'
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(self.day.read_bytes())
            with patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=([],fingerprint(self.action_path))), \
                 patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=self.generated):
                bars,_,view=self.service.run(symbol,'2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
            self.assertTrue(bars)
            self.assertEqual(view['backtest']['execution']['minimum_entry_shares'],minimum)
            self.assertEqual(view['backtest']['security_spec']['price_limit_rate'],limit)

    def test_current_st_guard(self):
        with patch.object(self.browser,'stock_metadata',return_value=dict(symbol='sh.600000',name='*ST测试')):
            with self.assertRaisesRegex(ValueError,'风险警示'):self.run_fixture()

    def test_engine_change_requires_restart(self):
        with patch.object(self.service,'_engine_hashes',return_value={'changed':'source'}):
            with self.assertRaisesRegex(ValueError,'重启'):self.run_fixture()

    def test_disk_restart_reuses_identical_signals_ledger_and_screening(self):
        from wavequant.interfaces.screening.buy_scanner import buy_match
        from wavequant.application.analytics.screening_funnel import funnel
        bars,g,before=self.run_fixture()
        self.service=TdxBacktester(self.browser,self.service.cache)
        with patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=(self.events,fingerprint(self.action_path))), \
             patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=AssertionError('must reuse')):
            b,g2,after=self.service.run('sh.600000','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
            self.assertEqual(after['performance']['cache'],'disk')
            self.assertEqual(b,bars);self.assertEqual(g,g2)
            self.assertEqual({k:v for k,v in before.items() if k!='performance'},
                             {k:v for k,v in after.items() if k!='performance'})
            with patch.object(self.service,'_run',side_effect=AssertionError('summary needs no full ledger')):
                for window in (1,5,20):
                    summary=self.service.screen('sh.600000','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution,window)
                    self.assertEqual((summary['match'],summary['skip']),buy_match(before,self.days[-1].isoformat(),window))
                    self.assertEqual(summary['gates'],funnel(before,window))
                    self.assertEqual(summary['performance']['cache'],'screen_disk')

    def test_execution_change_reuses_signal_artifact_only(self):
        _,_,before=self.run_fixture()
        self.execution['slippage_bps_per_side']=10
        with patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=AssertionError('must reuse')):
            _,_,after=self.service.run('sh.600000','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
        self.assertEqual(after['performance']['cache'],'signals_disk')
        self.assertNotEqual(before['run_id'],after['run_id'])
        self.assertEqual(before['signals'],after['signals'])
        self.assertNotEqual(before['metrics']['total_return'],after['metrics']['total_return'])

    def test_strategy_start_prefix_and_actions_invalidate(self):
        self.run_fixture()
        for strategy,start,end in ((dict(self.strategy,volume_lookback=10),'2020-01-01',self.days[-1].isoformat()),
                                  (self.strategy,self.days[22].isoformat(),self.days[-1].isoformat()),
                                  (self.strategy,'2020-01-01',self.days[-2].isoformat())):
            with patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=self.generated) as compute:
                self.service.run('sh.600000',start,end,strategy,self.execution)
                self.assertEqual(compute.call_count,1)
        self.action_path.write_bytes(b'new-actions')
        with patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=(self.events,fingerprint(self.action_path))), \
             patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=self.generated) as compute:
            self.service.run('sh.600000','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
            self.assertEqual(compute.call_count,1)

    def test_source_mutation_during_compute_does_not_publish_research(self):
        original=self.generated
        def mutate(bars,config):
            result=original(bars,config);self.day.write_bytes(self.day.read_bytes()+self.records[-1]);return result
        with patch.object(self,'generated',side_effect=mutate):
            with self.assertRaisesRegex(ValueError,'正在更新'):self.run_fixture()
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(self.service.artifacts.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM artifacts WHERE namespace IN ('signals','backtest','screen')").fetchone()[0],0)

    def test_actions_decode_once_and_catalog_not_scanned_per_stock(self):
        with patch.object(self.browser,'catalog',side_effect=AssertionError('full market scan')), \
             patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=(self.events,fingerprint(self.action_path))) as decode, \
             patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=self.generated):
            for end in self.days[-3:]:
                self.service.run('sh.600000','2020-01-01',end.isoformat(),self.strategy,self.execution)
            self.assertEqual(decode.call_count,1)

    def test_different_stock_not_blocked_by_one_cold_stock(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        self.day.with_name('sh600001.day').write_bytes(self.day.read_bytes())
        entered=Event();release=Event()
        def slow(bars,config):
            if bars[0].symbol=='sh.600000':entered.set();release.wait(5)
            return self.generated(bars,config)
        with patch('wavequant.interfaces.research_tools.tdx_backtest.read_actions',return_value=(self.events,fingerprint(self.action_path))), \
             patch('wavequant.interfaces.research_tools.tdx_backtest.generate_system_signals',side_effect=slow), \
             ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.service.run,'sh.600000','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
            try:
                self.assertTrue(entered.wait(2))
                second=pool.submit(self.service.run,'sh.600001','2020-01-01',self.days[-1].isoformat(),self.strategy,self.execution)
                self.assertEqual(second.result(timeout=3)[2]['symbol'],'sh.600001')
                self.assertFalse(first.done())
            finally:release.set();first.result(timeout=3)

    def test_deferred_exit_retains_original_decision_day(self):
        from dataclasses import replace
        from wavequant.application.analytics.backtest import run_portfolio
        bars,generated,_=self.run_fixture()
        bars[5]=replace(bars[5],sellable=False)
        result=run_portfolio({bars[0].symbol:bars},generated.signals,StrategyConfig(**self.execution))
        sells=[o for o in result.orders if o['side']=='SELL']
        self.assertEqual([o['status'] for o in sells],['deferred','filled'])
        self.assertEqual(sells[0]['signal_timestamp'],sells[1]['signal_timestamp'])
        self.assertEqual(sells[1]['signal_timestamp'],bars[4].timestamp.isoformat())
        self.assertEqual(sells[1]['timestamp'],bars[6].timestamp.isoformat())

    def test_stop_observation_does_not_fake_same_day_stop_fill(self):
        from dataclasses import replace
        from wavequant.application.analytics.backtest import run_portfolio
        bars,generated,_=self.run_fixture();signals=[s for s in generated.signals if s.side=='LONG']
        bars[4]=replace(bars[4],low=7)
        result=run_portfolio({bars[0].symbol:bars},signals,StrategyConfig(**self.execution))
        sell=next(o for o in result.orders if o['side']=='SELL' and o['status']=='filled')
        self.assertEqual(sell['decision_reason'],'structural_stop_observed')
        self.assertEqual(sell['observed_low'],7)
        self.assertEqual(sell['signal_timestamp'],bars[4].timestamp.isoformat())
        self.assertEqual(sell['timestamp'],bars[5].timestamp.isoformat())
        self.assertEqual(sell['price'],bars[5].open)


if __name__=='__main__': unittest.main()
