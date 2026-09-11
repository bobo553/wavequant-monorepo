"""Fixed-protocol chronological diagnostics; never promotes a profitable winner."""
from dataclasses import asdict, replace
from datetime import date
import json
from pathlib import Path

from .backtest import run_portfolio
from .config import StrategyConfig
from .data import dump_json, fingerprint
from .execution_diagnostics import execution_diagnostics
from .integrated_strategy import SystemStrategy, generate_system_signals
from .io import load_bars, write_signals
from .research import save_result, block_bootstrap


def walk_forward_folds(sessions, *, train=756, validation=252, test=126, gap=40):
    """Expanding train, rolling validation, disjoint test windows, explicit gaps.

    No fit/selection is performed here. Gap is a configured isolation buffer,
    NOT a proof of purging arbitrary overlapping labels or open positions.
    """
    if any(type(x) is not int or x<=0 for x in (train,validation,test)) or type(gap) is not int or gap<0:
        raise ValueError('invalid chronological window sizes')
    days=list(sessions)
    if days!=sorted(set(days)): raise ValueError('unique ordered sessions required')
    for d in days: date.fromisoformat(d)
    start=train+gap+validation+gap; folds=[]
    while start+test<=len(days):
        val_end=start-gap; val_start=val_end-validation; train_end=val_start-gap
        folds.append(dict(fold=len(folds),train=[days[0],days[train_end-1]],
            validation=[days[val_start],days[val_end-1]],test=[days[start],days[start+test-1]],
            gap_sessions=gap,test_sessions=test))
        start+=test
    return folds


def audit_annotations(expected, observed):
    """Exact dated event matching. Empty gold set is unavailable, not 100%."""
    def keys(rows):
        result=[]
        for r in rows:
            k=(r['symbol'],r['timestamp'],r['event'])
            if not all(isinstance(v,str) and v for v in k): raise ValueError('annotation identity required')
            result.append(k)
        if len(set(result))!=len(result): raise ValueError('duplicate annotation identity')
        return set(result)
    gold,pred=keys(expected),keys(observed)
    if not gold: return dict(status='unavailable_no_independent_annotations',precision=None,recall=None)
    tp=len(gold&pred)
    return dict(status='measured',true_positive=tp,false_positive=len(pred-gold),false_negative=len(gold-pred),
                precision=tp/len(pred) if pred else None,recall=tp/len(gold),
                missed=[list(k) for k in sorted(gold-pred)],extra=[list(k) for k in sorted(pred-gold)])


def run_validation_suite(csv_path, protocol_path, output):
    output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError('validation output must be empty')
    output.mkdir(parents=True,exist_ok=True)
    csv_path,protocol_path=Path(csv_path),Path(protocol_path)
    protocol=json.loads(protocol_path.read_text(encoding='utf-8'))
    metadata=json.loads(csv_path.with_suffix('.metadata.json').read_text(encoding='utf-8'))
    if fingerprint(csv_path)!=metadata['sha256']: raise ValueError('data provenance hash mismatch')
    grouped=load_bars(csv_path)
    sessions=sorted({b.timestamp.date().isoformat() for bars in grouped.values() for b in bars})
    if sessions[-1]!=protocol['data_end']: raise ValueError('protocol/data endpoint mismatch')
    base_execution=replace(StrategyConfig(),**protocol['execution'])
    base_execution.validate()
    folds=walk_forward_folds(sessions,gap=base_execution.max_hold_bars)
    report=dict(kind='previously_seen_history_fixed_rule_diagnostics',selection='none',
        folds=folds,variants={},protocol=protocol,data_sha256=fingerprint(csv_path),
        calendar_kind='observed_union_not_official_exchange_calendar',
        independent_holdout=False,production_ready=False,
        statistical_evidence='not_established',annotation_audit=audit_annotations([],[]),
        warning='Each test window starts in cash; positions are marked, not force-sold at its end. Fold returns cannot be concatenated into a tradable equity curve.')
    for name in ('strict_full','proxy_full'):
        cfg=dict(entry_policy='legacy_n_continuation',preflight_reward_risk=False,squeeze_pullback_entries=False)
        cfg.update(protocol['strategy']); cfg.update(protocol['variants'][name])
        strategy=SystemStrategy(**cfg); strategy.validate()
        signals=[]
        for symbol,bars in sorted(grouped.items()):
            signals.extend(generate_system_signals(bars,strategy).signals)
            print(f'validation {name}: {symbol}',flush=True)
        signals.sort(key=lambda s:(s.timestamp,s.symbol,s.side))
        write_signals(output/name/'signals.csv',signals)
        row=dict(strategy=asdict(strategy),folds=[],scenarios={})
        for f in folds:
            result=run_portfolio(grouped,signals,base_execution,*f['test'])
            save_result(output/name/'folds'/str(f['fold']),result)
            row['folds'].append(dict(fold=f['fold'],metrics=result.metrics,diagnostics=execution_diagnostics(result)))
        scenarios={
            'base':base_execution,
            'cost_2x':replace(base_execution,commission_bps_per_side=base_execution.commission_bps_per_side*2,
                            minimum_commission=base_execution.minimum_commission*2,
                            slippage_bps_per_side=base_execution.slippage_bps_per_side*2),
            'cost_3x':replace(base_execution,commission_bps_per_side=base_execution.commission_bps_per_side*3,
                            minimum_commission=base_execution.minimum_commission*3,
                            slippage_bps_per_side=base_execution.slippage_bps_per_side*3),
            'capacity_half':replace(base_execution,max_participation=base_execution.max_participation/2),
        }
        for scenario,execution in scenarios.items():
            result=run_portfolio(grouped,signals,execution)
            save_result(output/name/scenario,result)
            row['scenarios'][scenario]=dict(metrics=result.metrics,diagnostics=execution_diagnostics(result),
                                           execution=execution.to_dict())
            if scenario=='base':
                row['bootstrap']=block_bootstrap(result.equity,base_execution.initial_capital)
                row['bootstrap']['warning']='Exploratory return-resampling only; not selection-corrected evidence. Sparse trades cannot establish an edge.'
                row['closed_trade_attribution']={symbol:dict(trades=sum(t.symbol==symbol for t in result.trades),
                    pnl=sum(t.pnl for t in result.trades if t.symbol==symbol)) for symbol in grouped}
                row['minimum_trade_gate_passed']=result.metrics['trades']>=protocol['gate']['minimum_train_trades']
        report['variants'][name]=row
    dump_json(output/'report.json',report)
    return report
