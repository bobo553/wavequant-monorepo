"""高低点决定趋势线；行情证书提供独立的策略资格视图。

三个级别只改变来源层级和显示名称，单层结构判断始终调用 judge_trend。
不允许资格视图、N 附加证书或显示尾端反过来筛选下一层的结构来源。
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from bisect import bisect_right
from copy import deepcopy
from math import isfinite
from typing import Protocol, cast

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .lecture_trend import _annotate
from .n_trend_reversals import n_target_reversals
from .trend_judgement import judge_trend, mutable_points
from .trend_levels import Moment, _completed_source_point, _later_moment, _strip_source_license, completed_source_points
from .trend_landmarks import (
    bear_bull_alternation_lows, bear_to_bull_highs, bullish_turn_signals, post_alternation_bull_highs,
)
from .trend_publication import confirmed_trend_legs, publish_uptrends
from .trend_structure import _trend_level

STRUCTURAL_LEG_POLICY = 'high_low_point_geometry_v113'
STRUCTURAL_RULE = 'point_first_recursive_six_definitions_v113'
_NAMES = {1: '一级', 2: '二级', 3: '三级'}
_KINDS = {1: 'reversal', 2: 'secondary', 3: 'tertiary'}
_GEOMETRY_ANNOTATION_FIELDS = (
    'label', 'trend_level', 'source_level', 'trend', 'window_trend', 'reversal',
    'observations', 'levels', 'preceding_turn',
)
_ANNOTATION_REFERENCE_FIELDS = frozenset(('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label'))


class _DefinitionAnnotator(Protocol):
    def __call__(self, points: list[dict[str, object]], symbol: str, dates: Mapping[object, int],
                 *, trend_level: int) -> None: ...


def high_low_points_from_strokes(strokes: object) -> list[dict[str, object]]:
    """将分段中的高低点展开，显式保留路径断点；不读取私有候选或显示尾部。

    source_path 只用来保持输入路径的边界，不参与价格判别。每层的正式
    geometry strokes 的已结束点通过共同门禁成为下一层首参数。严格升级的
    开放端点仍可绘图，但不能递归；副本使用真实整波结束日，原图形自己的
    earliest available_at 保持不变，直接 judge(points) 与本适配器完全一致。
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
        path_points: list[dict[str, object]] = []
        for point in points:
            if not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point):
                raise ValueError('trend points must be mappings with string keys')
            path_points.append(dict(cast(Mapping[str, object], point), source_path=stroke['id']))
        if path_points:
            level = path_points[0].get('trend_level', 0)
            if type(level) is not int or level not in (0, 1, 2, 3):
                raise ValueError('recursive strokes must belong to a direct source level')
            # 原始高低折线仍需 seed 和最后邻点来提取中间真正峰谷；它不是
            # 已生成的趋势线，不能在三点几何投影之前丢掉这些边界邻点。
            # 一级及以上才有开放升级端点，需要和直接 judge 共用结束门禁。
            result.extend(path_points if level == 0 else completed_source_points(path_points, source_level=level))
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
    issues = drawing.get('issues', ())
    if not isinstance(issues, Sequence) or isinstance(issues, (str, bytes)):
        raise ValueError('drawing issues must be a sequence')
    issue_indices: list[int] = []
    for issue in issues:
        index = issue.get('index') if isinstance(issue, Mapping) else None
        if type(index) is not int or index < 0:
            raise ValueError('drawing issues require nonnegative indices')
        issue_indices.append(index)
    issue_indices.sort()
    result: list[dict[str, object]] = []
    for position, (path, points) in enumerate(groups.items()):
        first_index = points[0]['index']
        if type(first_index) is not int or first_index < 0:
            raise ValueError('polyline paths require a nonnegative origin index')
        # lecture ID 记录重启 epoch 的真实起点；无 seed 的教学端点可能稍晚。
        # 普通带 seed 的路径优先使用它自己的原始坐标，不用首个峰谷作起点。
        suffix = path.removeprefix('lecture-')
        origin = (int(suffix) if points[0].get('state') != 'seed' and path.startswith('lecture-')
                  and suffix.isdecimal() else first_index)
        caps: list[int] = []
        for point in points:
            if 'source_path_end_index' in point:
                cap = point['source_path_end_index']
                if type(cap) is not int or cap < 0:
                    raise ValueError('source_path_end_index must be a nonnegative index')
                caps.append(cap)
        issue_position = bisect_right(issue_indices, origin)
        if issue_position < len(issue_indices):
            caps.append(issue_indices[issue_position])
        turns = polyline_turns(points, source_path=path)
        if position+1 < len(paths):
            # 下一路径的 seed 可能比它的首个反转更早，甚至尚无反转。不能因
            # 去掉非峰谷端点而丢失原断点，让旧路径借用新路径的 K 线资格。
            next_seed = groups[paths[position+1]][0]['index']
            if type(next_seed) is not int or next_seed < 0:
                raise ValueError('polyline paths require a nonnegative seed index')
            caps.append(next_seed)
        # 多个空 epoch 没有可展开的后一条 stroke，仍须在首次 issue 关闭旧路径。
        # 显式 cap、下一 raw seed 与首次断路共同取最早值，保留首40而非再重启41。
        if caps:
            cutoff = min(caps)
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


