"""Observe a held position's first support break and a failed rebound causally."""

from dataclasses import dataclass
from datetime import datetime, time
from fractions import Fraction
import math
from zoneinfo import ZoneInfo

from ..models.model import Bar
from ..market_structure.polyline import observe_bar_relations
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
    bearish_child_warning_index: int | None = None
    bearish_mother_child_warning_index: int | None = None
    bearish_child_pattern_warning_index: int | None = None
    bearish_child_mother_warning_index: int | None = None


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
        prior_peak = state.peak_index
        assert support is not None and prior_peak is not None
        peak = prior_peak
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


def _massive_gap_reversal(bars: list[Bar], index: int) -> dict | None:
    """A record-volume bearish gap that closes below the entire prior candle."""
    window = 10
    if index < window:
        return None
    bar, previous = bars[index], bars[index - 1]
    prior_volumes = [item.volume for item in bars[index - window:index]]
    total_volume = sum(prior_volumes)
    if (total_volume <= 0 or bar.volume <= max(prior_volumes)
            or bar.volume * window < 2 * total_volume
            or bar.open <= previous.high or bar.close >= previous.low
            or (bar.open - bar.close) / bar.open < .05):
        return None
    mean_volume = total_volume / window
    return dict(reason='volume_massive_gap_reversal_clear', exit_fraction=1.0,
                execution_model='same_day_close',
                massive_volume_window=window, massive_volume_mean=mean_volume,
                massive_volume_previous_max=max(prior_volumes),
                massive_volume_multiple=bar.volume / mean_volume,
                bearish_body_fraction=(bar.open - bar.close) / bar.open,
                observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                observed_close=bar.close, observed_volume=bar.volume,
                previous_open=previous.open, previous_high=previous.high,
                previous_low=previous.low, previous_close=previous.close,
                previous_volume=previous.volume)


def _bullish_gap_bearish_reversal(bars: list[Bar], index: int) -> dict[str, float | str] | None:
    """Clear after a long bullish candle loses more than half its body at the next open."""
    if index < 2:
        return None
    bar, bullish, before_bullish = bars[index], bars[index - 1], bars[index - 2]
    body = bullish.close - bullish.open
    if (bullish.open <= 0 or before_bullish.volume <= 0 or body < bullish.open * .05
            or bar.open >= (bullish.open + bullish.close) / 2
            or bar.close >= bar.open):
        return None
    volume_benchmark = before_bullish
    volume_basis = 'previous_session'
    if bullish.volume <= before_bullish.volume:
        # Only step over one exceptional spike to the immediately earlier bullish session.
        if index < 3:
            return None
        earlier_bullish = bars[index - 3]
        if (before_bullish.close <= before_bullish.open
                or earlier_bullish.close <= earlier_bullish.open or earlier_bullish.volume <= 0
                or before_bullish.volume < 2 * earlier_bullish.volume
                or bullish.volume <= earlier_bullish.volume):
            return None
        volume_benchmark = earlier_bullish
        volume_basis = 'bullish_before_exceptional_volume'
    reference = index - 2
    while reference >= 0 and bars[reference].close >= bars[reference].open:
        reference -= 1
    if reference < 0 or bars[reference].volume <= 0 or bar.volume <= bars[reference].volume:
        return None
    bearish = bars[reference]
    return dict(reason='volume_bullish_gap_bearish_clear', exit_fraction=1.0,
                execution_model='same_day_close',
                bullish_date=bullish.timestamp.date().isoformat(),
                bullish_open=bullish.open, bullish_close=bullish.close,
                bullish_body_midpoint=(bullish.open + bullish.close) / 2,
                bullish_volume=bullish.volume,
                bullish_previous_volume=before_bullish.volume,
                bullish_volume_benchmark_date=volume_benchmark.timestamp.date().isoformat(),
                bullish_volume_benchmark=volume_benchmark.volume,
                bullish_volume_basis=volume_basis,
                bearish_reference_date=bearish.timestamp.date().isoformat(),
                bearish_reference_volume=bearish.volume,
                observed_open=bar.open, observed_close=bar.close,
                observed_volume=bar.volume)


def _bearish_outside_reversal(bars: list[Bar], index: int) -> dict | None:
    """Clear a bullish run's outside reversal only when it exceeds the run's last bearish volume."""
    if index < 2:
        return None
    bar, previous = bars[index], bars[index - 1]
    if (bar.close >= bar.open or previous.close <= previous.open
            or bar.high < previous.high or bar.close >= previous.low):
        return None
    reference = index - 2
    while reference >= 0 and bars[reference].close > bars[reference].open:
        reference -= 1
    if (reference < 0 or bars[reference].close >= bars[reference].open
            or bar.volume <= bars[reference].volume):
        return None
    bearish = bars[reference]
    return dict(reason='volume_bearish_outside_clear', exit_fraction=1.0,
                execution_model='same_day_close',
                bearish_reference_date=bearish.timestamp.date().isoformat(),
                bearish_reference_volume=bearish.volume,
                observed_open=bar.open, observed_high=bar.high,
                observed_low=bar.low, observed_close=bar.close,
                observed_volume=bar.volume,
                previous_open=previous.open, previous_high=previous.high,
                previous_low=previous.low, previous_close=previous.close,
                previous_volume=previous.volume)


