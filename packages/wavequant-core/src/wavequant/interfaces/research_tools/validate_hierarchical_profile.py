"""Fixed-sample V2 regression and causal-prefix checks, never profit optimization."""
from datetime import datetime
from pathlib import Path
from wavequant.infrastructure.market_data.data import dump_json, fingerprint
from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def validate(output, root='results/operations_v1', tdx_root='D:/TDX', start='2018-01-01', asof='2026-09-07'):
    repo=ChartRepository(Path(root),tdx_root=Path(tdx_root)); run=repo.catalog()['runs'][0]['id']
    target=Path(output).resolve()
    if target.exists():raise ValueError('new output directory required')
    target.mkdir(parents=True)
    symbols=sorted(set(repo.bars(run))|{'sh.600009'})
    rows=[];checks=[];proofs=[]
    for symbol in symbols:
        config=repo.strategy_config(run,'lecture_v2')
        bars,generated,view=repo.tdx_backtester.run(symbol,start,asof,config['strategy'],config['scenarios']['base']['execution'])
        view['strategy_profile']=dict(id='lecture_v2',version=config['profile_version'],definition=config['definition'])
        dump_json(target/f'{symbol}.json',view)
        for s in generated.signals:
            if s.side!='LONG':continue
            e=next(e for e in generated.audit if e['bar_index']==s.bar_index and e['event']=='long_transition_evidence')
            assert e['trend_level'] in (1,2,3)
            assert e['flip_index']<e['alternation_index']<e['attack']<s.bar_index
            assert s.regime in ('轧空','强轧空') and s.rvol>=1.2
            if e['priority']==2:
                assert e['alternation_index']<e['maturity_index']<=e['impulse_high_index']<e['pullback_index']<e['attack']
                assert s.retracement<config['strategy']['mature_shallow_ratio']
            else:assert not e['counter_filter_applied']
            proofs.append(dict(e,signal_date=s.timestamp.date().isoformat()))
        cuts={min(500,len(bars)-1),min(1000,len(bars)-1)}
        cuts.update(max(0,s.bar_index+d) for s in generated.signals if s.side=='LONG' for d in (-1,0,1))
        for i in sorted(cuts):
            if i>=len(bars):continue
            prefix=generate_system_signals(bars[:i+1],SystemStrategy(**config['strategy']))
            assert prefix.signals==[s for s in generated.signals if s.bar_index<=i],(symbol,i)
            # Both fills and rejection diagnostics are distinct from a LONG observation.
            checks.append(dict(symbol=symbol,asof=bars[i].timestamp.date().isoformat(),passed=True))
        counts=generated.counts
        assert counts.get('entry_candidate_evaluations',0)==counts.get('entry_candidate_rejections',0)+counts.get('long_signals',0)
        rows.append(dict(symbol=symbol,metrics=view['metrics'],counts=counts))
        print(f"{symbol}: type1={counts.get('buy_point_transition_squeeze',0)} type2={counts.get('buy_point_mature_shallow_squeeze',0)} fills={view['metrics']['entry_fills']} return={view['metrics']['total_return']:.4%}",flush=True)
    report=dict(created_at=datetime.now().astimezone().isoformat(),start=start,asof=asof,results=rows,
        strategy_profile=config,symbols=symbols,prefix_checks=checks,buy_point_evidence=proofs,
        source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        limitations=['fixed_previously_seen_sample_not_holdout','independent_single_stock_accounts_not_portfolio',
                     'not_a_profitability_claim','no_parameter_search'])
    dump_json(target/'report.json',report)
    print(target/'report.json',flush=True)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    parser.add_argument('--root',default='results/operations_v1');parser.add_argument('--tdx-root',default='D:/TDX')
    parser.add_argument('--start',default='2018-01-01');parser.add_argument('--asof',default='2026-09-07')
    validate(**vars(parser.parse_args()))
