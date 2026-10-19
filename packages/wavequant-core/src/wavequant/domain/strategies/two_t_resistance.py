"""Close-known two-T resistance shared by entry selection and holding risk."""

from collections.abc import Mapping, Sequence
from fractions import Fraction
from math import isfinite

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar
from ..market_state.volume_candle import bearish_body_engulfs, is_volume_bearish
from .event_history import EventHistoryStream


TWO_T_BODY_VOLUME_CLEAR = "wave_two_t_body_volume_clear"
BODY_VOLUME_CLEAR = "bearish_body_volume_clear"
VOLUME_SHADOW_MIDPOINT_CLEAR = 'volume_shadow_below_bearish_midpoint_clear'
TWO_T_VOLUME_BEARISH_REDUCE = "wave_two_t_volume_bearish_reduce"


def _body_volume_reversal(
    bars: Sequence[Bar], index: int, bearish_index: int | None,
) -> dict[str, object] | None:
    """A bearish body covers yesterday; either prior-session volume benchmark suffices."""
    if index < 1:
        return None
    bar, previous = bars[index], bars[index - 1]
    bearish = bars[bearish_index] if bearish_index is not None and 0 <= bearish_index < index else None
    above_previous = previous.volume > 0 and bar.volume > previous.volume
    above_bearish = bearish is not None and bearish.volume > 0 and bar.volume > bearish.volume
    if not is_volume_bearish(bar, previous, bearish) or not bearish_body_engulfs(bar, previous):
        return None
    return dict(
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        previous_open=previous.open, previous_high=previous.high,
        previous_low=previous.low, previous_close=previous.close, previous_volume=previous.volume,
        engulfed_date=previous.timestamp.date().isoformat(),
        bearish_reference_date=bearish.timestamp.date().isoformat() if bearish is not None else None,
        bearish_reference_volume=bearish.volume if bearish is not None else None,
        volume_above_previous=above_previous, volume_above_last_bearish=above_bearish,
    )


def _long_upper_shadow(bar: Bar) -> bool:
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    upper = high - max(opened, closed)
    # Decimal prices that give an equal wick/body must not fail through binary rounding.
    return high > low and 2 * upper >= high - low and upper >= abs(closed - opened)


def _upper_shadow_over_forty_percent(bar: Bar) -> bool:
    high, low, opened, closed = (Fraction(str(value)) for value in (bar.high, bar.low, bar.open, bar.close))
    return high > low and 5 * (high - max(opened, closed)) > 2 * (high - low)


def _next_volume_clear(
    bars: Sequence[Bar],
    index: int,
    bearish_index: int | None,
    evidence: dict[str, object],
    reason: str,
) -> dict[str, object] | None:
    previous, bar = bars[index - 1], bars[index]
    bearish = bars[bearish_index] if bearish_index is not None else None
    if (not is_volume_bearish(bar, previous, bearish)
            or bar.low >= previous.low or bar.close >= previous.close):
        return None
    return dict(
        evidence, reason=reason, exit_fraction=1.0,
        bearish_reference_date=bearish.timestamp.date().isoformat() if bearish is not None else None,
        bearish_reference_volume=bearish.volume if bearish is not None else None,
        previous_volume=previous.volume,
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        previous_low=previous.low, previous_close=previous.close,
    )


