"""高低点决定趋势线；行情证书提供独立的策略资格视图。

三个级别只改变来源层级和显示名称，单层结构判断始终调用 judge_trend。
不允许资格视图、N 附加证书或显示尾端反过来筛选下一层的结构来源。
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from bisect import bisect_right
from typing import Protocol, cast

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .lecture_trend import _annotate
from .n_trend_reversals import n_target_reversals
from .trend_judgement import judge_trend, mutable_points
from .trend_levels import Moment, _later_moment, _strip_source_license
from .trend_landmarks import (
    bear_bull_alternation_lows, bear_to_bull_highs, bullish_turn_signals, post_alternation_bull_highs,
)
from .trend_publication import confirmed_trend_legs, publish_uptrends
from .trend_structure import _trend_level

STRUCTURAL_LEG_POLICY = 'high_low_point_geometry_v113'
STRUCTURAL_RULE = 'point_first_recursive_six_definitions_v113'
_NAMES = {1: '一级', 2: '二级', 3: '三级'}
_KINDS = {1: 'reversal', 2: 'secondary', 3: 'tertiary'}


class _DefinitionAnnotator(Protocol):
    def __call__(self, points: list[dict[str, object]], symbol: str, dates: Mapping[object, int],
                 *, trend_level: int) -> None: ...


def high_low_points_from_strokes(strokes: object) -> list[dict[str, object]]:
    """将分段中的高低点展开，显式保留路径断点；不读取私有候选或显示尾部。

    source_path 只用来保持输入路径的边界，不参与价格判别。每层的正式
    geometry strokes 都能通过本适配器直接成为下一层的首参数。
    """
    if not isinstance(strokes, Sequence) or isinstance(strokes, (str, bytes)):
        raise ValueError('trend strokes must be a sequence')
    result: list[dict[str, object]] = []
    for stroke in strokes:
        if not isinstance(stroke, Mapping) or not isinstance(stroke.get('id'), str):
            raise ValueError('trend strokes require a path id')
        if stroke.get('display_only') is True:
            continue
        points = stroke.get('points')
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise ValueError('trend stroke points must be a sequence')
        for point in points:
            if not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point):
                raise ValueError('trend points must be mappings with string keys')
            result.append(dict(cast(Mapping[str, object], point), source_path=stroke['id']))
    return result


def polyline_high_low_points(drawing: Mapping[str, object]) -> list[dict[str, object]]:
    """基础折线只在入场时规范化一次，之后各级直接读取前一级输出。

    母子 K 线绘图端点的 kind 不一定是局部峰谷，故以左右相邻价格确定真实
    高低点。等值保留较早位置；seed/developing 没有确认反转，不作为来源。
    此处不判断多空、不升级趋势、不读取行情证书。
    """
    raw = high_low_points_from_strokes(drawing.get('strokes'))
    groups = _source_groups(raw)
    paths = list(groups)
    result: list[dict[str, object]] = []
    for position, (path, points) in enumerate(groups.items()):
        turns = polyline_turns(points, source_path=path)
        if position+1 < len(paths):
            # 下一路径的 seed 可能比它的首个反转更早，甚至尚无反转。不能因
            # 去掉非峰谷端点而丢失原断点，让旧路径借用新路径的 K 线资格。
            cutoff = groups[paths[position+1]][0]['index']
            turns = [dict(point, source_path_end_index=cutoff) for point in turns]
        result.extend(turns)
    return result


def polyline_turns(points: Sequence[Mapping[str, object]], *, source_path: str) -> list[dict[str, object]]:
    """图表与逐日历史共用的折线峰谷适配，不在历史入口另写一套判别。

    邻价峰谷只负责把绘图端点转成零级高低点；源的可知日保持原值，历史
    调用方可按首次实际观察日冻结时钟。等值点不会凭空制造正／负反转。
    """
    counts = Counter(str(point['time']) for point in points)
    ranks: Counter[str] = Counter()
    compact: list[dict[str, object]] = []
    for point in points:
        time = str(point['time'])
        projected = dict(point, projection_count=counts[time], projection_rank=ranks[time])
        ranks[time] += 1
        if not compact or projected['value'] != compact[-1]['value']:
            compact.append(projected)
    result: list[dict[str, object]] = []
    for left, point, right in zip(compact, compact[1:], compact[2:]):
        if point.get('state') in ('seed', 'developing') or point.get('display_only') is True:
            continue
        value, before, after = (cast(float, item['value']) for item in (point, left, right))
        kind = 'H' if value > max(before, after) else 'L' if value < min(before, after) else None
        if kind is not None:
            result.append(dict(point, kind=kind, source_kind=point['kind'], source_state=point.get('state'),
                               state='reversal', trend_level=0, source_path=source_path))
    return result


def _source_groups(points: Sequence[Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        raise ValueError('high_low_points must be a sequence of mappings')
    groups: dict[str, list[dict[str, object]]] = {}
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError('high_low_points must contain mappings')
        path = point.get('source_path', 'path-0')
        if not isinstance(path, str) or not path:
            raise ValueError('source_path must be a nonempty string')
        groups.setdefault(path, []).append(dict(point))
    return groups


def _definition_dates(points: Sequence[Mapping[str, object]]) -> dict[object, int]:
    """只由来源坐标建立展示用时钟，避免 K 线是否齐全改变结构或确认日期。

    整数/数字字符串保持其原索引。日期输入以来源的实际价格索引映射，
    稀疏来源中没有价格顶点的知识日只需要保持因果顺序和不早于价格位置。
    原 available_at 始终保留，绝不把这个展示适配索引写回结构证据。
    """
    result: dict[object, int] = {}
    ordered = sorted((str(point['time']), cast(int, point['index'])) for point in points)
    times = [time for time, _ in ordered]
    prefix_indices: list[int] = []
    for _, index in ordered:
        prefix_indices.append(max(index, prefix_indices[-1] if prefix_indices else index))
    for point in points:
        moment = point['available_at']
        if type(moment) is int:
            result[moment] = moment
        elif isinstance(moment, str) and moment.isdecimal():
            result[moment] = int(moment)
        elif isinstance(moment, str):
            position = bisect_right(times, moment) - 1
            result[moment] = max(cast(int, point['index']), prefix_indices[position] if position >= 0 else 0)
        else:
            raise ValueError('a confirmed point requires a knowledge index or date')
    return result


def qualify_trend_structure(
    candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]], bars: Sequence[Bar],
    *, trend_level: int, qualified_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False, n_target_sink: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """原发布器只建立策略许可，返回值不能再成为趋势线或下一层的输入。

    N 的行情达标证明只追加到这个独立视图。每份许可证仍用本级真实来源、
    知识日及行情前缀重新核验，下级证书不授予上级交易资格。
    """
    source_level = _trend_level(trend_level)
    if not bars:
        return []
    # 完整账本可在同一坐标保留早升级与晚普通反转。旧资格发布器消费的是
    # 结构节点，不能让晚证明把早已知关键位变成晚知，或重复追加同一节点。
    # 独立事实仍完整保存在 judge.candidates／candidate_strokes 中。
    earliest: dict[tuple[int, int], dict[str, object]] = {}
    for point in mutable_points(candidates):
        identity = cast(int, point['index']), cast(int, point.get('ordinal', 0))
        previous = earliest.get(identity)
        if previous is None or (_later_moment(cast(Moment, point['available_at']),
                cast(Moment, previous['available_at'])) == previous['available_at']
                and point['available_at'] != previous['available_at']):
            earliest[identity] = point
    qualified = [earliest[key] for key in sorted(earliest)]
    if n_target_trend_confirmation_enabled:
        qualified = n_target_reversals(qualified, source, bars, source_level=source_level,
            qualified_source=qualified_source, target_sink=n_target_sink, n_target_trend_confirmation_enabled=True)
    published = publish_uptrends(qualified, source, bars, source_level=source_level,
        qualified_source=qualified_source, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
    sessions = {session_date(bar): i for i, bar in enumerate(bars)}
    result: list[dict[str, object]] = []
    for point in published:
        known = point['available_at']
        known_index = (known if type(known) is int else int(known) if isinstance(known, str) and known.isdecimal()
                       else sessions.get(cast(str, known), -1))
        # 旧发布器可能保留尚无许可证的压力 H。它的几何归属不受影响，但
        # 超过本路径行情截止的未知压力不能进入策略视图或借下一路径日历。
        if 0 <= cast(int, point['index']) <= known_index < len(bars):
            result.append(point)
    return result


def _structural_legs(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [dict(points=[dict(left), dict(right)], direction='up' if left['kind'] == 'L' else 'down',
                 available_at=right['available_at'], basis=STRUCTURAL_RULE)
            for left, right in zip(points, points[1:])]


def _structural_tail(source: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                     *, path: str, trend_level: int) -> dict[str, object] | None:
    """已知下级极值延伸当前结构尾部，未闭合端点只供显示，不能递归升级。

    各级完全共用同一个极值选择，不等待行情证书。相等极值取早；两个端点
    都已经可知后才显示。源的许可证清除，display_only在路径及端点上明确标记。
    """
    if not points:
        return None
    anchor = points[-1]
    kind = 'H' if anchor['kind'] == 'L' else 'L'
    sign = 1 if kind == 'H' else -1
    eligible = [point for point in source if point.get('state') not in ('seed', 'developing')
        and point.get('display_only') is not True and point['kind'] == kind
        and (cast(int, point['index']), cast(int, point.get('ordinal', 0))) >
            (cast(int, anchor['index']), cast(int, anchor.get('ordinal', 0)))
        and sign * (cast(float, point['value']) - cast(float, anchor['value'])) > 0]
    if not eligible:
        return None
    extreme = eligible[0]
    for point in eligible[1:]:
        if sign * (cast(float, point['value']) - cast(float, extreme['value'])) > 0:
            extreme = point
    endpoint = dict(_strip_source_license(extreme), trend_level=trend_level, source_level=trend_level-1,
        state='developing', display_only=True, label=f'{kind}·发展', source_reversal_available_at=extreme['available_at'],
        available_at=_later_moment(cast(Moment, anchor['available_at']), cast(Moment, extreme['available_at'])))
    return dict(id=f'{_KINDS[trend_level]}-developing-{path}', source_path=path,
        kind=f'{_KINDS[trend_level]}-developing', trend_level=trend_level, source_level=trend_level-1,
        state='candidate', display_only=True, points=[dict(anchor), endpoint],
        confirmed_anchor=dict(anchor), wave_direction='up' if kind == 'H' else 'down',
        source_confirmed_count=1, pending_source_count=0, confirmation_policy=STRUCTURAL_RULE,
        leg_confirmation_policy=STRUCTURAL_LEG_POLICY, confirmed_legs=[])


def trend_lines(
    high_low_points: Sequence[Mapping[str, object]], bars: Sequence[Bar] = (), *, trend_level: int = 1,
    n_target_trend_confirmation_enabled: bool = False,
    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """统一单层入口：首参数始终是直接下级高低点，结构输出可直接递归。

    1 级传基础折线高低点，2 级传 1 级 strokes 的点，3 级传 2 级 strokes
    的点。judge_trend 是唯一判别器，级别只标注来源和名称。bars 和 N 开关
    只能影响 qualified_strokes/confirmed_legs，不能改变 strokes 的坐标、
    价格或结构可知日；没有资格也照样保留按六定义判出的趋势线。

    路径断点在所有级别采用同一规则：各路径独立判别，不以原 K 线极值
    额外拼接仅某一级的来源。candidate_strokes 保留结构事件，显示及策略
    消费者必须明确选择自己的视图，不能混作下一层的高低点。
    """
    source_level = _trend_level(trend_level)
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    groups = _source_groups(high_low_points)
    qualified_groups = _source_groups(qualified_source_points) if qualified_source_points is not None else {}
    strokes: list[dict[str, object]] = []
    qualified_strokes: list[dict[str, object]] = []
    developing_strokes: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    source_strokes: list[dict[str, object]] = []
    n_targets: list[dict[str, object]] = []
    kind, name = _KINDS[trend_level], _NAMES[trend_level]
    annotate = cast(_DefinitionAnnotator, _annotate)
    paths = list(groups)
    for path_position, (path, raw_source) in enumerate(groups.items()):
        source = mutable_points(raw_source)
        source_strokes.append(dict(id=path, points=source))
        judgement = judge_trend(source, trend_level=trend_level)
        points = mutable_points(judgement.points)
        events = mutable_points(judgement.candidates)
        identifier = f'{kind}-{path}'
        candidates.append(dict(id=identifier, source_path=path, trend_level=trend_level, points=events))
        # 行情只可用于自己的路径截止前缀，后一路径的首根 K 不属于前一路径。
        next_path = paths[path_position+1] if path_position+1 < len(paths) else None
        cutoff = raw_source[0].get('source_path_end_index',
            groups[next_path][0]['index'] if next_path is not None else len(bars))
        if type(cutoff) is not int or cutoff < 0:
            raise ValueError('source_path_end_index must be a nonnegative index')
        market = bars[:cutoff]
        permission_source = [dict(point, label=point.get('label', point.get('kind', '')))
                             for point in source]
        permissions = qualify_trend_structure(events, permission_source, market, trend_level=trend_level,
            qualified_source=qualified_groups.get(path, []) if qualified_source_points is not None else None,
            n_target_sink=n_targets,
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if permissions and market:
            # 资格事件仍需自己的末跌高、翻向等附加标注。只在独立副本上按真实
            # 行情日历生成，既不筛选几何，也不让资格日期倒写结构可知日。
            permission_dates: dict[object, int] = {session_date(bar): i for i, bar in enumerate(market)}
            permission_dates.update({i: i for i in range(len(market))})
            annotate(permissions, market[0].symbol, permission_dates, trend_level=trend_level)
        legs = confirmed_trend_legs(permissions, trend_level=trend_level, source_path=path)
        if permissions:
            from .secondary_trend import last_fall_high_reanchors

            reanchors = cast(Callable[..., list[dict[str, object]]], last_fall_high_reanchors)
            # 旧命名事件的外部契约使用交易所日期。整数历史保持整数时钟，
            # 不为这个日期展示附件转换或倒写任何资格／几何点。
            transitions = reanchors(permissions, market, trend_level=trend_level) if all(
                isinstance(point['available_at'], str) and not point['available_at'].isdecimal()
                for point in permissions) else []
            qualified_strokes.append(dict(id=identifier, source_path=path, trend_level=trend_level,
                kind=kind, points=permissions, confirmed_legs=legs, key_transitions=transitions, view='strategy_permissions'))
        if not points:
            continue
        definition_source = [point for point in source if point.get('state') not in ('seed', 'developing')
                             and point.get('display_only') is not True]
        annotate(points, bars[0].symbol if bars else 'STRUCTURE', _definition_dates([*definition_source, *points]),
                 trend_level=trend_level)
        tail = _structural_tail(source, points, path=path, trend_level=trend_level)
        if tail is not None:
            developing_strokes.append(tail)
        strokes.append(dict(id=identifier, source_path=path, source_paths=[path], kind=kind,
            trend_level=trend_level, points=points, input_turn_count=len(source), key_transitions=[],
            confirmation_policy=STRUCTURAL_RULE, leg_confirmation_policy=STRUCTURAL_LEG_POLICY,
            wave_display_policy='structural_points_independent_strategy_permissions_v113',
            n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
            structural_legs=_structural_legs(points), confirmed_legs=legs))
    # 这些命名事件是展示/交易附加事实，读取资格视图；不影响结构归并。
    highs = cast(Callable[..., object], bear_to_bull_highs)
    lows = cast(Callable[..., object], bear_bull_alternation_lows)
    post_highs = cast(Callable[..., object], post_alternation_bull_highs)
    signals = cast(Callable[..., object], bullish_turn_signals)
    return dict(name=f'{name}趋势线', trend_level=trend_level, source_level=source_level, strokes=strokes,
        source_strokes=source_strokes, qualified_strokes=qualified_strokes,
        candidate_strokes=candidates, structure_strokes=candidates, developing_strokes=developing_strokes,
        n_target_observations=n_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
        input_turn_count=sum(len(points) for points in groups.values()),
        confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in strokes),
        strategy_confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in qualified_strokes),
        developing_wave_count=len(developing_strokes), developing_point_count=2*len(developing_strokes),
        key_transition_count=sum(len(cast(list[object], stroke['key_transitions'])) for stroke in qualified_strokes),
        aggregation_rule=STRUCTURAL_RULE, trend_definition_rule=STRUCTURAL_RULE,
        scope='structural_trend_points_with_independent_strategy_permissions',
        bear_to_bull_highs=highs(qualified_strokes, trend_level=trend_level),
        bear_bull_alternation_lows=lows(qualified_strokes, trend_level=trend_level, source_strokes=source_strokes, bars=bars),
        post_alternation_bull_highs=post_highs(qualified_strokes, trend_level=trend_level, source_strokes=source_strokes, bars=bars),
        bullish_turn_signals=signals(qualified_strokes, bars, trend_level=trend_level, source_strokes=source_strokes),
        break_basis='confirmed_lower_level_high_low_points', retracement_threshold=.67,
        note='统一 judge_trend 的首参数为高低点：一级用折线，二级用一级输出，三级用二级输出；'
             '六定义和严格高低点突破升级只读取这个来源。相等不升级，不等待攻击之后的回调。'
             'K线突破、交替和可选N证书只提供策略资格，不筛掉趋势线结构点；路径断点各级一致保留。')