def _restore_annotation_references(
    value: object, originals: Mapping[tuple[int, int], Mapping[str, object]],
    *, labels: Mapping[tuple[int, int], str] | None = None,
) -> object:
    """只恢复派生字段中完整点引用的自身可知日，不改命名事件的上下文日期。"""
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('annotation fields must use string keys')
        fields = cast(Mapping[str, object], value)
        result = {key: _restore_annotation_references(child, originals, labels=labels)
                  for key, child in fields.items()}
        # 旧 _ref 的完整七字段结构才是点引用。观察事件还有 title/basis 等
        # 字段，即使也带坐标，仍必须保留生成事件时累计成立的上下文日期。
        if fields.keys() == _ANNOTATION_REFERENCE_FIELDS:
            index, ordinal = fields['index'], fields['ordinal']
            if type(index) is int and type(ordinal) is int:
                original = originals.get((index, ordinal))
                if original is not None and all(fields[field] == original[field] for field in
                        ('time', 'kind', 'value')):
                    result['available_at'] = original['available_at']
                    if labels is not None:
                        identity = index, ordinal
                        result['label'] = labels[identity] if identity in labels else original['label']
        return result
    if isinstance(value, list):
        return [_restore_annotation_references(child, originals, labels=labels) for child in value]
    if isinstance(value, tuple):
        return tuple(_restore_annotation_references(child, originals, labels=labels) for child in value)
    return value


def _annotate_geometry(
    points: list[dict[str, object]], symbol: str, dates: Mapping[object, int], *, trend_level: int,
) -> None:
    """为几何附加定义注释，原事实时钟与完整上下文时钟各自保留。

    按价格顺序选出的节点可能先有较晚可知的 H，再有较早可知的 L。
    它们各自的日期仍是真实事实；但依赖左侧全部节点的趋势和翻向观察，
    只能在此前节点均已可知后成立。旧标注器要求单调知识日，因此仅把
    最小坐标投影的 available_at 临时设为前缀最大值，不修改原节点。

    临时投影不携带 source_confirmation、definition_evidence、upgrade_evidence
    等证明，避免标注器接触或复制整份账本。只白名单回写派生字段；完整
    _ref 内恢复对应几何节点自己的日期，观察顶层保留累计日期。它既不能
    倒填事件，也不能把累计上下文日期冒充下一层来源的自身可知日。
    坐标顺序、交替种类及非零腿仍由原标注器严格校验，不放宽其公共契约。
    """
    shadows: list[dict[str, object]] = []
    originals: dict[tuple[int, int], Mapping[str, object]] = {}
    context_known: Moment | None = None
    for point in points:
        own_known = cast(Moment, point['available_at'])
        context_known = own_known if context_known is None else _later_moment(context_known, own_known)
        shadows.append(dict({field: point[field] for field in ('index', 'ordinal', 'time', 'kind', 'value')},
                            available_at=context_known))
        originals[(cast(int, point['index']), cast(int, point['ordinal']))] = point
    annotate = cast(_DefinitionAnnotator, _annotate)
    annotate(shadows, symbol, dates, trend_level=trend_level)
    # 必须等整个影子序列通过原校验才回写，失败时原几何和所有证明保持原样。
    for point, shadow in zip(points, shadows):
        for field in _GEOMETRY_ANNOTATION_FIELDS:
            if field in shadow:
                point[field] = _restore_annotation_references(shadow[field], originals)


