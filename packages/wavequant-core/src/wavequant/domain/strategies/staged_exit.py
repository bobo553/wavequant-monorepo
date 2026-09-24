"""Observe a held position's first support break and a failed rebound causally."""

from dataclasses import dataclass
from datetime import datetime, time
from fractions import Fraction
import math
from zoneinfo import ZoneInfo

from ..models.model import Bar
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


def support_break_reduction(day_low: float, current_price: float, support_low: float, support_close: float) -> float:
    """Return the cumulative reduction target relative to the original holding.

    During the closing window, current_price is the latest observable price,
    never the not-yet-known final daily close. Equality does not count as a break.
    """
    values = (day_low, current_price, support_low, support_close)
    if any(isinstance(value, bool) or not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("support-break prices must be finite and positive")
    if day_low > current_price or support_low > support_close:
        raise ValueError("a low cannot exceed its corresponding current/closing price")
    if day_low >= support_low:
        return 0.0
    return 0.65 if current_price < support_close else 0.35


@dataclass(frozen=True)
class ClosingReductionIntent:
    """A cumulative sell target, not a fill or a broker acknowledgement."""

    observed_at: datetime
    target_fraction: float
    target_quantity: int
    order_quantity: int


def closing_reduction_intent(
    *,
    observed_at: datetime,
    day_low: float,
    current_price: float,
    support_low: float,
    support_close: float,
    original_quantity: int,
    filled_quantity: int,
    pending_quantity: int,
    sellable_quantity: int,
    previous_target: float = 0.0,
    lot_size: int = 100,
) -> ClosingReductionIntent | None:
    """Size a closing-window order from observed prices and reconciled quantities.

    Quantities are raw shares on one consistent corporate-action basis. The
    caller supplies actual fills and all outstanding sells for this holding;
    sending an intent never changes filled_quantity. Trading-session validation
    and broker submission belong to the caller, not this decision function.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("closing-window observations require an explicit timezone")
    quantities = (original_quantity, filled_quantity, pending_quantity, sellable_quantity, lot_size)
    if any(type(value) is not int or value < 0 for value in quantities) or original_quantity == 0 or lot_size == 0:
        raise ValueError("share quantities must be non-negative integers with positive holding and lot")
    if (
        filled_quantity + pending_quantity > original_quantity
        or sellable_quantity > original_quantity - filled_quantity
    ):
        raise ValueError("sell quantities exceed the original holding")
    if previous_target not in (0.0, 0.35, 0.65):
        raise ValueError("unknown cumulative reduction target")
    target = max(previous_target, support_break_reduction(day_low, current_price, support_low, support_close))
    local = observed_at.astimezone(ZoneInfo("Asia/Shanghai"))
    # 15:00 is already the close; never relabel a final daily bar as an intraday observation.
    if not time(14, 30) <= local.time() < time(15):
        return None
    target_quantity = (original_quantity * round(target * 100) // (100 * lot_size)) * lot_size
    additional = max(0, target_quantity - filled_quantity - pending_quantity)
    available = max(0, sellable_quantity - pending_quantity)
    order_quantity = min(additional, available) // lot_size * lot_size
    if not order_quantity:
        return None
    return ClosingReductionIntent(observed_at, target, target_quantity, order_quantity)


@dataclass
class StagedExitState:
    breakdown_index: int | None = None
    support_index: int | None = None
    peak_index: int | None = None
    rebound_seen: bool = False
    rebound_peak: float | None = None
    recovered: bool = False
    exit_requested: bool = False
    reduction_target: float = 0.0
    inverse_index: int | None = None
    volume_trigger_index: int | None = None
    volume_support_index: int | None = None
    volume_reduction_target: float = 0.0


def observe_intraday_staged_exit(
    bars: list[Bar], index: int, state: StagedExitState, day_low: float, current_price: float
) -> dict | None:
    """Decide from completed prior daily bars and the current completed minute bar."""

    if state.recovered or state.exit_requested:
        return None
    if state.breakdown_index is None:
        support = next(
            (j for j in range(index - 2, 0, -1) if bars[j].low < min(bars[j - 1].low, bars[j + 1].low)), None
        )
        if support is None:
            return None
        peak = max(range(support + 1, index), key=lambda j: bars[j].high)
        if bars[peak].high <= day_low:
            return None
    else:
        support = state.support_index
        peak = state.peak_index
        assert support is not None and peak is not None
    target = support_break_reduction(day_low, current_price, bars[support].low, bars[support].close)
    if target <= state.reduction_target:
        return None
    if state.breakdown_index is None:
        state.breakdown_index, state.support_index, state.peak_index = index, support, peak
    state.reduction_target = target
    decision = _decision(
        bars, state, target,
        "support_low_close_break_reduce" if target == 0.65 else "support_low_break_reduce",
    )
    recovery = Fraction(str(day_low)) + (Fraction(str(bars[peak].high)) - Fraction(str(day_low))) * Fraction(2, 3)
    return dict(decision, exit_target_fraction=target, breakdown_low=day_low,
                rebound_threshold=float(recovery))


def observe_staged_exit(
    bars: list[Bar], index: int, state: StagedExitState, reduction_fraction: float, *, tiered: bool = False
) -> dict | None:
    """One reduction per holding; a recovered rebound leaves other risk exits active.

    A local support candle requires a strictly higher low on both neighboring
    candles. Only completed prior candles select support and the decline high.
    The break compares low with low AND close with close, not close with low.
    """
    bar = bars[index]
    if state.breakdown_index is None:
        support = next(
            (j for j in range(index - 2, 0, -1) if bars[j].low < min(bars[j - 1].low, bars[j + 1].low)), None
        )
        if support is None:
            return None
        target = support_break_reduction(bar.low, bar.close, bars[support].low, bars[support].close)
        if not target or (not tiered and target != 0.65):
            return None
        if not tiered and bars[index - 1].low < bars[support].low and bars[index - 1].close < bars[support].close:
            return None
        peak = max(range(support + 1, index), key=lambda j: bars[j].high)
        if (not tiered and bars[peak].high <= bars[support].high) or bars[peak].high <= bar.low:
            return None
        state.breakdown_index, state.support_index, state.peak_index = index, support, peak
        if tiered:
            state.reduction_target = target
            return dict(
                _decision(
                    bars,
                    state,
                    target,
                    "support_low_close_break_reduce" if target == 0.65 else "support_low_break_reduce",
                ),
                exit_target_fraction=target,
            )
        return _decision(bars, state, reduction_fraction, "support_low_close_break_reduce")

    if state.recovered or state.exit_requested or index <= state.breakdown_index:
        return None
    state.rebound_peak = max(state.rebound_peak or bar.high, bar.high)
    assert state.peak_index is not None
    low = Fraction(str(bars[state.breakdown_index].low))
    high = Fraction(str(bars[state.peak_index].high))
    recovery = low + (high - low) * Fraction(2, 3)
    # With daily OHLC, a threshold breach wins over a same-bar renewed decline:
    # their intraday order is unknown, so it cannot prove a weak rebound.
    if Fraction(str(state.rebound_peak)) > recovery:
        state.recovered = True
    was_rebounding = state.rebound_seen
    if bar.high > bars[index - 1].high:
        state.rebound_seen = True
    if not state.recovered and was_rebounding and Fraction(str(bar.close)) < low:
        state.exit_requested = True
        return _decision(bars, state, 1.0, "weak_rebound_two_thirds_exit")
    if tiered and state.reduction_target == 0.35:
        assert state.support_index is not None
        support_bar = bars[state.support_index]
        if support_break_reduction(bar.low, bar.close, support_bar.low, support_bar.close) == 0.65:
            state.reduction_target = 0.65
            return dict(_decision(bars, state, 0.65, "support_low_close_break_reduce"), exit_target_fraction=0.65)
    return None


def _decision(bars: list[Bar], state: StagedExitState, fraction: float, reason: str) -> dict:
    assert state.support_index is not None and state.peak_index is not None and state.breakdown_index is not None
    support, peak, broken = (bars[state.support_index], bars[state.peak_index], bars[state.breakdown_index])
    recovery = Fraction(str(broken.low)) + (Fraction(str(peak.high)) - Fraction(str(broken.low))) * Fraction(2, 3)
    return dict(
        reason=reason,
        exit_fraction=fraction,
        support_date=support.timestamp.date().isoformat(),
        support_low=support.low,
        support_close=support.close,
        decline_high_date=peak.timestamp.date().isoformat(),
        decline_high=peak.high,
        breakdown_date=broken.timestamp.date().isoformat(),
        breakdown_low=broken.low,
        rebound_threshold=float(recovery),
        rebound_basis="high",
        rebound_peak=state.rebound_peak,
    )


def observe_inverse_resistance_exit(bars: list[Bar], index: int, state: StagedExitState) -> dict | None:
    """Track remaining inventory from its filled inverse-N reduction, at daily close."""
    start = state.inverse_index
    if start is None or index <= start:
        return None
    defense = max(bars[start].high, bars[start-1].close) if start else bars[start].high
    if bars[index].high > defense:
        state.inverse_index = None
        return None
    if index < start + 2:
        return None
    prior, previous = bars[index-1], bars[index-2]
    resistance = observe_resistance(previous, prior, attack_direction=Direction.DOWN,
                                    shadow_policy=ShadowPolicy(0.5))
    virtual_low = min(prior.low, previous.close)
    if resistance.detected is not True or bars[index].close >= virtual_low:
        return None
    return dict(reason='inverse_n_bull_resistance_failed_exit', exit_fraction=1.0,
                inverse_n_date=bars[start].timestamp.date().isoformat(),
                resistance_date=prior.timestamp.date().isoformat(),
                resistance_virtual_low=virtual_low, resistance_close=prior.close,
                failure_close=bars[index].close, inverse_defense=defense,
                execution_model='same_day_close')


def observe_volume_down_exit(bars: list[Bar], index: int, state: StagedExitState, *,
                             positive_n_index: int | None = None, small_body_max_fraction: float = .01,
                             small_body_lookback: int = 10) -> dict | None:
    """Freeze confirmed support and escalate cumulative reduction without future N bars."""
    if index < 1:
        return None
    bar, previous = bars[index], bars[index-1]
    body = abs(Fraction(str(bar.close))-Fraction(str(bar.open)))
    mean_body = (sum(abs(Fraction(str(b.close))-Fraction(str(b.open))) for b in bars[index-small_body_lookback:index]) / small_body_lookback
                 if index >= small_body_lookback else None)
    n_bar = bars[positive_n_index] if positive_n_index is not None and 0 <= positive_n_index < index else None
    small_inside = (n_bar is not None and mean_body is not None
                    and body <= Fraction(str(bar.open))*Fraction(str(small_body_max_fraction))
                    and body < mean_body and n_bar.low <= bar.low and bar.high <= n_bar.high)
    eligible = bar.volume > previous.volume and bar.close < previous.close and (bar.close < bar.open or small_inside)
    target = .3 if small_inside else .7
    if state.volume_trigger_index is None:
        if not eligible:
            return None
        state.volume_trigger_index = index
        state.volume_support_index = next((j for j in range(index-2,0,-1)
            if bars[j].low < min(bars[j-1].low,bars[j+1].low)), None)
    start, support = state.volume_trigger_index, state.volume_support_index
    upgrading = eligible and target > state.volume_reduction_target
    trigger = index if upgrading else start
    evidence = dict(volume_trigger_date=bars[trigger].timestamp.date().isoformat(),
                    trigger_volume=bars[trigger].volume, previous_volume=bars[trigger-1].volume,
                    trigger_close=bars[trigger].close, previous_close=bars[trigger-1].close,
                    volume_support_date=bars[support].timestamp.date().isoformat() if support is not None else None,
                    volume_support_low=bars[support].low if support is not None else None)
    if support is not None and bar.low < bars[support].low:
        return dict(evidence, reason='volume_down_support_break_clear', exit_fraction=1.0,
                    execution_model='same_day_close')
    if index == start + 1 and bar.open < previous.close and bar.close < bar.open:
        return dict(evidence, reason='volume_down_next_gap_fade_clear', exit_fraction=1.0,
                    observed_open=bar.open, gap_previous_close=previous.close,
                    execution_model='same_day_close')
    if (index == start + 1 and state.volume_reduction_target >= .7
            and bar.close < bar.open and bar.close < bars[start].low):
        return dict(evidence, reason='volume_down_next_followthrough_clear', exit_fraction=1.0,
                    warning_low=bars[start].low, observed_open=bar.open,
                    execution_model='same_day_close')
    if upgrading:
        state.volume_reduction_target = target
        if small_inside:
            evidence.update(positive_n_date=n_bar.timestamp.date().isoformat(),
                            positive_n_low=n_bar.low, positive_n_high=n_bar.high,
                            small_body_fraction=float(body)/bar.open, small_body_mean=float(mean_body),
                            small_body_cap=small_body_max_fraction, small_body_lookback=small_body_lookback)
        return dict(evidence, reason='volume_down_small_n_reduce_30' if small_inside else 'volume_down_reduce_70',
                    exit_fraction=target, exit_target_fraction=target,
                    execution_model='next_open' if small_inside else 'same_day_close')
    return None
