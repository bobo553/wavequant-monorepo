"""Named trend definitions over the existing confirmed-polyline evidence.

These primitives report structural observations, not buy or sell instructions.
Polyline construction, attack policy and transition detection remain in their
existing observers; in particular, the caller still chooses ``AttackBasis``
explicitly when obtaining a ``TrendTransition``.

六项定义共用同一套高低点语义，只改变来源：trend_level=1 使用折线，2 使用一级趋势，
3 使用二级趋势。带 context 的入口拒绝跨级重用，单点入口由调用方保证点来自紧邻下级。
这里只陈述已经可知的结构事实；单个正/负反转并不直接表示多空趋势方向已转换。
"""

from dataclasses import dataclass

from .polyline import PointKind, ReversalPoint
from .price_action import AttackEvidence, Direction, KeyLevel, LevelKind, _index
from .trend_structure import (
    RetracementEvidence,
    StructuralTrend,
    StructureContext,
    TransitionStage,
    TrendTransition,
    _trend_level,
    preceding_turn,
)

__all__ = [
    'positive_reversal', 'negative_reversal', 'last_fall_high', 'last_rise_low',
    'is_bull_trend', 'is_bear_trend', 'shallow_countermove', 'weak_countermove',
    'TrendDefinitionSignals', 'observe_trend_definition_signals',
]


def positive_reversal(point: ReversalPoint, *, asof_index: int, trend_level: int = 1) -> bool:
    """正反转: a confirmed low, observable by the requested knowledge date.

    A low drawn before ``asof_index`` is still unavailable until its confirmation
    date. The point is from level ``trend_level - 1``; its kind and confirmation
    have the same meaning at every level. Invalid level/point/index raises ValueError.

    正反转对应下级路径由跌转涨的低点 L。一级读取折线低点，二级读取一级趋势低点，
    三级读取二级趋势低点，三者均要求 kind=LOW 且 confirmed_index <= asof_index。
    point.index 是低点发生的位置，confirmed_index 才是反转首次可知的位置；不能因为
    图上已经画出该低点，就在确认前返回 True。高点或尚未确认的低点返回 False。
    这一局部转向保留自身事实，不替代多头趋势所需的连续抬高高点与低点证据。
    """

    _trend_level(trend_level)
    _index(asof_index, 'asof_index')
    if not isinstance(point, ReversalPoint):
        raise ValueError('ReversalPoint required')
    return point.point.kind == PointKind.LOW and point.confirmed_index <= asof_index


def negative_reversal(point: ReversalPoint, *, asof_index: int, trend_level: int = 1) -> bool:
    """负反转: a confirmed high, observable by the requested knowledge date.

    The point is from level ``trend_level - 1``. Invalid level/point/index raises
    ValueError; an unconfirmed high is false even when its drawing date is visible.

    负反转对应下级路径由涨转跌的高点 H。trend_level=1/2/3 分别读取折线/一级/二级
    趋势的高点，统一要求 kind=HIGH 且 confirmed_index <= asof_index。高点发生日
    与确认日分开保存：发生在较早 K 线上的高点，直到反转确认才可用于历史判断。
    低点或确认日仍在观察时点之后的高点返回 False；负反转本身不等于空头趋势已成立。
    """

    _trend_level(trend_level)
    _index(asof_index, 'asof_index')
    if not isinstance(point, ReversalPoint):
        raise ValueError('ReversalPoint required')
    return point.point.kind == PointKind.HIGH and point.confirmed_index <= asof_index


def _require_context(context: StructureContext, *, trend_level: int) -> None:
    _trend_level(trend_level)
    if not isinstance(context, StructureContext):
        raise ValueError('StructureContext from observe_structure required')
    _trend_level(context.trend_level, context.source_level)
    if context.trend_level != trend_level:
        raise ValueError('context requires matching trend_level')


def _selected_extreme_key(
    context: StructureContext,
    target: ReversalPoint,
    *,
    target_kind: PointKind,
    level_kind: LevelKind,
    definition: str,
) -> KeyLevel | None:
    if not isinstance(target, ReversalPoint) or target.point.kind != target_kind:
        raise ValueError(f'{target_kind.value} ReversalPoint required')
    if target.confirmed_index > context.asof_index:
        raise ValueError('target must already be confirmed at context.asof_index')
    if target not in context.points:
        raise ValueError('target must belong to context.points')
    known = tuple(point for point in context.points if point.confirmed_index <= context.asof_index)
    preceding = preceding_turn(known, target)
    if preceding is None:
        return None
    return KeyLevel(
        context.symbol,
        context.timeframe,
        level_kind,
        preceding.point.price,
        preceding.point.index,
        context.asof_index,
        f'{definition}_window_{context.window_start}_through_{context.asof_index}'
        f'_preceding_{target_kind.value}_{target.point.index}_{target.point.ordinal}'
        f'_from_{preceding.source}',
    )