def _bearish_child_mother_volume(bars: list[Bar], index: int) -> dict[str, float | str] | None:
    """A later bearish mother encloses the child and exceeds the previous bearish volume."""
    if index < 1:
        return None
    child, mother = bars[index - 1], bars[index]
    if (mother.close >= mother.open or mother.high < child.high or mother.low > child.low
            or (mother.high == child.high and mother.low == child.low)):
        return None
    reference = index - 1
    while reference >= 0 and bars[reference].close >= bars[reference].open:
        reference -= 1
    if reference < 0:
        return None
    bearish = bars[reference]
    if bearish.volume <= 0 or mother.volume <= bearish.volume:
        return None
    return dict(child_date=child.timestamp.date().isoformat(),
                child_open=child.open, child_high=child.high, child_low=child.low,
                child_close=child.close, child_volume=child.volume,
                mother_date=mother.timestamp.date().isoformat(),
                mother_open=mother.open, mother_high=mother.high, mother_low=mother.low,
                mother_close=mother.close, mother_volume=mother.volume,
                bearish_reference_date=bearish.timestamp.date().isoformat(),
                bearish_reference_volume=bearish.volume)


def _bearish_mother_child_volume(bars: list[Bar], index: int) -> dict | None:
    """Compare a bearish child's volume with the last bearish bar before its mother."""
    if index < 2:
        return None
    mother, child = bars[index - 1], bars[index]
    bearish_mother = mother.close < mother.open
    contained = (child.high <= mother.high and child.low >= mother.low
                 and (child.high < mother.high or child.low > mother.low)) if bearish_mother else (
                     observe_bar_relations(mother, child).inside)
    if mother.close == mother.open or child.close >= child.open or not contained:
        return None
    reference = index - 2
    while reference >= 0 and bars[reference].close >= bars[reference].open:
        reference -= 1
    if reference < 0:
        return None
    bearish = bars[reference]
    if (bearish.volume <= 0 or child.volume <= bearish.volume
            or (not bearish_mother and mother.close <= bearish.high)):
        return None
    return dict(mother_date=mother.timestamp.date().isoformat(),
                mother_high=mother.high, mother_low=mother.low,
                mother_open=mother.open, mother_close=mother.close, mother_bearish=bearish_mother,
                bearish_reference_date=bearish.timestamp.date().isoformat(),
                bearish_reference_high=bearish.high,
                bearish_reference_volume=bearish.volume,
                child_date=child.timestamp.date().isoformat(),
                child_high=child.high, child_low=child.low, child_close=child.close,
                child_volume=child.volume,
                child_bearish_volume_multiple=child.volume / bearish.volume)


def _bearish_mother_child_pattern(bars: list[Bar], index: int) -> dict[str, float | str] | None:
    """Recognize a bearish child within a non-doji mother, allowing one shared extreme."""
    if index < 1:
        return None
    mother, child = bars[index - 1], bars[index]
    if (mother.close == mother.open or child.close >= child.open
            or child.high > mother.high or child.low < mother.low
            or (child.high == mother.high and child.low == mother.low)):
        return None
    return dict(mother_date=mother.timestamp.date().isoformat(),
                mother_high=mother.high, mother_low=mother.low,
                child_date=child.timestamp.date().isoformat(),
                child_high=child.high, child_low=child.low, child_close=child.close)


def observe_bearish_child_pattern_exit(
    bars: list[Bar], index: int, state: StagedExitState
) -> dict[str, float | str] | None:
    """Track without reducing; clear on a volume low/close break of the child."""
    if index < 1:
        return None
    warning = state.bearish_child_pattern_warning_index
    if warning is not None and index > warning:
        child, bar = bars[warning], bars[index]
        reference = index - 1
        while reference > warning and bars[reference].close >= bars[reference].open:
            reference -= 1
        bearish = bars[reference]
        volume_break = ((child.volume > 0 and bar.volume > child.volume)
                        or (bearish.volume > 0 and bar.volume > bearish.volume))
        if bar.low < child.low and bar.close < child.close and volume_break:
            return dict(reason='bearish_mother_child_break_clear', exit_fraction=1.0,
                        execution_model='same_day_close',
                        child_date=child.timestamp.date().isoformat(),
                        child_low=child.low, child_close=child.close,
                        child_volume=child.volume,
                        bearish_reference_date=bearish.timestamp.date().isoformat(),
                        bearish_reference_volume=bearish.volume,
                        observed_low=bar.low, observed_close=bar.close,
                        observed_volume=bar.volume)
    pattern = _bearish_mother_child_pattern(bars, index)
    if pattern is None:
        return None
    state.bearish_child_pattern_warning_index = index
    return None


