"""Derive bullish permission from frozen bearish context and confirmed alternation."""
from dataclasses import dataclass

from ..market_structure.price_action import AttackBasis
from ..market_structure.trend_structure import (StructuralTrend, TransitionStage, observe_structure,
                              observe_trend_transition)


@dataclass(frozen=True)
class BullPermission:
    context_index: int
    key_source_index: int
    key_price: float
    flip_index: int
    alternation_index: int
    bullish_index: int

    def permits(self, attack_index: int) -> bool:
        # Late regime confirmation cannot recycle an N predating permission.
        return self.flip_index < self.alternation_index <= self.bullish_index < attack_index


def bull_permission_history(bars, snapshots, epochs, blocked, window, *, maximum_retracement=None):
    """Frozen first bearish context; all evidence is consumed at its known date.

    Unbroken contexts expire after `window` bars. Broken original lows, changed
    frozen prefixes, later bearish structure and last-rise-low close breaks
    revoke permission. Ambiguous strict segments never bridge. Confirmed
    alternation is a historical fact, not redrawn by later proxy replacements.
    """
    permissions, events = {}, []
    context = observation = ready = previous_points = previous_ctx = None
    last_epoch = None

    def log(i, event, **fields):
        events.append(dict(bar_index=i, event=event, **fields))

    for i, bar in enumerate(bars):
        points = snapshots[i]
        if epochs[i] != last_epoch or i in blocked:
            if context is not None:
                log(i, 'bull_permission_revoked', reason='structure_segment_interrupted')
            context = observation = ready = previous_points = previous_ctx = None
        last_epoch = epochs[i]
        permissions[i] = None
        if i in blocked:
            continue
        current = observe_structure(points, symbol=bar.symbol, timeframe='1d',
            window_start=max(epochs[i], i-window), asof_index=i)
        reason = None
        if context is not None:
            if bar.low < context.window_low.point.price:
                reason = 'frozen_bottom_breached'
            elif observation is not None and observation.alternation_confirmed_index is not None:
                level = previous_ctx.last_rise_low if previous_ctx else None
                if level is not None and bars[i-1].close >= level.price > bar.close:
                    reason = 'last_rise_low_close_broken'
                elif current.trend == StructuralTrend.BEAR:
                    reason = 'bearish_structure_returned'
            elif not all(p in points for p in context.points):
                reason = 'frozen_context_replaced_by_proxy'
            elif (observation is None or observation.attack is None) and i-context.asof_index > window:
                reason = 'unbroken_context_expired'
        if reason:
            log(i, 'bull_permission_revoked', reason=reason)
            context = observation = ready = previous_points = None
        if context is None and current.trend == StructuralTrend.BEAR and current.last_fall_high is not None:
            if bar.low >= current.window_low.point.price and bar.close <= current.last_fall_high.price:
                context = current
                log(i, 'bearish_context_frozen', key=current.last_fall_high.price,
                    key_source=current.last_fall_high.source_index,
                    bottom=current.window_low.point.price)
        if context is not None and i > context.asof_index:
            completed = observation is not None and observation.alternation_confirmed_index is not None
            crossed = bars[i-1].close <= context.last_fall_high.price < bar.close
            if not completed and (points != previous_points or crossed):
                old = observation
                observation = observe_trend_transition(bars, points, context=context,
                    attack_basis=AttackBasis.CLOSE, asof_index=i)
                if (maximum_retracement is not None and observation.retracement is not None
                        and observation.retracement.ratio>=maximum_retracement):
                    log(i,'bull_permission_revoked',reason='countermove_not_below_exact_two_thirds')
                    context=observation=ready=None
                    previous_points,previous_ctx=points,current
                    continue
                if observation.attack is not None and (old is None or old.attack is None):
                    log(i, 'bear_to_bull_flip', flip_index=observation.attack.bar_index,
                        context_index=context.asof_index, key=context.last_fall_high.price)
                if observation.alternation_confirmed_index is not None:
                    log(i, 'bear_bull_alternation',
                        flip_index=observation.attack.bar_index,
                        alternation_index=observation.alternation_confirmed_index,
                        ratio=observation.retracement.ratio)
                elif observation.stage in (TransitionStage.DEEP_COUNTERMOVE, TransitionStage.INVALIDATED):
                    log(i, 'bull_permission_revoked', reason=observation.stage.value)
                    context = observation = ready = None
            if (observation is not None and observation.alternation_confirmed_index is not None
                    and current.trend == StructuralTrend.BULL):
                if ready is None:
                    ready = BullPermission(context.asof_index, context.last_fall_high.source_index,
                        context.last_fall_high.price, observation.attack.bar_index,
                        observation.alternation_confirmed_index, i)
                    log(i, 'bullish_entry_permission_confirmed', **ready.__dict__)
                permissions[i] = ready
        previous_points, previous_ctx = points, current
    return permissions, events