def last_fall_high(
    context: StructureContext, low: ReversalPoint | None = None, *, trend_level: int = 1,
) -> KeyLevel | None:
    """末跌高: the nearest confirmed negative reversal to a selected low's left.

    With no selection, return the existing key of the context's window minimum.
    A selected low must belong to this observed context and already be confirmed;
    otherwise raise ``ValueError``. Missing left context returns ``None``. The
    key keeps the context's symbol, timeframe and observation date. The context
    must have the requested level and its immediate lower source level.

    默认以窗口最低低点为目标 L，也可通过 low 明确选定其他已确认低点。末跌高是
    目标 L 左侧最近的已确认负反转 H，即左侧高点中时间/序号最大的一个；不按价格
    选左侧最高点，也不使用 L 右侧后来出现的高点。图008的 L0 对应 H1，L7 对应 H6。
    一级/二级/三级分别在折线/一级趋势/二级趋势中应用同一查找。目标必须属于该窗口
    且已经确认，否则抛 ValueError；窗口缺少目标或它左侧没有高点时返回 None。
    """

    _require_context(context, trend_level=trend_level)
    if low is None:
        return context.last_fall_high
    return _selected_extreme_key(
        context,
        low,
        target_kind=PointKind.LOW,
        level_kind=LevelKind.RESISTANCE,
        definition='last_fall_high',
    )


def last_rise_low(
    context: StructureContext, high: ReversalPoint | None = None, *, trend_level: int = 1,
) -> KeyLevel | None:
    """末升低: the nearest confirmed positive reversal to a selected high's left.

    With no selection, return the existing key of the context's window maximum.
    Invalid kind, membership or confirmation date raises ``ValueError``; missing
    left context returns ``None``. Explicit selections preserve key provenance.
    The context must have the requested level and immediate lower source level.

    默认以窗口最高高点为目标 H，high 可指定另一个已确认高点。末升低是 H 左侧
    最近的已确认正反转 L，即左侧低点中时间/序号最大的一个。寻找的是最近相邻
    转折职责，不是左侧价格最低的点，也不能借用右侧低点。图008的 H0 对应 L4，
    H4 对应 L3。一级/二级/三级分别从折线/一级趋势/二级趋势取证，级别必须匹配
    context；目标不属于窗口或尚未确认抛 ValueError，缺少左侧低点返回 None。
    """

    _require_context(context, trend_level=trend_level)
    if high is None:
        return context.last_rise_low
    return _selected_extreme_key(
        context,
        high,
        target_kind=PointKind.HIGH,
        level_kind=LevelKind.SUPPORT,
        definition='last_rise_low',
    )


def is_bull_trend(context: StructureContext, *, trend_level: int = 1) -> bool | None:
    """多头趋势: every successive confirmed high and low rises in the window.

    Insufficient confirmed highs or lows return ``None``. Equality or any mixed
    pair returns false, even if the latest two highs and lows both rise. The
    context must match the requested level and immediate lower source level.

    多头趋势要求窗口内每一对相邻同类高点满足 H[i] > H[i-1]，每一对相邻同类低点
    满足 L[j] > L[j-1]；两类点各至少两个才能比较。任一对相等或向下都返回 False，
    高点或低点不足则返回 None，表达证据不足而非已证明非多头。本入口读取全窗
    window_trend，不能用局部最近两高两低的 trend 替代“每一个”的定义。
    trend_level=1/2/3 的来源依次是折线/一级趋势/二级趋势，价格比较公式完全相同。
    """

    _require_context(context, trend_level=trend_level)
    if context.window_trend == StructuralTrend.UNKNOWN:
        return None
    return context.window_trend == StructuralTrend.BULL


def is_bear_trend(context: StructureContext, *, trend_level: int = 1) -> bool | None:
    """空头趋势: every successive confirmed high and low falls in the window.

    Insufficient confirmed highs or lows return ``None``. Equality or any mixed
    pair returns false; the observer's local two-pair trend is not substituted.
    The context must match the requested level and immediate lower source level.

    空头趋势要求窗口内所有相邻同类高点 H[i] < H[i-1]，所有相邻同类低点
    L[j] < L[j-1]。高点和低点各至少两个；任一相等、上升或两类方向不同都返回
    False，缺少足够点返回 None。这里只采用全窗每一对的 window_trend，近期两对
    转为下降并不能抹去窗口早期的上升反例。三个级别分别从折线、一级趋势和二级
    趋势取点，共用同一严格下降定义，context 与请求级别不符时抛 ValueError。
    """

    _require_context(context, trend_level=trend_level)
    if context.window_trend == StructuralTrend.UNKNOWN:
        return None
    return context.window_trend == StructuralTrend.BEAR


