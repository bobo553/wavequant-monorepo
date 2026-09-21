"""Derive level-2 trends from confirmed level-1 structural evidence.

The primary route freezes the last-fall-high or last-rise-low of its running
extreme until a new extreme or opposite break.  Level 2 also has one narrower
promotion route: a bull-flip high that clears the previous formal level-2
last-fall-high may become a formal high after a shallow scene pullback and a
causally confirmed nested level-2 low.  The two routes keep separate evidence.
"""
from zoneinfo import ZoneInfo

from .hierarchical_development import hierarchical_developing_path
from .lecture_trend import _annotate, _ref, _wave_reversals
from .trend_landmarks import (
    bear_bull_alternation_lows,
    bear_to_bull_highs,
    bullish_turn_signals,
    post_alternation_bull_highs,
)


def _bar_date(bar):
    """Return one market bar's Shanghai session date.

    Trend points store exchange dates rather than timestamps.  Normalizing here
    keeps the close-break proof consistent for naive local bars and timezone-
    aware bars supplied by API/backtest consumers.
    """
    timestamp=bar.timestamp
    if timestamp.tzinfo:
        timestamp=timestamp.astimezone(ZoneInfo('Asia/Shanghai'))
    return timestamp.date().isoformat()


def _first_close_break_below(bars,low):
    """Find the first causal close crossing below an already-known trend low.

    A lower intraday wick is intentionally insufficient.  The previous close
    must be at or above the low and the current close strictly below it.  Search
    starts only when the low itself is confirmed, preventing future knowledge
    from rewriting an earlier chart prefix.
    """
    known=max(low['time'],low['available_at'])
    for index,(previous,current) in enumerate(zip(bars,bars[1:]),1):
        time=_bar_date(current)
        if time<known or previous.close<low['value'] or current.close>=low['value']:
            continue
        return dict(index=index,time=time,available_at=time,kind='K',label='收盘跌破 K线',
                    value=current.close,open=current.open,high=current.high,low=current.low,
                    previous_close=previous.close,break_basis='close_cross')
    return None


def last_fall_high_reanchors(points,bars,*,trend_level=2):
    """Describe causal last-fall-high moves after an older low is invalidated.

    The lowest confirmed point remains the structural anchor while price stays
    above it.  Once a market close strictly crosses below that old low, the
    active segment advances to the next confirmed structure on its right.  A
    formal same-level low is preferred.  At an open tail, the confirming
    source-level low of an already-confirmed same-level high supplies the
    causal low evidence, so the key can move without inventing a future formal
    low.  In both cases the intervening same-level high is the new 末跌高.

    Only evidence is returned; confirmed trend points are never mutated or
    synthesized.  Consumers can therefore replay the transition at
    ``available_at`` without changing the historical level-2 polyline.  The
    transition date is never earlier than both the market break and the point
    that makes the replacement key knowable.
    """
    events=[]
    for position,broken_low in enumerate(points):
        if broken_low['kind']!='L':
            continue
        broken_at=_first_close_break_below(bars,broken_low)
        if broken_at is None:
            continue
        previous_key=next((point for point in reversed(points[:position]) if point['kind']=='H'),None)
        if previous_key is None:
            continue

        # Preserve the established rule when a later formal low already exists:
        # only a low already known at the break is allowed to move the segment.
        # This keeps historical events stable and confines the fallback below
        # to the one genuinely open tail at the end of the confirmed sequence.
        later_lows=[(index,point) for index,point in enumerate(points[position+1:],position+1)
                    if point['kind']=='L']
        known_lows=[item for item in later_lows if item[1]['available_at']<=broken_at['time']]
        formal_low=known_lows[-1] if known_lows else None
        if formal_low:
            active_position,active_low=formal_low
            new_key=next((point for point in reversed(points[:active_position]) if point['kind']=='H'),None)
            active_low_source_level=trend_level
        else:
            # A last H can be fully confirmed by a lower source-level L while
            # the corresponding next same-level L is still developing.  This
            # is exactly the live-tail state: use the proof already attached to
            # H, but do not append that proof to the formal level-2 polyline.
            if position!=len(points)-2 or points[-1]['kind']!='H':
                continue
            new_key=points[-1]
            active_low=new_key.get('confirmed_by')
            if (not isinstance(active_low,dict) or active_low.get('kind')!='L'
                    or active_low.get('value')>=broken_low['value']):
                continue
            active_low_source_level=trend_level-1

        if previous_key is None or new_key is None or new_key['index']==previous_key['index']:
            continue
        available_at=max(broken_at['time'],new_key['available_at'],active_low['available_at'])
        events.append(dict(
            id=f'level{trend_level}-last-fall-high-reanchor-{broken_low["index"]}-'
               f'{active_low["index"]}-{available_at}',
            kind='last_fall_high_reanchor',trend_level=trend_level,available_at=available_at,
            confirmation_rule='market_close_break_and_confirmed_replacement_key',
            active_low_source_level=active_low_source_level,
            previous_key=_ref(previous_key),broken_low=_ref(broken_low),
            active_low=_ref(active_low),new_key=_ref(new_key),confirmed_by=broken_at))
    return events


