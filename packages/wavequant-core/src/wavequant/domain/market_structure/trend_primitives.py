"""Named trend definitions over the existing confirmed-polyline evidence.

These primitives report structural observations, not buy or sell instructions.
Polyline construction, attack policy and transition detection remain in their
existing observers; in particular, the caller still chooses ``AttackBasis``
explicitly when obtaining a ``TrendTransition``.
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
    preceding_turn,
)

__all__ = [
    'positive_reversal', 'negative_reversal', 'last_fall_high', 'last_rise_low',
    'is_bull_trend', 'is_bear_trend', 'shallow_countermove', 'weak_countermove',
    'TrendDefinitionSignals', 'observe_trend_definition_signals',
]


def positive_reversal(point: ReversalPoint, *, asof_index: int) -> bool:
    """正反转: a confirmed low, observable by the requested knowledge date.

    A low drawn before ``asof_index`` is still unavailable until its confirmation
    date. Invalid point or index inputs raise ``ValueError``.
    """

    _index(asof_index, 'asof_index')
    if not isinstance(point, ReversalPoint):
        raise ValueError('ReversalPoint required')
    return point.point.kind == PointKind.LOW and point.confirmed_index <= asof_index


def negative_reversal(point: ReversalPoint, *, asof_index: int) -> bool:
    """负反转: a confirmed high, observable by the requested knowledge date.

    Invalid point or index inputs raise ``ValueError``; an unconfirmed high is
    false even if its drawing date is already inside the visible interval.
    """

    _index(asof_index, 'asof_index')
    if not isinstance(point, ReversalPoint):
        raise ValueError('ReversalPoint required')
    return point.point.kind == PointKind.HIGH and point.confirmed_index <= asof_index


def _require_context(context: StructureContext) -> None:
    if not isinstance(context, StructureContext):
        raise ValueError('StructureContext from observe_structure required')


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


def last_fall_high(context: StructureContext, low: ReversalPoint | None = None) -> KeyLevel | None:
    """末跌高: the nearest confirmed negative reversal to a selected low's left.

    With no selection, return the existing key of the context's window minimum.
    A selected low must belong to this observed context and already be confirmed;
    otherwise raise ``ValueError``. Missing left context returns ``None``. The
    key keeps the context's symbol, timeframe and observation date.
    """

    _require_context(context)
    if low is None:
        return context.last_fall_high
    return _selected_extreme_key(
        context,
        low,
        target_kind=PointKind.LOW,
        level_kind=LevelKind.RESISTANCE,
        definition='last_fall_high',
    )


def last_rise_low(context: StructureContext, high: ReversalPoint | None = None) -> KeyLevel | None:
    """末升低: the nearest confirmed positive reversal to a selected high's left.

    With no selection, return the existing key of the context's window maximum.
    Invalid kind, membership or confirmation date raises ``ValueError``; missing
    left context returns ``None``. Explicit selections preserve key provenance.
    """

    _require_context(context)
    if high is None:
        return context.last_rise_low
    return _selected_extreme_key(
        context,
        high,
        target_kind=PointKind.HIGH,
        level_kind=LevelKind.SUPPORT,
        definition='last_rise_low',
    )


def is_bull_trend(context: StructureContext) -> bool | None:
    """多头趋势: every successive confirmed high and low rises in the window.

    Insufficient confirmed highs or lows return ``None``. Equality or any mixed
    pair returns false, even if the latest two highs and lows both rise.
    """

    _require_context(context)
    if context.window_trend == StructuralTrend.UNKNOWN:
        return None
    return context.window_trend == StructuralTrend.BULL


def is_bear_trend(context: StructureContext) -> bool | None:
    """空头趋势: every successive confirmed high and low falls in the window.

    Insufficient confirmed highs or lows return ``None``. Equality or any mixed
    pair returns false; the observer's local two-pair trend is not substituted.
    """

    _require_context(context)
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
    formed = suspicion and flip
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
