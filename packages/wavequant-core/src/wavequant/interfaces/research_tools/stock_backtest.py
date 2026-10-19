"""Independent single-security diagnostic accounts, never live orders."""

from wavequant.domain.market_structure.trend_options import with_strategy_trend_options
from dataclasses import asdict
from datetime import datetime
from typing import Callable
import json
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.backtest_history_reuse import BacktestHistoryReuse
from wavequant.application.analytics.execution_diagnostics import execution_diagnostics
from wavequant.application.analytics.trade_evidence import enrich_ledger


def _post_b_wave_exit_events(bars, audit, signal_row):
    """Measure C-segment Ns with the same lifetime reducer used by live accounts."""
    from .signal_execution_stream import post_b_wave_exit_events
    return post_b_wave_exit_events(bars, audit, signal_row)


def signal_progress_callbacks(
    callback: Callable[[int, str], None] | None,
) -> tuple[Callable[[int], None] | None, Callable[[str], None] | None]:
    if callback is None:
        return None, None
    current = 35
    current_stage = '生成信号'

    def advance(percent: int) -> None:
        nonlocal current
        current = max(current, 35 + percent * 25 // 100)
        callback(current, current_stage)

    def stage(name: str) -> None:
        nonlocal current_stage
        current_stage = name
        callback(current, name)

    return advance, stage


def simulation_progress_callbacks(
    callback: Callable[[int, str], None] | None,
) -> tuple[Callable[[int], None] | None, Callable[[str], None] | None]:
    """Report real simulation boundaries without moving its percentage backwards."""
    if callback is None:
        return None, None
    current = 65

    def advance(percent: int) -> None:
        nonlocal current
        current = max(current, 65 + percent * 25 // 100)
        callback(current, '模拟成交')

    def stage(name: str) -> None:
        callback(current, name)

    return advance, stage


def signal_execution_facts(bars, signal_result):
    from .signal_execution_stream import SignalExecutionFactsStream
    return SignalExecutionFactsStream(bars).advance(len(bars) - 1, getattr(signal_result, 'audit', []))


@with_strategy_trend_options("strategy")
def single_stock_result(bars, strategy, execution, signal_result=None, *, minute_loader=None, progress=None,
                        stage: Callable[[str], None] | None = None,
                        history_reuse: BacktestHistoryReuse | None = None,
                        account_result=None, entry_fallbacks_override=None):
    if not bars or len({b.symbol for b in bars})!=1:
        raise ValueError('exactly one nonempty security history required')
    effective_strategy=SystemStrategy(**strategy); effective_strategy.validate()
    if account_result is None and effective_strategy.buy_point_definition == 'whole_flip_wave_v3':
        # 预计算信号没有账户逐日资格边界；整波策略统一重新走同日协调器。
        from .streaming_backtest import streaming_stock_result
        def daily_progress(percent, message):
            if stage is not None: stage(message)
            if progress is not None: progress(percent)
        return streaming_stock_result(bars,strategy,execution,minute_loader=minute_loader,
                                      progress=daily_progress)[1]
    if history_reuse is not None: history_reuse.require_input(bars, effective_strategy)
    if signal_result is None:
        signal_result=generate_system_signals(bars,effective_strategy,history_reuse=history_reuse)
    config=StrategyConfig(**execution); config.validate()
    entry_executions: dict = {}
    entry_fallbacks: list[dict] = []
    if account_result is None and config.consolidation_entry_intraday and signal_result.counts.get('trading_interval_count') != 0:
        from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
        if stage is not None: stage('准备分钟入场历史')
        signal_result, entry_executions, entry_fallbacks = resolve_consolidation_entries(
            bars, signal_result, effective_strategy, minute_loader,
            daily_fallback=config.missing_minute_daily_fallback, stage=stage, history_reuse=history_reuse)
    n_bars, wave_events = signal_execution_facts(bars, signal_result) if account_result is None else ({}, [])
    result=account_result if account_result is not None else run_portfolio({bars[0].symbol:bars},signal_result.signals,config,minute_loader=minute_loader,positive_n_bars={bars[0].symbol:n_bars},entry_executions=entry_executions,
        wave_events={bars[0].symbol:wave_events},progress=progress, stage=stage,
        history_reuse={bars[0].symbol:history_reuse} if history_reuse is not None else None,
        n_target_trend_confirmation_enabled=effective_strategy.n_target_trend_confirmation_enabled)
    if entry_fallbacks_override is not None:
        entry_fallbacks = entry_fallbacks_override
    result.minute_fallbacks.extend(entry_fallbacks)
    for order in result.orders:
        if order['side'] == 'BUY' and order.get('execution_model') == 'same_day_close':
            fallback = next((f for f in entry_fallbacks if f['date'] == order['timestamp'][:10]), None)
            if fallback is not None:
                order['minute_fallback'] = fallback
    if stage is not None: stage('整理成交证据')
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
                              screening=screening,minute_fallbacks=result.minute_fallbacks,
                              diagnostics=execution_diagnostics(result),open_positions=result.open_positions,
                              holding_drawdowns=result.holding_drawdowns,
                              provenance='current_engine_on_verified_snapshot_prefix',
                              warning='历史诊断；独立账户不等于组合分摊，无成交或样本有限不能证明策略有效。'))


def export_all(root,output,run_id=None):
    from wavequant.interfaces.charts.visualization import ChartRepository, VARIANTS, SCENARIOS
    from wavequant.infrastructure.market_data.data import dump_json, fingerprint
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
