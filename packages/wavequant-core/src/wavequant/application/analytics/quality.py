"""Audit dataset completeness and optional independent raw-price snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.infrastructure.market_data.io import load_bars


def audit_data(csv_path: Path) -> dict:
    grouped=load_bars(csv_path)
    calendar={b.timestamp for bars in grouped.values() for b in bars}
    summary=dict(symbols=len(grouped),rows=sum(map(len,grouped.values())),
                 union_sessions=len(calendar),zero_volume_rows=sum(b.volume==0 for bars in grouped.values() for b in bars),
                 missing_sessions={s:len(calendar-{b.timestamp for b in bars}) for s,bars in grouped.items()},
                 latest_by_symbol={s:bars[-1].timestamp.date().isoformat() for s,bars in grouped.items()},
                 note='Missing sessions may be suspensions, listing gaps or missing downloads; not automatically filled.')
    checks=[]
    for symbol,bars in sorted(grouped.items()):
        # Only use existing explicitly named local cache files; no network request.
        snapshot=csv_path.parent/'cache'/f'{symbol}_2018-01-01_2025-12-31.json'
        if not snapshot.exists(): continue
        cached=json.loads(snapshot.read_text(encoding='utf-8'))
        raw={r['date']:r for r in cached['raw'] if r['tradestatus']=='1'}
        matched=0
        mismatches=[]
        for b in bars:
            row=raw.get(b.timestamp.date().isoformat())
            if not row: continue
            matched+=1
            for field in ('open','high','low','close'):
                tdx_value=getattr(b,field)/b.adjustment_factor
                if abs(tdx_value-float(row[field]))>0.011:
                    mismatches.append(dict(date=row['date'],field=field,tdx=tdx_value,cached=float(row[field])))
        checks.append(dict(symbol=symbol,snapshot=str(snapshot),sha256=fingerprint(snapshot),
                           compared_bars=matched,mismatch_fields=len(mismatches),examples=mismatches[:10]))
    summary['independent_cached_raw_price_check']=dict(
        status='checked' if checks else 'unavailable',source='existing BaoStock raw snapshots',
        tolerance_yuan=0.011,compared_bars=sum(c['compared_bars'] for c in checks),
        mismatch_fields=sum(c['mismatch_fields'] for c in checks),checks=checks,
        caveat='Checks only overlapping cached OHLC, not corporate-action completeness or 2026 data.')
    return summary
