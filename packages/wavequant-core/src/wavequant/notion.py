"""Local note inventory and reproducible, fixed-hypothesis research runner."""
from __future__ import annotations

from dataclasses import asdict, replace
import html
import json
from pathlib import Path
import re
from urllib.parse import unquote

from .backtest import run_portfolio
from .config import StrategyConfig
from .data import dump_json, fingerprint
from .io import load_bars, write_signals
from .research import benchmark, block_bootstrap, make_signals, save_result
from .structure import StructureConfig, structural_signals


def inventory_notes(root: Path) -> dict:
    if not root.is_dir():
        raise ValueError('Notion export directory does not exist')
    pages, external = [], set()
    for path in sorted(root.rglob('*.md')):
        content = path.read_text(encoding='utf-8-sig')
        images = re.findall(r'!\[[^\]]*\]\(([^\n]+)\)', content)
        local = []
        for ref in images:
            ref = ref.strip('<>')
            if ref.startswith(('https://', 'http://')):
                external.add(ref)
            else:
                asset = (path.parent/unquote(ref)).resolve()
                within = asset.is_relative_to(root.resolve())
                local.append(dict(reference=ref, exists=within and asset.is_file()))
        title = next((s.lstrip('# ').strip() for s in content.splitlines() if s.strip()), path.stem)
        pages.append(dict(path=path.relative_to(root).as_posix(), title=title,
                          sha256=fingerprint(path), lines=len(content.splitlines()),
                          empty_body=not '\n'.join(content.splitlines()[1:]).strip(),
                          local_images=local, external_image_references=sum(
                              r.startswith(('https://','http://')) for r in images)))
    if not pages:
        raise ValueError('No Markdown pages in export')
    return dict(page_count=len(pages), pages=pages,
                external_images=[dict(url=u, status='not_visually_verified') for u in sorted(external)],
                note='Inventory checks files, not comprehension. Human-readable coverage is in docs/notion_review.md.')


