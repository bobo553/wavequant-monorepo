"""Derive the level-1 trend line (一级趋势线) from the confirmed base polyline.

A small geometric turn is input evidence, not automatically a solid-line node.
HH+HL establishes up; LH+LL establishes down; mixed/equal comparisons hold the
previous direction. Only a confirmed direction switch finalizes a wave extreme.
"""
from collections import Counter

from .polyline import LinePoint, PointKind, ReversalPoint
from .price_action import Direction
from .trend_structure import observe_structure, StructuralTrend, retracement_evidence


def _ref(p):
    return dict(index=p['index'], ordinal=p['ordinal'], time=p['time'],
                kind=p['kind'], value=p['value'], available_at=p['available_at'], label=p['label'])


def _context_key(points, bull):
    candidates=[p for p in points if p['kind']==('H' if bull else 'L')]
    anchor=(max if bull else min)(candidates,key=lambda p:p['value'])
    previous=next((p for p in reversed(points[:points.index(anchor)]) if p['kind']!=anchor['kind']),None)
    return anchor,previous


def _wave_reversals(turns):
    """Aggregate multiple small swings into one confirmed wave, causally."""
    highs=[]; lows=[]; direction=None; candidate=None; selected=[]
    for j,p in enumerate(turns):
        (highs if p['kind']=='H' else lows).append((j,p))
        if len(highs)<2 or len(lows)<2:
            continue
        dh=highs[-1][1]['value']-highs[-2][1]['value']
        dl=lows[-1][1]['value']-lows[-2][1]['value']
        current='up' if dh>0 and dl>0 else 'down' if dh<0 and dl<0 else None
        if direction is None:
            if current is None: continue
            direction=current
            pool=highs if direction=='up' else lows
            candidate=pool[-1]  # Do not import an extreme from pre-direction history.
            continue
        target='H' if direction=='up' else 'L'
        sign=1 if direction=='up' else -1
        if p['kind']==target and sign*(p['value']-candidate[1]['value'])>0:
            candidate=(j,p)
        if current is None or current==direction:
            continue  # A small counter-swing or mixed structure is not reversal.
        position,extreme=candidate
        proof=[highs[-2][1],highs[-1][1],lows[-2][1],lows[-1][1]]
        selected.append(dict(extreme,source_reversal_available_at=extreme['available_at'],
                             available_at=p['available_at'],wave_direction_before=direction,
                             wave_direction_after=current,confirmation_rule='HH_HL_or_LH_LL_switch',
                             confirmed_by=[dict(index=q['index'],ordinal=q['ordinal'],kind=q['kind'],
                                                value=q['value'],time=q['time'],available_at=q['available_at']) for q in proof],
                             source_turn_position=position,confirmed_on_turn=j))
        direction=current
        pool=[(k,q) for k,q in enumerate(turns[position+1:j+1],position+1) if q['kind']==('H' if direction=='up' else 'L')]
        candidate=(max if direction=='up' else min)(pool,key=lambda v:v[1]['value'])
    return selected


