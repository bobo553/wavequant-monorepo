"""Four fixed whole-wave ratio plans; independent accounts, not a profitability proof."""
from pathlib import Path

from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.domain.strategies.strategy_profiles import WAVE_PROFILES
from wavequant.domain.strategies.integrated_strategy import SystemStrategy,generate_system_signals
from wavequant.domain.strategies.whole_wave_entry import price,threshold
from wavequant.infrastructure.market_data.data import dump_json


def validate(output):
    target=Path(output);target.mkdir(parents=True,exist_ok=False)
    repo=ChartRepository('results/operations_v1','D:/TDX');rid='acceptance_20260908_verified'
    symbols=['sh.600009','sh.600104','sh.600519','sh.600276','sh.600900','sh.601318',
             'sh.600036','sz.000333','sz.000651','sz.000858','sz.002415']
    rows=[];checks=0
    for variant,(deep,shallow) in WAVE_PROFILES.items():
        config=repo.strategy_config(rid,variant);nwin=nclosed=0;counts=[0,0]
        for symbol in symbols:
            bars,g,view=repo.tdx_backtester.run(symbol,'2018-01-01','2026-09-07',config['strategy'],config['scenarios']['base']['execution'])
            for s in g.signals:
                if s.side!='LONG':continue
                e=next(e for e in g.audit if e['event']=='long_transition_evidence' and e['bar_index']==s.bar_index)
                assert e['definition']=='whole_flip_wave_v3'
                assert e['flip_index']<e['alternation_index']<e['attack']<=s.bar_index
                assert min(b.low for b in bars[e['origin_index']:s.bar_index+1])>=e['origin_price']
                if e['priority']==1:
                    ratio=(price(e['flip_high_price'])-price(e['alternation_low_price']))/(price(e['flip_high_price'])-price(e['origin_price']))
                    assert ratio>threshold(deep)
                else:
                    assert e['alternation_index']<e['maturity_index']<=e['peak_index']<e['pullback_index']<e['attack']
                    assert e['counter_price']==min(b.close for b in bars[e['peak_index']:s.bar_index+1])
                    ratio=(price(e['peak_price'])-price(e['counter_price']))/(price(e['peak_price'])-price(e['origin_price']))
                    assert ratio<=threshold(shallow)
                assert abs(float(ratio)-s.retracement)<1e-12
                counts[e['priority']-1]+=1
            cuts={500,1000}
            # Check the first and latest observed LONG boundaries per security.
            longs=[s.bar_index for s in g.signals if s.side=='LONG']
            for i in (longs[:1]+longs[-1:]):cuts.update((i-1,i,i+1))
            for cut in sorted(i for i in cuts if 0<=i<len(bars)):
                prefix=generate_system_signals(bars[:cut+1],SystemStrategy(**config['strategy']))
                assert prefix.signals==[s for s in g.signals if s.bar_index<=cut],(variant,symbol,cut)
                checks+=1
            nclosed+=len(view['trades']);nwin+=sum(t['pnl']>0 for t in view['trades'])
            for o in view['orders']:
                if o['side']=='BUY' and o['status']=='filled':
                    assert all(c['passed'] is True for c in o['entry_conditions'][:3]),o
            dump_json(target/variant/f'{symbol}.json',view)
            print(variant,symbol,'LONG',g.counts['long_signals'],'fills',view['metrics']['entry_fills'],flush=True)
        rows.append(dict(variant=variant,deep=deep,shallow=shallow,type1=counts[0],type2=counts[1],
                         closed=nclosed,winners=nwin,pooled_trade_win_rate=nwin/nclosed if nclosed else None))
        dump_json(target/'report.json',dict(rows=rows,prefix_checks=checks,symbols=symbols,
            start='2018-01-01',asof='2026-09-07',scope='fixed_previously_seen_11_stock_independent_accounts',
            warning='descriptive_in_sample_comparison_not_portfolio_or_holdout_proof',engine=repo.tdx_backtester.engine))
    print(rows,flush=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);validate(p.parse_args().output)
