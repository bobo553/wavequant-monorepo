"""二级薄适配器：一级结构高低点直接传入统一趋势判断。

本模块的旧私有工具仅保留兼容测试及历史调用；生产 secondary_trends
不调用这些独立循环。所有级别都由 judge_trend 判别，资格视图只核验策略。
"""
from copy import deepcopy
from bisect import bisect_left
from collections.abc import Mapping, Sequence
from zoneinfo import ZoneInfo

from ..models.model import Bar
from .lecture_trend import _ref
from .trend_publication import publish_uptrends
from .n_trend_reversals import n_target_reversals
from .trend_level_upgrades import promote_trend_key_breaks


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


def _first_close_break_below(bars,low,*,_session_dates=None,_ordered_dates=False):
    """Find the first causal close crossing below an already-known trend low.

    A lower intraday wick is intentionally insufficient.  The previous close
    must be at or above the low and the current close strictly below it.  Search
    starts only when the low itself is confirmed, preventing future knowledge
    from rewriting an earlier chart prefix.
    """
    known=max(low['time'],low['available_at'])
    start=max(1,bisect_left(_session_dates,known)) if _session_dates is not None and _ordered_dates else 1
    for index in range(start,len(bars)):
        previous,current=bars[index-1],bars[index]
        time=_session_dates[index] if _session_dates is not None else _bar_date(current)
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
    session_dates=tuple(_bar_date(bar) for bar in bars)
    ordered_dates=all(left<=right for left,right in zip(session_dates,session_dates[1:]))
    for position,broken_low in enumerate(points):
        if broken_low['kind']!='L':
            continue
        broken_at=_first_close_break_below(bars,broken_low,
            _session_dates=session_dates,_ordered_dates=ordered_dates)
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


def _level2_high_promotion(points,base,candidate,old_key,published_on,*,source_level=1):
    """Publish a confirmed source high breaking a previously known formal key.

    ``old_key`` retains its formal level-2 publication date; the original
    level-1 high's earlier date cannot stand in for that evidence. The same
    reducer is replayed with either session dates or integer session indices.
    The base low must already be published; an earlier impulse high cannot
    acquire that base's proof before the actual source turn that supplied it.
    No later source low is read or invented by this confirmation route.
    """
    if candidate>published_on or published_on>=len(points):
        return None
    high=points[candidate]
    if (base['kind']!='L' or high['kind']!='H' or old_key['kind']!='H'
            or high.get('state') in ('seed','developing') or high['value']<=old_key['value']):
        return None
    known=old_key['available_at']
    price_session=high['index'] if type(known) is int else high['time']
    if known>=price_session:
        return None
    return dict(high,source_label=high['label'],trend_level=source_level+1,
                available_at=max(high['available_at'],base['available_at']),
                **{f'source_level{source_level}_available_at':high['available_at'],
                   f'source_level{source_level}_position':candidate,f'confirmed_on_level{source_level}':published_on},
                confirmation_rule=f'level{source_level}_confirmed_high_breaks_known_level{source_level+1}_last_fall_high',
                flip=('二级' if source_level==1 else '三级')+'末跌高突破升级',broken_key=_ref(old_key),confirmed_by=_ref(high),
                source_confirmation=deepcopy(high.get('confirmed_by')),
                wave_direction_before='up',wave_direction_after='down')


def _candidate_structural_reversals(points, *, source_level=1):
    """Track structural keys; candidates do not themselves authorize a trend."""
    highs: list[int] = []
    lows: list[int] = []
    selected: list[dict[str, object]] = []
    direction=None; anchor=None; key=None
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
            if key is None:
                # A first-vertex extreme has no preceding opposite key. Start
                # from the latest locally confirmed extreme instead of leaving
                # this higher level unable to observe any future reversal.
                anchor=pool[-1]
                key=anchor-1 if anchor else None
            continue
        up=direction=='up'; target='H' if up else 'L'; sign=1 if up else -1
        if anchor is None:
            # A directly promoted high has no causal source low yet. Only a
            # subsequently published low can seed the next downward wave.
            last_high=selected[-1]
            if (p['kind']==target and p.get('state') not in ('seed','developing')
                    and (p['index'],p.get('ordinal',0))>(last_high['index'],last_high.get('ordinal',0))
                    and p['value']<last_high['value']):
                anchor=j; key=j-1 if j else None
            continue
        if p['kind']==target and sign*(p['value']-points[anchor]['value'])>0:
            anchor=j; key=j-1
            if up and selected and selected[-1]['kind']=='L':
                old_key=next((k for k in range(len(selected)-2,-1,-1)
                              if selected[k]['kind']=='H'),None)
                promoted=(_level2_high_promotion(points,selected[-1],j,selected[old_key],j,source_level=source_level)
                           if old_key is not None else None)
                if promoted is not None:
                    selected.append(promoted)
                    direction='down'; anchor=key=None
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
        assert anchor is not None
        pool=[k for k in range(anchor+1,j+1) if points[k]['kind']==('L' if up else 'H')]
        anchor=(min if up else max)(pool,key=lambda k:points[k]['value'])
        key=anchor-1
        if not up and len(selected)>=2 and selected[-2]['kind']=='H':
            promoted=_level2_high_promotion(
                points,
                selected[-1],
                anchor,
                selected[-2],
                j,
                source_level=source_level,
            )
            if promoted is not None:
                selected.append(promoted)
                direction='down'; anchor=key=None
    return selected


def _structural_reversals(points, *, source_level=1, bars=(), n_target_trend_confirmation_enabled: bool = False):
    """Publish only complete upward confirmations from the shared gate."""
    from .trend_levels import trend_reversals
    candidates=promote_trend_key_breaks(points,trend_reversals(points,trend_level=source_level+1),
                                       trend_level=source_level+1)
    if n_target_trend_confirmation_enabled:
        candidates=n_target_reversals(candidates,points,bars,source_level=source_level,
                                     n_target_trend_confirmation_enabled=True)
    return publish_uptrends(candidates,
                            points,bars,source_level=source_level,
                            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)


def secondary_trends(level1: Mapping[str, object], bars: Sequence[Bar], *,
                     n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    """一级趋势线的高低点直接成为首参数，不另写二级判断规则。

    qualified_strokes仅给策略证书核验使用，下一层的结构来源始终是strokes。
    """
    from .trend_lines import high_low_points_from_strokes, trend_lines

    points = high_low_points_from_strokes(level1.get('strokes'))
    qualified = high_low_points_from_strokes(level1.get('qualified_strokes', level1.get('strokes')))
    return trend_lines(points, bars, trend_level=2, qualified_source_points=qualified,
                       n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
