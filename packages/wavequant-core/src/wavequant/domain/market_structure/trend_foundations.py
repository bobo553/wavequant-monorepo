"""Compose candle, confirmed-trend and N-defense foundations at one knowledge date.

Callers supply confirmed reversal evidence, an optional frozen transition context
and an optional N setup. This observer neither guesses a polyline nor creates
orders. Unavailable evidence remains explicit in the immutable result.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ..models.model import Bar
from .candle_primitives import BarRelations, observe_bar_relations, virtual_high, virtual_low
from .n_defense import selloff_high as observe_selloff_high, squeeze_low as observe_squeeze_low
from .n_shape import MilestoneBasis, NObservation, NSetup, observe_n
from .polyline import PointKind, ReversalPoint
from .price_action import AttackBasis, Direction, _index, _ordered_pair, _validate_bar
from .trend_primitives import TrendDefinitionSignals, observe_trend_definition_signals
from .trend_structure import StructureContext, TrendTransition, observe_structure, observe_trend_transition

__all__ = ['TrendFoundations', 'observe_trend_foundations']


@dataclass(frozen=True)
class TrendFoundations:
    """Observable facts for 20 concepts, with independent transition/N evidence.

    ``structure`` describes the requested visible window. A transition can retain
    an older frozen context with a different window. Candle facts are absent only
    at the series' first bar. N defense references retain historical completion;
    they are not permission to trade after a later breach.
    """

    structure: StructureContext
    bar_relations: BarRelations | None
    virtual_low: float | None
    virtual_high: float | None
    positive_reversals: tuple[ReversalPoint, ...]
    negative_reversals: tuple[ReversalPoint, ...]
    transition: TrendTransition | None
    signals: TrendDefinitionSignals | None
    n_observation: NObservation | None
    squeeze_low: float | None
    selloff_high: float | None


def _visible_bars(bars: Sequence[Bar], *, symbol: str, end: int) -> tuple[Bar, ...]:
    """Copy only the visible prefix so delegated observers cannot inspect its future."""
    visible: list[Bar] = []
    for index in range(end + 1):
        bar = bars[index]
        if not isinstance(bar, Bar):
            raise ValueError('Bar sequence required')
        if bar.symbol != symbol:
            raise ValueError('symbol mismatch')
        if visible:
            _ordered_pair(visible[-1], bar)
        else:
            _validate_bar(bar)
        visible.append(bar)
    return tuple(visible)


def _visible_points(
    points: Sequence[ReversalPoint], bars: Sequence[Bar], *, end: int,
) -> tuple[ReversalPoint, ...]:
    """Check source prices only after confirmation makes a reversal observable."""
    visible: list[ReversalPoint] = []
    for reversal in points:
        if not isinstance(reversal, ReversalPoint):
            raise ValueError('ReversalPoint sequence required')
        if reversal.confirmed_index > end:
            continue
        _index(reversal.confirmed_index, 'reversal confirmation index')
        _index(reversal.point.index, 'reversal source index')
        if reversal.point.index > reversal.confirmed_index:
            raise ValueError('reversal confirmation cannot precede its source')
        source = bars[reversal.point.index]
        price = source.high if reversal.point.kind == PointKind.HIGH else source.low
        if reversal.point.price != price:
            raise ValueError('pivot price differs from source bar and adjustment basis')
        visible.append(reversal)
    return tuple(visible)


def observe_trend_foundations(
    bars: Sequence[Bar],
    points: Sequence[ReversalPoint],
    *,
    symbol: str,
    timeframe: str,
    window_start: int = 0,
    asof_index: int | None = None,
    transition_context: StructureContext | None = None,
    attack_basis: AttackBasis = AttackBasis.INTRABAR,
    n_setup: NSetup | None = None,
    milestone_basis: MilestoneBasis = MilestoneBasis.EXTREME,
) -> TrendFoundations:
    """Observe the existing foundations without inferring pivots or transition seeds.

    The default flip uses a strict high/low break (figure 008: H4 > H1 or L7 <
    L4). ``AttackBasis.CLOSE`` explicitly requests close confirmation instead.
    Only bars through ``asof_index`` and reversals confirmed by that index affect
    the result. A missing transition context or N setup produces absent evidence.

    Raises:
        ValueError: For invalid windows, OHLC/time order, source pivot prices,
            enum policies or mismatched symbol/timeframe in supplied context/setup.

    Chart labels, turn counts and segment lengths are not inputs to these rules.
    The wrapper copies/filters its visible inputs a constant number of times and
    adds no daily replay; existing observers retain their own complexity.
    """
    if (not isinstance(bars, Sequence) or isinstance(bars, (str, bytes))
            or not isinstance(points, Sequence) or isinstance(points, (str, bytes))):
        raise ValueError('bar and reversal sequences required')
    end = len(bars) - 1 if asof_index is None else asof_index
    _index(window_start, 'window_start')
    _index(end, 'asof_index')
    if end >= len(bars) or window_start > end:
        raise ValueError('available ordered window required')
    if (not isinstance(symbol, str) or not symbol.strip()
            or not isinstance(timeframe, str) or not timeframe.strip()):
        raise ValueError('explicit symbol and timeframe required')
    if not isinstance(attack_basis, AttackBasis) or not isinstance(milestone_basis, MilestoneBasis):
        raise ValueError('explicit AttackBasis and MilestoneBasis required')
    if transition_context is not None and (
        not isinstance(transition_context, StructureContext)
        or transition_context.symbol != symbol or transition_context.timeframe != timeframe
    ):
        raise ValueError('transition context requires matching symbol and timeframe')
    if n_setup is not None and (
        not isinstance(n_setup, NSetup) or n_setup.symbol != symbol or n_setup.timeframe != timeframe
    ):
        raise ValueError('N setup requires matching symbol and timeframe')

    visible_bars = _visible_bars(bars, symbol=symbol, end=end)
    visible_points = _visible_points(points, visible_bars, end=end)
    structure = observe_structure(
        visible_points, symbol=symbol, timeframe=timeframe, window_start=window_start, asof_index=end,
    )
    relation = None
    low = high = None
    if end > 0:
        previous, current = visible_bars[end - 1], visible_bars[end]
        relation = observe_bar_relations(previous, current)
        low, high = virtual_low(previous, current), virtual_high(previous, current)

    transition = signals = None
    if transition_context is not None:
        transition = observe_trend_transition(
            visible_bars, visible_points, context=transition_context, attack_basis=attack_basis, asof_index=end,
        )
        signals = observe_trend_definition_signals(transition)

    n_observation = None
    squeeze = selloff = None
    if n_setup is not None:
        n_observation = observe_n(
            visible_bars, n_setup, timeframe=timeframe, milestone_basis=milestone_basis, asof_index=end,
        )
        if n_setup.direction == Direction.UP:
            squeeze = observe_squeeze_low(n_observation)
        else:
            selloff = observe_selloff_high(n_observation)

    return TrendFoundations(
        structure=structure,
        bar_relations=relation,
        virtual_low=low,
        virtual_high=high,
        positive_reversals=tuple(point for point in structure.points if point.point.kind == PointKind.LOW),
        negative_reversals=tuple(point for point in structure.points if point.point.kind == PointKind.HIGH),
        transition=transition,
        signals=signals,
        n_observation=n_observation,
        squeeze_low=squeeze,
        selloff_high=selloff,
    )
