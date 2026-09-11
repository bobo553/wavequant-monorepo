"""Reproducible historical split, baseline, ablations and execution-cost stress."""
from __future__ import annotations

import csv
import html
import json
import platform
import random
import statistics
from dataclasses import replace
from pathlib import Path

from .backtest import BacktestResult, equity_metrics, run_portfolio
from .config import StrategyConfig
from .data import dump_json, fingerprint
from .features import compute_features
from .io import load_bars, write_signals, write_trades
from .quality import audit_data
from .strategy import breakout_signals, generate_signals


def write_rows(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w',encoding='utf-8',newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def save_result(directory: Path, result: BacktestResult) -> None:
    directory.mkdir(parents=True,exist_ok=True)
    write_trades(directory/'trades.csv',result.trades)
    write_rows(directory/'equity.csv',result.equity,
               ['timestamp','cash','market_value','equity','exposure','positions'])
    order_columns = ['timestamp','symbol','side','status','reason','quantity','price','fee']
    order_columns += sorted({key for row in result.orders for key in row}-set(order_columns))
    write_rows(directory/'orders.csv', result.orders, order_columns)
    dump_json(directory/'open_positions.json',result.open_positions)
    dump_json(directory/'metrics.json',result.metrics)


def make_signals(grouped, config, plain=False):
    generator = breakout_signals if plain else generate_signals
    return sorted([s for bars in grouped.values() for s in generator(compute_features(bars,config),config)],
                  key=lambda s:(s.timestamp,s.symbol,s.side))


def benchmark(grouped, start: str, end: str, capital: float) -> BacktestResult:
    """Gross fixed-basket buy-and-hold; missing symbols keep their original cash allocation."""
    calendar = {}
    for symbol,bars in grouped.items():
        for b in bars:
            if start <= b.timestamp.date().isoformat() <= end:
                calendar.setdefault(b.timestamp,{})[symbol] = b
    cash, units, marks, curve = capital, {}, {}, []
    allocation = capital/len(grouped)
    for when, current in sorted(calendar.items()):
        for symbol,b in current.items():
            if symbol not in units:
                units[symbol] = allocation/b.open
                cash -= allocation
            marks[symbol] = b.close
        value = sum(q*marks[s] for s,q in units.items())
        curve.append(dict(timestamp=when.isoformat(),cash=cash,market_value=value,
                          equity=cash+value,exposure=value/(cash+value),positions=len(units)))
    return BacktestResult([],equity_metrics(curve,capital),curve)


def _pct(value):
    return f'{value:.2%}' if value is not None else 'N/A'


def _num(value):
    return f'{value:.2f}' if value is not None else 'N/A'


def block_bootstrap(curve: list[dict], capital: float, seed: int = 20260908) -> dict:
    """Exploratory 20-session circular block bootstrap; not multiple-testing corrected."""
    values=[capital]+[r['equity'] for r in curve]
    returns=[b/a-1 for a,b in zip(values,values[1:])]
    rng=random.Random(seed)
    estimates=[]
    n=len(returns)
    if not n: return {'status':'unavailable'}
    for _ in range(1000):
        sample=[]
        while len(sample)<n:
            start=rng.randrange(n)
            sample.extend(returns[(start+j)%n] for j in range(min(20,n-len(sample))))
        estimates.append(statistics.mean(sample)*252)
    estimates.sort()
    return dict(status='exploratory',seed=seed,replications=1000,block_sessions=20,
                annualized_arithmetic_mean_ci95=[estimates[24],estimates[974]],
                caveat='Resamples the realized selected-strategy return path; selection bias and regime changes are not corrected.')


def report_files(output: Path, report: dict, test_curves: dict) -> None:
    lines = ['# 通达信主控波浪量化研究报告', '',
             f"数据：{report['data']['start']} ～ {report['data']['end']}；{report['data']['rows']:,} 条日线，{len(report['data']['symbols'])} 只股票。", '',
             f"研究门槛：**{'通过（仅研究门槛）' if report['research_gate']['passed'] else '未通过'}**。实盘就绪：**否**。", '',
             '程序跑通不等于发现超额收益；未通过的指标如实保留，不以修改测试期参数美化结果。', '',
             '## 方法与参数', '',
             f"训练期 {report['periods']['train']}，验证期 {report['periods']['validation']}，测试期 {report['periods']['test']}。候选只按训练期选择；验证与测试不参与选择。每段独立初始资金、空仓开始，指标保留此前热身历史。", '',
             f"训练期选中回撤阈值 `{report['selected_config']['max_retracement']}`、RVOL `{report['selected_config']['min_rvol']}`。",
             '所有策略共用资金账户、风险预算及成交规则。等权持有为相同股票篮子的无费用、满仓参考，不是可交易指数，也不是风险匹配基准。', '',
             '## 分段结果', '',
             '| 策略 | 区间 | 累计收益 | 年化收益 | 最大回撤 | Sharpe | 平均仓位 | 已平仓笔数 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for name, periods in report['results'].items():
        for period,m in periods.items():
            lines.append(f"| {name} | {period} | {_pct(m['total_return'])} | {_pct(m['annualized_return'])} | {_pct(m['max_drawdown'])} | {_num(m['sharpe'])} | {_pct(m['average_exposure'])} | {m.get('trades','—')} |")
    lines += ['', '## 研究门槛检查', '']
    for name, passed in report['research_gate']['checks'].items():
        lines.append(f"- {'PASS' if passed else 'FAIL'}：{name}")
    interval=report['bootstrap']['annualized_arithmetic_mean_ci95']
    lines += ['',f"20 日分块 Bootstrap（1,000 次）：选中策略测试期年化算术平均收益的探索性 95% 区间为 {_pct(interval[0])} ～ {_pct(interval[1])}。不等同于 CAGR 区间，不校正参数筛选偏差。",'',
              f"默认策略与关闭盘态过滤的信号是否完全相同：{report['diagnostics']['regime_filter_redundant']}。若为 True，说明该过滤在当前规则/样本中没有增量作用，不能当作额外确认依据。"]
    lines += ['', '## 测试期年度切片', '',
              '每年重置资金与持仓，只作稳定性诊断，不参与调参。', '',
              '| 年份 | 收益 | 最大回撤 | 交易数 |','|---|---:|---:|---:|']
    for year,m in report['annual_test_diagnostics'].items():
        lines.append(f"| {year} | {_pct(m['total_return'])} | {_pct(m['max_drawdown'])} | {m['trades']} |")
    lines += ['', '## 数据与执行边界', '']
    audit=report['data_quality']['independent_cached_raw_price_check']
    lines += [f"本地独立缓存交叉核验：{audit['compared_bars']:,} 条重叠日线 OHLC，超过 0.011 元容差的字段差异 {audit['mismatch_fields']} 处；只覆盖已有缓存日期，不能证明 2026 数据或除权事件完整性。",'']
    for item in report['limitations']:
        lines.append('- '+item)
    lines += ['', '## 可复核产物', '',
              '`report.json` 包含完整参数、数据及源码 SHA-256、版本、门槛和分段统计；`training_grid.json` 保留全部训练候选。', '',
              '每个策略/区间目录包含逐日净值、成交记录、拒单/延迟退出记录、未平仓持仓及指标。`test_log.txt` 是流水线的实际自动测试输出。', '',
              '年度收益按 252 个交易日年化，Sharpe 的无风险利率设为 0；未平仓按末次收盘市值计入净值，不强制卖出。', '',
              '费用历史依据：[印花税减半公告](https://shanxi.chinatax.gov.cn/web/detail/sx-11400-545-1780448)、[中国结算过户费调整公告报道](https://www.news.cn/money/20220429/709c5ae3ffdd44f09541ae5d590d61e1/c.html)。券商佣金属于可配置假设，需按真实账户核验。']
    markdown = '\n'.join(lines)+'\n'
    (output/'report.md').write_text(markdown,encoding='utf-8')
    # Self-contained HTML: static SVG, no scripts, network or external dependencies.
    colors = ['#167d8d','#e56b35','#6b55a3','#657a23','#bb4672','#8594a5']
    points = [r['equity']/report['selected_config']['initial_capital'] for curve in test_curves.values() for r in curve]
    low,high = min(points+[1.0]),max(points+[1.0])
    spread = max(high-low,.01)
    paths, legend = [], []
    for (name,curve),color in zip(test_curves.items(),colors):
        coords = ' '.join(f"{35+i/max(1,len(curve)-1)*930:.1f},{290-(r['equity']/report['selected_config']['initial_capital']-low)/spread*260:.1f}" for i,r in enumerate(curve))
        paths.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="2"/>')
        legend.append(f'<span style="color:{color}">{html.escape(name)}</span>')
    rendered = []
    in_table = in_list = False
    for line in lines:
        if line.startswith('|'):
            if set(line.replace('|','').replace('-','').replace(':','')) <= {' '}:
                continue
            if not in_table:
                rendered.append('<table>'); in_table = True
            rendered.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
            continue
        if in_table: rendered.append('</table>'); in_table=False
        if line.startswith('- '):
            if not in_list: rendered.append('<ul>'); in_list=True
            rendered.append('<li>'+html.escape(line[2:])+'</li>'); continue
        if in_list: rendered.append('</ul>'); in_list=False
        if line.startswith('# '): rendered.append('<h1>'+html.escape(line[2:])+'</h1>')
        elif line.startswith('## '): rendered.append('<h2>'+html.escape(line[3:])+'</h2>')
        elif line: rendered.append('<p>'+html.escape(line).replace('**','').replace('`','')+'</p>')
    if in_table: rendered.append('</table>')
    if in_list: rendered.append('</ul>')
    chart = f'<h2>测试期净值（初始 1.0）</h2><p>{" · ".join(legend)}</p><svg role="img" aria-label="测试期净值曲线" viewBox="0 0 1000 330"><text x="0" y="25">{high:.2f}</text><text x="0" y="310">{low:.2f}</text>{"".join(paths)}</svg><p>{report["periods"]["test"][0]} → {report["periods"]["test"][1]}</p>'
    (output/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>通达信量化研究报告</title><style>body{font:16px/1.7 system-ui;margin:40px auto;padding:0 24px;max-width:1200px;color:#19313c;background:#f6f8f9}table{width:100%;border-collapse:collapse;background:white;font-size:14px}td{padding:8px;border-bottom:1px solid #d9e1e4}tr:first-child{font-weight:700;background:#e6eef0}h1,h2{line-height:1.3}svg{background:white;width:100%;border:1px solid #d9e1e4}p{overflow-wrap:anywhere}</style>'+chart+''.join(rendered)+'</html>',encoding='utf-8')


def run_research(csv_path: Path, output: Path, config: StrategyConfig,
                 protocol_path: Path, test_status: dict | None = None) -> dict:
    grouped = load_bars(csv_path)
    # The research metrics/capacity model is daily-only. Reject accidental minute inputs.
    for bars in grouped.values():
        if len({b.timestamp.date() for b in bars}) != len(bars):
            raise ValueError('research requires daily bars; intraday needs a separate execution model')
    metadata_path = csv_path.with_suffix('.metadata.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    digest = fingerprint(csv_path)
    if metadata.get('sha256',digest) != digest:
        raise ValueError('dataset SHA-256 does not match metadata')
    first = min(b[0].timestamp.date().isoformat() for b in grouped.values())
    # Use common last date to avoid silently mixing stale securities with newer ones.
    last = min(b[-1].timestamp.date().isoformat() for b in grouped.values())
    protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
    periods = {p:[protocol[p][0],protocol[p][1] or last] for p in ('train','validation','test')}
    if not (periods['train'][0] <= periods['train'][1] < periods['validation'][0]
            <= periods['validation'][1] < periods['test'][0] <= periods['test'][1] <= last):
        raise ValueError('periods must be nonempty, ordered, non-overlapping and covered by data')
    for label,(begin,finish) in periods.items():
        if not any(begin<=b.timestamp.date().isoformat()<=finish for bars in grouped.values() for b in bars):
            raise ValueError(f'no observations for {label}')
    output.mkdir(parents=True,exist_ok=True)
    dump_json(output/'protocol.json',protocol)
    grid = []
    features = {s:compute_features(b,config) for s,b in grouped.items()}
    for q in protocol['retracement_grid']:
        for rvol in protocol['rvol_grid']:
            candidate = replace(config,max_retracement=q,min_rvol=rvol)
            signals = [s for f in features.values() for s in generate_signals(f,candidate)]
            metrics = run_portfolio(grouped,signals,candidate,*periods['train']).metrics
            eligible = metrics['trades'] >= protocol['minimum_training_trades'] and metrics['sharpe'] is not None
            grid.append(dict(max_retracement=q,min_rvol=rvol,eligible=eligible,metrics=metrics))
    eligible = [g for g in grid if g['eligible']]
    winner = max(eligible,key=lambda g:g['metrics']['sharpe']) if eligible else None
    selected = replace(config,max_retracement=winner['max_retracement'],min_rvol=winner['min_rvol']) if winner else config
    dump_json(output/'training_grid.json',grid)
    strategies = {'wave_default':(config,False), 'plain_breakout':(config,True),
                  'no_volume':(replace(config,min_rvol=0),False),
                  'no_regime':(replace(config,require_non_bearish_regime=False),False),
                  'train_selected':(selected,False)}
    results, test_curves, signal_sets = {}, {}, {}
    selected_signals = []
    for name,(c,plain) in strategies.items():
        signals = make_signals(grouped,c,plain)
        signal_sets[name]=signals
        write_signals(output/name/'signals.csv',signals)
        results[name] = {}
        for label,(begin,finish) in periods.items():
            r = run_portfolio(grouped,signals,c,begin,finish)
            results[name][label] = r.metrics
            save_result(output/name/label,r)
            if label=='test': test_curves[name]=r.equity
        if name=='train_selected': selected_signals=signals
        print(f"research {name}: test return {results[name]['test']['total_return']:.2%}",flush=True)
    stress = replace(selected,commission_bps_per_side=selected.commission_bps_per_side*2,
                     minimum_commission=selected.minimum_commission*2,
                     slippage_bps_per_side=selected.slippage_bps_per_side*2)
    stressed = run_portfolio(grouped,selected_signals,stress,*periods['test'])
    results['selected_cost_2x'] = {'test':stressed.metrics}
    save_result(output/'selected_cost_2x'/'test',stressed)
    results['equal_weight_gross'] = {}
    for label,(begin,finish) in periods.items():
        b = benchmark(grouped,begin,finish,config.initial_capital)
        results['equal_weight_gross'][label]=b.metrics
        save_result(output/'equal_weight_gross'/label,b)
        if label=='test': test_curves['equal_weight_gross']=b.equity
    annual = {}
    for year in range(int(periods['test'][0][:4]),int(periods['test'][1][:4])+1):
        begin,finish=max(f'{year}-01-01',periods['test'][0]),min(f'{year}-12-31',periods['test'][1])
        r = run_portfolio(grouped,selected_signals,selected,begin,finish)
        annual[str(year)] = r.metrics
        save_result(output/'annual_test'/str(year),r)
    thresholds=protocol['research_gate']
    tested=results['train_selected']['test']
    checks={
        '验证期净收益大于 0':results['train_selected']['validation']['total_return']>0,
        '测试期净收益大于 0':tested['total_return']>0,
        f"测试期交易数至少 {thresholds['minimum_test_trades']}":tested['trades']>=thresholds['minimum_test_trades'],
        f"测试期 Sharpe 至少 {thresholds['minimum_test_sharpe']}":tested['sharpe'] is not None and tested['sharpe']>=thresholds['minimum_test_sharpe'],
        f"测试期最大回撤不超过 {thresholds['maximum_test_drawdown']:.0%}":tested['max_drawdown']>=-thresholds['maximum_test_drawdown'],
        '佣金与滑点翻倍后净收益大于 0':stressed.metrics['total_return']>0}
    quality=audit_data(csv_path)
    dump_json(output/'data_quality.json',quality)
    report=dict(data=dict(metadata,start=first,end=last,rows=sum(map(len,grouped.values())),
                          symbols=sorted(grouped),sha256=digest),
                periods=periods,protocol=protocol,protocol_sha256=fingerprint(protocol_path),
                base_config=config.to_dict(),selected_config=selected.to_dict(),
                stress_config=stress.to_dict(),selection_fallback=not bool(winner),
                results=results,annual_test_diagnostics=annual,
                data_quality=quality,bootstrap=block_bootstrap(test_curves['train_selected'],config.initial_capital),
                diagnostics=dict(regime_filter_redundant=signal_sets['wave_default']==signal_sets['no_regime']),
                research_gate=dict(passed=all(checks.values()),checks=checks),
                production_ready=False,engineering_tests=test_status or {'status':'not_run_by_this_command'},
                environment=dict(python=platform.python_version(),platform=platform.platform()),
                source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                limitations=[
                    '固定 10 股便利样本，有选择/幸存者偏差，不能外推到全 A 股；并非历史指数成分股回测。',
                    '通达信 .day 无历史 ST/退市风险标识；本导入器假设成熟非 ST 沪深主板。请勿直接用于其他板块、ST 或上市初期股票。',
                    '除权事件按当日向后累乘复权，使用等价份额近似再投资；未模拟红利税、现金到账、配股缴款或碎股。',
                    '开盘涨跌停按前收/除权参考价拒单；未建模竞价队列和逐笔成交，历史平均成交量只代表容量估计。',
                    '收盘信号最早次日开盘执行；止损/止盈触及后也是次开盘退出，不保证止损价，也不保证一定更保守。',
                    '持仓受共享现金、单股上限和风险预算约束；不强制期末卖出。缺失行情保留末次估值，可能低估停牌风险。',
                    '训练/验证/测试是历史时间切分，并非真正前瞻锁箱；看过本报告后再次调参须另留未来数据。',
                    '研究门槛只是预设工程化筛查，不是显著性检验或实盘验收；尚未做完整多重检验校正、行业中性及券商模拟撮合。',
                    '本报告是旧版突破—浅回撤—再创新高—相对量子集；新增笔记映射见 docs/notion_review.md，独立结构对照请运行 notion-research；不能声称所有战法已实现。'])
    dump_json(output/'report.json',report)
    report_files(output,report,test_curves)
    return report