def _permission_leg(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    """许可账本中的邻点只有真实交替、严格价格腿才共享命名上下文。"""
    return ((cast(int, left['index']), cast(int, left['ordinal'])) <
            (cast(int, right['index']), cast(int, right['ordinal']))
            and left['kind'] != right['kind']
            and (cast(float, right['value']) > cast(float, left['value']) if right['kind'] == 'H'
                 else cast(float, right['value']) < cast(float, left['value'])))


def _permission_origin(point: Mapping[str, object], dates: Mapping[object, int]) -> dict[str, object] | None:
    """仅消费本次 incoming 证明的完整真实起点，不借旧 active 压力波的起点。"""
    proof = point.get('incoming_trend_confirmation')
    if point.get('incoming_trend_state') != 'confirmed' or not isinstance(proof, Mapping):
        return None
    origin = proof.get('wave_origin', proof.get('origin'))
    if not isinstance(origin, Mapping) or not _ANNOTATION_REFERENCE_FIELDS.issubset(origin):
        return None
    index, ordinal, price = origin['index'], origin['ordinal'], origin['value']
    if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
            or type(price) not in (int, float) or not isfinite(cast(float, price)) or cast(float, price) <= 0
            or origin['kind'] not in ('H', 'L') or not isinstance(origin['time'], str) or not origin['time']
            or not isinstance(origin['label'], str) or not origin['label']):
        return None
    knowns = (origin['available_at'], proof.get('available_at'), point['available_at'])
    indices: list[int] = []
    for known in knowns:
        if not (type(known) is int or isinstance(known, str) and bool(known)):
            return None
        known_index = (int(known) if isinstance(known, str) and known.isdecimal()
                       else dates.get(known))
        if known_index is None or type(known_index) is not int or known_index < 0:
            return None
        indices.append(known_index)
    candidate = {field: origin[field] for field in _ANNOTATION_REFERENCE_FIELDS}
    if (not index <= indices[0] <= indices[1] <= indices[2]
            or proof.get('direction') != ('up' if point['kind'] == 'H' else 'down')
            or not _permission_leg(candidate, point)):
        return None
    return candidate


