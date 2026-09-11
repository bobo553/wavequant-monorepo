"""Immutable data manifests and readiness checks without inventing PIT facts."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .data import fingerprint
from .event_store import canonical, utc
from .io import load_bars
from .security_master import SecurityMaster


def market_close(when):
    if when.tzinfo is None:
        when=when.replace(hour=15,minute=0,second=0,tzinfo=ZoneInfo('Asia/Shanghai'))
    return utc(when)


def register_dataset(store, csv_path, when, *, master=None, calendar=None):
    path=Path(csv_path).resolve()
    grouped=load_bars(path)
    metadata_path=path.with_suffix('.metadata.json')
    if not metadata_path.exists(): raise ValueError('provenance metadata required')
    metadata=json.loads(metadata_path.read_text(encoding='utf-8'))
    digest=fingerprint(path)
    if metadata.get('sha256')!=digest: raise ValueError('data/provenance hash mismatch')
    master=master or SecurityMaster()
    sessions=sorted({b.timestamp.date().isoformat() for bs in grouped.values() for b in bs})
    supplied_calendar=None if calendar is None else sorted(set(calendar))
    if supplied_calendar is not None:
        for day in supplied_calendar: datetime.strptime(day,'%Y-%m-%d')
        if not set(sessions)<=set(supplied_calendar): raise ValueError('bars outside supplied exchange calendar')
    unknown=0; missing=[]; out_of_universe=0; future_rows=0
    for symbol,bars in grouped.items():
        available={b.timestamp.date().isoformat() for b in bars}
        for b in bars:
            stamp=market_close(b.timestamp)
            if stamp>utc(when): future_rows+=1
            fact=master.at(symbol,b.timestamp.date().isoformat(),stamp)
            if fact is None: unknown+=1
            elif not fact.active: out_of_universe+=1
        if supplied_calendar is not None:
            for day in supplied_calendar:
                # A complete exchange calendar may include years outside this
                # snapshot. Only audit the requested snapshot's observed span.
                if day < sessions[0] or day > sessions[-1]: continue
                stamp=market_close(datetime.fromisoformat(day))
                fact=master.at(symbol,day,stamp)
                if fact is not None and fact.active and not fact.suspended and day not in available:
                    missing.append(dict(symbol=symbol,session=day))
    checks=dict(provenance_hash=True,point_in_time_security_coverage=unknown==0,
                exchange_calendar_available=supplied_calendar is not None,
                expected_sessions_complete=not missing,listed_period_only=out_of_universe==0,
                no_future_rows=future_rows==0,
                minor_timeframe_available=False)
    # CSV day bars cannot establish actual minor-path or delisted-universe completeness.
    manifest=dict(schema_version=1,path=str(path),sha256=digest,metadata_sha256=fingerprint(metadata_path),
        rows=sum(map(len,grouped.values())),symbols=sorted(grouped),start=sessions[0],end=sessions[-1],
        security_master_sha256=hashlib.sha256(canonical(master.snapshot()).encode()).hexdigest(),
        calendar_sha256=None if supplied_calendar is None else hashlib.sha256(canonical(supplied_calendar).encode()).hexdigest(),
        checks=checks,unknown_security_rows=unknown,outside_listing_rows=out_of_universe,
        future_rows=future_rows,missing_expected_sessions=missing,
        data_ready=all(v for k,v in checks.items() if k!='minor_timeframe_available'),
        strict_polyline_ready=False,universe_completeness='not_established_from_bar_files',
        coverage_scope='provided_symbols_only_not_whole_market',
        limitations=metadata.get('limitations',[]))
    mid=hashlib.sha256(canonical(manifest).encode()).hexdigest()
    with store.transaction():
        if not store.get('dataset:'+mid): store.append('dataset:'+mid,'DATASET_REGISTERED',when,manifest)
    return dict(manifest_id=mid,**manifest)