def shallow_countermove(evidence: RetracementEvidence) -> bool:
    """Strong-side-or-shallower pullback/rebound: partial and strictly < 0.67.

    Reuse decimal-derived evidence, without replacing 67% with two thirds. Zero
    countermovement is not a pullback/rebound. Invalid evidence raises ValueError.
    """

    if not isinstance(evidence, RetracementEvidence):
        raise ValueError('RetracementEvidence required')
    return evidence.partial and evidence.below_67_percent


def weak_countermove(evidence: RetracementEvidence) -> bool:
    """Weak pullback/rebound: partial and strictly < 0.33 of the prior impulse.

    The comparison uses the existing literal 33% evidence, not one third. Invalid
    evidence raises ``ValueError``; zero countermovement is not a confirmed leg.
    """

    if not isinstance(evidence, RetracementEvidence):
        raise ValueError('RetracementEvidence required')
    return evidence.partial and evidence.below_33_percent


@dataclass(frozen=True)
class TrendDefinitionSignals:
    """Historical trend events observable at ``asof_index``, with active stage.

    The boolean definitions preserve events after a later invalidation. ``stage``
    describes the active transition state; the booleans are not current trading
    permission. A key attack alone establishes a flip, while alternation requires
    its separate confirmation date. Head/bottom formation additionally requires
    the corresponding earlier suspicion. None of these events is a buy/sell rule.
    """

    asof_index: int
    direction: Direction
    stage: TransitionStage
    flip_to_bull: bool
    flip_to_bear: bool
    bear_to_bull_alternation: bool
    bull_to_bear_alternation: bool
    head_suspicion: bool
    bottom_suspicion: bool
    head_formed: bool
    bottom_formed: bool


def observe_trend_definition_signals(transition: TrendTransition) -> TrendDefinitionSignals:
    """Project existing causal transition evidence onto the named definitions.

    Attack, suspicion and alternation dates remain separate historical facts.
    An invalidated active stage retains facts confirmed before its failure. A
    direct key break without earlier suspicion does not establish head/bottom
    formation. Invalid or chronologically inconsistent evidence raises ValueError.
    """

    if not isinstance(transition, TrendTransition):
        raise ValueError('TrendTransition from observe_trend_transition required')
    _index(transition.asof_index, 'asof_index')
    if not isinstance(transition.direction, Direction) or not isinstance(transition.stage, TransitionStage):
        raise ValueError('explicit transition direction and stage required')
    attack = transition.attack
    if attack is not None:
        if not isinstance(attack, AttackEvidence) or attack.direction != transition.direction:
            raise ValueError('matching AttackEvidence direction required')
        _index(attack.bar_index, 'attack bar_index')
        if attack.bar_index > transition.asof_index or attack.level != transition.key:
            raise ValueError('observable attack on the frozen transition key required')
    for name, index in (
        ('suspicion_index', transition.suspicion_index),
        ('alternation_confirmed_index', transition.alternation_confirmed_index),
        ('invalidated_index', transition.invalidated_index),
    ):
        if index is not None:
            _index(index, name)
            if index > transition.asof_index:
                raise ValueError(f'{name} must already be observable')
    suspicion_index = transition.suspicion_index
    alternation_index = transition.alternation_confirmed_index
    if suspicion_index is not None and attack is not None and suspicion_index >= attack.bar_index:
        raise ValueError('suspicion must precede the key attack')
    if alternation_index is not None:
        if attack is None:
            raise ValueError('alternation requires an earlier key attack')
        if alternation_index <= attack.bar_index:
            raise ValueError('alternation confirmation must follow the key attack')
    suspicion = suspicion_index is not None
    alternation = alternation_index is not None
    up = transition.direction == Direction.UP
    flip = attack is not None
    formed = transition.formation_name is not None
    return TrendDefinitionSignals(
        asof_index=transition.asof_index,
        direction=transition.direction,
        stage=transition.stage,
        flip_to_bull=up and flip,
        flip_to_bear=not up and flip,
        bear_to_bull_alternation=up and alternation,
        bull_to_bear_alternation=not up and alternation,
        head_suspicion=not up and suspicion,
        bottom_suspicion=up and suspicion,
        head_formed=not up and formed,
        bottom_formed=up and formed,
    )
