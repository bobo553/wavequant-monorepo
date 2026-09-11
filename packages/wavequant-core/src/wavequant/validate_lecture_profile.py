"""Reproducible, bounded TDX comparison. Not a holdout or an optimization search."""
from datetime import datetime
from pathlib import Path

from .data import dump_json, fingerprint
from .integrated_strategy import SystemStrategy, generate_system_signals
from .visualization import ChartRepository


def validate(root,tdx_root,output,run=None,start='2018-01-01',asof=None):
    repo=ChartRepository(Path(root),tdx_root=Path(tdx_root))
    run=run or repo.catalog()['runs'][0]['id']
    asof=asof or repo.runs[run]['report']['data']['end']
    # Fixed pre-existing research sample plus the user's Shanghai Airport case.
    # No selection by this run's returns, and no unrequested all-market workload.
    symbols=sorted(set(repo.bars(run))|{'sh.600009'})
    target=Path(output).resolve()
    if target.exists():raise ValueError('use a new output directory; no overwriting validation runs')
    target.mkdir(parents=True)
    rows=[];prefix_checks=[]
    for symbol in symbols:
        for variant,scenario in [('strict_full','base'),('proxy_full','base'),('lecture_v1','base'),('lecture_v1','cost_2x')]:
            config=repo.strategy_config(run,variant)
            bars,generated,view=repo.tdx_backtester.run(symbol,start,asof,config['strategy'],config['scenarios'][scenario]['execution'])
            view.update(variant=variant,scenario=scenario,parameter_source_run=run,
                        strategy_profile=dict(id=variant,version=config.get('profile_version',variant),definition=config.get('definition',{})))
            relative=f'{variant}/{scenario}/{symbol}.json';dump_json(target/relative,view)
            counts=generated.counts
            assert counts.get('entry_candidate_evaluations',0)==counts.get('entry_candidate_rejections',0)+counts.get('long_signals',0)
            if variant=='lecture_v1' and scenario=='base':
                for signal in generated.signals:
                    if signal.side!='LONG':continue
                    evidence=next(e for e in generated.audit if e['bar_index']==signal.bar_index and e['event']=='long_transition_evidence')
                    assert evidence['flip_index']<evidence['alternation_index']<=evidence['bullish_index']<evidence['attack']<signal.bar_index
                    assert signal.regime in ('轧空','强轧空') and signal.rvol>=1.2 and signal.retracement<2/3
                cuts={min(n,len(bars)-1) for n in (125,500,1000)}
                cuts.update(max(0,s.bar_index+d) for s in generated.signals if s.side=='LONG' for d in (-1,0,1))
                for i in sorted(cuts):
                    if i>=len(bars):continue
                    prefix=generate_system_signals(bars[:i+1],SystemStrategy(**config['strategy']))
                    assert prefix.signals==[s for s in generated.signals if s.bar_index<=i],(symbol,i,'signal prefix changed')
                    prefix_checks.append(dict(symbol=symbol,asof=bars[i].timestamp.date().isoformat(),passed=True))
            rows.append(dict(symbol=symbol,variant=variant,scenario=scenario,path=relative,
                             metrics=view['metrics'],counts=counts,diagnostics=view['backtest']['diagnostics']))
            print(f'{symbol} {variant}/{scenario}: LONG={counts.get("long_signals",0)} fills={view["metrics"]["entry_fills"]} '
                  f'closed={view["metrics"]["trades"]} return={view["metrics"]["total_return"]:.4%}',flush=True)
    report=dict(created_at=datetime.now().astimezone().isoformat(),parameter_source_run=run,start=start,asof=asof,
                strategy_profile=repo.strategy_config(run,'lecture_v1'),symbols=symbols,results=rows,prefix_checks=prefix_checks,
                source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                scope='independent_single_stock_accounts_not_a_shared_portfolio',
                limitations=['previously_seen_sample_not_holdout','current_local_membership_survivorship_bias',
                             'no_parameter_optimization','sparse_trades_cannot_establish_an_edge'])
    dump_json(target/'report.json',report)
    dump_json(target/'artifacts.json',{p.relative_to(target).as_posix():fingerprint(p) for p in sorted(target.rglob('*.json'))})
    print(str(target/'report.json'),flush=True)
    return target


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',default='results/operations_v1');parser.add_argument('--tdx-root',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--run');parser.add_argument('--start',default='2018-01-01')
    parser.add_argument('--asof');args=parser.parse_args();validate(**vars(args))
