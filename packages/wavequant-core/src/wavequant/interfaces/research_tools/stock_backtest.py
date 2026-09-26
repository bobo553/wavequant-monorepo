"""Independent single-security diagnostic accounts, never live orders."""
from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.application.analytics.execution_diagnostics import execution_diagnostics
from wavequant.application.analytics.trade_evidence import enrich_ledger


def _post_b_wave_exit_events(bars, audit, signal_row):
    """Measure exit stages from positive Ns whose own origin belongs to C's segment."""
    b_index = signal_row['wave_b_low_index']
    owner = signal_row['bar_index']
    events = [dict(event='wave_segment_start', attack=owner, bar_index=owner,
                   origin_index=b_index, owner_signal_index=owner)]
    for row in audit:
        if (row.get('event') != 'n_completed' or row.get('direction') != 'up'
                or row['origin'] < b_index or row['bar_index'] <= b_index):
            continue
        attack = row['bar_index']
        known = max(attack, row['known_at'])
        origin_price = bars[row['origin']].low
        defense = row.get('defense', origin_price)
        reached = set()
        for j in range(known, len(bars)):
            if j > attack and bars[j].low < defense:
                events.append(dict(event='wave_projection_invalidated', attack=attack,
                                   origin_index=row['origin'], bar_index=j,
                                   owner_signal_index=owner))
                break
            # A target first known on this bar can only use its close. Later
            # bars may use their high, matching the causal N milestone rule.
            observed = bars[j].close if j == known else bars[j].high
            for stage in ('one_p', 'two_t'):
                target = row[stage]
                if target is not None and stage not in reached and observed >= target:
                    events.append(dict(event='wave_n_target_reached', attack=attack,
                                       origin_index=row['origin'], bar_index=j,
                                       reached_stage=stage, reached_target=target,
                                       owner_signal_index=owner))
                    reached.add(stage)
    for row in audit:
        if (row.get('event', '').startswith('wave_projection_')
                and row.get('origin_index', -1) >= b_index
                and row['attack'] > b_index):
            events.append(dict(row, owner_signal_index=owner))
    return events


def single_stock_result(bars, strategy, execution, signal_result=None, *, minute_loader=None, progress=None):
    if not bars or len({b.symbol for b in bars})!=1:
        raise ValueError('exactly one nonempty security history required')
    if signal_result is None: signal_result=generate_system_signals(bars,SystemStrategy(**strategy))
    config=StrategyConfig(**execution); config.validate()
    entry_executions, entry_fallbacks = {}, []
    if config.consolidation_entry_intraday:
        from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
        signal_result, entry_executions, entry_fallbacks = resolve_consolidation_entries(
            bars, signal_result, SystemStrategy(**strategy), minute_loader,
            daily_fallback=config.missing_minute_daily_fallback)
    n_bars = {}
    for row in getattr(signal_result, 'audit', []):
        if row.get('event') == 'n_completed' and row.get('direction') == 'up':
            attack = row['bar_index']
            available = max(attack, row.get('known_at', attack))
            n_bars[available] = max(attack, n_bars.get(available, -1))
    audit_rows = getattr(signal_result, 'audit', [])
    wave_events=[row for row in audit_rows if row.get('event', '').startswith('wave_projection_')]
    for row in audit_rows:
        if row.get('event') == 'long_signal' and row.get('channel') == 'wave_push_gap' and isinstance(row.get('wave_b_low_index'), int):
            wave_events.extend(_post_b_wave_exit_events(bars, audit_rows, row))
        elif row.get('event') == 'long_signal' and row.get('wave_a_class') == 'ordinary':
            wave_events.append(dict(event='wave_ordinary_entry', attack=row['attack'],
                bar_index=row['bar_index'], target=row['wave_equal_target'],
                one_p=row['wave_entry_one_p'], two_t=row['wave_entry_two_t'],
                a_origin=row['wave_a_origin'], a_high=row['wave_a_high'],
                a_high_index=row['wave_a_high_index'], b_low=row['wave_b_low'],
                b_low_index=row['wave_b_low_index']))
    result=run_portfolio({bars[0].symbol:bars},signal_result.signals,config,minute_loader=minute_loader,positive_n_bars={bars[0].symbol:n_bars},entry_executions=entry_executions,
        wave_events={bars[0].symbol:wave_events},progress=progress)
    result.minute_fallbacks.extend(entry_fallbacks)
    for order in result.orders:
        if order['side'] == 'BUY' and order.get('execution_model') == 'same_day_close':
            fallback = next((f for f in entry_fallbacks if f['date'] == order['timestamp'][:10]), None)
            if fallback is not None:
                order['minute_fallback'] = fallback
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
