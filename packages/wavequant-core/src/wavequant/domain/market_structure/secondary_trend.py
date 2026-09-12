"""Derive level-2 trends from structural key breaks on confirmed level-1 points.

Unlike level 1's HH/HL direction switch, this layer freezes the last-fall-high
or last-rise-low of its running extreme until a new extreme or opposite break.
No retracement/alternation threshold is required to confirm a flip.
"""
from zoneinfo import ZoneInfo

from .lecture_trend import _annotate, _ref


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
    active segment advances to the latest *already confirmed* same-level low to
    its right.  That low's nearest preceding same-level high is the new 末跌高.

    Only evidence is returned; confirmed trend points are never mutated or
    synthesized.  Consumers can therefore replay the transition at
    ``available_at`` without changing the historical level-2 polyline.
    """
    events=[]
    for position,broken_low in enumerate(points):
        if broken_low['kind']!='L':
            continue
        broken_at=_first_close_break_below(bars,broken_low)
        if broken_at is None:
            continue
        successors=[(index,point) for index,point in enumerate(points[position+1:],position+1)
                    if point['kind']=='L' and point['available_at']<=broken_at['time']]
        if not successors:
            continue
        active_position,active_low=successors[-1]
        previous_key=next((point for point in reversed(points[:position]) if point['kind']=='H'),None)
        new_key=next((point for point in reversed(points[:active_position]) if point['kind']=='H'),None)
        if previous_key is None or new_key is None or new_key['index']==previous_key['index']:
            continue
        events.append(dict(
            id=f'level{trend_level}-last-fall-high-reanchor-{broken_low["index"]}-'
               f'{active_low["index"]}-{broken_at["time"]}',
            kind='last_fall_high_reanchor',trend_level=trend_level,available_at=broken_at['time'],
            confirmation_rule='market_close_strictly_below_previous_structural_low',
            previous_key=_ref(previous_key),broken_low=_ref(broken_low),
            active_low=_ref(active_low),new_key=_ref(new_key),confirmed_by=broken_at))
    return events


def _structural_reversals(points, *, source_level=1):
    """Shared key-break rule; source_level only names lineage metadata."""
    highs=[]; lows=[]; direction=None; anchor=None; key=None; selected=[]
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
    return selected


def secondary_trends(level1,bars):
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]
    for source in level1['strokes']:
        points=_structural_reversals(source['points'])
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates)
        for p in points:
            p['levels'].insert(0,dict(name='一级'+('末升低' if p['flip']=='翻多为空' else '末跌高'),price=p['broken_key']['value']))
        transitions=last_fall_high_reanchors(points,bars,trend_level=2)
        strokes.append(dict(id='secondary-'+source['id'],source_path=source['id'],kind='secondary',
                            trend_level=2,points=points,input_turn_count=len(source['points']),
                            key_transitions=transitions))
    return dict(name='二级趋势线',trend_level=2,source_level=1,strokes=strokes,
                input_turn_count=sum(len(s['points']) for s in level1['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                key_transition_count=sum(len(s['key_transitions']) for s in strokes),
                aggregation_rule='level1_structural_key_break',scope='lecture_level2_not_strategy_confirmation',
                note='一级点突破末跌高确认整段低点，跌破末升低确认整段高点；旧二级低点被市场收盘严格跌破后，末跌高换锚到后续已确认二级低点左侧高点；不等待67%交替，不跨原路径断点，不绘制未确认尾端。')
