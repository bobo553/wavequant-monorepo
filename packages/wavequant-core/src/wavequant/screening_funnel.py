"""Per-security gate evidence, with evaluations kept distinct from stock counts."""
from collections import Counter


def funnel(view,lookback):
    bars=view['bars'];cutoff=bars[-min(lookback,len(bars))]['time'] if bars else '9999'
    events=[e for e in view.get('audit',[]) if e['timestamp'][:10]>=cutoff]
    counts=Counter(e['event'] for e in events)
    reasons=Counter(e.get('reason','unknown') for e in events if e['event'] in ('entry_rejected','entry_preflight_rejected'))
    return dict(events=dict(counts),rejections=dict(reasons),
        no_long_signal=int(not any(s['side']=='LONG' and s.get('time',s.get('timestamp','')[:10])>=cutoff for s in view['signals'])),
        structure_interrupted=int(bool(counts['strict_structure_interrupted'])),
        has_n=int(bool(counts['n_completed'])),has_squeeze=int(any(e['event']=='regime_confirmation'
            and e.get('regime') in ('轧空','强轧空') for e in events)))