def _annotate_permissions(
    points: list[dict[str, object]], symbol: str, dates: Mapping[object, int], *, trend_level: int,
) -> None:
    """按合法波段标注策略许可，不能把独立压力点强行拼成一条几何折线。

    发布器可以保留旧压力 H，同时发布从另一真实 H 开始的下跌许可 L，
    两个公开节点之间并不存在下跌价格腿。连续合法段沿用原命名状态；
    不合法连接切断状态，只有完整且当时已知的 incoming 起点可作为本波
    影子上下文。证明不足时从单点开始，不跨断点制造翻向或交替事件。
    影子起点不加入公开许可数组，也不创造新交易资格。

    标注投影仅含坐标和事实日期。原节点、所有证书及自己的可知日保持
    原样；观察用累计上下文日期，嵌套点引用恢复自己的日期。整条路径的
    H/L 编号连续，局部段不会重复命名；清理缺失的派生字段，防止继承的
    preceding_turn 把旧压力伪装成新波起点。源几何仍由严格入口单独校验。
    """
    runs: list[tuple[list[dict[str, object]], dict[str, object] | None]] = []
    labels: dict[tuple[int, int], str] = {}
    counts: Counter[str] = Counter()
    for point in points:
        identity = cast(int, point['index']), cast(int, point['ordinal'])
        kind = cast(str, point['kind'])
        counts[kind] += 1
        labels[identity] = f'{kind}{counts[kind]}'
        if runs and _permission_leg(runs[-1][0][-1], point):
            runs[-1][0].append(point)
        else:
            origin = _permission_origin(point, dates) if runs else None
            runs.append(([point], origin))
    derived: list[tuple[dict[str, object], dict[str, object]]] = []
    context_dates = dict(dates)
    for run, origin in runs:
        contexts = [*([origin] if origin is not None else []), *run]
        # 同一坐标可以有公开几何事实与稍晚成立的本波起点证明。引用属于
        # 本段真实上下文，不能被别段同坐标的早日期覆盖；标签仍用全路径表。
        originals: dict[tuple[int, int], Mapping[str, object]] = {
            (cast(int, point['index']), cast(int, point['ordinal'])): point for point in contexts}
        shadows = [{field: point[field] for field in ('index', 'ordinal', 'time', 'kind', 'value', 'available_at')}
                   for point in contexts]
        for shadow in shadows:
            known = shadow['available_at']
            if isinstance(known, str) and known.isdecimal():
                context_dates[known] = int(known)
        _annotate_geometry(shadows, symbol, context_dates, trend_level=trend_level)
        for point, shadow in zip(run, shadows[1:] if origin is not None else shadows):
            identity = cast(int, point['index']), cast(int, point['ordinal'])
            local_label, label = cast(str, shadow['label']), labels[identity]
            fields = {field: _restore_annotation_references(shadow[field], originals, labels=labels)
                      for field in _GEOMETRY_ANNOTATION_FIELDS if field in shadow}
            fields['label'] = label
            # levels 的名称不是点引用，单独修正本点前缀；转换依据名称不变。
            fields['levels'] = [dict(level, name=label + cast(str, level['name'])[len(local_label):])
                if cast(str, level['name']).startswith(local_label + ' 的') else dict(level)
                for level in cast(Sequence[Mapping[str, object]], fields['levels'])]
            derived.append((point, fields))
    # 所有段校验完毕才写回，绝不把许可证字段或未知扩展字段随 dict delta 带入。
    for point, fields in derived:
        for field in _GEOMETRY_ANNOTATION_FIELDS:
            if field in fields:
                point[field] = fields[field]
            else:
                point.pop(field, None)


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
    completions: dict[tuple[int, int], dict[str, object]] = {}
    for point in mutable_points(candidates):
        # 行情和 N 只核验本级已有波的策略资格，不能授予 pending 方向事实
        # 整波身份，否则资格递归会把同一条下级波重新恢复为上级假波。
        if point.get('wave_identity_state') == 'direction_only':
            continue
        identity = cast(int, point['index']), cast(int, point.get('ordinal', 0))
        if 'wave_completed_at' in point:
            # 只核验、收集真实整波结束事实，不用结束证明替换较早策略候选。
            # 同坐标 earlystrict18 与 lateordinary23 的完整账本是唯一来源。
            _completed_source_point(point)
            previous_completion = completions.get(identity)
            if previous_completion is None or _later_moment(cast(Moment, point['wave_completed_at']),
                    cast(Moment, previous_completion['wave_completed_at'])) == previous_completion['wave_completed_at']:
                completions[identity] = point
        previous = earliest.get(identity)
        if previous is None or (_later_moment(cast(Moment, point['available_at']),
                cast(Moment, previous['available_at'])) == previous['available_at']
                and point['available_at'] != previous['available_at']):
            earliest[identity] = point
    qualified = [earliest[key] for key in sorted(earliest)]
    for point in qualified:
        if 'wave_completion_policy' not in point:
            continue
        # publication 可把 available_at 改为许可证真正可知日27；此前 geometry18
        # 与实际整波结束23必须分别保存，不能拿晚许可证日期反过来否定结构。
        point.setdefault('source_geometry_available_at', point['available_at'])
        completion = completions.get((cast(int, point['index']), cast(int, point.get('ordinal', 0))))
        if completion is not None:
            for field in ('wave_completion_policy', 'wave_completed_at', 'wave_completion_evidence'):
                point[field] = deepcopy(completion[field])
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


