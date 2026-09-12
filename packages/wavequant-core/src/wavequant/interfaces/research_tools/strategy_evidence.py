"""Frozen strategy challengers, disclosed failures and no automatic promotion."""
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.infrastructure.market_data.data import dump_json, fingerprint
from wavequant.infrastructure.persistence.event_store import EventStore
from wavequant.infrastructure.persistence.experiment_registry import ExperimentRegistry, environment_snapshot
from wavequant.application.analytics.evidence_statistics import equity_returns, joint_block_test, choose_development, evidence_gate
from wavequant.application.analytics.execution_diagnostics import execution_diagnostics
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.infrastructure.market_data.io import load_bars, write_signals
from wavequant.infrastructure.filesystem.project_paths import PROJECT_ROOT
from wavequant.application.analytics.research import save_result, benchmark
from wavequant.interfaces.research_tools.research_panel import build_research_panel
from wavequant.application.analytics.validation_suite import walk_forward_folds


def strategy_configs(protocol):
    base=protocol['strategy']
    result={name:SystemStrategy(**(base|item['changes'])) for name,item in protocol['candidates'].items()}
    result['strict_reference']=SystemStrategy(**(base|protocol['strict_reference']))
    for c in result.values():
        c.validate()
        if c.entry_policy!='transitioned_squeeze' or not c.regime_filter:
            raise ValueError('study must retain transitioned squeeze entry policy')
    return result


def now(): return datetime.now(timezone.utc).isoformat()