def observe_volume_down_exit(bars: list[Bar], index: int, state: StagedExitState, *,
                             positive_n_index: int | None = None, small_body_max_fraction: float = .01,
                             small_body_lookback: int = 10) -> dict | None:
    """Freeze confirmed support and escalate cumulative reduction without future N bars."""
    if index < 1:
        return None
    mother_warning = state.bearish_child_mother_warning_index
    if mother_warning is not None and index == mother_warning + 1:
        mother, bar = bars[mother_warning], bars[index]
        if bar.low < mother.low and bar.close < mother.close:
            return dict(reason='volume_bearish_child_mother_break_clear', exit_fraction=1.0,
                        execution_model='same_day_close',
                        mother_date=mother.timestamp.date().isoformat(),
                        mother_low=mother.low, mother_close=mother.close,
                        observed_low=bar.low, observed_close=bar.close)
    if mother_warning is not None and index > mother_warning + 1:
        state.bearish_child_mother_warning_index = None
    bearish_warning = state.bearish_mother_child_warning_index
    if bearish_warning is not None and index > bearish_warning:
        mother, child, bar = bars[bearish_warning - 1], bars[bearish_warning], bars[index]
        if bar.low < child.low:
            return dict(reason='volume_bearish_mother_child_low_clear', exit_fraction=1.0,
                        execution_model='same_day_close',
                        mother_date=mother.timestamp.date().isoformat(),
                        child_date=child.timestamp.date().isoformat(),
                        child_low=child.low, child_close=child.close,
                        observed_low=bar.low, observed_close=bar.close)
    warning = state.bearish_child_warning_index
    if warning is not None and index == warning + 1:
        child, bar = bars[warning], bars[index]
        if bar.low < child.low and bar.close < child.close:
            return dict(reason='volume_bearish_child_break_clear', exit_fraction=1.0,
                        execution_model='same_day_close',
                        child_date=child.timestamp.date().isoformat(),
                        child_low=child.low, child_close=child.close,
                        observed_low=bar.low, observed_close=bar.close)
    massive_reversal = _massive_gap_reversal(bars, index)
    if massive_reversal is not None:
        return massive_reversal
    bullish_reversal = _bullish_gap_bearish_reversal(bars, index)
    if bullish_reversal is not None:
        return bullish_reversal
    outside_reversal = _bearish_outside_reversal(bars, index)
    if outside_reversal is not None:
        return outside_reversal
    bearish_mother = _bearish_child_mother_volume(bars, index)
    if bearish_mother is not None:
        state.bearish_child_mother_warning_index = index
        if state.volume_reduction_target < .7:
            state.volume_reduction_target = .7
            return dict(bearish_mother, reason='volume_bearish_child_mother_reduce_70',
                        exit_fraction=.7, exit_target_fraction=.7,
                        execution_model='same_day_close')
    bearish_child = _bearish_mother_child_volume(bars, index)
    # The bullish-mother warning keeps its double-break requirement, while
    # an overlapping bearish pair can independently protect its child's low.
    if warning is not None and index == warning + 1:
        if bearish_child is not None and bearish_child['mother_bearish']:
            state.bearish_mother_child_warning_index = index
        return None
    if bearish_child is not None:
        if bearish_child['mother_bearish']:
            state.bearish_mother_child_warning_index = index
        else:
            state.bearish_child_warning_index = index
    if bearish_child is not None and state.volume_reduction_target < .7:
        state.volume_trigger_index = index
        state.volume_support_index = next((j for j in range(index - 2, 0, -1)
            if bars[j].low < min(bars[j - 1].low, bars[j + 1].low)), None)
        state.volume_reduction_target = .7
        reason = ('volume_bearish_mother_child_reduce_70' if bearish_child['mother_bearish']
                  else 'volume_bearish_child_reduce_70')
        return dict(bearish_child, reason=reason,
                    exit_fraction=.7, exit_target_fraction=.7,
                    execution_model='same_day_close')
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
            assert n_bar is not None and mean_body is not None
            evidence.update(positive_n_date=n_bar.timestamp.date().isoformat(),
                            positive_n_low=n_bar.low, positive_n_high=n_bar.high,
                            small_body_fraction=float(body)/bar.open, small_body_mean=float(mean_body),
                            small_body_cap=small_body_max_fraction, small_body_lookback=small_body_lookback)
        return dict(evidence, reason='volume_down_small_n_reduce_30' if small_inside else 'volume_down_reduce_70',
                    exit_fraction=target, exit_target_fraction=target,
                    execution_model='next_open' if small_inside else 'same_day_close')
    return None