def prepare_trend_lines(
    high_low_points: Sequence[Mapping[str, object]], *, trend_level: int = 1, symbol: str = 'STRUCTURE',
) -> dict[str, object]:
    """完成本级全部路径的结构判断，结果可直接供下一级准备阶段读取。

    一级来源是全部基础折线高低点，二、三级只读取已经完成的紧邻下级
    strokes。各路径统一调用 judge_trend；六定义标注、候选账本和显示尾部
    都只读取这些点。symbol 仅标识展示上下文，不提供行情或改变判断规则。
    本函数没有行情或 N 开关，也不生成或核验交易证书。
    调用方可依次准备完整一级、二级、三级，再按同样顺序附加各级资格。

    来源、正式点和候选账本各自拥有独立副本，调用者的嵌套证据不会被
    标注修改。返回的几何数组此后按只读契约复用；资格阶段只复制路径
    外壳，不再次复制全部几何。私有路径截止表保存原始 seed 断点，最后
    一条路径没有截止时用 None 表示开放尾部，绝不依赖尚未传入的行情。
    """
    source_level = _trend_level(trend_level)
    groups = _source_groups(high_low_points)
    strokes: list[dict[str, object]] = []
    developing_strokes: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    source_strokes: list[dict[str, object]] = []
    path_market_end_indices: dict[str, int | None] = {}
    kind, name = _KINDS[trend_level], _NAMES[trend_level]
    paths = list(groups)
    for path_position, (path, raw_source) in enumerate(groups.items()):
        source = mutable_points(raw_source)
        source_strokes.append(dict(id=path, points=source))
        judgement = judge_trend(source, trend_level=trend_level)
        points = mutable_points(judgement.points)
        events = mutable_points(judgement.candidates)
        identifier = f'{kind}-{path}'
        candidates.append(dict(id=identifier, source_path=path, trend_level=trend_level, points=events))
        # 即使后一条路径尚无可升级点，也保留来源携带的原始断点；资格
        # 阶段只能借用断点之前的行情，不能以筛选后路径的首个反转替代 seed。
        next_path = paths[path_position+1] if path_position+1 < len(paths) else None
        cutoff = raw_source[0].get('source_path_end_index',
            groups[next_path][0]['index'] if next_path is not None else None)
        if ((cutoff is None and 'source_path_end_index' in raw_source[0])
                or (cutoff is not None and (type(cutoff) is not int or cutoff < 0))):
            raise ValueError('source_path_end_index must be a nonnegative index')
        path_market_end_indices[path] = cutoff
        if not points:
            continue
        definition_source = [point for point in source if point.get('state') not in ('seed', 'developing')
                             and point.get('display_only') is not True]
        # 股票名只标识公共定义的结构上下文；确认日始终由来源点映射，
        # 不能因为资格阶段的真实行情日历更密而提前或推迟几何事实。
        _annotate_geometry(points, symbol, _definition_dates([*definition_source, *points]), trend_level=trend_level)
        tail = _structural_tail(source, points, path=path, trend_level=trend_level)
        if tail is not None:
            developing_strokes.append(tail)
        strokes.append(dict(id=identifier, source_path=path, source_paths=[path], kind=kind,
            trend_level=trend_level, points=points, input_turn_count=len(source), key_transitions=[],
            confirmation_policy=STRUCTURAL_RULE, leg_confirmation_policy=STRUCTURAL_LEG_POLICY,
            wave_display_policy='structural_points_independent_strategy_permissions_v113',
            structural_legs=_structural_legs(points), confirmed_legs=[]))
    return dict(name=f'{name}趋势线', trend_level=trend_level, source_level=source_level, strokes=strokes,
        source_strokes=source_strokes, qualified_strokes=[],
        candidate_strokes=candidates, structure_strokes=candidates, developing_strokes=developing_strokes,
        n_target_observations=[], input_turn_count=sum(len(points) for points in groups.values()),
        confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in strokes),
        strategy_confirmed_wave_count=0, developing_wave_count=len(developing_strokes),
        developing_point_count=2*len(developing_strokes), key_transition_count=0,
        aggregation_rule=STRUCTURAL_RULE, trend_definition_rule=STRUCTURAL_RULE,
        scope='structural_trend_points_with_independent_strategy_permissions',
        bear_to_bull_highs=[], bear_bull_alternation_lows=[], post_alternation_bull_highs=[], bullish_turn_signals=[],
        break_basis='confirmed_lower_level_high_low_points', retracement_threshold=.67,
        _path_market_end_indices=path_market_end_indices,
        note='统一 judge_trend 的首参数为高低点：一级用折线，二级用一级输出，三级用二级输出；'
             '六定义和严格高低点突破升级只读取这个来源。相等不升级，不等待攻击之后的回调。'
             'K线突破、交替和可选N证书只提供策略资格，不筛掉趋势线结构点；路径断点各级一致保留。')


