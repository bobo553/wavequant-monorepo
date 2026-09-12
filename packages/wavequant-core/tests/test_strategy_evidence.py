from concurrent.futures import ProcessPoolExecutor
import contextlib
import io
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from wavequant.application.analytics.evidence_statistics import joint_block_test, equity_returns, choose_development, evidence_gate
from wavequant.interfaces.research_tools.research_panel import freeze_universe
from wavequant.interfaces.research_tools.strategy_evidence import strategy_configs, _evaluate_symbol, run_strategy_evidence
from wavequant.infrastructure.market_data.data import write_dataset, dump_json
from wavequant.infrastructure.market_data.tdx import RECORD
from wavequant.domain.strategies.integrated_strategy import generate_system_signals
from tests.test_integrated_strategy import fixture


class StatisticsTests(unittest.TestCase):
    def test_flat_cash_is_not_significant(self):
        r=joint_block_test({'cash':[0.0]*80},replications=100)
        self.assertIsNone(r['candidates']['cash']['family_adjusted_p'])
        self.assertIsNone(r['candidates']['cash']['bonferroni_mean_ci'])

    def test_joint_sampling_preserves_identical_candidate_dependence(self):
        values=[.001,-.001,.002,-.001]*30
        r=joint_block_test({'a':values,'b':values},replications=200)
        self.assertEqual(r['candidates']['a'],r['candidates']['b'])

    def test_seed_is_reproducible_and_p_in_bounds(self):
        rng=random.Random(7); values=[rng.uniform(-.01,.01) for _ in range(160)]
        a=joint_block_test({'a':values},replications=200)
        self.assertEqual(a,joint_block_test({'a':values},replications=200))
        self.assertTrue(0<=a['candidates']['a']['family_adjusted_p']<=1)

    def test_negative_mean_cannot_pass_one_sided_test(self):
        r=joint_block_test({'a':[-.002,.001]*60},replications=100)
        self.assertEqual(r['candidates']['a']['family_adjusted_p'],1)

    def test_strong_synthetic_mean_detected_but_still_exploratory(self):
        r=joint_block_test({'a':[.01,.011,.009,.0105]*30},replications=200)
        self.assertLess(r['candidates']['a']['family_adjusted_p'],.05)
        self.assertGreater(r['candidates']['a']['bonferroni_mean_ci'][0],0)
        self.assertFalse(r['complete_historical_trial_correction'])

    def test_family_confidence_interval_not_narrower_than_single(self):
        values=[.001,-.002,.003]*40
        a=joint_block_test({'a':values},replications=200)['candidates']['a']['bonferroni_mean_ci']
        b=joint_block_test({'a':values,'b':values},replications=200)['candidates']['a']['bonferroni_mean_ci']
        self.assertLessEqual(b[0],a[0]); self.assertGreaterEqual(b[1],a[1])

    def test_alignment_and_invalid_numbers_fail(self):
        for series in ({'a':[1,2],'b':[1]},{'a':[float('nan')]*50}):
            with self.assertRaises(ValueError): joint_block_test(series,replications=100)
        self.assertEqual(joint_block_test({'a':[1,2]},replications=100)['status'],'unavailable_short_history')

    def test_returns_include_first_day_and_validate_timestamps(self):
        curve=[{'timestamp':'2020-01-01','equity':110},{'timestamp':'2020-01-02','equity':99}]
        days,r=equity_returns(curve,100)
        self.assertAlmostEqual(r[0],.1); self.assertAlmostEqual(r[1],-.1)
        with self.assertRaises(ValueError): equity_returns(curve[::-1],100)

    def test_cash_when_training_evidence_insufficient(self):
        good=dict(trades=29,traded_symbols=5,total_return=1,cost_2x_return=.5)
        self.assertEqual(choose_development({'winner':good}),'CASH')
        self.assertEqual(choose_development({'winner':dict(good,trades=30,cost_2x_return=-.01)}),'CASH')

    def test_training_selection_tie_deterministic(self):
        good=dict(trades=30,traded_symbols=5,total_return=.1,cost_2x_return=.05)
        self.assertEqual(choose_development({'b':good,'a':good}),'a')
        self.assertEqual(choose_development({'b':dict(good,total_return=.2),'a':good}),'b')

    def test_good_statistics_never_override_missing_external_evidence(self):
        requirements=dict(minimum_closed_trades=30,minimum_symbols_with_closed_trades=5,minimum_active_sessions=60,alpha=.05)
        g=evidence_gate(dict(trades=100,total_return=.2),dict(bonferroni_mean_ci=[.001,.002],family_adjusted_p=.01,active_return_sessions=100),
              traded_symbols=20,cost_return=.1,requirements=requirements,independent_holdout=False,pit_universe=False,actual_theory_path=False)
        self.assertTrue(g['research_passed']); self.assertFalse(g['validated'])