def _level2_high_promotion(points,base,candidate,old_key):
    """Build the second, causal confirmation route for one level-2 high.

    ``base`` is the formal level-2 low just confirmed by ``candidate``.  The
    candidate must also clear ``old_key``: the preceding formal level-2 high
    and therefore the old level-2 last-fall-high.  That same breakout alone is
    insufficient because it is already the proof for ``base``.

    The next confirmed level-1 low must remain above the base and retrace
    strictly less than two thirds of the base-to-candidate impulse.  Finally,
    the ordinary level-1 wave reducer must confirm a nested low.  This latter
    point is the chart's existing "informal level-2 reversal"; its proof date,
    not its historical price date, controls when the high can become formal.
    """
    base_point=points[base]; high=points[candidate]; previous_high=points[old_key]
    if (base_point['kind']!='L' or high['kind']!='H' or previous_high['kind']!='H'
            or high['value']<=previous_high['value']):
        return None

    pullback=None
    for position in range(candidate+1,len(points)):
        point=points[position]
        if point['kind']=='H':
            # A higher high starts a different impulse.  The caller will
            # schedule that newer candidate when it becomes the active anchor.
            if point['value']>high['value']:
                return None
            continue
        pullback=(position,point)
        break
    if pullback is None:
        return None
    pullback_position,pullback_point=pullback
    impulse=high['value']-base_point['value']
    retraced=high['value']-pullback_point['value']
    if (impulse<=0 or retraced<=0 or pullback_point['value']<=base_point['value']
            or retraced*3>=impulse*2):
        return None

    # Reuse the same reducer that produces the purple display-only development
    # path.  Its output is prefix-stable and carries the actual proof date.
    nested=_wave_reversals(points[candidate:])
    provisional=next((point for point in nested
                      if point['kind']=='L' and point['source_turn_position']>0),None)
    if provisional is None or provisional['value']<=base_point['value']:
        return None
    provisional_position=candidate+provisional['source_turn_position']
    confirmed_on=candidate+provisional['confirmed_on_turn']
    if confirmed_on<=pullback_position or confirmed_on>=len(points):
        return None
    return dict(
        confirmed_on=confirmed_on,
        provisional_position=provisional_position,
        point=dict(
            high,
            source_label=high['label'],
            trend_level=2,
            available_at=points[confirmed_on]['available_at'],
            source_level1_available_at=high['available_at'],
            source_level1_position=candidate,
            confirmed_on_level1=confirmed_on,
            confirmation_rule='level1_old_level2_key_break_alternation_and_nested_reversal',
            flip='空多交替高点升级',
            broken_key=_ref(previous_high),
            confirmed_by=_ref(points[confirmed_on]),
            alternation=dict(
                origin=_ref(base_point),
                impulse_high=_ref(high),
                point=_ref(pullback_point),
                retracement_ratio=retraced/impulse,
                confirmation_rule='higher_pullback_strictly_below_two_thirds',
            ),
            provisional_reversal=_ref(provisional),
            wave_direction_before='up',
            wave_direction_after='down',
        ),
    )


