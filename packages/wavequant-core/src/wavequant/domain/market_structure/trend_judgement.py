"""以直接下级高低点唯一决定本级结构，交易许可证不参与趋势几何。

一、二、三级共用同一过程：普通有序高低点定义、严格本级高低点突破升级，
再将当时已知的结构事件归并为交替高低点。下一层直接消费 ``points``；
``candidates`` 保存没有进入当前折线的真实结构事件，供审计和独立资格层
使用。本模块不读取 K 线、不计算 N、不调用 publisher，也不借交易证书
删改结构点。市场证书的方向、日期和资格由调用方另行计算。
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from .trend_level_upgrades import promote_trend_key_breaks
from .trend_levels import Moment, _later_moment, _source_turns, trend_reversals

__all__ = ['TrendJudgement', 'judge_trend', 'mutable_points']


@dataclass(frozen=True, slots=True)
class TrendJudgement:
    """一个层级的只读结构快照，价格坐标与首次可知日分别保留。

    ``points`` 是当前交替几何，可原样作为下一次 ``judge_trend`` 的首参数。
    ``candidates`` 是本快照普通归并及严格升级的完整候选并集；同向端点被
    更高 H／更低 L 延伸后，原事件仍在这里保留。两个元组的字典及嵌套证据
    容器递归冻结，调用方需要 JSON 或可变工作副本时使用 ``mutable_points``。
    """

    trend_level: int
    source_level: int
    points: tuple[Mapping[str, object], ...]
    candidates: tuple[Mapping[str, object], ...]


def _mutable_value(value: object) -> object:
    """解冻容器并复制其他审计值，避免与输入或不可变快照共用可变引用。"""
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {deepcopy(key): _mutable_value(item) for key, item in source.items()}
    if isinstance(value, (tuple, list)):
        return [_mutable_value(item) for item in cast(Sequence[object], value)]
    if isinstance(value, (set, frozenset)):
        # 集合中的tuple是合法审计键，不能按普通证据序列解成不可哈希list。
        return {deepcopy(item) for item in cast(set[object] | frozenset[object], value)}
    return deepcopy(value)


def mutable_points(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """返回可修改、可供既有字典消费者使用的独立点副本。

    MappingProxyType 不能直接被 deepcopy 或 JSON 编码；本函数在这些边界
    递归转回字典／列表，不改变原快照，也不授予任何市场或跨级许可证。
    """
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise ValueError('trend points must be a sequence of mappings')
    result: list[dict[str, object]] = []
    for point in points:
        if not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point):
            raise ValueError('trend points must be mappings with string keys')
        result.append({key: _mutable_value(value) for key, value in point.items()})
    return result


def _frozen_value(value: object) -> object:
    """将字典、序列与集合证据容器变为只读快照。"""
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return MappingProxyType({deepcopy(key): _frozen_value(item) for key, item in source.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_frozen_value(item) for item in cast(Sequence[object], value))
    if isinstance(value, (set, frozenset)):
        return frozenset(_frozen_value(item) for item in cast(set[object] | frozenset[object], value))
    return deepcopy(value)


def _frozen_point(point: Mapping[str, object]) -> Mapping[str, object]:
    return cast(Mapping[str, object], _frozen_value(point))


def _moment_key(moment: Moment) -> tuple[int, int | str]:
    """同一时钟内比较知识顺序，数字字符串的 10 必须晚于 9。"""
    if type(moment) is int and moment >= 0:
        return 0, moment
    if isinstance(moment, str) and moment:
        return (1, int(moment)) if moment.isdecimal() else (2, moment)
    raise ValueError('asof must be a nonnegative confirmation index or nonempty date string')


def _known(point: Mapping[str, object]) -> Moment:
    moment = point['available_at']
    if type(moment) is int or isinstance(moment, str):
        return moment
    raise ValueError('a structural point requires a known confirmation session')


def _order(point: Mapping[str, object]) -> tuple[int, int]:
    return cast(int, point['index']), cast(int, point.get('ordinal', 0))


def _visible_source(
    points: Sequence[Mapping[str, object]], source_level: int, asof: Moment | None,
) -> tuple[list[dict[str, object]], tuple[int, ...]]:
    """先建立可知来源前缀；类型校验不属于高低点趋势判断。

    可知时间可能晚于价格位置且并非单调，因此不能截取价格位置的简单前缀。
    未完／显示点由共同来源校验排除，其余点按自身真实可知日过滤之后，
    才允许普通定义或严格升级读取。原首参数位置单独保存，迟知点被过滤
    不会令输出 source_position 指向别的来源。
    """
    working = mutable_points(points)
    turns = _source_turns(working, source_level)
    cutoff = _moment_key(asof) if asof is not None else None
    visible: list[dict[str, object]] = []
    positions: list[int] = []
    for turn in turns:
        known = _moment_key(turn.available_at)
        if cutoff is not None:
            if known[0] != cutoff[0]:
                raise ValueError('asof and source confirmation clocks must match')
            if asof is not None and _later_moment(turn.available_at, asof) != asof:
                continue
        visible.append(dict(turn.source))
        positions.append(turn.position)
    return visible, tuple(positions)


def _restore_source_positions(
    candidates: Sequence[Mapping[str, object]], positions: Sequence[int], source_level: int,
) -> list[dict[str, object]]:
    """公共归并器在可知子序列上运算，公开来源引用仍指向调用方原序列。"""
    fields = (('source_turn_position', 'confirmed_on_turn') if source_level == 0 else
              (f'source_level{source_level}_position', f'confirmed_on_level{source_level}'))
    restored = mutable_points(candidates)
    for point in restored:
        for field in fields:
            position = point.get(field)
            if type(position) is int:
                point[field] = positions[position]
    return restored


def _event_key(point: Mapping[str, object]) -> tuple[tuple[int, int], str, str, str | None, str | None, tuple[int, int | str], int | None]:
    """坐标相同不代表同一结构事实；方向、定义及知识日均是证据身份。"""
    trigger = next((value for field in ('confirmed_on_turn', 'confirmed_on_level1', 'confirmed_on_level2')
                    if type(value := point.get(field)) is int), None)
    return (_order(point), cast(str, point['kind']), cast(str, point.get('confirmation_rule', '')),
            cast(str | None, point.get('wave_direction_before')),
            cast(str | None, point.get('wave_direction_after')), _moment_key(_known(point)), trigger)


def _complete_candidates(
    ordinary: Sequence[Mapping[str, object]], upgraded: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """保留普通整波定义与更早严格升级的各自证据，几何另行归并。

    旧升级器按坐标保留最早事实，以供沿用同一关键位推进升级状态。该坐标
    后来可能又被普通HH→HL／LL→LH定义确认成反向整波起点；不能因此丢掉
    后知证据，也不能把它倒写到较早单向升级端点。审计候选保留两份事实，
    points仍仅取该坐标最早可知的几何节点。线性去重后只排序一次。
    """
    result: list[dict[str, object]] = []
    identities: set[tuple[tuple[int, int], str, str, str | None, str | None, tuple[int, int | str], int | None]] = set()
    for events in (upgraded, ordinary):
        for point in events:
            identity = _event_key(point)
            if identity not in identities:
                result.extend(mutable_points((point,)))
                identities.add(identity)
    return sorted(result, key=lambda point: (_order(point), _moment_key(_known(point)), _event_key(point)[2]))


def _canonical_points(candidates: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """将可知结构事件归并为同一条交替折线，不读取任何许可证。

    先按知识日期纳入候选，再按真实价格位置归并；未知事件没有机会参与
    极值选择。同类 H 保留最高点、L 保留最低点；相等时坐标最早者保留。
    替换端点只复制新端点自己的 available_at，绝不把同组更早的知识日
    移给后来才知的更大／更小极值。原端点及严格突破证明仍留在 candidates。
    """
    known_events = sorted(candidates, key=lambda point: (_moment_key(_known(point)), _order(point)))
    # 每个已知事件仅入库一次；随后一次价格排序与线性归并，避免逐事件重放
    # 整个前缀。来源和候选已通过公共校验，同坐标的较晚证据不能倒写早事实。
    by_order: dict[tuple[int, int], dict[str, object]] = {}
    for point in known_events:
        by_order.setdefault(_order(point), dict(point))
    result: list[dict[str, object]] = []
    for point in sorted(by_order.values(), key=_order):
        if result and point['kind'] == result[-1]['kind']:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) > 0:
                result[-1] = point
            continue
        if result:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) <= 0:
                raise ValueError('structural candidates require nonzero alternating high/low legs')
        result.append(point)
    return result


def judge_trend(
    high_low_points: Sequence[Mapping[str, object]], *, trend_level: int = 1, asof: Moment | None = None,
) -> TrendJudgement:
    """只根据直接下级高低点判断一个层级，并返回可直接递归的结构点。

    一级读取折线高低点，二级读取一级 points，三级读取二级 points；级别
    只校验来源与填写元数据，不选择另一套价格公式。普通方向要求 HH 与 HL／
    LL 与 LH 都已可知，两类证据不限定先后；严格已知本级 H/L 突破升级仍
    共用同一底层实现。严格升级
    仍可同时确认真实整波起点与单向端点，不等待未来 holding 点；上涨
    端点的 up→up 或下降端点的 down→down 证据不被改成反向整波许可。

    asof 必须在源点判断之前过滤知识日期：完整输入中的未来点不能先
    改变归并结果，再靠末端过滤隐藏。发生坐标与可知日期都保留，原来源
    位置不会因为筛选或跳过未完点而改变。下级交易证书由共享复制边界
    清除，只保存 source_confirmation 审计引用；K 线和 N 证书不参与。

    没有普通闭合证据或严格升级证据时返回空结构，不新增闭合门槛，也
    不制造缺失尾端。纯显示发展线和独立策略资格由外部通用流程负责。
    输入不会被修改，结果及嵌套证据容器只读；mutable_points 可取得副本。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    if asof is not None:
        _moment_key(asof)
    source_level = trend_level - 1
    source, positions = _visible_source(high_low_points, source_level, asof)
    ordinary = trend_reversals(source, trend_level=trend_level)
    upgrade_events: list[dict[str, object]] = []
    merged = promote_trend_key_breaks(source, ordinary, trend_level=trend_level, event_sink=upgrade_events)
    candidates = _restore_source_positions(_complete_candidates(ordinary, [*merged, *upgrade_events]), positions, source_level)
    points = _canonical_points(candidates)
    # 本级交替点具有统一递归表示：初级输入可能叫confirmed或teaching，
    # 但输出已经由本级定义判定，不能让presentation改state/label后再递归
    # 改变下一层的完整来源引用。候选仍保存原来源状态、标签及独立证明。
    counts = {'H': 0, 'L': 0}
    for point in points:
        kind = cast(str, point['kind'])
        counts[kind] += 1
        point.update(state='reversal', label=f'{kind}{counts[kind]}')
    return TrendJudgement(trend_level, source_level,
                          tuple(_frozen_point(point) for point in points),
                          tuple(_frozen_point(point) for point in candidates))