class UniverseTests(unittest.TestCase):
    def write_day(self, root, symbol, first=20170101, price=1000):
        market,code=symbol.split('.'); p=root/'vipdoc'/market/'lday'/f'{market}{code}.day'
        p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(RECORD.pack(first,price,price+100,price-100,price,10000.,100,0))
        return p

    def test_sample_does_not_change_when_prices_change(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for s in ('sh.600001','sh.600002','sz.000001','sz.000002'): self.write_day(root,s)
            a=freeze_universe(root,'2018-01-01',size=2,seed='fixed')
            self.write_day(root,'sh.600001',price=100000)
            b=freeze_universe(root,'2018-01-01',size=2,seed='fixed')
            self.assertEqual(a['selected'],b['selected']); self.assertFalse(a['selection_uses_returns'])

    def test_late_listing_excluded_without_return_filter(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.write_day(root,'sh.600001'); self.write_day(root,'sh.600002',20200101)
            r=freeze_universe(root,'2018-01-01',size=1,seed='fixed')
            self.assertEqual(r['selected'],['sh.600001']); self.assertEqual(len(r['exclusions']),1)

    def test_missing_required_and_short_sample_fail(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); self.write_day(root,'sh.600001')
            with self.assertRaises(ValueError): freeze_universe(root,'2018-01-01',size=2,seed='fixed')
            with self.assertRaises(ValueError): freeze_universe(root,'2018-01-01',size=1,seed='fixed',required=['sz.000001'])


class ChallengerTests(unittest.TestCase):
    def protocol(self):
        return json.loads((Path(__file__).resolve().parent.parent/'configs/squeeze_evidence_v5.json').read_text(encoding='utf-8'))

    def test_all_candidates_keep_core_user_policy(self):
        configs=strategy_configs(self.protocol())
        self.assertEqual(len(configs),5)
        for c in configs.values():
            self.assertEqual(c.entry_policy,'transitioned_squeeze'); self.assertTrue(c.regime_filter)
            self.assertTrue(c.volume_filter); self.assertTrue(c.preflight_reward_risk)

    def test_candidates_do_not_repaint_prefixes(self):
        bars=fixture()
        for cfg in strategy_configs(self.protocol()).values():
            full=generate_system_signals(bars,cfg).signals
            for stop in (5,9,11):
                self.assertEqual(generate_system_signals(bars[:stop],cfg).signals,[s for s in full if s.bar_index<stop])

    def test_no_silent_policy_relaxation(self):
        p=self.protocol(); p['strategy']['entry_policy']='legacy_n_continuation'
        with self.assertRaises(ValueError): strategy_configs(p)

    def test_parallel_stock_evaluation_equals_serial(self):
        cfg=strategy_configs(self.protocol())['proxy_control']
        jobs=[('proxy_control','TEST',fixture(),cfg,True)]*2
        expected=list(map(_evaluate_symbol,jobs))
        with ProcessPoolExecutor(max_workers=2) as pool: actual=list(pool.map(_evaluate_symbol,jobs))
        self.assertEqual(actual,expected)

    def test_complete_report_pipeline_with_nonempty_rolling_fold(self):
        p=self.protocol()
        p['data_start']='2026-01-01'; p['data_end']='2026-01-13'
        p['periods']={'development':['2026-01-01','2026-01-04'],
                      'validation':['2026-01-05','2026-01-08'],'diagnostic':['2026-01-09','2026-01-13']}
        p['statistics']['replications']=100
        def panel(root,output,protocol):
            output.mkdir(parents=True,exist_ok=True)
            rows=[dict(timestamp=b.timestamp.date().isoformat(),symbol=b.symbol,open=b.open,high=b.high,
                       low=b.low,close=b.close,volume=b.volume,buyable=1,sellable=1,adjustment_factor=1) for b in fixture()]
            path=output/'daily.csv'; meta=write_dataset(path,rows,dict(start=p['data_start'],end=p['data_end'],quarantine=[]))
            u=dict(selected=['TEST'],eligible_symbols=['TEST'])
            dump_json(output/'universe_frozen.json',u)
            return path,meta,u
        fold=dict(fold=0,train=['2026-01-01','2026-01-04'],validation=['2026-01-05','2026-01-08'],
                  test=['2026-01-09','2026-01-13'],gap_sessions=0,test_sessions=5)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); protocol=root/'protocol.json'; dump_json(protocol,p)
            with patch('wavequant.interfaces.research_tools.strategy_evidence.build_research_panel',side_effect=panel), \
                 patch('wavequant.interfaces.research_tools.strategy_evidence.walk_forward_folds',return_value=[fold]),contextlib.redirect_stdout(io.StringIO()):
                r=run_strategy_evidence(root,protocol,root/'out',{'status':'passed'},workers=1)
            self.assertEqual(r['rolling'][0]['test'],fold['test'])
            self.assertIn('test_metrics',r['rolling'][0]); self.assertFalse(r['validated'])
            self.assertEqual(r['selected_for_forward_observation'],'CASH')
            self.assertTrue((root/'out/report.md').exists())
            self.assertTrue((root/'out/proxy_control/candidate_checkpoint.json').exists())