def _structural_reversals(points, *, source_level=1):
    """Reduce source points with strict key breaks plus the level-2 promotion route."""
    highs=[]; lows=[]; direction=None; anchor=None; key=None; selected=[]; promotion=None
    for j,p in enumerate(points):
        (highs if p['kind']=='H' else lows).append(j)
        if direction is None:
            if len(highs)<2 or len(lows)<2:
                continue
            dh=points[highs[-1]]['value']-points[highs[-2]]['value']
            dl=points[lows[-1]]['value']-points[lows[-2]]['value']
            direction='up' if dh>0 and dl>0 else 'down' if dh<0 and dl<0 else None
            if direction is None:
                continue
            pool=highs if direction=='up' else lows
            anchor=(max if direction=='up' else min)(pool,key=lambda k:points[k]['value'])
            key=anchor-1 if anchor else None
            continue
        up=direction=='up'; target='H' if up else 'L'; sign=1 if up else -1
        if p['kind']==target and sign*(p['value']-points[anchor]['value'])>0:
            anchor=j; key=j-1
            if source_level==1 and up and selected and selected[-1]['kind']=='L':
                old_key=next((k for k in range(len(selected)-2,-1,-1)
                              if selected[k]['kind']=='H'),None)
                promotion=(_level2_high_promotion(points,selected[-1]['source_level1_position'],j,
                                                   selected[old_key]['source_level1_position'])
                           if old_key is not None else None)
            elif up:
                promotion=None
        if promotion is not None and j==promotion['confirmed_on']:
            selected.append(promotion['point'])
            direction='down'
            anchor=promotion['provisional_position']
            key=anchor-1 if anchor else None
            promotion=None
            continue
        if key is None or p['kind']==target:
            continue
        # Up flips down on a strict break BELOW last-rise-low; down mirrors it.
        if sign*(p['value']-points[key]['value'])>=0:
            continue
        extreme=points[anchor]; trigger='翻多为空' if up else '翻空为多'
        selected.append(dict(extreme,source_label=extreme['label'],trend_level=source_level+1,
                             available_at=p['available_at'],
                             **{f'source_level{source_level}_available_at':extreme['available_at'],
                                f'source_level{source_level}_position':anchor,f'confirmed_on_level{source_level}':j},
                             confirmation_rule=f'level{source_level}_structural_key_break',
                             flip=trigger,broken_key=_ref(points[key]),confirmed_by=_ref(p),
                             wave_direction_before=direction,wave_direction_after='down' if up else 'up'))
        # Keep the whole intervening wave, not merely the bar that broke the key.
        direction='down' if up else 'up'
        pool=[k for k in range(anchor+1,j+1) if points[k]['kind']==('L' if up else 'H')]
        anchor=(min if up else max)(pool,key=lambda k:points[k]['value'])
        key=anchor-1
        promotion=None
        if source_level==1 and not up and len(selected)>=2 and selected[-2]['kind']=='H':
            promotion=_level2_high_promotion(
                points,
                selected[-1]['source_level1_position'],
                anchor,
                selected[-2]['source_level1_position'],
            )
    return selected


def secondary_trends(level1,bars):
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]; developing_strokes=[]
    for source in level1['strokes']:
        points=_structural_reversals(source['points'])
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates)
        for p in points:
            if p['confirmation_rule']=='level1_old_level2_key_break_alternation_and_nested_reversal':
                p['levels'].insert(0,dict(name='已突破的旧二级末跌高',price=p['broken_key']['value']))
            else:
                p['levels'].insert(0,dict(name='一级'+('末升低' if p['flip']=='翻多为空' else '末跌高'),price=p['broken_key']['value']))
        transitions=last_fall_high_reanchors(points,bars,trend_level=2)
        strokes.append(dict(id='secondary-'+source['id'],source_path=source['id'],kind='secondary',
                            trend_level=2,points=points,input_turn_count=len(source['points']),
                            key_transitions=transitions))
        tail=hierarchical_developing_path(source,points,trend_level=2,source_level=1,kind='secondary')
        if tail:
            developing_strokes.append(tail)
    return dict(name='二级趋势线',trend_level=2,source_level=1,strokes=strokes,
                bear_to_bull_highs=bear_to_bull_highs(strokes,trend_level=2),
                bear_bull_alternation_lows=bear_bull_alternation_lows(strokes,trend_level=2,source_strokes=level1['strokes'],bars=bars),
                post_alternation_bull_highs=post_alternation_bull_highs(strokes,trend_level=2,source_strokes=level1['strokes'],bars=bars),
                bullish_turn_signals=bullish_turn_signals(strokes,bars,trend_level=2,source_strokes=level1['strokes']),
                developing_strokes=developing_strokes,
                input_turn_count=sum(len(s['points']) for s in level1['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                developing_wave_count=len(developing_strokes),
                developing_point_count=sum(len(s['points']) for s in developing_strokes),
                key_transition_count=sum(len(s['key_transitions']) for s in strokes),
                aggregation_rule='level1_structural_key_break_or_confirmed_alternation_promotion',
                scope='lecture_level2_not_strategy_confirmation',
                note='一级点突破末跌高确认整段低点，跌破末升低确认整段高点；翻空为多高点若同时突破旧二级末跌高，'
                     '并在较高且严格小于2/3的场景回撤后出现已确认非正式二级低点，则在该反转可知日升级为正式二级高点；'
                     '旧二级低点被市场收盘严格跌破后，'
                     '末跌高换锚到后续已确认二级低点左侧高点；开放尾部尚无下一二级低点时，可用已确认二级高点及其'
                     '一级确认低点换锚，但不把一级点升级为二级点；最后一个正式二级点之后的已确认一级演化另作'
                     '纯显示发展路径，不进入正式点、三级趋势、策略或回测；普通关键位突破不等待67%交替，新增高点升级通道'
                     '必须满足上述场景回撤与非正式反转证据；不跨原路径断点。')