def run_notion(csv_path: Path, notes: Path, output: Path, protocol_path: Path,
               config: StrategyConfig, test_status: dict | None = None) -> dict:
    config.validate()
    grouped = load_bars(csv_path)
    for bars in grouped.values():
        if len({b.timestamp.date() for b in bars}) != len(bars):
            raise ValueError('Notion research requires daily bars')
    digest = fingerprint(csv_path)
    metadata_path = csv_path.with_suffix('.metadata.json')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    if metadata.get('sha256', digest) != digest:
        raise ValueError('Data hash differs from metadata')
    protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
    periods = {k: protocol[k] for k in ('train', 'validation', 'diagnostic')}
    last = min(b[-1].timestamp.date().isoformat() for b in grouped.values())
    if not (periods['train'][0] <= periods['train'][1] < periods['validation'][0]
            <= periods['validation'][1] < periods['diagnostic'][0]
            <= periods['diagnostic'][1] <= last < protocol['future_start']):
        raise ValueError('Invalid periods, stale data, or future data mixed into historical run; use a separately frozen forward evaluation')
    for begin, end in periods.values():
        if any(not any(begin <= b.timestamp.date().isoformat() <= end for b in bars)
               for bars in grouped.values()):
            raise ValueError('Every symbol needs observations in every period')
    inv = inventory_notes(notes)
    output.mkdir(parents=True, exist_ok=True)
    dump_json(output/'notes_inventory.json', inv)
    dump_json(output/'protocol.json', protocol)
    settings = {name: StructureConfig(**values) for name, values in protocol['variants'].items()}
    for value in settings.values():
        value.validate()
    dump_json(output/'resolved_variants.json', {name: asdict(c) for name, c in settings.items()})
    signals = {'legacy_v1': make_signals(grouped, config),
               'plain_breakout': make_signals(grouped, config, True)}
    signals.update({name: sorted([s for bars in grouped.values() for s in structural_signals(bars, c)],
                                key=lambda s: (s.timestamp, s.symbol, s.side))
                    for name, c in settings.items()})
    results, intervals, counts, yearly = {}, {}, {}, {}
    stress = replace(config, commission_bps_per_side=config.commission_bps_per_side*2,
                     minimum_commission=config.minimum_commission*2,
                     slippage_bps_per_side=config.slippage_bps_per_side*2)
    for name, sig in signals.items():
        write_signals(output/name/'signals.csv', sig)
        counts[name] = len(sig)
        results[name] = {}
        for label, (begin, end) in periods.items():
            r = run_portfolio(grouped, sig, config, begin, end)
            save_result(output/name/label, r)
            results[name][label] = r.metrics
            if label == 'diagnostic':
                intervals[name] = block_bootstrap(r.equity, config.initial_capital)
        r = run_portfolio(grouped, sig, stress, *periods['diagnostic'])
        save_result(output/name/'cost_2x', r)
        results[name]['cost_2x'] = r.metrics
        yearly[name] = {}
        for year in range(int(periods['diagnostic'][0][:4]), int(periods['diagnostic'][1][:4])+1):
            begin = max(f'{year}-01-01', periods['diagnostic'][0])
            end = min(f'{year}-12-31', periods['diagnostic'][1])
            r = run_portfolio(grouped, sig, config, begin, end)
            yearly[name][str(year)] = r.metrics
            save_result(output/name/'annual_reset'/str(year), r)
        print(f"notion {name}: diagnostic {results[name]['diagnostic']['total_return']:.2%}", flush=True)
    eligible = [name for name in settings if results[name]['train']['trades'] >= 20
                and results[name]['train']['sharpe'] is not None]
    selected = max(eligible, key=lambda n: results[n]['train']['sharpe']) if eligible else None
    checks = dict(training_candidate_exists=selected is not None)
    if selected:
        r = results[selected]
        checks.update(positive_train=r['train']['total_return'] > 0,
                      positive_validation=r['validation']['total_return'] > 0,
                      positive_diagnostic=r['diagnostic']['total_return'] > 0,
                      sufficient_trades=r['diagnostic']['trades'] >= 30,
                      sharpe_at_least_half=(r['diagnostic']['sharpe'] or 0) >= .5,
                      drawdown_below_20pct=r['diagnostic']['max_drawdown'] >= -.2,
                      positive_cost_stress=r['cost_2x']['total_return'] > 0)
    bench = benchmark(grouped, *periods['diagnostic'], config.initial_capital)
    save_result(output/'equal_weight_gross'/'diagnostic', bench)
    report = dict(kind='exploratory_not_fresh_holdout', production_ready=False,
                  data=dict(path=str(csv_path.resolve()), sha256=digest, common_end=last,
                            rows=sum(map(len,grouped.values())), symbols=sorted(grouped)),
                  base_config=config.to_dict(), stress_config=stress.to_dict(),
                  protocol=protocol, protocol_sha256=fingerprint(protocol_path),
                  variants={name: asdict(c) for name,c in settings.items()},
                  note_page_count=inv['page_count'], unverified_external_images=len(inv['external_images']),
                  results=results, signal_counts=counts, annual_reset_diagnostics=yearly,
                  bootstrap=intervals, equal_weight_gross=bench.metrics, selected_by_train=selected,
                  research_gate=dict(passed=all(checks.values()), checks=checks),
                  engineering_tests=test_status or {'status':'not_run_by_this_command'},
                  source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))})
    dump_json(output/'report.json', report)
    lines = ['# 主控波浪：笔记规则的日线检验', '',
             '这是已看过历史数据后的固定假设对照，不是新的锁箱测试；不得据此宣称实盘有效。', '',
             f"本地正文 {inv['page_count']} 页；{len(inv['external_images'])} 个不同外链图片地址未完成视觉核验。逐页结论见 docs/notion_review.md。", '',
             f"数据 {sum(map(len,grouped.values())):,} 条，{len(grouped)} 股，共同结束日 {last}。", '',
             '| 策略 | 训练收益 | 验证收益 | 历史诊断收益 | 最大回撤 | Sharpe | 交易数 | 平均仓位 | 成本翻倍收益 |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for name, r in results.items():
        m = r['diagnostic']
        sharpe = f"{m['sharpe']:.2f}" if m['sharpe'] is not None else '—'
        lines.append(f"| {name} | {r['train']['total_return']:.2%} | {r['validation']['total_return']:.2%} | {m['total_return']:.2%} | {m['max_drawdown']:.2%} | {sharpe} | {m['trades']} | {m['average_exposure']:.2%} | {r['cost_2x']['total_return']:.2%} |")
    lines += ['', f"仅按训练期选择：{selected or '无合格候选'}。研究筛查：{'通过' if all(checks.values()) else '未通过'}；生产就绪：否。", '',
              f"固定股票篮子等权持有、未扣费用的诊断期收益：{bench.metrics['total_return']:.2%}。资金暴露不同，不能直接解释为风险调整后的 alpha。", '',
              '## 执行与解读边界', '',
              '- 收盘确认、次开盘下单；止损/目标触及也在次开盘退出，不假设能成交在目标价。',
              '- 潮汐目标固定为 2×颈线−起涨低点；瞬爆目标固定为突破日 2×收盘−最低，均非保证能到达。',
              '- RR2/RR4 在实际开盘价和数量上计入假设费用/滑点；跳空风险仍可能超过预算。',
              '- 新策略只新增日线 N 字入场，沿用结构止损、目标、时间退出；没有伪装成完整波浪计数或分钟战法。',
              '- 固定 10 股有选择/幸存者偏差；缺历史 ST、退市、行业成分和完整股息现金账，未达到全市场生产回测标准。',
              '- 年度切片各自重置持仓；Bootstrap 只作探索区间，不校正多重比较，也不是盈利显著性证明。',
              '- 所有假设、逐笔交易、拒单、持仓、净值、文件哈希均保留；没有删除表现差的组。', '',
              '方法依据：[回测过拟合研究](https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf)；交易约束需按[上交所现行交易规则](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml)及对应市场历史版本核验。', '']
    (output/'report.md').write_text('\n'.join(lines), encoding='utf-8')
    # Self-contained accessible table, no remote scripts/assets.
    rendered = []
    table = False
    for line in lines:
        if line.startswith('|---'):
            continue
        if line.startswith('|'):
            if not table:
                rendered.append('<table>'); table = True
            rendered.append('<tr>'+''.join('<td>'+html.escape(c.strip())+'</td>' for c in line.strip('|').split('|'))+'</tr>')
        else:
            if table:
                rendered.append('</table>'); table = False
            rendered.append('<p>'+html.escape(line)+'</p>')
    (output/'report.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>主控波浪研究</title><style>body{font:16px/1.7 system-ui;margin:32px;color:#192f3c}table{border-collapse:collapse}td{padding:8px;border:1px solid #ccd5dd}tr:first-child{font-weight:bold;background:#edf4f5}p{max-width:1200px}</style>'+''.join(rendered)+'</html>', encoding='utf-8')
    return report
