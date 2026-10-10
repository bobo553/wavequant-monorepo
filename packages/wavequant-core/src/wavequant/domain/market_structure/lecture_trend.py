"""Derive the level-1 trend line (一级趋势线) from the confirmed base polyline.

A small geometric turn is input evidence, not automatically a solid-line node.
HH+HL establishes up; LH+LL establishes down; mixed/equal comparisons hold the
previous direction. A wave switch needs a retracement AFTER the counter-impulse:
an older holding point cannot confirm a new impulse that has yet to retrace.
"""
from collections import Counter
from collections.abc import Sequence
from typing import NotRequired, TypedDict, cast

from .polyline import LinePoint, PointKind, ReversalPoint
from .price_action import Direction
from .trend_landmarks import (
    bear_bull_alternation_lows,
    bear_to_bull_highs,
    bullish_turn_signals,
    post_alternation_bull_highs,
)
from .trend_levels import trend_reversals
from .trend_level_upgrades import promote_trend_key_breaks
from .trend_primitives import last_fall_high, last_rise_low, negative_reversal, positive_reversal
from .trend_structure import (
    StructuralTrend, TrendPriceState, observe_structure, preceding_turn, retracement_evidence,
)


def _ref(p):
    return dict(index=p['index'], ordinal=p['ordinal'], time=p['time'],
                kind=p['kind'], value=p['value'], available_at=p['available_at'], label=p['label'])


def _context_key(points, bull, *, symbol, dates, trend_level):
    """按公共定义选择整窗极值以及其左侧最近的相反反转。

    多头背景冻结最高点之前的末升低，空头背景冻结最低点之前的末跌高。
    左侧最近点与窗口最高/最低价是两项不同条件；不能用更早的更高点、
    更晚的回调点或当前图表最右点替换该关键位。typed adapter仅统一证据表示，
    原始字典保留实际股票、坐标、ordinal和已知日期供后续事件引用。
    """
    typed=tuple(ReversalPoint(LinePoint(p['index'],p['ordinal'],PointKind(p['kind']),p['value']),
                              dates[p['available_at']],'published_trend_definition') for p in points)
    context=observe_structure(typed,symbol=symbol,timeframe='source',window_start=typed[0].point.index,
                              asof_index=typed[-1].confirmed_index,trend_level=trend_level)
    anchor=context.window_high if bull else context.window_low
    assert anchor is not None
    key=(last_rise_low(context,trend_level=trend_level) if bull else
         last_fall_high(context,trend_level=trend_level))
    previous=preceding_turn(typed,anchor) if key is not None else None
    return points[typed.index(anchor)],points[typed.index(previous)] if previous is not None else None


class _WaveTurn(TypedDict):
    index: int
    ordinal: int
    kind: str
    value: float
    time: str
    available_at: str
    state: NotRequired[str]
    source_state: NotRequired[str]
    source_kind: NotRequired[str]
    projection_count: NotRequired[int]
    projection_rank: NotRequired[int]


class _WaveReversal(_WaveTurn):
    source_reversal_available_at: str
    wave_direction_before: str
    wave_direction_after: str
    confirmation_rule: str
    confirmed_by: list[_WaveTurn]
    source_turn_position: int
    confirmed_on_turn: int


def _wave_reversals(turns: Sequence[_WaveTurn]) -> list[_WaveReversal]:
    """保留一级旧调用入口，方向判定委托同一个按级别传参的公共归并器。

    转折L/H只表示局部正/负反转；正式方向变化还需同类高、低点都严格同向，
    且持稳点发生在反向推进之后。突破升级另有独立通道，不由此函数猜测。
    """
    return cast(list[_WaveReversal], trend_reversals(turns, trend_level=1))


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