class TwoTResistanceHistory(EventHistoryStream):
    """Retain bearish context and evaluate only today's target windows.

    Target-window precedence follows the caller's original event order. Body
    reversals select the latest source exactly as the batch rule did; a window
    full exit keeps priority. Historical input corrections are handled by the
    owned event journal, while new dates never rescan old candles.
    """

    def __init__(self, bars: Sequence[Bar], *, reduction_fraction: float = .8, start_index: int = 0) -> None:
        if not isfinite(reduction_fraction) or not 0 < reduction_fraction < 1:
            raise ValueError("reduction fraction must be between zero and one")
        self.reduction_fraction = reduction_fraction
        super().__init__(bars, start_index=start_index)

    def _accepts(self, event: Mapping[str, object]) -> bool:
        return (event.get('event') in ('wave_projection_ready', 'wave_projection_invalidated')
                or event.get('event') in ('wave_n_target_reached', 'wave_projection_target_reached')
                and event.get('reached_stage') == 'two_t')

    def _reset_state(self) -> None:
        self.invalidated: dict[tuple[int, int | None], int] = {}
        self.previous_bearish: list[int | None] = [None] * self.start_index
        self.bearish_index: int | None = None

    def _advance_day(self, index: int) -> None:
        bars, risks = self.bars, self.history
        bar = bars[index]
        self.previous_bearish.append(self.bearish_index)
        reversal = _body_volume_reversal(bars, index, self.bearish_index)
        bearish = bars[self.bearish_index] if self.bearish_index is not None else None
        volume_bearish = is_volume_bearish(bar, bars[index - 1] if index else None, bearish)
        if bar.close < bar.open:
            self.bearish_index = index
        for event in self.dated.get(index, ()):
            attack, origin = event.get('attack'), event.get('origin_index')
            if event.get('event') == 'wave_projection_invalidated' and type(attack) is int:
                key = attack, origin if type(origin) is int else None
                self.invalidated[key] = min(index, self.invalidated.get(key, index))
        body_risk: dict[str, object] | None = None
        body_source: tuple[int, int, int] = (-1, -1, -1)
        for event in self.events:
            if event.get('event') == 'wave_projection_invalidated':
                continue
            reached, attack = event.get('bar_index'), event.get('attack')
            target = event.get('two_t', event.get('reached_target'))
            origin = event.get('origin_index')
            if (type(reached) is not int or type(attack) is not int
                    or not 0 <= attack <= reached <= index
                    or not isinstance(target, (int, float)) or isinstance(target, bool)
                    or not isfinite(target) or target <= 0):
                continue
            observed = bars[reached].close if reached in (attack, self.start_index) else bars[reached].high
            if observed < target:
                continue
            key = attack, origin if type(origin) is int else None
            invalidated = self.invalidated.get(key, len(bars))
            if invalidated <= reached:
                continue
            # Old targets matter today only for a body reversal. Their small
            # warning window must never rebuild evidence on every later close.
            if index > reached + 2 and ((reversal is None and not volume_bearish) or index >= invalidated):
                continue
            target_evidence: dict[str, object] = dict(
                attack=attack, wave_n_date=bars[attack].timestamp.date().isoformat(),
                wave_reached_stage='two_t', wave_reached_price=target,
                wave_reached_date=bars[reached].timestamp.date().isoformat(),
                execution_model='same_day_close',
            )
            if type(origin) is int and 0 <= origin <= reached:
                target_evidence.update(wave_n_origin_date=bars[origin].timestamp.date().isoformat(),
                                       wave_n_origin_price=bars[origin].low)
            if volume_bearish and reached <= index < invalidated and risks.get(index, {}).get('exit_fraction') != 1.0:
                risks[index] = dict(target_evidence, reason=TWO_T_VOLUME_BEARISH_REDUCE,
                    exit_fraction=self.reduction_fraction, exit_target_fraction=self.reduction_fraction,
                    observed_open=bar.open, observed_close=bar.close, observed_volume=bar.volume,
                    previous_volume=bars[index - 1].volume if index else None,
                    bearish_reference_date=bearish.timestamp.date().isoformat() if bearish is not None else None,
                    bearish_reference_volume=bearish.volume if bearish is not None else None,
                    target_trigger='reached_two_t_volume_bearish')
            source = reached, attack, origin if type(origin) is int else -1
            if reversal is not None and reached < index < invalidated and source > body_source:
                body_source = source
                body_risk = dict(target_evidence, **reversal, reason=TWO_T_BODY_VOLUME_CLEAR,
                    exit_fraction=1.0,
                    target_trigger='post_two_t_bearish_body_engulf_volume_gt_last_bearish_or_previous')
            if index == reached + 1 and _upper_shadow_over_forty_percent(bars[reached]):
                confirmation = observe_resistance(bars[reached], bar,
                    attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5))
                clear = _next_volume_clear(bars, index, self.previous_bearish[index],
                    dict(target_evidence, target_trigger='first_two_t_reach_upper_gt_40pct_next_session',
                         previous_upper_shadow_fraction=(bars[reached].high
                             - max(bars[reached].open, bars[reached].close))
                             / (bars[reached].high - bars[reached].low),
                         target_confirmation_patterns=list(confirmation.reasons)),
                    'wave_two_t_next_volume_clear')
                if clear is not None and risks.get(index, {}).get('exit_fraction') != 1.0:
                    risks[index] = clear
            for warning_index in range(max(1, reached), min(reached + 2, index + 1)):
                if invalidated <= warning_index:
                    break
                if index not in (warning_index, warning_index + 1):
                    continue
                warning = bars[warning_index]
                if not _long_upper_shadow(warning):
                    continue
                resistance = observe_resistance(bars[warning_index - 1], warning,
                    attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5))
                patterns = list(resistance.reasons)
                if 'long_upper_shadow' not in patterns:
                    patterns.append('long_upper_shadow')
                evidence: dict[str, object] = dict(
                    target_evidence, target_warning_index=warning_index,
                    target_warning_date=warning.timestamp.date().isoformat(),
                    target_warning_low=warning.low, target_warning_close=warning.close,
                    wave_upper_shadow_fraction=(warning.high - max(warning.open, warning.close))
                        / (warning.high - warning.low),
                    target_resistance_patterns=patterns,
                    observed_open=warning.open, observed_high=warning.high,
                    observed_low=warning.low, observed_close=warning.close,
                    observed_volume=warning.volume, previous_close=bars[warning_index - 1].close,
                )
                reduction = dict(evidence, reason='wave_two_t_resistance_reduce',
                    exit_fraction=self.reduction_fraction, exit_target_fraction=self.reduction_fraction)
                if index == warning_index:
                    if risks.get(index, {}).get('exit_fraction') != 1.0:
                        risks[index] = reduction
                    continue
                confirmation = observe_resistance(warning, bar, attack_direction=Direction.UP,
                                                  shadow_policy=ShadowPolicy(.5))
                if confirmation.detected is True and risks.get(index, {}).get('exit_fraction') != 1.0:
                    risks[index] = dict(reduction, target_confirmation_patterns=list(confirmation.reasons),
                        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                        observed_close=bar.close, observed_volume=bar.volume, previous_close=warning.close)
                clear = _next_volume_clear(bars, index, self.previous_bearish[index],
                    dict(evidence, target_confirmation_patterns=list(confirmation.reasons)),
                    'wave_two_t_resistance_volume_clear')
                if clear is not None:
                    risks[index] = clear
        if body_risk is not None and risks.get(index, {}).get('exit_fraction') != 1.0:
            risks[index] = body_risk


