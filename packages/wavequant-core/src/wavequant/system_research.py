"""Fixed, non-selecting integration experiment, preserving earlier reports."""
import html
import json
import platform
from dataclasses import asdict, replace
from pathlib import Path

from .config import StrategyConfig
from .data import dump_json, fingerprint
from .io import load_bars, write_signals
from .quality import audit_data
from .backtest import run_portfolio
from .research import benchmark, make_signals, save_result
from .integrated_strategy import SystemStrategy, generate_system_signals
from .execution_diagnostics import execution_diagnostics


def run_system_research(csv_path: Path, output: Path, protocol_path: Path, tests: dict):
    if tests.get('status') != 'passed':
        raise ValueError('engineering test gate must pass before research')
    if output.exists() and any(p.name != 'test_log.txt' for p in output.iterdir()):
        raise ValueError('nonempty output; use a new directory to preserve historical experiments')
    protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
    grouped = load_bars(csv_path)
    metadata_path = csv_path.with_suffix('.metadata.json')
    if not metadata_path.exists():
        raise ValueError('data provenance metadata required')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
    digest = fingerprint(csv_path)
    if digest != metadata['sha256']:
        raise ValueError('data hash differs from provenance metadata')
    last = min(b[-1].timestamp.date().isoformat() for b in grouped.values())
    if last != protocol['data_end'] or any(b[-1].timestamp.date().isoformat() != last for b in grouped.values()):
        raise ValueError('fixed protocol data end must match all securities; do not silently roll forward')
    periods = protocol['periods']
    previous = None
    for label in ('train', 'validation', 'diagnostic'):
        begin, end = periods[label]
        if begin > end or end > last or (previous is not None and previous >= begin):
            raise ValueError('ordered nonoverlapping covered periods required')
        if not all(any(begin <= b.timestamp.date().isoformat() <= end for b in bars) for bars in grouped.values()):
            raise ValueError('each security requires observations in each period')
        previous = end
    execution = replace(StrategyConfig(), **protocol['execution'])
    execution.validate()
    output.mkdir(parents=True, exist_ok=True)
    dump_json(output/'protocol.json', protocol)
    dump_json(output/'execution.json', execution.to_dict())
    hashes = {p.name: fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))}
    dump_json(output/'source_sha256.json', hashes)
    results, coverage, configs, signal_sets, diagnostics = {}, {}, {}, {}, {}
    for name, changes in protocol['variants'].items():
        # Old saved protocols without entry_policy keep their original meaning.
        base = dict(entry_policy='legacy_n_continuation', preflight_reward_risk=False,
                    squeeze_pullback_entries=False)
        base.update(protocol['strategy'])
        base.update(changes)
        config = SystemStrategy(**base)
        config.validate()
        configs[name] = asdict(config)
        signals, evidence, counts = [], [], {}
        for symbol, bars in sorted(grouped.items()):
            r = generate_system_signals(bars, config)
            signals.extend(r.signals)
            evidence.extend(r.audit)
            counts[symbol] = r.counts
            print(f'{name} {symbol}: N={r.counts.get("completed_up_n",0)}, long signals={r.counts.get("long_signals",0)}', flush=True)
        signals.sort(key=lambda s: (s.timestamp, s.symbol, s.side))
        signal_sets[name] = signals
        coverage[name] = counts
        write_signals(output/name/'signals.csv', signals)
        dump_json(output/name/'event_audit.json', evidence)
        dump_json(output/name/'coverage.json', counts)
        results[name] = {}
        diagnostics[name] = {}
        for label, (begin, end) in periods.items():
            r = run_portfolio(grouped, signals, execution, begin, end)
            save_result(output/name/label, r)
            results[name][label] = r.metrics
            diagnostics[name][label] = execution_diagnostics(r)
        stress_config = replace(execution, commission_bps_per_side=execution.commission_bps_per_side*2,
            minimum_commission=execution.minimum_commission*2, slippage_bps_per_side=execution.slippage_bps_per_side*2)
        stress = run_portfolio(grouped, signals, stress_config, *periods['diagnostic'])
        results[name]['cost_2x'] = stress.metrics
        diagnostics[name]['cost_2x'] = execution_diagnostics(stress)
        save_result(output/name/'cost_2x', stress)
        print(f'{name} diagnostic: {results[name]["diagnostic"]}', flush=True)
    plain = make_signals(grouped, execution, plain=True)
    write_signals(output/'plain_breakout'/'signals.csv', plain)
    for name in ('plain_breakout', 'equal_weight_gross'):
        results[name] = {}
        for label, (begin, end) in periods.items():
            r = (run_portfolio(grouped, plain, execution, begin, end) if name == 'plain_breakout' else
                 benchmark(grouped, begin, end, execution.initial_capital))
            results[name][label] = r.metrics
            save_result(output/name/label, r)
    gates = {}
    g = protocol['gate']
    for name in protocol['variants']:
        r = results[name]
        m = r['diagnostic']
        checks = {
            'training_trade_count': r['train']['trades'] >= g['minimum_train_trades'],
            'validation_positive': r['validation']['total_return'] > 0,
            'diagnostic_positive': m['total_return'] > 0,
            'diagnostic_trade_count': m['trades'] >= g['minimum_diagnostic_trades'],
            'diagnostic_sharpe': m['sharpe'] is not None and m['sharpe'] >= g['minimum_sharpe'],
            'drawdown_limit': m['max_drawdown'] >= -g['maximum_drawdown'],
            'cost_stress_positive': r['cost_2x']['total_return'] > 0,
        }
        gates[name] = dict(passed=all(checks.values()), checks=checks)
    annual = {}
    for name in ('strict_full', 'proxy_full'):
        annual[name] = {}
        for year in range(2024, int(last[:4])+1):
            r = run_portfolio(grouped, signal_sets[name], execution, f'{year}-01-01', min(f'{year}-12-31', last))
            annual[name][str(year)] = r.metrics
            save_result(output/name/'annual'/str(year), r)
    quality = audit_data(csv_path)
    dump_json(output/'data_quality.json', quality)
    dump_json(output/'execution_diagnostics.json', diagnostics)
    report = dict(protocol=protocol, protocol_sha256=fingerprint(protocol_path), data=metadata,
        strategy_configs=configs, execution=execution.to_dict(), results=results, coverage=coverage,
        gates=gates, annual=annual, engineering_tests=tests, source_sha256=hashes,
        execution_diagnostics=diagnostics,
        python=platform.python_version(), production_ready=False,
        selection='fixed_primary_no_selection_no_proxy_promotion',
        unavailable=['actual_minor_timeframe_data', 'historical_ST_and_delist_universe', 'auction_queue_and_real_broker_fills'])
    dump_json(output/'report.json', report)
    lines = ['# 主控理论系统集成回测', '',
        f'协议：{protocol["version"]}；实际参数见 strategy_configs / protocol.json。', '',
        f'通达信：{metadata["start"]} 至 {last}；{metadata["rows"]:,} 条日线，{len(grouped)} 只固定样本。', '',
        '**这是已看过历史上的固定协议诊断，不是新的样本外锁箱，也不是实盘验收。没有按结果调参或筛选赢家。**', '',
        f'固定主策略 strict_full 研究门槛：{"通过" if gates["strict_full"]["passed"] else "未通过"}；实盘就绪：否。', '',
        '## 分段结果', '', '| 策略 | 区间 | 净收益 | 年化 | 最大回撤 | Sharpe | 平均仓位 | 平仓笔数 |',
        '|---|---|---:|---:|---:|---:|---:|---:|']
    for name, chunks in results.items():
        for label, m in chunks.items():
            sharpe = 'N/A' if m['sharpe'] is None else f'{m["sharpe"]:.2f}'
            lines.append(f'| {name} | {label} | {m["total_return"]:.2%} | {m["annualized_return"]:.2%} | {m["max_drawdown"]:.2%} | {sharpe} | {m["average_exposure"]:.2%} | {m.get("trades", "—")} |')
    lines += ['', '## 覆盖与信号', '', '| 策略 | 内外包中断棒 | 正 N | 六态确认 | 洗盘再攻击 | 扭转疑虑 | 扭转确认 | 多头信号 |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name, symbols in coverage.items():
        total = lambda key: sum(c.get(key, 0) for c in symbols.values())
        states = sum(sum(v for k, v in c.items() if k.startswith('regime_')) for c in symbols.values())
        lines.append(f'| {name} | {total("ambiguous_bars")} | {total("completed_up_n")} | {states} | {total("washout_confirmed")} | {total("turn_suspicions")} | {total("turn_confirmed")} | {total("long_signals")} |')
    lines += ['', '## 无成交与执行健康检查', '',
        '净值 0% 是账户记账结果；无成交不是可评价的盈利表现。成交不足时研究门槛仍失败。', '',
        '| 策略 | 区间 | 执行状态 | 入场尝试 | 买入成交 | 拒绝原因 |',
        '|---|---|---|---:|---:|---|']
    for name, chunks in diagnostics.items():
        for label, d in chunks.items():
            lines.append(f'| {name} | {label} | {d["status"]} | {d["entry_attempts"]} | {d["entry_fills"]} | {d["rejection_reasons"]} |')
    lines += ['', '## 方法与限制', '',
        '- squeeze_pullback_entries=true 时额外检验已确认轧空的回压再上涨：此前确认至少两棒前，防守全程未破，昨收低于此前确认收盘，今低不低于昨低且红棒收盘突破昨高；仍须同一已完成交替的多头许可、原 N 量能和原止损／目标／盈亏比。它是单独标注的策略状态假设，不改写六态创新高事件。',
        '- preflight_reward_risk=true 时：发单前先要求收盘毛盈亏比达到门槛，不通过只记录候选、不消耗 N。后续必须再次出现合格盘态，且仍用首个未达目标；已实际发单的事件仍只尝试一次。开盘重新计算费用后盈亏比，预检查不保证成交。',
        '- strict_full 使用讲义普通折线；内外包缺少次级证据时终止该段、发出风险退出，下一棒新建独立段，不跨段连接。初始方向按首棒红黑作显式种子，不是确认拐点。',
        '- proxy_full 是左右各 2 棒确认的日线拐点代理，不是讲义折线。后续同类极值更新当前结构视图，过去快照与已冻结 N 不改写。',
        '- transitioned_squeeze：已知空头背景 → 收盘突破冻结末跌高 → 首次确认回档小于 67% → 已确认高低点均抬高 → 后续新 N → 当前轧空或强轧空。盘坚及提前形成的旧 N 不入场。legacy_n_continuation 仅保留作旧版对照。',
        '- 洗盘／正扭转只增加来源分类，不绕过入场门槛。无真实次级数据，扭转确认通道保持未知。',
        '- 反向 N、末升低收盘跌破、负扭转或严格结构中断触发持多风险退出；同棒退出优先于入场，不开空仓。',
        '- 首次 N 放量至少为过去 20 棒均量的 1.2 倍，N 回档小于 2/3；轧空用轧空低，旧版盘坚才用波段起点作止损；取尚未触及的首个等浪／1P／2T 目标。no_volume 仅是显式取消量能的诊断组。',
        '- 入场按下个可交易开盘加滑点重新检查结构目标、费用后盈亏比至少 1.5、5% 跳空上限。每事件只发一次入场，未成交不追单。',
        '- 百万元共享账户，单次风险 0.5%，单股最多 20%，最多 5 股；过去平均量的 1% 容量、100 股整手、T+1、涨跌停标志。',
        '- 佣金每边 3bp、滑点每边 5bp，另含现有历史印花税／过户费假设。两倍压力只翻倍佣金、最低佣金及滑点，不翻倍法定税率。',
        '- 止损／止盈由日内极值观察后在下一开盘退出，不是按止损价精确成交；最长 40 棒，不强制期末平仓，未平仓按末收计价。',
        '- 每个时期、年度诊断均重置资金与持仓；保留此前指标热身，但不带入分段前持仓或挂单。',
        '- 等权持有是同篮子无费用满仓参考，不是风险匹配、可直接交易的指数；plain_breakout 共用执行约束作简单规则对照。',
        '- 固定十只股票有幸存者／选择偏差；日线不含完整历史 ST、退市、竞价队列，复权等价份额非真实股息现金台账。不能外推全 A 股。',
        '- 无成交、低仓位和小回撤不等于好策略；完整成交、拒单、净值和事件审计均保留。', '',
        '## 研究门槛', '']
    for name, gate in gates.items():
        lines.append(f'- {name}：{"通过" if gate["passed"] else "未通过"}；未满足：'+', '.join(k for k, v in gate['checks'].items() if not v))
    lines += ['', '## 可复核产物', '',
        'report.json、protocol.json、execution.json、source_sha256.json、data_quality.json、test_log.txt；各策略的 signals.csv、event_audit.json、coverage.json 及各区间 trades.csv、orders.csv、equity.csv、open_positions.json。', '']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    rendered, table = [], False
    for line in lines:
        if line.startswith('|'):
            if set(line.replace('|', '').replace('-', '').replace(':', '').strip()) == set():
                continue
            if not table:
                rendered.append('<table>'); table = True
            rendered.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
        else:
            if table:
                rendered.append('</table>'); table = False
            tag = 'h1' if line.startswith('# ') else 'h2' if line.startswith('## ') else 'p'
            if line:
                rendered.append(f'<{tag}>'+html.escape(line.lstrip('# ').replace('**', ''))+f'</{tag}>')
    if table:
        rendered.append('</table>')
    (output/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>主控理论集成回测</title><style>body{max-width:1200px;margin:40px auto;padding:20px;font:16px/1.7 system-ui;color:#203040}table{border-collapse:collapse;width:100%;font-size:14px}td{padding:8px;border:1px solid #ccd5dc}tr:first-child{background:#e9f0f4;font-weight:bold}p{overflow-wrap:anywhere}</style>'+''.join(rendered)+'</html>', encoding='utf-8')
    return report