def _annotate(points, symbol, dates, *, trend_level=1):
    """为本级已确认点附加公共定义及独立关键位事件的展示证据。

    TrendPriceState同步给出最近两组的局部方向与整个路径的严格方向；
    后者要求“每一个”同类点均升高或降低，相等和混合不会被最新局部方向覆盖。
    below-67%交替和严格关键位突破仍是独立事件，单个正/负反转不等于趋势翻向。
    日期由原始available_at映射至行情索引，绝不以绘图的price index替代确认日。
    """
    price_state=TrendPriceState(trend_level=trend_level)
    previous_known=None
    high_count=low_count=0
    background=None; anchor=key=attack=None; suspicion=False
    for j,p in enumerate(points):
        if p['kind']=='H': high_count+=1; p['label']=f'H{high_count}'
        else: low_count+=1; p['label']=f'L{low_count}'
        p['trend_level']=trend_level
        p['source_level']=trend_level-1
        p['observations']=[]; p['levels']=[]
        previous=points[j-1] if j else None
        if previous:
            name='末升低' if p['kind']=='H' else '末跌高'
            p['preceding_turn']=_ref(previous)
            p['levels'].append(dict(name=f'{p["label"]} 的{name}',price=previous['value']))
        current=ReversalPoint(LinePoint(p['index'],p['ordinal'],PointKind(p['kind']),p['value']),
                              dates[p['available_at']],'lecture_geometric_turn_not_strategy')
        p['reversal']=('正反转' if positive_reversal(current,asof_index=current.confirmed_index,trend_level=trend_level)
                       else '负反转' if negative_reversal(current,asof_index=current.confirmed_index,trend_level=trend_level)
                       else None)
        if previous_known:
            if ((current.point.index,current.point.ordinal)<=
                    (previous_known.point.index,previous_known.point.ordinal)
                    or current.point.kind==previous_known.point.kind
                    or current.confirmed_index<previous_known.confirmed_index):
                raise ValueError('ordered alternating causally confirmed points required')
            if ((current.point.kind==PointKind.HIGH and current.point.price<=previous_known.point.price)
                    or (current.point.kind==PointKind.LOW and current.point.price>=previous_known.point.price)):
                raise ValueError('nonzero alternating high/low legs required')
        price_state.append(current.point.kind,current.point.price)
        previous_known=current
        trend=price_state.trend
        window_trend=price_state.window_trend
        p['trend']=trend.value
        p['window_trend']=window_trend.value

        def observation(title, **extra):
            p['observations'].append(dict(title=title,available_at=p['available_at'],
                                          basis='confirmed_polyline_extreme',**extra))
            if extra.get('key'):
                ref=extra['key']
                p['levels'].append(dict(name='转换依据：'+('末跌高' if ref['kind']=='H' else '末升低'),price=ref['value']))

        if background is None:
            if trend in (StructuralTrend.BULL,StructuralTrend.BEAR):
                background=trend
                anchor,key=_context_key(points[:j+1],background==StructuralTrend.BULL,symbol=symbol,dates=dates,trend_level=trend_level)
            continue
        if key is None:
            anchor,key=_context_key(points[:j+1],background==StructuralTrend.BULL,symbol=symbol,dates=dates,trend_level=trend_level)
            continue
        assert anchor is not None
        up=background==StructuralTrend.BEAR
        extreme_kind='H' if up else 'L'
        sign=1 if up else -1
        if attack is None:
            # Refresh a continuing trend's extreme and its immediate predecessor.
            if p['kind']==anchor['kind'] and previous is not None and sign*(p['value']-anchor['value'])<0:
                anchor,key=p,previous; suspicion=False
            elif p['kind']==anchor['kind'] and previous and key is not None and sign*(p['value']-anchor['value'])>=0 and sign*(previous['value']-key['value'])<0:
                if not suspicion:
                    observation('底部疑虑' if up else '头部疑虑',key=_ref(key)); suspicion=True
            if p['kind']==extreme_kind and key is not None and sign*(p['value']-key['value'])>0 and previous:
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
                            weak_countermove=ratio.below_33_percent,abc_confirmed=False,
                            **(dict(flip_high=_ref(attack['extreme']),confirmed_bear_low=_ref(attack['anchor']),
                                    broken_key=_ref(attack['key']),origin=_ref(attack['origin'])) if up else {}))
                background=StructuralTrend.BULL if up else StructuralTrend.BEAR
                anchor,key=attack['extreme'],attack['origin']; attack=None; suspicion=False
            else:
                observation('回档未通过交替条件' if up else '反弹未通过交替条件',ratio=ratio.ratio)
        elif p['kind']==extreme_kind and sign*(p['value']-attack['extreme']['value'])>0:
            attack.update(origin=previous,extreme=p,checked=False)
        if attack and p['kind']==attack['anchor']['kind'] and sign*(p['value']-attack['anchor']['value'])<0:
            observation('翻多观察失效' if up else '翻空观察失效')
            anchor,key=p,previous; attack=None; suspicion=False