def two_t_resistance_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]], *, reduction_fraction: float = .8,
) -> dict[int, dict[str, object]]:
    """Batch compatibility uses the same dated reducer as the daily stream."""
    return TwoTResistanceHistory(bars, reduction_fraction=reduction_fraction).advance(len(bars) - 1, events)


def _volume_shadow_midpoint_clear(
    bars: Sequence[Bar], index: int, previous_volume_bearish: bool,
) -> dict[str, object] | None:
    """前一日放量阴线实体中点下方的放量长上影，阳线同样清仓。"""
    if index < 1 or not previous_volume_bearish:
        return None
    previous, bar = bars[index - 1], bars[index]
    if (previous.symbol != bar.symbol or previous.timestamp >= bar.timestamp
            or previous.close >= previous.open or previous.volume <= 0 or bar.volume <= previous.volume):
        return None
    opened, closed, high, prior_open, prior_close = (
        Fraction(str(value)) for value in (bar.open, bar.close, bar.high, previous.open, previous.close))
    upper, body = high - max(opened, closed), abs(closed - opened)
    midpoint = (prior_open + prior_close) / 2
    if upper <= body or closed >= midpoint:
        return None
    return dict(reason=VOLUME_SHADOW_MIDPOINT_CLEAR, exit_fraction=1.0,
        execution_model='same_day_close', target_trigger='volume_upper_gt_body_below_previous_volume_bearish_midpoint',
        bearish_reference_date=previous.timestamp.date().isoformat(), previous_volume_bearish=True,
        previous_open=previous.open, previous_close=previous.close, previous_volume=previous.volume,
        bearish_body_midpoint=float(midpoint), upper_shadow=float(upper), candle_body=float(body),
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume,
        decision_timestamp=bar.timestamp.replace(hour=15, minute=0).isoformat())


class HoldingResistanceHistory(TwoTResistanceHistory):
    """Shared target risks plus independent close-known body reversal clearance."""

    def _reset_state(self) -> None:
        super()._reset_state()
        # 资格窗口外不补发交易风险，但最近阴线仍是已知的行情量能基准。
        self.bearish_index = next((index for index in range(self.start_index - 1, -1, -1)
            if self.bars[index].close < self.bars[index].open), None)
        previous = self.start_index - 1
        earlier_bearish = next((i for i in range(previous - 1, -1, -1)
                               if self.bars[i].close < self.bars[i].open), None)
        self.previous_volume_bearish = previous >= 0 and is_volume_bearish(
            self.bars[previous], self.bars[previous - 1] if previous > 0 else None,
            self.bars[earlier_bearish] if earlier_bearish is not None else None)

    def _advance_day(self, index: int) -> None:
        shadow = _volume_shadow_midpoint_clear(self.bars, index, self.previous_volume_bearish)
        self.previous_volume_bearish = is_volume_bearish(
            self.bars[index], self.bars[index - 1] if index else None,
            self.bars[self.bearish_index] if self.bearish_index is not None else None)
        super()._advance_day(index)
        reversal = _body_volume_reversal(self.bars, index, self.previous_bearish[index])
        if reversal is not None and self.history.get(index, {}).get('exit_fraction') != 1.0:
            self.history[index] = dict(reversal, reason=BODY_VOLUME_CLEAR, exit_fraction=1.0,
                execution_model='same_day_close',
                target_trigger='bearish_body_engulf_volume_gt_previous_or_last_bearish')
        if shadow is not None and self.history.get(index, {}).get('exit_fraction') != 1.0:
            self.history[index] = shadow


def holding_resistance_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]], *, reduction_fraction: float = .8,
) -> dict[int, dict[str, object]]:
    """The batch and daily account paths share the same independent clearance."""
    return HoldingResistanceHistory(bars, reduction_fraction=reduction_fraction).advance(len(bars) - 1, events)