def _prepared_strokes(prepared: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    """校验准备阶段的路径外壳，借用只读点数组，避免全层深复制。"""
    strokes = prepared.get(field)
    if not isinstance(strokes, Sequence) or isinstance(strokes, (str, bytes)):
        raise ValueError(f'prepared {field} must be a sequence')
    result: list[Mapping[str, object]] = []
    for stroke in strokes:
        if not isinstance(stroke, Mapping) or any(not isinstance(key, str) for key in stroke):
            raise ValueError(f'prepared {field} must contain mappings with string keys')
        result.append(cast(Mapping[str, object], stroke))
    return result


def _prepared_points(stroke: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    points = stroke.get('points')
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or any(
        not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point) for point in points
    ):
        raise ValueError('prepared stroke points must be mappings with string keys')
    return cast(Sequence[Mapping[str, object]], points)


def attach_trend_permissions(
    prepared: Mapping[str, object], bars: Sequence[Bar] = (), *,
    n_target_trend_confirmation_enabled: bool = False,
    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """只给已完成的本级结构附加资格和命名事件，不再判断或归并几何。

    prepared 必须来自 prepare_trend_lines。全部结构路径完成后才调用本函数；
    可重复用不同 N 模式或行情前缀核验同一份几何，结果不会改写 prepared。
    资格按原路径截止重新核验，紧邻下级许可仅是独立核验的依据，不直接
    授予上级交易资格。没有行情或尚未可知的压力点不能进入策略视图。

    返回值复制顶层、路径外壳及资格数组，几何点、候选和来源数组只读共享。
    每条路径需要修改的许可点由资格发布器独立复制，避免为三级历史反复
    深复制全部几何证据。调用者不得通过返回的几何数组修改准备阶段结果；
    需要可变导出时应在导出边界另行复制。私有截止表不进入公开结果。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    raw_level = prepared.get('trend_level')
    if type(raw_level) is not int:
        raise ValueError('prepared trend_level must be 1, 2 or 3')
    trend_level = raw_level
    _trend_level(trend_level)
    raw_cutoffs = prepared.get('_path_market_end_indices')
    if not isinstance(raw_cutoffs, Mapping) or any(
        not isinstance(path, str) or (cutoff is not None and (type(cutoff) is not int or cutoff < 0))
        for path, cutoff in raw_cutoffs.items()
    ):
        raise ValueError('prepared path cutoffs must contain nonnegative indices or None')
    cutoffs = cast(Mapping[str, int | None], raw_cutoffs)
    source_strokes = _prepared_strokes(prepared, 'source_strokes')
    candidate_strokes = _prepared_strokes(prepared, 'candidate_strokes')
    geometry_strokes = _prepared_strokes(prepared, 'strokes')
    developing_strokes = _prepared_strokes(prepared, 'developing_strokes')
    candidates_by_path = {stroke['source_path']: _prepared_points(stroke) for stroke in candidate_strokes}
    qualified_groups = _source_groups(qualified_source_points) if qualified_source_points is not None else {}
    qualified_strokes: list[dict[str, object]] = []
    legs_by_path: dict[str, list[dict[str, object]]] = {}
    n_targets: list[dict[str, object]] = []
    kind = _KINDS[trend_level]
    for source_stroke in source_strokes:
        path = source_stroke.get('id')
        if not isinstance(path, str) or path not in cutoffs or path not in candidates_by_path:
            raise ValueError('prepared source paths require matching candidates and cutoffs')
        source = _prepared_points(source_stroke)
        cutoff = cutoffs[path]
        # 行情索引仍从原股票的第零根起算，只截断尾部；不能把路径子段
        # 切成从零开始的新市场，也不能把后一路径的突破借给前一路径。
        market = bars if cutoff is None else bars[:cutoff]
        permission_source = [dict(point, label=point.get('label', point.get('kind', ''))) for point in source]
        permissions = qualify_trend_structure(candidates_by_path[path], permission_source, market,
            trend_level=trend_level,
            qualified_source=qualified_groups.get(path, []) if qualified_source_points is not None else None,
            n_target_sink=n_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
        if permissions and market:
            # 命名资格附件需要真实行情日历；它们只写入本次许可副本，
            # 不倒写纯结构阶段保留的首次可知日期、坐标或来源证据。
            permission_dates: dict[object, int] = {session_date(bar): i for i, bar in enumerate(market)}
            permission_dates.update({i: i for i in range(len(market))})
            _annotate_permissions(permissions, market[0].symbol, permission_dates, trend_level=trend_level)
        legs = confirmed_trend_legs(permissions, trend_level=trend_level, source_path=path)
        legs_by_path[path] = legs
        if permissions:
            from .secondary_trend import last_fall_high_reanchors

            reanchors = cast(Callable[..., list[dict[str, object]]], last_fall_high_reanchors)
            # 旧命名事件只支持交易所日期；整数历史维持整数时钟，不为
            # 展示附件转换或补造任何资格／几何点的确认日。
            transitions = reanchors(permissions, market, trend_level=trend_level) if all(
                isinstance(point['available_at'], str) and not point['available_at'].isdecimal()
                for point in permissions) else []
            qualified_strokes.append(dict(id=f'{kind}-{path}', source_path=path, trend_level=trend_level,
                kind=kind, points=permissions, confirmed_legs=legs, key_transitions=transitions,
                view='strategy_permissions'))
    source_shells = [dict(stroke) for stroke in source_strokes]
    candidate_shells = [dict(stroke) for stroke in candidate_strokes]
    strokes = [dict(stroke, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                    confirmed_legs=legs_by_path[cast(str, stroke['source_path'])]) for stroke in geometry_strokes]
    # 这些命名事件只读取本次资格视图；其产物永不进入下一层高低点。
    highs = cast(Callable[..., object], bear_to_bull_highs)
    lows = cast(Callable[..., object], bear_bull_alternation_lows)
    post_highs = cast(Callable[..., object], post_alternation_bull_highs)
    signals = cast(Callable[..., object], bullish_turn_signals)
    result = dict(prepared)
    result.pop('_path_market_end_indices')
    result.update(strokes=strokes, source_strokes=source_shells, qualified_strokes=qualified_strokes,
        candidate_strokes=candidate_shells, structure_strokes=candidate_shells,
        developing_strokes=[dict(stroke) for stroke in developing_strokes],
        n_target_observations=n_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
        strategy_confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in qualified_strokes),
        key_transition_count=sum(len(cast(list[object], stroke['key_transitions'])) for stroke in qualified_strokes),
        bear_to_bull_highs=highs(qualified_strokes, trend_level=trend_level),
        bear_bull_alternation_lows=lows(qualified_strokes, trend_level=trend_level, source_strokes=source_shells, bars=bars),
        post_alternation_bull_highs=post_highs(qualified_strokes, trend_level=trend_level,
            source_strokes=source_shells, bars=bars),
        bullish_turn_signals=signals(qualified_strokes, bars, trend_level=trend_level, source_strokes=source_shells))
    return result


def trend_lines(
    high_low_points: Sequence[Mapping[str, object]], bars: Sequence[Bar] = (), *, trend_level: int = 1,
    n_target_trend_confirmation_enabled: bool = False,
    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """兼容单层入口：先完成所有路径结构，再单独附加行情资格。

    原签名和公开结果字段保持不变。需要股票级完整阶段顺序的入口应直接
    依次调用三层 prepare_trend_lines，再依次调用 attach_trend_permissions；
    下一层始终消费 strokes，qualified_strokes 只用于独立资格核验。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    prepared = prepare_trend_lines(high_low_points, trend_level=trend_level,
                                   symbol=bars[0].symbol if bars else 'STRUCTURE')
    return attach_trend_permissions(prepared, bars,
        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
        qualified_source_points=qualified_source_points)
