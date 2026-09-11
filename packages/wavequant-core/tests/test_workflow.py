from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from wavequant.config import StrategyConfig
from wavequant.data import dump_json, synthetic_dataset
from wavequant.research import benchmark, run_research


def protocol():
    return dict(train=['2018-01-01','2018-02-28'],validation=['2018-03-01','2018-04-30'],
                test=['2018-05-01',None],retracement_grid=[.33,.5],rvol_grid=[1.0],
                minimum_training_trades=1,research_gate=dict(minimum_test_trades=1,
                minimum_test_sharpe=.5,maximum_test_drawdown=.2))


class WorkflowTests(unittest.TestCase):
    def test_cli_demo_validate_backtest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            result=subprocess.run([sys.executable,'-m','wavequant.cli','demo','--output-dir',tmp],
                                  capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['kind'],'synthetic_engineering_only')
            for args in (['validate',str(root/'synthetic.csv')],
                         ['backtest',str(root/'synthetic.csv'),'--output-dir',str(root/'backtest')]):
                r=subprocess.run([sys.executable,'-m','wavequant.cli',*args],capture_output=True,text=True,encoding='utf-8')
                self.assertEqual(r.returncode,0,r.stderr)
                json.loads(r.stdout)
            self.assertTrue((root/'backtest/equity.csv').exists())

    def test_research_pipeline_reproducible_and_artifacts_exist(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp)
            source=root/'input.csv'
            synthetic_dataset(source,sessions=150,symbols=2)
            spec=root/'protocol.json'
            dump_json(spec,protocol())
            a=run_research(source,root/'a',StrategyConfig(),spec)
            b=run_research(source,root/'b',StrategyConfig(),spec)
            self.assertEqual(a,b)
            for name in ('report.html','report.md','report.json','data_quality.json','training_grid.json',
                         'train_selected/test/equity.csv','train_selected/test/trades.csv'):
                self.assertTrue((root/'a'/name).exists(),name)
            self.assertEqual(a['engineering_tests']['status'],'not_run_by_this_command')
            self.assertFalse(a['production_ready'])

    def test_test_future_changes_cannot_change_training_selection(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root=Path(tmp)
            source=root/'input.csv'
            synthetic_dataset(source,sessions=150,symbols=2)
            spec=root/'protocol.json'
            dump_json(spec,protocol())
            a=run_research(source,root/'a',StrategyConfig(),spec)
            import csv
            with source.open(encoding='utf-8',newline='') as handle:
                rows=list(csv.DictReader(handle))
            for row in rows:
                if row['timestamp']>='2018-05-01':
                    for key in ('open','high','low','close'): row[key]=float(row[key])*2
            from wavequant.data import write_dataset
            write_dataset(source,rows,{'kind':'synthetic_modified_test_only'})
            b=run_research(source,root/'b',StrategyConfig(),spec)
            self.assertEqual(a['selected_config'],b['selected_config'])
            self.assertEqual(a['results']['train_selected']['train'],b['results']['train_selected']['train'])
            self.assertEqual(a['results']['train_selected']['validation'],b['results']['train_selected']['validation'])

    def test_hash_mismatch_and_overlapping_periods_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            source=root/'input.csv'
            synthetic_dataset(source,sessions=150,symbols=2)
            spec=root/'protocol.json'
            p=protocol();p['validation'][0]='2018-02-01'
            dump_json(spec,p)
            with self.assertRaises(ValueError): run_research(source,root/'out',StrategyConfig(),spec)
            dump_json(spec,protocol())
            source.write_text(source.read_text(encoding='utf-8')+'\n',encoding='utf-8')
            with self.assertRaises(ValueError): run_research(source,root/'out',StrategyConfig(),spec)

    def test_equal_weight_benchmark_matches_hand_calculation(self):
        from datetime import datetime
        from wavequant.model import Bar
        grouped={'A':[Bar(datetime(2025,1,2),'A',10,20,10,20,100)],
                 'B':[Bar(datetime(2025,1,2),'B',10,10,5,5,100)]}
        result=benchmark(grouped,'2025-01-01','2025-01-03',1000)
        self.assertAlmostEqual(result.metrics['total_return'],.25)


if __name__=='__main__': unittest.main()
