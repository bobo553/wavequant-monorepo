"""Derive confirmed and developing level-3 trends from level-2 structure.

Confirmed level-3 points remain immutable structural reversals. A separate
display-only path exposes confirmed nested level-2 turns plus the unresolved
tail after the latest reversal, so charts do not look truncated while the
opposite level-2 key has not yet been broken.
"""
from zoneinfo import ZoneInfo

from .lecture_trend import _annotate, _ref, _wave_reversals
from .secondary_trend import _structural_reversals


def _developing_point(point,position,available_at,role):
    """Copy one confirmed level-2 point into the display-only level-3 path."""
    result=_ref(point)
    result.update(available_at=available_at,state='developing',trend_level=3,
                  source_level2_position=position,source_label=point['label'],
                  display_only=True,development_role=role)
    return result


def _strongest(points,kind):
    """Choose the strongest point of one kind; equal prices keep the first."""
    candidates=[item for item in points if item[1]['kind']==kind]
    if not candidates:
        return None
    return (max if kind=='H' else min)(candidates,key=lambda item:item[1]['value'])


def _developing_path(source,confirmed):
    """Return the complete, display-only path after the latest level-3 point.

    The latest confirmed level-3 point fixes the beginning and direction of the
    unfinished structure.  Merely returning its first candidate extreme hides
    every later level-2 turn when a wide level-3 key remains unbroken, as in
    Shanghai Electric Power after 2015.  The display path therefore:

    1. starts at the latest formal level-3 point;
    2. reads only confirmed level-2 points from the proof that confirmed it;
    3. reuses the existing HH/HL versus LH/LL wave reducer to retain confirmed
       nested turns inside the still-unfinished level-3 structure; and
    4. preserves every confirmed level-2 point in the unresolved final tail.

    A missing first nested turn is preceded by the strongest point in the
    formal point's new direction. Equal extremes keep the first occurrence.
    After the last confirmed nested turn no point may be discarded: each is
    still live evidence and the final one is the current endpoint. Every
    coordinate comes from a real level-2 point and every display date is clamped
    to when both the formal start and nested evidence were knowable.

    This stroke is deliberately separate from ``confirmed``: it may extend when
    new level-2 evidence becomes known and must never become level-4, strategy,
    or backtest input.
    """
    if not confirmed:
        return None
    start=confirmed[-1]
    proof_position=start['confirmed_on_level2']
    source_points=source['points']
    if proof_position>=len(source_points):
        return None
    suffix=source_points[proof_position:]
    nested=_wave_reversals(suffix)
    initial_kind='H' if start['wave_direction_after']=='up' else 'L'
    path=[]
    start_ref=_ref(start)
    start_ref.update(state='confirmed',trend_level=3,
                     source_level2_position=start['source_level2_position'],
                     display_only=True,development_role='formal_start')
    path.append(start_ref)

    # If the nested reducer first confirms the opposite kind, preserve the
    # preceding candidate that connected the formal point to that turn.
    first_position=(proof_position+nested[0]['source_turn_position'] if nested else len(source_points))
    if not nested or nested[0]['kind']!=initial_kind:
        initial=_strongest(enumerate(source_points[proof_position:first_position+1],proof_position),initial_kind)
        if initial:
            position,point=initial
            known=max(start['available_at'],point['available_at'])
            path.append(_developing_point(point,position,known,'confirmed_candidate'))

    for turn in nested:
        position=proof_position+turn['source_turn_position']
        known=max(start['available_at'],turn['available_at'])
        path.append(_developing_point(turn,position,known,'confirmed_nested_turn'))

    # The reducer intentionally omits its unresolved final tail. Keep every
    # confirmed source point in that tail so the inspection line reaches the
    # latest level-2 evidence instead of stopping at an older extreme.
    if nested:
        last=nested[-1]
        last_position=proof_position+last['source_turn_position']
        for position,point in enumerate(source_points[last_position+1:],last_position+1):
            known=max(path[-1]['available_at'],point['available_at'])
            path.append(_developing_point(point,position,known,'pending_evidence'))

    if len(path)<2:
        return None
    path[-1]['development_role']='active_endpoint'
    active_direction='up' if path[-1]['kind']=='H' else 'down'
    return dict(id='tertiary-developing-'+source['id'],source_path=source['id'],kind='tertiary-developing',
                trend_level=3,source_level=2,state='developing',display_only=True,
                initial_direction=start['wave_direction_after'],wave_direction=active_direction,
                available_at=path[-1]['available_at'],
                nested_turn_count=sum(p['development_role']=='confirmed_nested_turn' for p in path),
                pending_point_count=sum(p['development_role'] in ('pending_evidence','active_endpoint') for p in path),
                confirmation_rule='confirmed_level2_development_path_after_last_level3_reversal',
                points=path)


def tertiary_trends(level2,bars):
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    strokes=[]; developing_strokes=[]
    for source in level2['strokes']:
        points=_structural_reversals(source['points'],source_level=2)
        if not points:
            continue
        _annotate(points,bars[0].symbol,dates)
        for p in points:
            p['levels'].insert(0,dict(name='二级'+('末升低' if p['flip']=='翻多为空' else '末跌高'),price=p['broken_key']['value']))
        strokes.append(dict(id='tertiary-'+source['id'],source_path=source['id'],kind='tertiary',
                            trend_level=3,points=points,input_turn_count=len(source['points'])))
        tail=_developing_path(source,points)
        if tail:
            developing_strokes.append(tail)
    return dict(name='三级趋势线',trend_level=3,source_level=2,strokes=strokes,
                developing_strokes=developing_strokes,
                input_turn_count=sum(len(s['points']) for s in level2['strokes']),
                confirmed_wave_count=sum(len(s['points']) for s in strokes),
                developing_wave_count=len(developing_strokes),
                developing_point_count=sum(len(s['points']) for s in developing_strokes),
                aggregation_rule='level2_structural_key_break',scope='lecture_level3_not_strategy_confirmation',
                note='仅以已确认二级点为输入；突破二级末跌高确认整段低点，跌破二级末升低确认整段高点；'
                     '最后一个正式三级点之后的已确认二级演化另作纯显示发展路径，不进入正式点、策略或回测；'
                     '不等待67%交替，不跨断点。')
