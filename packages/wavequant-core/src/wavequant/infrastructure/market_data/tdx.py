"""Adapt read-only Tongdaxin daily files without modifying the installation."""
from __future__ import annotations

import json
import math
import struct
from datetime import date, datetime
from pathlib import Path

from .data import DEFAULT_SYMBOLS, dump_json, fingerprint, opening_permissions, write_dataset
from wavequant.domain.models.a_share_security import is_supported_a_share

RECORD = struct.Struct('<5If2I')


def action_symbol(market: int, code: str) -> str:
    """Translate TDX gbbq market ids, including market 2 (Beijing)."""
    prefix={0:'sz',1:'sh',2:'bj'}.get(int(market))
    if prefix is None:
        raise ValueError(f'unsupported TDX corporate-action market {market}')
    return f'{prefix}.{code}'


def read_day(path: Path) -> list[dict]:
    data = path.read_bytes()
    if not data or len(data) % RECORD.size:
        raise ValueError(f'{path}: empty or truncated 32-byte day records')
    rows = []
    for d, op, hi, lo, cl, amount, volume, _ in RECORD.iter_unpack(data):
        day = datetime.strptime(str(d), '%Y%m%d').date()
        if rows and day <= rows[-1]['date']:
            raise ValueError(f'{path}: duplicate/non-increasing date {day}')
        if min(op, hi, lo, cl) <= 0 or lo > min(op, cl) or hi < max(op, cl) or not math.isfinite(amount):
            raise ValueError(f'{path}: invalid OHLC on {day}')
        rows.append(dict(date=day, open=op/100, high=hi/100, low=lo/100,
                         close=cl/100, volume=volume))
    return rows


def ex_reference(previous: float, event: dict) -> float:
    value = (previous * 10 - event['cash'] + event['rights_price'] * event['rights']) / (
        10 + event['bonus'] + event['rights'])
    if not math.isfinite(value) or value <= 0:
        raise ValueError('invalid corporate-action reference price')
    return value


def read_actions(path: Path, cache: Path) -> tuple[list[dict], str]:
    digest = fingerprint(path)
    cached = cache / f'gbbq_{digest}.json'
    if cached.exists():
        return json.loads(cached.read_text(encoding='utf-8')), digest
    from pytdx.reader.gbbq_reader import GbbqReader
    print('Decoding local gbbq corporate actions ...', flush=True)
    frame = GbbqReader().get_df(str(path))
    events = []
    for row in frame.itertuples(index=False):
        if int(row.category) not in (1, 11, 12):
            continue
        events.append(dict(symbol=action_symbol(row.market,str(row.code)),
                           date=datetime.strptime(str(row.datetime), '%Y%m%d').date().isoformat(),
                           category=int(row.category), cash=float(row.hongli_panqianliutong),
                           rights_price=float(row.peigujia_qianzongguben),
                           bonus=float(row.songgu_qianzongguben), rights=float(row.peigu_houzongguben)))
    dump_json(cached, events)
    return events, digest


def adjust_rows(raw: list[dict], events: list[dict], start: date, end: date, symbol: str,
                *, include_close_permission: bool = False) -> list[dict]:
    """Forward accumulation of event factors: future actions cannot alter earlier prices."""
    relevant = sorted([e for e in events if start <= date.fromisoformat(e['date']) <= end], key=lambda e:e['date'])
    for e in relevant:
        if e['category'] != 1:
            raise ValueError(f'{symbol}: unsupported split/consolidation on {e["date"]}')
    result, factor, cursor, previous = [], 1.0, 0, None
    for r in raw:
        if r['date'] > end:
            break
        if r['date'] < start:
            previous = r['close']
            continue
        reference = previous if previous is not None else r['open']
        while cursor < len(relevant) and relevant[cursor]['date'] <= r['date'].isoformat():
            new_reference = ex_reference(reference, relevant[cursor])
            factor *= reference / new_reference
            reference = new_reference
            cursor += 1
        can_buy, can_sell = opening_permissions(dict(date=r['date'].isoformat(), isST='0',
                                                    tradestatus='1', preclose=str(reference), open=str(r['open'])),
                                                    symbol)
        row = {key: r[key] * factor for key in ('open','high','low','close')}
        row.update(timestamp=r['date'].isoformat(), symbol=symbol, volume=r['volume'],
                   buyable=int(can_buy), sellable=int(can_sell), adjustment_factor=factor)
        if include_close_permission:
            row['nonflat_close_buyable'] = bool(r['volume'] > 0 and r['high'] > r['low'] and opening_permissions(
                dict(date=r['date'].isoformat(), isST='0', tradestatus='1', preclose=str(reference), open=str(r['low'])), symbol)[0])
            row['close_buyable'] = opening_permissions(dict(date=r['date'].isoformat(), isST='0',
                tradestatus='1' if r['volume'] > 0 else '0', preclose=str(reference), open=str(r['close'])), symbol)[0]
        result.append(row)
        previous = r['close']
    return result


def import_tdx(root: Path, output: Path, start: str = '2018-01-01', end: str | None = None,
               symbols: list[str] | None = None) -> dict:
    symbols = symbols or DEFAULT_SYMBOLS
    first, last = date.fromisoformat(start), date.fromisoformat(end) if end else date.today()
    if first > last or len(set(symbols)) != len(symbols):
        raise ValueError('invalid dates or duplicate symbols')
    actions, digest = read_actions(root/'T0002/hq_cache/gbbq', output.parent/'cache')
    rows, sources = [], []
    for symbol in symbols:
        if not is_supported_a_share(symbol):
            raise ValueError('TDX importer supports established SH/SZ/BJ A shares only')
        market, code = symbol.split('.')
        path = root / 'vipdoc' / market / 'lday' / f'{market}{code}.day'
        raw = read_day(path)
        if raw[0]['date'] >= first:
            raise ValueError(f'{symbol}: choose a start after listing; IPO rules are not modeled')
        converted = adjust_rows(raw, [e for e in actions if e['symbol']==symbol], first, last, symbol)
        if not converted:
            raise ValueError(f'{symbol}: no data in requested range')
        rows.extend(converted)
        sources.append(dict(symbol=symbol, file=str(path), sha256=fingerprint(path),
                            first=converted[0]['timestamp'], last=converted[-1]['timestamp'], rows=len(converted)))
        print(f'TDX {symbol}: {len(converted)} daily bars, latest {converted[-1]["timestamp"]}', flush=True)
    return write_dataset(output, rows, dict(kind='real_market', source='Tongdaxin local .day + gbbq',
         frequency='daily', requested_start=start, requested_end=end,
         start=min(r['timestamp'] for r in rows), end=max(r['timestamp'] for r in rows),
         sources=sources, gbbq_sha256=digest, price_basis='causal multiplicative adjusted equivalent units',
         limitations=['Fixed convenience universe: selection/survival bias; not all-market evidence.',
                      'No point-in-time ST history in .day; permissions assume established non-ST ordinary shares.',
                      'Corporate actions modeled as adjusted equivalent units, not cash-dividend tax/rights cash ledger.',
                      'No auction queue; opens at daily limits rejected; no minute execution claims.']))
