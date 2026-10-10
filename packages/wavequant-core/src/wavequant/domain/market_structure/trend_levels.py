"""用同一高低点定义递归观察一级、二级和三级趋势。

一级来源为已确认折线反转；二级来源为已发布一级点；三级来源为已发布二级点。
正／负反转描述局部折线转向，多／空趋势要求两类同向严格抬高或降低。
本模块产生整波趋势方向证据；突破已知本级高低点的立即升级是独立通道，
普通方向变化不能冒充翻空为多、翻多为空或其他严格关键位突破事件。
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from math import isfinite
from typing import Literal, TypeAlias

from .polyline import LinePoint, PointKind, ReversalPoint
from .trend_structure import StructuralTrend, classify_trend_prices, preceding_turn

Moment: TypeAlias = int | str
WaveDirection: TypeAlias = Literal['up', 'down']

__all__ = ['trend_reversals']


@dataclass(frozen=True)
class _SourceTurn:
    """保留价格坐标、原始来源位置和首次可知时间，不把绘图日期当确认日期。"""
    source: Mapping[str, object]
    position: int
    index: int
    ordinal: int
    kind: Literal['H', 'L']
    value: float
    time: str
    available_at: Moment
    reversal: ReversalPoint


def _source_turns(points: Sequence[Mapping[str, object]], source_level: int) -> list[_SourceTurn]:
    """校验直接下级来源并排除未完尾端，原始位置包含被排除点以保持来源可追溯。"""
    turns: list[_SourceTurn] = []
    clock: Literal['index', 'numeric_string', 'date_string'] | None = None
    previous_order: tuple[int, int] | None = None
    for position, raw in enumerate(points):
        if not isinstance(raw, Mapping):
            raise ValueError('trend source points must be mappings')
        level = raw.get('trend_level')
        if 'trend_level' in raw and (type(level) is not int or level != source_level):
            raise ValueError('trend source points must belong to the immediately lower level')
        if raw.get('state') in ('seed', 'developing') or raw.get('display_only') is True:
            continue
        index, ordinal = raw.get('index'), raw.get('ordinal', 0)
        kind, price = raw.get('kind'), raw.get('value')
        time, available = raw.get('time'), raw.get('available_at')
        if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
                or kind not in ('H', 'L') or not isinstance(price, (int, float)) or isinstance(price, bool)
                or not isfinite(price) or price <= 0 or not isinstance(time, str) or not time):
            raise ValueError('ordered finite high/low source points required')
        order = index, ordinal
        if previous_order is not None and order <= previous_order:
            raise ValueError('strictly ordered source points required')
        previous_order = order
        if type(available) is int:
            if available < index:
                raise ValueError('source confirmation cannot precede its price index')
            current_clock: Literal['index', 'numeric_string', 'date_string'] = 'index'
            moment: Moment = available
        elif isinstance(available, str) and available:
            numeric_clock = available.isdecimal() and time.isdecimal()
            if (int(available) < int(time) if numeric_clock else available < time):
                raise ValueError('source confirmation cannot precede its price date')
            current_clock = 'numeric_string' if numeric_clock else 'date_string'
            moment = available
        else:
            raise ValueError('source points require a confirmation index or date')
        if clock is not None and current_clock != clock:
            raise ValueError('source confirmation clocks must match')
        clock = current_clock
        # 公共 preceding_turn 只需要相对位置；内部适配器不伪造确认知识。
        # 真实来源确认时间单独保存，输出时覆盖所有已消费的历史证据。
        reversal = ReversalPoint(
            LinePoint(index, ordinal, PointKind(kind), float(price)), index,
            f'level{source_level}_source_position_{position}',
        )
        turns.append(_SourceTurn(raw, position, index, ordinal, 'H' if kind == 'H' else 'L',
                                 float(price), time, moment, reversal))
    return turns


def _reference(turn: _SourceTurn) -> dict[str, object]:
    """复制可审计的真实来源坐标，缺省 ordinal 不改变同一 K 线上的排序。"""
    return dict(index=turn.index, ordinal=turn.ordinal, kind=turn.kind, value=turn.source['value'],
                time=turn.time, available_at=turn.available_at,
                label=turn.source.get('label', turn.kind))


def _strip_source_license(source: Mapping[str, object]) -> dict[str, object]:
    """新建上级点只继承真实来源几何，许可证与等级必须由本级重新取得。

    普通反转、严格关键位升级和可选N新点共享同一复制边界。下级incoming
    上涨证书尤其不能被上级H当成本级入射腿证据。source_confirmation仅保存
    下级为何已存在的审计引用，不参与本级授权；同坐标已存在的本级结构点
    不走此清理函数，其自身更早证书需在追加独立N证据时继续保留。
    """
    result = dict(source)
    for field in (
        'flip', 'trend_level', 'source_level', 'source_turn_position', 'confirmed_on_turn', 'source_reversal_available_at',
        'source_level1_available_at', 'source_level1_position', 'confirmed_on_level1',
        'source_level2_available_at', 'source_level2_position', 'confirmed_on_level2',
        'incoming_trend_confirmation', 'incoming_trend_state', 'active_trend_confirmation',
        'trend_confirmation', 'trend_confirmation_route', 'n_target_confirmation',
        'incoming_leg_confirmation', 'wave_id', 'confirmed_legs', 'leg_confirmation_policy',
        'wave_display_policy', 'confirmation_policy', 'n_target_trend_confirmation_enabled',
        'structural_confirmation_rule', 'definition_evidence', 'direction_definition', 'upgrade_evidence',
    ):
        result.pop(field, None)
    # 来源的确认链只用于说明这个下级点为何已存在，不能供本级 publisher 授权。
    result['source_confirmation'] = deepcopy(source.get('confirmed_by'))
    return result


def _candidate_from_source(turn: _SourceTurn) -> dict[str, object]:
    """复制真实来源，移除下级许可证；上级必须由本级门禁重新取得发布证书。"""
    result = _strip_source_license(turn.source)
    result.update(_reference(turn))
    return result


def _later_moment(left: Moment, right: Moment) -> Moment:
    if isinstance(left, str) and isinstance(right, str):
        # 既有几何用例用字符串表示 K 线索引，数值 10 必须晚于 9。
        # 保留原时钟表示，日期字符串则按正常日期顺序比较。
        return max(left, right, key=int) if left.isdecimal() else max(left, right)
    if type(left) is int and type(right) is int:
        return max(left, right)
    raise ValueError('source confirmation clocks must match')


def trend_reversals(
    points: Sequence[Mapping[str, object]], *, trend_level: int,
) -> list[dict[str, object]]:
    """按高点、低点共同严格升降的定义提取任一级的整波极值。

    多头方向由高点与低点同时严格抬高确认，空头方向完全镜像。上涨整波
    结束时必须同时有更低低点和更低高点；下跌整波则必须同时有更高
    高点和更高低点。两类证据不限定先后：HL→HH 与 HH→HL 都能确认多头，
    LH→LL 与 LL→LH 都能确认空头。来源自身必须已经反转确认，单个局部
    正／负反转、相等极值或高低不同向仍不足以改变本级整波方向。

    初始方向只使用最近两类点，不从方向确立前的历史导入极值；方向成立
    后持续保存整波最高 H／最低 L。价格坐标保留原日期，知识日期覆盖
    已消耗来源前缀，旧方向、旧关键位等证据滚出最近四点后仍不能被遗忘。
    普通候选的 broken_key 仅指极值左侧最近反转，不代表价格已经突破它；
    direction_definition 记录多空定义，严格突破升级由独立模块证明。

    未确认尾端和纯显示点不参与任何层级；同日多点由 ordinal 排序。
    输入必须是调用方已发布的直接下级前缀，原 source_position 不因跳过
    未完点而改变。非法等级、来源、价格、排序或混合时钟抛出 ValueError。
    输出只是结构事实，既有 publication gate 继续提供方向和交易资格。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise ValueError('trend source points must be a sequence of mappings')
    source_level = trend_level - 1
    turns = _source_turns(points, source_level)
    highs: list[int] = []
    lows: list[int] = []
    direction: WaveDirection | None = None
    candidate: int | None = None
    selected: list[dict[str, object]] = []
    prefix_known: Moment | None = None
    source_positions = {turn.reversal: turn for turn in turns}
    preceding_points: dict[ReversalPoint, ReversalPoint | None] = {}
    latest_by_kind: dict[str, _SourceTurn] = {}
    for turn in turns:
        opposite = latest_by_kind.get('L' if turn.kind == 'H' else 'H')
        # 顺序扫描缓存最近异类点，二元输入与完整左侧前缀的末跌高／末升低
        # 定义等价；仍由公共 preceding_turn 定位，避免每个整波重复扫描历史。
        neighbors = (turn.reversal,) if opposite is None else (opposite.reversal, turn.reversal)
        preceding_points[turn.reversal] = preceding_turn(neighbors, turn.reversal)
        latest_by_kind[turn.kind] = turn
    for position, point in enumerate(turns):
        prefix_known = point.available_at if prefix_known is None else _later_moment(prefix_known, point.available_at)
        (highs if point.kind == 'H' else lows).append(position)
        current_trend = classify_trend_prices(
            [turns[index].value for index in highs[-2:]],
            [turns[index].value for index in lows[-2:]],
            trend_level=trend_level,
        )
        current: WaveDirection | None = (
            'up' if current_trend == StructuralTrend.BULL else
            'down' if current_trend == StructuralTrend.BEAR else None
        )
        if direction is None:
            if current is None:
                continue
            direction = current
            # 方向确立前的历史不属于新整波，避免导入较早但无本波证据的极值。
            candidate = (highs if direction == 'up' else lows)[-1]
            continue
        assert candidate is not None
        target = 'H' if direction == 'up' else 'L'
        sign = 1 if direction == 'up' else -1
        if point.kind == target and sign * (point.value - turns[candidate].value) > 0:
            candidate = position
        # 方向只由两类已确认极值共同决定。旧代码还要求最后证据与旧整波
        # 极值同类，导致先抬低、后创新高的多头要等待另一低点才被接纳。
        # point 已通过来源可知校验；无论最后补齐的是 H 还是 L，都在真实
        # 可知日确认，既不等待未来 holding 点，也不把单边突破冒充趋势。
        if current is None or current == direction:
            continue
        extreme = turns[candidate]
        evidence = [turns[index] for index in [*highs[-2:], *lows[-2:]]]
        preceding = preceding_points[extreme.reversal]
        key = _reference(source_positions[preceding]) if preceding is not None else None
        proof = [_reference(turn) for turn in evidence]
        result = _candidate_from_source(extreme)
        # confirmation_rule 的历史协议名称保留，避免改动证据消费者；名称
        # 中的 then 不再是额外门槛，真实充分条件和确认日由四个来源点证明。
        result.update(trend_level=trend_level, source_level=source_level, available_at=prefix_known,
                      wave_direction_before=direction, wave_direction_after=current,
                      direction_definition=current_trend.value, broken_key=key,
                      confirmation_rule='ordered_HH_then_HL_or_LL_then_LH_switch',
                      definition_evidence=proof)
        if source_level == 0:
            result.update(source_reversal_available_at=extreme.available_at,
                          source_turn_position=extreme.position,
                          confirmed_on_turn=point.position, confirmed_by=proof)
        else:
            result.update(source_label=extreme.source.get('label', extreme.kind),
                          confirmed_by=_reference(point),
                          **{f'source_level{source_level}_available_at': extreme.available_at,
                             f'source_level{source_level}_position': extreme.position,
                             f'confirmed_on_level{source_level}': point.position})
        selected.append(result)
        direction = current
        pool = [index for index in range(candidate + 1, position + 1)
                if turns[index].kind == ('H' if direction == 'up' else 'L')]
        candidate = (max if direction == 'up' else min)(pool, key=lambda index: turns[index].value)
    return selected