def reversal_trends(drawing, bars, *, n_target_trend_confirmation_enabled: bool = False):
    """一级趋势以已确认高低折线为来源，保留发生日与首次可知日。

    未确认seed/developing不充当正负反转或升级证据。普通方向归并与严格
    高/低点突破升级共用后续发布许可，但保持各自确认规则及证据；可选N确认
    按既有开关工作。跨路径衔接只可复用当时已确认的真实折线极值。
    """
    # Drawing sessions are Shanghai dates, including timezone-aware market bars.
    from zoneinfo import ZoneInfo
    dates={(b.timestamp.astimezone(ZoneInfo('Asia/Shanghai')) if b.timestamp.tzinfo else b.timestamp).date().isoformat():i
           for i,b in enumerate(bars)}
    from .n_trend_reversals import n_target_reversals
    result=[]; source_strokes=[]; local_count=0; candidate_strokes=[]; source_kinds={}; developing_strokes=[]
    n_targets: list[dict[str,object]]=[]
    for stroke_index,stroke in enumerate(drawing['strokes']):
        raw=stroke['points']; counts=Counter(p['time'] for p in raw)
        ranks: Counter[str] = Counter()
        compact: list[_WaveTurn] = []
        projected=[]
        for source in raw:
            p: _WaveTurn = {**source, 'index':source['index'], 'ordinal':source['ordinal'],
                           'kind':source['kind'], 'value':source['value'],
                           'time':source['time'], 'available_at':source['available_at'],
                           'projection_count':counts[source['time']],
                           'projection_rank':ranks[source['time']]}
            ranks[source['time']]+=1
            projected.append(dict(p))
            if compact and p['value']==compact[-1]['value']:
                continue  # Earliest equal extreme wins; no zero-length reversal.
            compact.append(p)
        source_strokes.append(dict(id=stroke['id'],points=projected))
        turns: list[_WaveTurn] = []
        for left,p,right in zip(compact,compact[1:],compact[2:]):
            if p['state'] in ('seed','developing'):
                continue
            high=p['value']>left['value'] and p['value']>right['value']
            low=p['value']<left['value'] and p['value']<right['value']
            if high or low:
                turns.append({**p, 'kind':'H' if high else 'L', 'source_kind':p['kind'], 'state':'reversal',
                              'source_state':p['state']})
                source_kinds[(stroke['id'],p['index'],p['ordinal'])]='H' if high else 'L'
        local_count+=len(turns)
        geometric_waves=promote_trend_key_breaks(turns,_wave_reversals(turns),trend_level=1)
        later=drawing['strokes'][stroke_index+1:]
        cutoff=later[0]['points'][0]['index'] if later else len(bars)
        waves=(n_target_reversals(geometric_waves,turns,bars[:cutoff],source_level=0,target_sink=n_targets,
                                 n_target_trend_confirmation_enabled=True)
               if n_target_trend_confirmation_enabled else geometric_waves)
        if waves:
            result.append(dict(id=f'reversal-{stroke["id"]}',source_path=stroke['id'],kind='reversal',points=waves,
                               input_turn_count=len(turns)))
    # Continuity is candidate evidence. Qualifying each fragment first would
    # lose a known pressure anchor or the whole-wave low across its boundary.
    candidates=_connect_reversal_strokes(result,source_strokes)
    result=[]
    from .trend_publication import LEG_CONFIRMATION_POLICY, OPTIONAL_LEG_CONFIRMATION_POLICY, WAVE_DISPLAY_POLICY, confirmed_trend_legs, publish_uptrends
    for stroke in candidates:
        ancestors=set(stroke['source_paths'])
        last_source=max(index for index,source in enumerate(source_strokes) if source['id'] in ancestors)
        following=[source for source in source_strokes[last_source+1:] if source['points']]
        cutoff=following[0]['points'][0]['index'] if following else len(bars)
        market=bars[:cutoff]
        source_points=sorted((dict(point,label=point.get('label',point['kind']),
                                  kind=source_kinds.get((source['id'],point['index'],point['ordinal']),point['kind']))
                              for source in source_strokes if source['id'] in ancestors
                              for point in source['points']),key=_point_order)
        positions={_point_order(point):position for position,point in enumerate(source_points)}
        points=[dict(point,source_turn_position=positions[_point_order(point)]) for point in stroke['points']]
        # 候选并集可以同时保留已升级上涨端点H和后来确认的整波H。
        # 它们是不同可知时点的来源参考，尚不是一条正式高低交替路径；
        # 先由publisher独立验证/归并同波端点，再给正式路径标注正负反转。
        candidate_strokes.append(dict(stroke,points=points))
        published=publish_uptrends(points,source_points,market,source_level=0,
                                  n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if published:
            public=dict(stroke,points=published,confirmation_policy='trend_routes_v110',
                        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                        leg_confirmation_policy=OPTIONAL_LEG_CONFIRMATION_POLICY if n_target_trend_confirmation_enabled else LEG_CONFIRMATION_POLICY,
                        wave_display_policy=WAVE_DISPLAY_POLICY,
                        confirmed_legs=confirmed_trend_legs(published,trend_level=1,source_path=stroke['id']))
            result.append(public)
            if n_target_trend_confirmation_enabled and any(
                    point.get('trend_confirmation_route')=='source_n_strict_one_p_target' for point in published):
                from .hierarchical_development import hierarchical_developing_path
                tail=hierarchical_developing_path(dict(id=stroke['id'],points=source_points),published,
                        trend_level=1,source_level=0,kind='reversal',bars=market,structural=points,
                        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
                if tail:
                    developing_strokes.append(tail)
    for stroke in result:
        _annotate(stroke['points'],bars[0].symbol,dates)
    return dict(strokes=result,candidate_strokes=candidate_strokes,structure_strokes=candidate_strokes,
                developing_strokes=developing_strokes,
                n_target_observations=n_targets,
                n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                trend_level=1,name='一级趋势线',scope='lecture_wave_structure_not_strategy_confirmation',
                bear_to_bull_highs=bear_to_bull_highs(result,trend_level=1),
                bear_bull_alternation_lows=bear_bull_alternation_lows(result,trend_level=1,bars=bars),
                post_alternation_bull_highs=post_alternation_bull_highs(result,trend_level=1,bars=bars),
                bullish_turn_signals=bullish_turn_signals(result,bars,trend_level=1),
                aggregation_rule='ordered_HH_then_HL_or_LL_then_LH_switch_with_confirmed_cross_path_extremes',input_turn_count=local_count,
                trend_definition_rule='shared_six_definitions_and_strict_same_level_extreme_upgrade',
                confirmed_wave_count=sum(len(s['points']) for s in result),
                break_basis='confirmed_polyline_extreme',retracement_threshold=.67,
                note='六个定义按级别共用：一级读取基础高低折线，二级读取正式一级趋势，三级读取正式二级趋势；'
                     '同类高低点均严格升/降才成立多头/空头定义，局部正负反转不单独改变趋势方向；'
                     '已确认来源高点或低点严格突破此前已知本级同类极点时直接升级，不等待后续回调；'+
                     ('一级与二、三级使用相同三路线：本级关键位严格突破，或基础折线关键位突破、交替、后续收盘转向，'
                      '或正N/倒N完成后严格超过攻击箱测算的一饱确认对应趋势；等值不确认，首次证书冻结；'
                      if n_target_trend_confirmation_enabled else
                      '一级与二、三级使用相同两路线：本级关键位严格突破，或基础折线关键位突破、交替、后续收盘转向；')+
                     '有序高低点转换只生成内部结构候选，不单独授予上涨权限；'
                     '不能用推进前的旧回档确认新推进，确认前仍跟踪整段极值；相邻分段以真实已确认原折线极值正式衔接，'
                     '并作为二级输入；未完成波段不画实线。')
