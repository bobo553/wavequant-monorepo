"""Derive the level-1 trend line (一级趋势线) from the confirmed base polyline.

A small geometric turn is input evidence, not automatically a solid-line node.
HH+HL establishes up; LH+LL establishes down; mixed/equal comparisons hold the
previous direction. Only a confirmed direction switch finalizes a wave extreme.
"""
from collections import Counter

from .polyline import LinePoint, PointKind, ReversalPoint
from .price_action import Direction
from .trend_landmarks import bear_to_bull_highs
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


def _point_order(point):
    """Return the stable source order, including multiple vertices on one bar."""
    return point['index'],point.get('ordinal',0)


def _is_alternating_leg(left,right):
    """Require both alternating H/L kinds and the matching price direction."""
    return left['kind']!=right['kind'] and (
        right['value']>left['value'] if left['kind']=='L' else right['value']<left['value'])


def _continuity_proof(point):
    """Keep only stable evidence fields; local labels are assigned after merging."""
    return {key:point[key] for key in ('index','ordinal','kind','value','time','available_at')}


def _formal_bridge_point(source,known,left,right):
    """Promote a confirmed base extreme to a causally confirmed level-1 point.

    The source extreme can occur well before the right level-1 endpoint.  It only
    becomes a level-1 continuity point once both surrounding level-1 endpoints
    are known, so ``available_at`` must not reuse the earlier base confirmation.
    """
    point=dict(source)
    point.update(state='reversal',source_state=source.get('state'),source_kind=source['kind'],
                 source_reversal_available_at=source['available_at'],available_at=known,
                 confirmation_rule='cross_path_confirmed_base_extreme',cross_path=True,
                 confirmed_by=[_continuity_proof(left),_continuity_proof(right)])
    return point


def _alternating_pair_bridge(left,right,candidates):
    """Find real H/L extremes when unlike endpoints have an invalid price leg."""
    first_kind=right['kind']; first_sign=1 if first_kind=='H' else -1
    best_first=best_pair=None; best_score=float('-inf')
    for point in candidates:
        if point['kind']==first_kind and _is_alternating_leg(left,point):
            if best_first is None or first_sign*(point['value']-best_first['value'])>0:
                best_first=point
            continue
        if (point['kind']!=left['kind'] or best_first is None or
                not _is_alternating_leg(best_first,point) or not _is_alternating_leg(point,right)):
            continue
        score=(abs(best_first['value']-left['value'])+abs(point['value']-best_first['value'])+
               abs(right['value']-point['value']))
        if score>best_score:
            best_score=score; best_pair=[best_first,point]
    return best_pair


def _continuity_bridge(left,right,source_points):
    """Return zero, one, or two confirmed base extremes joining two level-1 paths."""
    if _point_order(left)>=_point_order(right):
        return None
    known=max(left['available_at'],right['available_at'])
    candidates=[p for p in source_points if _point_order(left)<_point_order(p)<_point_order(right)
                and p.get('state') in ('confirmed','teaching') and p['available_at']<=known]
    if left['kind']==right['kind']:
        kind='H' if left['kind']=='L' else 'L'; sign=1 if kind=='H' else -1
        eligible=[p for p in candidates if p['kind']==kind and
                  sign*(p['value']-left['value'])>0 and sign*(p['value']-right['value'])>0]
        if not eligible:
            return None
        # Strict replacement preserves the earlier point when extremes are equal.
        extreme=eligible[0]
        for point in eligible[1:]:
            if sign*(point['value']-extreme['value'])>0:
                extreme=point
        return [_formal_bridge_point(extreme,known,left,right)]
    if _is_alternating_leg(left,right):
        return []
    pair=_alternating_pair_bridge(left,right,candidates)
    return None if pair is None else [_formal_bridge_point(p,known,left,right) for p in pair]


def _connect_reversal_strokes(strokes,source_strokes):
    """Merge level-1 paths with the same evidence used by the chart.

    Undefined base-bar ordering still splits the base lecture drawing.  A later
    pair of confirmed level-1 endpoints may nevertheless identify real,
    confirmed base extremes between those paths.  Promoting those extremes here
    gives rendering and higher trend levels one authoritative point sequence.
    """
    paths=sorted((dict(stroke,points=[dict(p) for p in stroke['points']]) for stroke in strokes
                  if stroke['points']),key=lambda stroke:_point_order(stroke['points'][0]))
    if not paths:
        return []
    source_points=sorted((dict(p,source_path=stroke['id']) for stroke in source_strokes
                          for p in stroke['points']),key=_point_order)

    def begin(path):
        source=path.get('source_path',path['id'])
        return dict(path,points=[dict(p) for p in path['points']],source_path=source,source_paths=[source])

    def finish(path):
        if len(path['source_paths'])>1:
            first,last=path['source_paths'][0],path['source_paths'][-1]
            path['id']=f'reversal-continuous-{first}-{last}'
            path['source_path']='→'.join(path['source_paths'])
            path['continuity_rule']='confirmed_base_extreme_across_path_boundary'
        return path

    merged=[]; current=begin(paths[0])
    for path in paths[1:]:
        bridge=_continuity_bridge(current['points'][-1],path['points'][0],source_points)
        if bridge is None:
            merged.append(finish(current)); current=begin(path); continue
        for point in [*bridge,*path['points']]:
            previous=current['points'][-1]
            if (_point_order(previous),previous['kind'],previous['value'])!=(
                    _point_order(point),point['kind'],point['value']):
                current['points'].append(dict(point))
        current['source_paths'].append(path.get('source_path',path['id']))
        current['input_turn_count']=current.get('input_turn_count',0)+path.get('input_turn_count',0)
    merged.append(finish(current))
    return merged


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
                confirmed_extreme={'confirmed_low':_ref(anchor)} if up else {'confirmed_high':_ref(anchor)}
                observation('翻空为多' if up else '翻多为空',key=_ref(key),
                            formation='底部成形' if up else '头部成形',**confirmed_extreme)
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
    result=[]; source_strokes=[]; local_count=0
    for stroke in drawing['strokes']:
        raw=stroke['points']; counts=Counter(p['time'] for p in raw); ranks=Counter(); compact=[]; projected=[]
        for source in raw:
            p=dict(source,projection_count=counts[source['time']],projection_rank=ranks[source['time']])
            ranks[source['time']]+=1
            projected.append(dict(p))
            if compact and p['value']==compact[-1]['value']:
                continue  # Earliest equal extreme wins; no zero-length reversal.
            compact.append(p)
        source_strokes.append(dict(id=stroke['id'],points=projected))
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
            result.append(dict(id=f'reversal-{stroke["id"]}',source_path=stroke['id'],kind='reversal',points=waves,
                               input_turn_count=len(turns)))
    result=_connect_reversal_strokes(result,source_strokes)
    for stroke in result:
        _annotate(stroke['points'],bars[0].symbol,dates)
    return dict(strokes=result,trend_level=1,name='一级趋势线',scope='lecture_wave_structure_not_strategy_confirmation',
                bear_to_bull_highs=bear_to_bull_highs(result,trend_level=1),
                aggregation_rule='HH_HL_or_LH_LL_switch_with_confirmed_cross_path_extremes',input_turn_count=local_count,
                confirmed_wave_count=sum(len(s['points']) for s in result),
                break_basis='confirmed_polyline_extreme',retracement_threshold=.67,
                note='先按原折线高低点确认短期多空，再在方向转换时取整段极值；相邻分段以真实已确认原折线极值正式衔接，并作为二级输入；未完成波段不画实线。')
