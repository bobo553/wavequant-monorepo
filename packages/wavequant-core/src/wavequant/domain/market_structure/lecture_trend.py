"""基础折线适配及共享定义展示；生产趋势判断统一进入 point-first judge。"""
from collections.abc import Mapping, Sequence
from typing import NotRequired, TypedDict, cast

from ..models.model import Bar
from .polyline import LinePoint, PointKind, ReversalPoint
from .price_action import Direction
from .trend_levels import Moment, _formal_key_attack_point, _later_moment, trend_reversals
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


def _initial_context_key(points, bull, *, symbol, dates, trend_level):
    """已成立方向的首端极值没有前驱时，从首端之后的完整背景取证。

    路径首低可能属于更早多头，首高可能属于更早空头。它们没有左侧
    关键位，不能永久占据后来反向背景的极值；只排除这个无前驱首端，
    保留其后的全部已知点。有效关键位不走此回退，后续继续严格冻结。
    """
    anchor, key = _context_key(points, bull, symbol=symbol, dates=dates, trend_level=trend_level)
    if key is None and len(points) >= 4 and anchor is points[0]:
        anchor, key = _context_key(points[1:], bull, symbol=symbol, dates=dates, trend_level=trend_level)
    return anchor, key


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


def _strict_upgrade_context(
    point: Mapping[str, object], own: Mapping[tuple[int, int], dict[str, object]],
) -> tuple[dict[str, object], dict[str, object], bool] | None:
    """稀疏本级结构可用真实严格突破初始化背景，不等待额外两高两低。

    起点与关键位必须都存在于同一本级已知前缀；升级标签及下级较早坐标
    不能代替本级身份，也不能据此改写已有有效冻结关键位。
    """
    proof = point.get('upgrade_evidence')
    if (point.get('wave_identity_state') != 'owned' or not isinstance(proof, Mapping)
            or proof.get('role') != 'endpoint'):
        return None
    _formal_key_attack_point(point)
    refs = [proof.get('origin'), proof.get('broken_key')]
    actual: list[dict[str, object]] = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            return None
        index, ordinal = ref.get('index'), ref.get('ordinal', 0)
        if type(index) is not int or type(ordinal) is not int:
            return None
        current = own.get((index, ordinal))
        if (current is None or any(current.get(field) != ref.get(field) for field in ('kind', 'value', 'time'))
                or _later_moment(cast(Moment, current['available_at']),
                                 cast(Moment, point['available_at'])) != point['available_at']):
            return None
        actual.append(current)
    origin, key = actual
    if not _point_order(key) < _point_order(origin) < _point_order(point):
        return None
    rising = proof.get('direction') == 'up'
    sign = 1 if rising else -1
    if (origin['kind'] != ('L' if rising else 'H') or key['kind'] != point['kind']
            or sign * (cast(float, point['value']) - cast(float, key['value'])) <= 0
            or sign * (cast(float, key['value']) - cast(float, origin['value'])) <= 0):
        return None
    key_known = cast(Moment, key['available_at'])
    attack_time = cast(str, point['time'])
    earlier = (key_known < cast(int, point['index']) if isinstance(key_known, int) else
               int(key_known) < int(attack_time) if key_known.isdecimal() and attack_time.isdecimal() else
               key_known < attack_time)
    return (origin, key, rising) if earlier else None


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
    own_points: dict[tuple[int, int], dict[str, object]] = {}
    for j,p in enumerate(points):
        if p['kind']=='H': high_count+=1; p['label']=f'H{high_count}'
        else: low_count+=1; p['label']=f'L{low_count}'
        p['trend_level']=trend_level
        p['source_level']=trend_level-1
        p['observations']=[]; p['levels']=[]; p['lifecycle_context']=None
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

        strict = _strict_upgrade_context(p, own_points)
        own_points[_point_order(p)] = p
        if strict is not None:
            strict_origin, strict_key, strict_up = strict
            # 相反严格攻击跌破/突破上一翻向的真实起点时，新背景来自本级
            # 极值与旧关键位；同向延伸或局部较弱 key 不重置现有冻结上下文。
            reverses_pending = (attack is not None
                and p['kind'] != attack['extreme']['kind']
                and _point_order(strict_key) == _point_order(attack['origin']))
            if background is None or key is None or reverses_pending:
                background = StructuralTrend.BEAR if strict_up else StructuralTrend.BULL
                anchor, key, attack, suspicion = strict_origin, strict_key, None, False
        if background is None:
            if trend in (StructuralTrend.BULL,StructuralTrend.BEAR):
                background=trend
                anchor,key=_initial_context_key(points[:j+1],background==StructuralTrend.BULL,symbol=symbol,dates=dates,trend_level=trend_level)
            continue
        if key is None:
            # 路径首点可能恰是整窗最高/最低点，却没有左侧关键位。此时背景
            # 尚未获得完整身份；后续相反方向成立时必须允许重新初始化，
            # 否则会永远查询缺失的前驱，吞掉多年后的真实末跌高突破。
            selected = trend if trend in (StructuralTrend.BULL, StructuralTrend.BEAR) else background
            candidate_anchor,candidate_key=_initial_context_key(points[:j+1],selected==StructuralTrend.BULL,
                symbol=symbol,dates=dates,trend_level=trend_level)
            if candidate_key is not None:
                background,anchor,key=selected,candidate_anchor,candidate_key
            continue
        assert anchor is not None
        up=background==StructuralTrend.BEAR
        # 资格入口复用本级冻结关键位；局部升级证书不能另选末跌高。
        # 逐点重新写入，避免将下级或旧路径上下文带入当前级别。
        p['lifecycle_context']=dict(direction='up' if up else 'down',
                                    key=_ref(key),origin=_ref(anchor),available_at=p['available_at'])
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
                # 翻多起点是冻结末跌高之后的最低点，局部反弹低点不能另换测幅基准。
                attack=dict(origin=anchor if up else previous,extreme=p,anchor=anchor,key=key,checked=False)
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
            attack.update(origin=attack['origin'] if up else previous,extreme=p,checked=False)
        if attack and p['kind']==attack['anchor']['kind'] and sign*(p['value']-attack['anchor']['value'])<0:
            observation('翻多观察失效' if up else '翻空观察失效')
            anchor,key=p,previous; attack=None; suspicion=False


def reversal_trends(drawing: Mapping[str, object], bars: Sequence[Bar], *,
                    n_target_trend_confirmation_enabled: bool = False) -> dict[str, object]:
    """一级只把基础折线转成真实高低点，再调用共同的单层判断入口。

    K线和N开关只传给独立策略资格视图；不在这里判断趋势或筛选结构点。
    """
    from .trend_lines import polyline_high_low_points, trend_lines

    return trend_lines(polyline_high_low_points(drawing), bars, trend_level=1,
                       n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
