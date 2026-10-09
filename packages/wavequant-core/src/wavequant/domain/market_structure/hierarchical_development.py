"""Expose live higher-level paths only after a complete direction proof."""

from .lecture_trend import _ref
from typing import cast
from .hierarchical_confirmation import DirectionConfirmation, TrendReference, market_trend_confirmation, session_date, source_trend_reversal
from .trend_confirmation import qualify_downtrend, qualify_uptrend
from .n_trend_confirmation import N_TARGET_CONFIRMATION, is_n_target_reversal, qualified_n_source


def _canonical_confirmation(proof):
    """A first market proof keeps one shape across later formal publication."""
    result=dict(proof)
    for field in ('origin','broken_key','confirmed_by'):
        point=proof[field]
        result[field]={name:point[name] for name in ('index','time','kind','value','available_at','label')}
        result[field]['ordinal']=point.get('ordinal',0)
    if proof['confirmation_rule']=='strict_same_level_market_key_break':
        result.pop('flip_high',None)
        result.pop('flip_low',None)
        result.pop('alternation_low',None)
        result.pop('alternation_high',None)
    return result


def _cycle_direction(source_points, start, position, bars, cutoff, n_source, n_target_trend_confirmation_enabled):
    """Choose the first completed source cycle after a frozen formal key."""
    rising=start['kind']=='H'
    origin_kind='L' if rising else 'H'
    qualify=qualify_uptrend if rising else qualify_downtrend
    proofs=[]
    for origin_position in range(position+1,len(source_points)):
        origin=source_points[origin_position]
        if origin['kind']!=origin_kind or origin['index']>cutoff:
            continue
        proof=qualify(source_points,origin,origin_position,None,bars,cutoff,n_source=n_source,
                      n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if proof is not None:
            proofs.append(proof)
    return cast(DirectionConfirmation,min(proofs,key=lambda proof:str(proof['available_at']))) if proofs else None


def _live_endpoint(confirmation,bars,cutoff):
    rising=confirmation['direction']=='up'
    origin=confirmation.get('wave_origin',confirmation['origin'])
    if origin['index']>=cutoff:
        return None
    positions=range(origin['index']+1,cutoff+1)
    position=(max if rising else min)(positions,key=lambda index:bars[index].high if rising else bars[index].low)
    date=session_date(bars[position])
    return dict(index=position,ordinal=0,time=date,kind='H' if rising else 'L',
                value=bars[position].high if rising else bars[position].low,
                available_at=max(date,confirmation['available_at']),label='当前高点' if rising else '当前低点')


def _countertrend_cycle(source_points,endpoint,bars,cutoff,n_target_trend_confirmation_enabled):
    """A live extreme becomes a source origin only after its own confirmation."""
    return source_trend_reversal(source_points,'up' if endpoint['kind']=='H' else 'down',endpoint,
                                 cutoff,session_date(bars[cutoff]),bars,
                                 n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)


def _direction_proof(point, n_target_trend_confirmation_enabled):
    """A disabled optional route cannot reuse a certificate from an earlier run."""
    for field in ('trend_confirmation', 'incoming_trend_confirmation'):
        proof=point.get(field)
        if proof and (n_target_trend_confirmation_enabled or proof.get('confirmation_rule')!=N_TARGET_CONFIRMATION):
            return proof
    return None


def hierarchical_developing_path(source,confirmed,*,trend_level,source_level,kind,bars=(),end_index=None,structural=(),qualified_source_points=None,n_target_trend_confirmation_enabled: bool = False):
    """Share enabled direction routes while keeping the endpoint display-only.

    A source-key break alone grants neither a solid rising direction nor an
    inspection dash. Completed proofs freeze their first knowledge date; a
    changing live extreme never becomes input to another level or a strategy.
    """
    if not n_target_trend_confirmation_enabled:
        confirmed=[point for point in confirmed if not is_n_target_reversal(point)]
        structural=[point for point in structural if not is_n_target_reversal(point)]
    if not confirmed or not bars:
        return None
    cutoff=len(bars)-1 if end_index is None else min(end_index,len(bars)-1)
    if cutoff<0:
        return None
    keys=[point for point in structural if point['available_at']<=session_date(bars[cutoff])]
    keys=keys or confirmed
    start=keys[-1]
    public=next((point for point in reversed(confirmed)
                 if all(point[field]==start[field] for field in ('index','kind','value'))),None)
    position_field='source_turn_position' if source_level==0 else f'source_level{source_level}_position'
    source_points=source['points']
    n_source=(qualified_n_source(source_points,qualified_source_points)
              if n_target_trend_confirmation_enabled and qualified_source_points is not None else source_points)
    position=start.get(position_field)
    if not isinstance(position,int) or not 0<=position<len(source_points):
        return None
    direct=market_trend_confirmation(source_points,start,position,bars,cutoff)
    prior_direction=False
    if direct is None and len(keys)>1:
        previous=keys[-2]
        established=market_trend_confirmation(source_points,previous,previous[position_field],bars,cutoff)
        if established and all(established['confirmation']['origin'][field]==start[field]
                               for field in ('index','kind','value')):
            direct=established
            prior_direction=True
    confirmation=direct['confirmation'] if direct else None
    endpoint=direct['endpoint'] if direct else None
    active=_direction_proof(confirmed[-1],n_target_trend_confirmation_enabled)
    if active is not None and active['available_at']<=session_date(bars[cutoff]):
        origin=active.get('wave_origin',active['origin'])
        origin_position=next((index for index,point in enumerate(source_points)
                              if all(point[field]==origin[field] for field in ('index','kind','value'))),None)
        opposite=(market_trend_confirmation(source_points,cast(TrendReference,dict(origin,available_at=active['available_at'])),
                                             origin_position,bars,cutoff) if origin_position is not None else None)
        if opposite is not None:
            if direct is None or direct['confirmation']['available_at']<=opposite['confirmation']['available_at']:
                confirmation=opposite['confirmation']; endpoint=opposite['endpoint']
        else:
            confirmation=cast(DirectionConfirmation,dict(active))
            endpoint=cast(TrendReference,_live_endpoint(confirmation,bars,cutoff))
        prior_direction=True
    if confirmation is None:
        published=_direction_proof(public or start,n_target_trend_confirmation_enabled)
        if published and published['available_at']<=session_date(bars[cutoff]):
            confirmation=cast(DirectionConfirmation,dict(published))
            prior_direction=True
        else:
            confirmation=_cycle_direction(source_points,start,position,bars,cutoff,n_source,
                                          n_target_trend_confirmation_enabled)
            prior_direction=True
        if confirmation is None:
            return None
        endpoint=cast(TrendReference,_live_endpoint(confirmation,bars,cutoff))
    if endpoint is None:
        return None
    confirmation=cast(DirectionConfirmation,_canonical_confirmation(confirmation))
    known=confirmation['available_at']
    origin=dict(confirmation.get('wave_origin',confirmation['origin']),state='confirmed',display_only=True,available_at=known,
                trend_level=trend_level,development_role='confirmed_direction_origin')
    endpoint=cast(TrendReference,dict(endpoint,state='developing',display_only=True,trend_level=trend_level,
                  development_role='active_endpoint'))
    path=[origin,endpoint] if prior_direction else [
        dict(_ref(start),state='confirmed',display_only=True,trend_level=trend_level,
             development_role='formal_start'),origin,endpoint]
    counter_sources=source_points if qualified_source_points is None else qualified_source_points
    countertrend=_countertrend_cycle(counter_sources,endpoint,bars,cutoff,n_target_trend_confirmation_enabled)
    for source_position,point in enumerate(source_points):
        if (countertrend is None or point['index']<=endpoint['index'] or point['index']>cutoff
                or point['available_at']>session_date(bars[cutoff])
                or point.get('state') in ('seed','developing')):
            continue
        previous=path[-1]
        if point['kind']==previous['kind'] or (point['value']<=previous['value'] if point['kind']=='H'
                                             else point['value']>=previous['value']):
            continue
        pending=dict(_ref(point),available_at=max(countertrend['available_at'],point['available_at']),
                     state='developing',display_only=True,trend_level=trend_level,
                     **{position_field:source_position},source_label=point['label'],
                     development_role='pending_evidence',edge_state='developing')
        path.append(pending)
    return dict(id=f'{kind}-developing-{source["id"]}',source_path=source['id'],kind=f'{kind}-developing',
                trend_level=trend_level,source_level=source_level,state='confirmed',display_only=True,
                confirmation_policy='trend_routes_v109',
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                initial_direction=start['wave_direction_after'],wave_direction=confirmation['direction'],
                available_at=known,endpoint_state='developing',confirmation=confirmation,
                confirmed_endpoint=endpoint,confirmation_rule=confirmation['confirmation_rule'],nested_turn_count=0,
                pending_point_count=len(path)-(1 if prior_direction else 2),points=path,
                **({'countertrend_confirmation':countertrend} if countertrend is not None else {}))
