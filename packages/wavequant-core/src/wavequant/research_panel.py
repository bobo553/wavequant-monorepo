"""Return-blind, frozen local-file sampling with explicit exclusions; not PIT."""
from datetime import date, datetime
import hashlib
from pathlib import Path
import re

from .data import DEFAULT_SYMBOLS, dump_json, write_dataset, fingerprint
from .tdx import RECORD, read_day, read_actions, adjust_rows


def freeze_universe(root, start, *, size, seed, required=()):
    root=Path(root); first=date.fromisoformat(start)
    if type(size) is not int or size<=0 or not seed: raise ValueError('positive sample size and fixed seed required')
    eligible={}; excluded=[]
    for market,prefix in (('sh','60'),('sz','00')):
        for path in sorted((root/'vipdoc'/market/'lday').glob(f'{market}{prefix}*.day')):
            if not re.fullmatch(market+prefix+r'\d{4}\.day',path.name): continue
            symbol=market+'.'+path.stem[2:]
            try:
                with path.open('rb') as handle: record=handle.read(RECORD.size)
                if len(record)!=RECORD.size: raise ValueError('missing first daily record')
                day=datetime.strptime(str(RECORD.unpack(record)[0]),'%Y%m%d').date()
                if day>=first:
                    excluded.append(dict(symbol=symbol,reason='local_history_not_before_start')); continue
                eligible[symbol]=dict(path=str(path.resolve()),first_local_date=day.isoformat())
            except (ValueError,OSError) as exc: excluded.append(dict(symbol=symbol,reason=str(exc)))
    required=set(required)
    if len(required)>size or not required<=set(eligible): raise ValueError('required symbols unavailable or sample too small')
    ordered=sorted(set(eligible)-required,key=lambda s:hashlib.sha256((seed+':'+s).encode()).hexdigest())
    selected=sorted(required|set(ordered[:size-len(required)]))
    if len(selected)!=size: raise ValueError('insufficient eligible local histories')
    return dict(seed=seed,size=size,selection_uses_returns=False,required=sorted(required),
        selected=selected,selected_sources={s:eligible[s] for s in selected},eligible_symbols=sorted(eligible),
        exclusions=excluded,historical_universe_complete=False,
        warning='Local file coverage is not a security master; survivor and availability bias remain.')


def build_research_panel(root, output, protocol):
    output=Path(output)
    if output.exists() and any(output.iterdir()): raise ValueError('new panel directory required')
    output.mkdir(parents=True,exist_ok=True)
    u=protocol['universe']; start,end=protocol['data_start'],protocol['data_end']
    universe=freeze_universe(root,start,size=u['size'],seed=u['seed'],
                             required=DEFAULT_SYMBOLS if u['include_existing_ten'] else ())
    # Persist selected identities BEFORE reading OHLC paths or strategy outcomes.
    dump_json(output/'universe_frozen.json',universe)
    actions,action_hash=read_actions(Path(root)/'T0002/hq_cache/gbbq',Path('data/cache'))
    rows=[]; sources=[]; quarantine=[]
    for symbol in universe['selected']:
        path=Path(universe['selected_sources'][symbol]['path'])
        source_hash=fingerprint(path)
        try:
            raw=read_day(path)
            converted=adjust_rows(raw,[e for e in actions if e['symbol']==symbol],
                                  date.fromisoformat(start),date.fromisoformat(end),symbol)
            if not converted: raise ValueError('no observations in research period')
            if fingerprint(path)!=source_hash: raise ValueError('source changed while importing')
            rows.extend(converted)
            sources.append(dict(symbol=symbol,file=str(path),sha256=source_hash,rows=len(converted),
                                first=converted[0]['timestamp'],last=converted[-1]['timestamp']))
        except (ValueError,OSError) as exc:
            quarantine.append(dict(symbol=symbol,sha256=source_hash,reason=str(exc)))
        print(f'panel {symbol}: {"quarantined" if quarantine and quarantine[-1]["symbol"]==symbol else "imported"}',flush=True)
    dump_json(output/'quarantine.json',quarantine)
    if not rows: raise ValueError('no usable panel data; see quarantine')
    path=output/'daily.csv'
    meta=write_dataset(path,rows,dict(kind='real_market_exploratory_panel',source='TDX local .day + gbbq',
        frequency='daily',start=min(r['timestamp'] for r in rows),end=max(r['timestamp'] for r in rows),
        requested_start=start,requested_end=end,sources=sources,gbbq_sha256=action_hash,
        frozen_universe_sha256=fingerprint(output/'universe_frozen.json'),quarantine=quarantine,
        price_basis='causal multiplicative adjusted equivalent units',
        limitations=['Local file hash sample is not a PIT universe; survivorship/availability bias remains.',
          'No historical ST status; existing importer assumes established non-ST main-board permissions.',
          'Missing bars and unsupported corporate actions are reported, never replaced with another winning stock.',
          'Adjusted-equivalent share accounting; no historical cash dividend tax/rights cash ledger.',
          'No minor timeframe, real auction queue or independent holdout certification.']))
    return path,meta,universe
