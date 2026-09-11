"""Independent single-security diagnostic accounts, never live orders."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from .backtest import run_portfolio
from .config import StrategyConfig
from .integrated_strategy import SystemStrategy, generate_system_signals
from .execution_diagnostics import execution_diagnostics
from .trade_evidence import enrich_ledger


def single_stock_result(bars, strategy, execution, signal_result=None):
    if not bars or len({b.symbol for b in bars})!=1:
        raise ValueError('exactly one nonempty security history required')
    if signal_result is None: signal_result=generate_system_signals(bars,SystemStrategy(**strategy))
    config=StrategyConfig(**execution); config.validate()
    result=run_portfolio({bars[0].symbol:bars},signal_result.signals,config)
    screening=enrich_ledger(bars,result,signal_result,strategy)
    def serial(value):
        return json.loads(json.dumps(value,default=lambda obj:obj.isoformat() if isinstance(obj,datetime) else str(obj)))
    metrics=dict(result.metrics)
    metrics['win_rate']=metrics['win_rate'] if metrics['trades'] else None
    metrics['equity']=metrics['final_equity']
    audit=[]
    for event in getattr(signal_result,'audit',[]):
        item=dict(event)
        if item.get('buy_point_type'):
            for key in ('flip_index','alternation_index','maturity_index','attack','pullback_index',
                        'flip_high_index','alternation_low_index','impulse_origin_index','impulse_high_index',
                        'origin_index','peak_index','minimum_close_index','eligibility_frozen_at'):
                if isinstance(item.get(key),int) and 0<=item[key]<=item['bar_index']:
                    item[key+'_date']=bars[item[key]].timestamp.date().isoformat()
        audit.append(item)
    return dict(metrics=metrics,equity=result.equity,orders=result.orders,audit=audit,
                trades=serial([asdict(t) for t in result.trades]),
                signals=[dict(serial(asdict(s)),time=s.timestamp.date().isoformat()) for s in signal_result.signals],
                backtest=dict(scope='single_stock_independent_account',start=bars[0].timestamp.date().isoformat(),
                              end=bars[-1].timestamp.date().isoformat(),initial_capital=config.initial_capital,
                              strategy=strategy,execution=config.to_dict(),counts=signal_result.counts,
                              screening=screening,
                              diagnostics=execution_diagnostics(result),open_positions=result.open_positions,
                              provenance='current_engine_on_verified_snapshot_prefix',
                              warning='历史诊断；独立账户不等于组合分摊，无成交或样本有限不能证明策略有效。'))


def export_all(root,output,run_id=None):
    from .visualization import ChartRepository, VARIANTS, SCENARIOS
    from .data import dump_json, fingerprint
    repository=ChartRepository(root); run=run_id or repository.catalog()['runs'][0]['id']
    target=Path(output).resolve()
    if target.exists(): raise ValueError('use a new output directory; never overwrite an existing run')
    rows=[]; target.mkdir(parents=True)
    for variant in VARIANTS:
        for scenario in SCENARIOS:
            for symbol,bars in sorted(repository.bars(run).items()):
                view=repository.stock_view(run,variant,symbol,bars[-1].timestamp.date().isoformat(),scenario)
                relative=f'{variant}/{scenario}/{symbol}.json'
                dump_json(target/relative,view)
                rows.append(dict(symbol=symbol,variant=variant,scenario=scenario,metrics=view['metrics'],
                                 diagnostics=view['backtest']['diagnostics'],path=relative))
                print(f'{variant} {scenario} {symbol}: trades={view["metrics"]["trades"]} return={view["metrics"]["total_return"]:.6%}',flush=True)
    dump_json(target/'report.json',dict(source_run=run,created_at=datetime.now().astimezone().isoformat(),
        account_scope='independent_single_stock',results=rows,
        source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
        source_manifest_sha256=repository.runs[run]['state']['artifacts_sha256'],
        warning='Fixed existing strategies, no tuning, no new holdout; not proof of profitability.'))
    dump_json(target/'artifacts.json',{p.relative_to(target).as_posix():fingerprint(p) for p in sorted(target.rglob('*.json'))})
    return target


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',required=True);parser.add_argument('--output',required=True);parser.add_argument('--run')
    args=parser.parse_args();print(export_all(args.root,args.output,args.run))