def _evaluate_symbol(job):
    name,symbol,bars,cfg,check_prefix=job
    r=generate_system_signals(bars,cfg)
    checks=[]
    if check_prefix:
        for stop in (len(bars)//2,3*len(bars)//4):
            prefix=generate_system_signals(bars[:stop],cfg).signals
            passed=prefix==[s for s in r.signals if s.bar_index<stop]
            checks.append(dict(candidate=name,symbol=symbol,bars=stop,passed=passed))
            if not passed: raise AssertionError('future suffix altered a dated signal')
    return (symbol,r.signals,r.counts,dict(Counter(e['event'] for e in r.audit)),
            dict(Counter(e['reason'] for e in r.audit if e['event'] in ('entry_rejected','entry_preflight_rejected'))),checks)


def run_strategy_evidence(tdx_root, protocol_path, output, tests, *, workers=4):
    if tests.get('status')!='passed': raise ValueError('engineering test gate must pass')
    if type(workers) is not int or not 1<=workers<=8: raise ValueError('workers must be between 1 and 8')
    output=Path(output); protocol_path=Path(protocol_path).resolve()
    if output.exists() and any(p.name!='test_log.txt' for p in output.iterdir()): raise ValueError('new experiment directory required')
    output.mkdir(parents=True,exist_ok=True)
    protocol=json.loads(protocol_path.read_text(encoding='utf-8')); configs=strategy_configs(protocol)
    periods=protocol['periods']; last=None
    for label in ('development','validation','diagnostic'):
        begin,end=periods[label]
        if begin>end or (last and begin<=last) or begin<protocol['data_start'] or end>protocol['data_end']:
            raise ValueError('ordered covered nonoverlapping development/validation/diagnostic periods required')
        last=end
    dump_json(output/'protocol_frozen.json',protocol)
    path,metadata,universe=build_research_panel(tdx_root,output/'panel',protocol)
    grouped=load_bars(path)
    if metadata['end']!=protocol['data_end']: raise ValueError('actual panel endpoint does not reach fixed protocol')
    execution=replace(StrategyConfig(),**protocol['execution']); execution.validate()
    stress=replace(execution,commission_bps_per_side=execution.commission_bps_per_side*2,
                   minimum_commission=execution.minimum_commission*2,slippage_bps_per_side=execution.slippage_bps_per_side*2)
    store=EventStore(output/'experiments.sqlite'); registry=ExperimentRegistry(store); run_id='squeeze-evidence-v5'
    environment=environment_snapshot(PROJECT_ROOT)
    inputs={str(p.resolve()):fingerprint(p) for p in (protocol_path,path,path.with_suffix('.metadata.json'),output/'panel/universe_frozen.json')}
    registry.start(run_id,now(),config=protocol,inputs=inputs,environment=environment)
    dump_json(output/'environment.json',environment)
    try:
        candidates={}; all_signals={}; development={}; diagnostic_paths={}; diagnostic_dates=None
        prefix_checks=[]
        for name,cfg in configs.items():
            signals=[]; event_counts=Counter(); reasons=Counter(); stock_counts={}
            jobs=[(name,symbol,bars,cfg,number==0) for number,(symbol,bars) in enumerate(sorted(grouped.items()))]
            pool=ProcessPoolExecutor(max_workers=workers) if workers>1 else None
            try:
                results=pool.map(_evaluate_symbol,jobs) if pool else map(_evaluate_symbol,jobs)
                for number,(symbol,ss,counts,ec,rc,checks) in enumerate(results,1):
                    signals.extend(ss); stock_counts[symbol]=counts
                    event_counts.update(ec); reasons.update(rc); prefix_checks.extend(checks)
                    print(f'evidence {name} {number}/{len(grouped)} {symbol}: long={counts.get("long_signals",0)}',flush=True)
            finally:
                if pool: pool.shutdown(wait=True,cancel_futures=True)
            signals.sort(key=lambda s:(s.timestamp,s.symbol,s.side)); all_signals[name]=signals
            write_signals(output/name/'signals.csv',signals)
            dump_json(output/name/'stock_counts.json',stock_counts)
            row=dict(config=asdict(cfg),event_counts=dict(event_counts),rejection_observations=dict(reasons),
                     count_unit='dated_observations_not_independent_trades',periods={},stress={})
            for label,(begin,end) in periods.items():
                r=run_portfolio(grouped,signals,execution,begin,end)
                s=run_portfolio(grouped,signals,stress,begin,end)
                save_result(output/name/label,r); save_result(output/name/(label+'_cost2x'),s)
                traded=len({t.symbol for t in r.trades})
                row['periods'][label]=dict(metrics=r.metrics,diagnostics=execution_diagnostics(r),traded_symbols=traded,
                    closed_trade_pnl_by_symbol={sym:sum(t.pnl for t in r.trades if t.symbol==sym) for sym in grouped})
                row['stress'][label]=s.metrics
                if label=='development' and name!='strict_reference':
                    development[name]=dict(trades=r.metrics['trades'],traded_symbols=traded,
                        total_return=r.metrics['total_return'],cost_2x_return=s.metrics['total_return'])
                if label=='diagnostic' and name!='strict_reference':
                    days,values=equity_returns(r.equity,execution.initial_capital)
                    if diagnostic_dates is not None and diagnostic_dates!=days: raise AssertionError('unaligned candidate return dates')
                    diagnostic_dates=days; diagnostic_paths[name]=values
            candidates[name]=row
            dump_json(output/name/'candidate_checkpoint.json',row)
            dump_json(output/'prefix_checks.json',prefix_checks)
        stat_cfg=protocol['statistics']
        kwargs=dict(block=stat_cfg['block_sessions'],replications=stat_cfg['replications'],seed=stat_cfg['seed'],alpha=stat_cfg['alpha'])
        statistics=joint_block_test(diagnostic_paths,**kwargs)
        control=diagnostic_paths['proxy_control']
        incremental=joint_block_test({name:[a-b for a,b in zip(values,control)]
                                     for name,values in diagnostic_paths.items() if name!='proxy_control'},**kwargs)
        selected=choose_development(development,minimum_trades=stat_cfg['minimum_closed_trades'],
                                    minimum_symbols=stat_cfg['minimum_symbols_with_closed_trades'])
        gates={}
        for name in protocol['candidates']:
            row=candidates[name]; d=row['periods']['diagnostic']
            gates[name]=evidence_gate(d['metrics'],statistics['candidates'].get(name,{}),
                traded_symbols=d['traded_symbols'],cost_return=row['stress']['diagnostic']['total_return'],requirements=stat_cfg,
                independent_holdout=False,pit_universe=False,actual_theory_path=False)
        sessions=sorted({b.timestamp.date().isoformat() for bs in grouped.values() for b in bs})
        folds=walk_forward_folds(sessions,gap=execution.max_hold_bars); rolling=[]
        for f in folds:
            train={}
            for name in protocol['candidates']:
                a=run_portfolio(grouped,all_signals[name],execution,*f['train'])
                b=run_portfolio(grouped,all_signals[name],stress,*f['train'])
                train[name]=dict(trades=a.metrics['trades'],traded_symbols=len({t.symbol for t in a.trades}),
                    total_return=a.metrics['total_return'],cost_2x_return=b.metrics['total_return'])
            choice=choose_development(train,minimum_trades=stat_cfg['minimum_closed_trades'],
                                      minimum_symbols=stat_cfg['minimum_symbols_with_closed_trades'])
            chosen=[] if choice=='CASH' else all_signals[choice]
            val=run_portfolio(grouped,chosen,execution,*f['validation'])
            r=run_portfolio(grouped,chosen,execution,*f['test'])
            save_result(output/'rolling'/str(f['fold'])/'validation',val)
            save_result(output/'rolling'/str(f['fold'])/'test',r)
            rolling.append(dict(f,selected_from_train_only=choice,train_scores=train,validation_metrics=val.metrics,
                                test_metrics=r.metrics,diagnostic=execution_diagnostics(r)))
        for label,period in periods.items(): save_result(output/'equal_weight_gross'/label,benchmark(grouped,*period,execution.initial_capital))
        report=dict(protocol=protocol,data=metadata,universe_summary=dict(selected=len(universe['selected']),
            imported=len(grouped),eligible=len(universe['eligible_symbols']),quarantine=metadata['quarantine']),
            engineering_tests=tests,workers=workers,prefix_checks=prefix_checks,candidates=candidates,
            development_scores=development,selected_for_forward_observation=selected,
            family_statistics=statistics,incremental_vs_control=incremental,gates=gates,rolling=rolling,
            benchmark='equal_weight_gross fixed local basket; not fee/risk-matched, not market alpha',
            validated=False,production_ready=False,default_strategy_changed=False,
            required_next_evidence=['historical_ST_suspension_delist_PIT_universe','actual_minor_path_or_independent_proxy_annotations',
                                    'complete_trial_history','new_unseen_forward_data_after_freeze'])
        dump_json(output/'report.json',report)
        lines=['# 轧空策略有效性研究 v5','',
            '结论：候选可被检验，但本轮不能证明策略已经有效；默认策略未替换，未启用实盘。','',
            f"固定抽样 {len(universe['selected'])} 只，导入 {len(grouped)} 只、{metadata['rows']:,} 条；隔离 {len(metadata['quarantine'])} 只且不补选。",
            '先冻结代码哈希抽样与四个假设，再计算结果。股票文件覆盖不是历史当时股票池，不能排除幸存偏差。','',
            '## 固定候选诊断结果','', '| 候选 | 开发期收益／笔数 | 验证期收益／笔数 | 诊断期收益／笔数 | 诊断成本2x | 本组调整p |',
            '|---|---:|---:|---:|---:|---:|']
        for name,row in candidates.items():
            cells=[f"{row['periods'][l]['metrics']['total_return']:.3%} / {row['periods'][l]['metrics']['trades']}" for l in periods]
            p=statistics['candidates'].get(name,{}).get('family_adjusted_p')
            lines.append(f"| {name} | {' | '.join(cells)} | {row['stress']['diagnostic']['total_return']:.3%} | {f'{p:.4f}' if p is not None else 'N/A'} |")
        lines += ['',f'开发期规则选出：**{selected}**。不够最低样本／成本要求时选 CASH，而不是强行选收益最高者。',
                  '', '## 门禁', '', '| 候选 | 研究检查通过 | 验证有效 |', '|---|---|---|']
        lines += [f"| {name} | {g['research_passed']} | {g['validated']} |" for name,g in gates.items()]
        lines += ['','## 方法与限制','',
            '- 四个候选都保留翻空为多、空多交替、多头趋势与轧空／强轧空，只分别修改一个经验阈值；严格折线另报，不用代理版冒充讲义路径。',
            '- 观察次数、信号数、委托和独立已平仓交易分开统计；重复观察不是扩大的有效样本。',
            '- 同步日期块重采样保留候选相关性，报告本组最大均值调整 p 和 Bonferroni 均值区间；另报告相对原策略增量。不是 DSR/SPA，不校正全部历史人工尝试。',
            '- p 值比较扣成本日收益均值与零现金基准，不是市场因子调整 alpha。块长、样本量和门槛是本项目事先约定，不是全球统一标准。',
            '- 30 笔／5 只／60 活跃日只是排除极稀疏结果的工程门槛，不等同于统计功效充足。',
            '- 滚动过程仅用此前训练段选择候选或 CASH，验证和测试不参与选择；各窗空仓起始，不拼接为实盘净值。',
            '- 全部历史均按开发／验证／诊断标记，原十股历史已反复检查，不称为独立新锁箱。',
            '- 下一步需要独立资料与冻结后的新数据。无论本组 p 值多小，缺失证据都不能被收益覆盖。','',
            '参考：[QuantConnect Research Guide](https://www.quantconnect.com/docs/v2/writing-algorithms/key-concepts/research-guide)、[Bailey / López de Prado, DSR](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf)。']
        (output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        registry.finish(run_id,now(),artifacts=[p for p in output.rglob('*') if p.is_file() and '.sqlite' not in p.name])
        return report
    except BaseException as exc:
        if store.get('experiment:end:'+run_id) is None:
            registry.finish(run_id,now(),artifacts=[],status='FAILED',error=f'{type(exc).__name__}: {exc}')
        raise
    finally: store.close()