def _annotate(points, symbol, dates):
    known=[]; high_count=low_count=0
    background=None; anchor=key=attack=None; suspicion=False
    for j,p in enumerate(points):
        if p['kind']=='H': high_count+=1; p['label']=f'H{high_count}'
        else: low_count+=1; p['label']=f'L{low_count}'
        p['reversal']='负反转' if p['kind']=='H' else '正反转'
        p['observations']=[]; p['levels']=[]
        previous=points[j-1] if j else None
        if previous:
            name='末升低' if p['kind']=='H' else '末跌高'
            p['preceding_turn']=_ref(previous)
            p['levels'].append(dict(name=f'{p["label"]} 的{name}',price=previous['value']))
        known.append(ReversalPoint(LinePoint(p['index'],p['ordinal'],PointKind(p['kind']),p['value']),
                                   dates[p['available_at']],'lecture_geometric_turn_not_strategy'))
        context=observe_structure(known,symbol=symbol,timeframe='1d',window_start=known[0].point.index,
                                  asof_index=known[-1].confirmed_index)
        p['trend']=context.trend.value
        p['window_trend']=context.window_trend.value

        def observation(title, **extra):
            p['observations'].append(dict(title=title,available_at=p['available_at'],
                                          basis='confirmed_polyline_extreme',**extra))
            if extra.get('key'):
                ref=extra['key']
                p['levels'].append(dict(name='转换依据：'+('末跌高' if ref['kind']=='H' else '末升低'),price=ref['value']))

        if background is None:
            if context.trend in (StructuralTrend.BULL,StructuralTrend.BEAR):
                background=context.trend
                anchor,key=_context_key(points[:j+1],background==StructuralTrend.BULL)
            continue
        if key is None:
            anchor,key=_context_key(points[:j+1],background==StructuralTrend.BULL)
            continue
        up=background==StructuralTrend.BEAR
        extreme_kind='H' if up else 'L'
        sign=1 if up else -1
        if attack is None:
            # Refresh a continuing trend's extreme and its immediate predecessor.
            if p['kind']==anchor['kind'] and sign*(p['value']-anchor['value'])<0:
                anchor,key=p,previous; suspicion=False
            elif p['kind']==anchor['kind'] and previous and sign*(p['value']-anchor['value'])>=0 and sign*(previous['value']-key['value'])<0:
                if not suspicion:
                    observation('底部疑虑' if up else '头部疑虑',key=_ref(key)); suspicion=True
            if p['kind']==extreme_kind and sign*(p['value']-key['value'])>0 and previous:
                attack=dict(origin=previous,extreme=p,anchor=anchor,key=key,checked=False)
                observation('翻空为多' if up else '翻多为空',key=_ref(key),
                            formation='底部成形' if up else '头部成形')
        elif p['kind']!=extreme_kind and not attack['checked']:
            ratio=retracement_evidence(attack['origin']['value'],attack['extreme']['value'],p['value'],
                                       direction=Direction.UP if up else Direction.DOWN)
            attack['checked']=True
            if ratio.partial and ratio.below_67_percent:
                observation('空多交替' if up else '多空交替',ratio=ratio.ratio,
                            weak_countermove=ratio.below_33_percent,abc_confirmed=False)
                background=StructuralTrend.BULL if up else StructuralTrend.BEAR
                anchor,key=attack['extreme'],attack['origin']; attack=None; suspicion=False
            else:
                observation('回档未通过交替条件' if up else '反弹未通过交替条件',ratio=ratio.ratio)
        elif p['kind']==extreme_kind and sign*(p['value']-attack['extreme']['value'])>0:
            attack.update(origin=previous,extreme=p,checked=False)
        if attack and p['kind']==attack['anchor']['kind'] and sign*(p['value']-attack['anchor']['value'])<0:
            observation('翻多观察失效' if up else '翻空观察失效')
            anchor,key=p,previous; attack=None; suspicion=False


def reversal_trends(drawing, bars):
    # Drawing sessions are Shanghai dates, including timezone-aware market bars.
    from zoneinfo import ZoneInfo
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    result=[]; local_count=0
    for stroke in drawing['strokes']:
        raw=stroke['points']; counts=Counter(p['time'] for p in raw); ranks=Counter(); compact=[]
        for source in raw:
            p=dict(source,projection_count=counts[source['time']],projection_rank=ranks[source['time']])
            ranks[source['time']]+=1
            if compact and p['value']==compact[-1]['value']:
                continue  # Earliest equal extreme wins; no zero-length reversal.
            compact.append(p)
        turns=[]
        for left,p,right in zip(compact,compact[1:],compact[2:]):
            if p['state'] in ('seed','developing'):
                continue
            high=p['value']>left['value'] and p['value']>right['value']
            low=p['value']<left['value'] and p['value']<right['value']
            if high or low:
                turns.append(dict(p,kind='H' if high else 'L',source_kind=p['kind'],state='reversal',
                                  source_state=p['state']))
        local_count+=len(turns)
        waves=_wave_reversals(turns)
        if waves:
            _annotate(waves,bars[0].symbol,dates)
            result.append(dict(id=f'reversal-{stroke["id"]}',source_path=stroke['id'],kind='reversal',points=waves,
                               input_turn_count=len(turns)))
    return dict(strokes=result,trend_level=1,name='一级趋势线',scope='lecture_wave_structure_not_strategy_confirmation',
                aggregation_rule='HH_HL_or_LH_LL_switch_mixed_holds',input_turn_count=local_count,
                confirmed_wave_count=sum(len(s['points']) for s in result),
                break_basis='confirmed_polyline_extreme',retracement_threshold=.67,
                note='先按原折线高低点确认短期多空，再在方向转换时取整段极值；实线可以跨多个小拐点，未完成波段不画实线。编号仅为顺序，不照搬图008。')
