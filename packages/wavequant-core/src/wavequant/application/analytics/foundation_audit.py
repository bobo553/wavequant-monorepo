"""Run a descriptive foundation audit without orders or profit claims."""
from collections import Counter
import json
from pathlib import Path

from wavequant.domain.market_structure.foundations import bar_relations, resistance_evidence
from wavequant.infrastructure.market_data.data import dump_json, fingerprint
from wavequant.infrastructure.market_data.io import load_bars
from .research import write_rows


def audit_foundations(csv: Path, output: Path, test_status: dict | None = None) -> dict:
    grouped = load_bars(csv)
    digest = fingerprint(csv)
    meta = csv.with_suffix('.metadata.json')
    if meta.exists() and json.loads(meta.read_text(encoding='utf-8')).get('sha256') != digest:
        raise ValueError('dataset hash mismatch')
    if any(len({b.timestamp.date() for b in bars}) != len(bars) for bars in grouped.values()):
        raise ValueError('foundation audit requires daily bars')
    output.mkdir(parents=True,exist_ok=True)
    totals, coverage = Counter(), {}
    for number, (symbol, bars) in enumerate(sorted(grouped.items()),1):
        rows, counts = [], Counter()
        for prev, bar in zip(bars,bars[1:]):
            evidence = bar_relations(bar,prev)
            bear = resistance_evidence(bar,prev,direction='UP')
            bull = resistance_evidence(bar,prev,direction='DOWN')
            row = dict(timestamp=bar.timestamp.isoformat(),symbol=symbol,**evidence,
                       bearish_resistance_shape=bear['resistance'], bullish_resistance_shape=bull['resistance'])
            rows.append(row)
            counts.update({key:int(value) for key,value in evidence.items() if type(value) is bool})
        # Numeric filenames avoid trusting arbitrary CSV symbols as paths.
        name = f'symbol_{number:03d}.csv'
        write_rows(output/name,rows,list(rows[0]) if rows else ['timestamp','symbol'])
        coverage[symbol] = dict(rows=len(bars), annotated_rows=len(rows),first=bars[0].timestamp.isoformat(),
                                last=bars[-1].timestamp.isoformat(),counts=dict(counts),file=name)
        totals.update(counts)
    report = dict(kind='descriptive_foundation_audit', production_ready=False,
                  rows=sum(len(b) for b in grouped.values()),
                  annotated_rows=sum(max(0,len(b)-1) for b in grouped.values()),
                  data_sha256=digest,source_csv=str(csv.resolve()),symbols=coverage,counts=dict(totals),
                  source_sha256={p.name:fingerprint(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                  engineering_tests=test_status or {'status':'not_run_by_this_command'},
                  limitations=['No N formation inferred from adjacent candles alone.',
                               'Resistance shape counts alone do not establish a six-state regime.',
                               'Inside/outside candles flag lower-timeframe ambiguity, not an invented intraday path.',
                               'This report contains no backtest returns, orders or evidence of main-operator intent.'])
    dump_json(output/'report.json',report)
    lines = ['# N 形讲义基础层：通达信数据核验', '',
             f"输入 {report['rows']:,} 条日线，{len(grouped)} 只股票；相邻 K 线标注 {report['annotated_rows']:,} 条。每股第一条没有前收，不作虚拟高低点计算。", '',
             '这些是基础关系计数，条件可以重叠。它们不是买卖信号，也不是策略收益测试。', '',
             '| 基础关系 | 次数 |','|---|---:|']
    names = {'shrinking_head':'缩头','lifting_foot':'缩脚','extending_head':'出头','falling_tail':'落尾',
             'sunrise':'日出','sunset':'日落','inside':'严格内包','outside':'严格外包',
             'equal_high':'等高','equal_low':'等低','requires_lower_timeframe':'需次级线图澄清'}
    lines += [f'| {label} | {totals[key]:,} |' for key,label in names.items()]
    lines += ['', '## 每股数据覆盖', '', '| 股票 | 开始 | 结束 | 日线数 | 明细 |', '|---|---|---|---:|---|']
    lines += [f"| {symbol} | {c['first'][:10]} | {c['last'][:10]} | {c['rows']} | {c['file']} |" for symbol,c in coverage.items()]
    lines += ['', '完整基础说明见 docs/n_foundations.md。六大盘态仅提供显式前置条件的三棒分类函数，没有把每一个交易日强塞进六类。', '',
              '原策略、参数与旧报告保持不变；本次不据讲义新增公式直接认定盈利能力。', '']
    (output/'report.md').write_text('\n'.join(lines),encoding='utf-8')
    return report
