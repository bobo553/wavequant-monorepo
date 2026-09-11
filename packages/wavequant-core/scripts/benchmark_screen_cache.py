"""Fixed-sample cache/correctness benchmark. No parameter optimization.

Run as: .venv/Scripts/python.exe scripts/benchmark_screen_cache.py --output results/cache_benchmark_<new>
Writes evidence only under the explicitly selected output folder.
"""
import argparse
import inspect
import json
from pathlib import Path
import subprocess
import sys
import time

from wavequant.data import dump_json
from wavequant.integrated_strategy import SystemStrategy,generate_system_signals
from wavequant.visualization import ChartRepository


def benchmark(output,phase):
    repo=ChartRepository('results/operations_v1','D:/TDX')
    rid='acceptance_20260908_verified';config=repo.strategy_config(rid,'lecture_v2')
    symbols=['sh.600009','sh.600104','sh.600519','sh.600276','sh.600900','sh.601318',
             'sh.600036','sz.000333','sz.000651','sz.000858','sz.002415']
    rows=[]
    original=None
    if phase=='cold':
        import wavequant.integrated_strategy as module
        # Reference executes the SAME current rules, with the pre-optimization
        # list + repeated-validation path. It does not change live module code.
        source=inspect.getsource(generate_system_signals)
        needle='    from .validated_bars import ValidatedBars\n    bars = ValidatedBars(bars)'
        assert needle in source
        source=source.replace(needle,'    validate_prefix(bars, symbol=bars[0].symbol, end=len(bars)-1)')
        scope=dict(vars(module));exec(compile(source,'<reference-validation-path>','exec'),scope)
        original=scope['generate_system_signals']
    for symbol in symbols:
        t=time.perf_counter()
        bars,g,view=repo.tdx_backtester.run(symbol,'2018-01-01','2026-09-07',config['strategy'],config['scenarios']['base']['execution'])
        row=dict(symbol=symbol,backtest_seconds=time.perf_counter()-t,cache=view['performance']['cache'],bars=len(bars),
                 long_signals=sum(s.side=='LONG' for s in g.signals),fills=view['metrics']['entry_fills'])
        if phase=='cold':
            assert row['cache']=='computed', 'Use a new engine version/cache for cold measurements, never relabel warm as cold'
            old=Path('results/lecture_v2_validation_final_20260909')/f'{symbol}.json'
            previous=json.loads(old.read_text(encoding='utf-8'))
            for field in ('bars','signals','audit','orders','trades','metrics','curve','markers'):
                assert view[field]==previous[field],(symbol,field,'differs from pre-optimization evidence')
            row['previous_ledger_equivalent']=True
            if symbol in symbols[:3]:
                t=time.perf_counter();reference=original(bars,SystemStrategy(**config['strategy']))
                row['old_validation_seconds']=time.perf_counter()-t
                t=time.perf_counter();optimized=generate_system_signals(bars,SystemStrategy(**config['strategy']))
                row['new_validation_seconds']=time.perf_counter()-t
                assert reference==optimized==g
                row['reference_equivalent']=True
            for cut in (500,1000):
                prefix=generate_system_signals(bars[:cut+1],SystemStrategy(**config['strategy']))
                assert prefix.signals==[s for s in g.signals if s.bar_index<=cut]
            row['prefix_checks']=2
        t=time.perf_counter()
        summary=repo.tdx_backtester.screen(symbol,'2018-01-01','2026-09-07',config['strategy'],config['scenarios']['base']['execution'],20)
        row['screen_seconds']=time.perf_counter()-t;row['screen_cache']=summary['performance']['cache']
        if symbol in symbols[:3]:
            t=time.perf_counter();chart=repo.tdx_backtest(rid,'lecture_v2',symbol,'2026-09-07','base','2018-01-01')
            row['chart_seconds']=time.perf_counter()-t
            t=time.perf_counter();again=repo.tdx_backtest(rid,'lecture_v2',symbol,'2026-09-07','base','2018-01-01')
            row['chart_warm_seconds']=time.perf_counter()-t
            assert chart['theory']==again['theory']
            dump_json(output/f'{phase}_{symbol}_theory.json',chart['theory'])
        rows.append(row);print(json.dumps(row),flush=True)
    if phase=='cold':
        # After 11 stocks, the 8-entry RAM cache cannot conceal a disk-cache miss.
        t=time.perf_counter();_,_,view=repo.tdx_backtester.run(symbols[0],'2018-01-01','2026-09-07',config['strategy'],config['scenarios']['base']['execution'])
        eviction=dict(cache=view['performance']['cache'],seconds=time.perf_counter()-t)
        assert eviction['cache']=='disk'
    else:eviction=None
    dump_json(output/f'{phase}.json',dict(rows=rows,evicted_stock=eviction,engine=repo.tdx_backtester.engine,
                                        cache_path=str(repo.tdx_backtester.artifacts.path)))
    if phase=='restart':
        # Exercise the actual job/progress path on the explicitly labelled fixed
        # sample, not a claimed full-market benchmark.
        catalog=repo.tdx.catalog()
        repo.tdx.catalog=lambda:dict(catalog,stocks=[s for s in catalog['stocks'] if s['symbol'] in symbols])
        params=dict(run=rid,variant='lecture_v2',scenario='base',source='tdx',
                    start='2018-01-01',asof='2026-09-07',lookback=20)
        jobs=[]
        for _ in range(2):
            t=time.perf_counter();job=repo.buy_scanner.start(params);updates=[]
            while job['status'] in ('running','cancelling'):
                job=repo.buy_scanner.get(job['id'],after=job['revision'],timeout=20)
                updates.append(dict(processed=job['processed'],revision=job['revision'],status=job['status']))
            assert job['status']=='completed' and job['processed']==len(symbols) and job['failed']==0,job
            assert job['performance']['cache_hits']==len(symbols) and job['performance']['recomputed']==0
            jobs.append(dict(seconds=time.perf_counter()-t,job=job,updates=updates))
        assert jobs[0]['job']['results']==jobs[1]['job']['results']
        assert jobs[0]['job']['funnel']==jobs[1]['job']['funnel']
        dump_json(output/'scanner_jobs.json',dict(scope='fixed_11_stock_sample',runs=jobs))
        print('REAL_SCANNER_SECONDS', [j['seconds'] for j in jobs],flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--phase',choices=['cold','restart'],default='cold')
    args=parser.parse_args();output=Path(args.output).resolve()
    if args.phase=='cold':output.mkdir(parents=True,exist_ok=False)
    benchmark(output,args.phase)
    if args.phase=='cold':
        subprocess.run([sys.executable,__file__,'--output',str(output),'--phase','restart'],check=True)
