"""Close-observed exhaustion risk after the holding's own N reaches a target."""

from ..models.config import StrategyConfig
from ..models.model import Bar
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


def observe_wave_exhaustion(
    bars: list[Bar], index: int, events: list[dict], config: StrategyConfig, *, reduced: bool = False
) -> dict | None:
    # Confirm only the immediately preceding trading candle's known warning.
    # Failed/rounded partial fills must not prevent a subsequent full exit.
    if index >= 2 and bars[index].close < bars[index - 1].close:
        warning = _observe_target_candle(bars, index - 1, events, config)
        if warning is not None and "exit_target_fraction" in warning:
            return dict(
                {
                    key: warning[key]
                    for key in ("wave_n_date", "wave_reached_date", "wave_reached_stage", "wave_reached_price")
                },
                reason="wave_abnormal_followthrough_clear",
                exit_fraction=1.0,
                abnormal_date=bars[index - 1].timestamp.date().isoformat(),
                abnormal_close=bars[index - 1].close,
                abnormal_reason=warning["reason"],
                observed_open=bars[index].open,
                observed_close=bars[index].close,
                observed_low=bars[index].low,
                observed_high=bars[index].high,
                observed_volume=bars[index].volume,
                previous_volume=bars[index - 1].volume,
                previous_close=bars[index - 1].close,
                execution_model="same_day_close",
            )
    return _observe_target_candle(bars, index, events, config, reduced=reduced)


def _observe_target_candle(
    bars: list[Bar], index: int, events: list[dict], config: StrategyConfig, *, reduced: bool = False
) -> dict | None:
    """Caller supplies only this entry N's dated projection events.

    Reaching a target alone never sells. Neither a bearish engulfing candle nor
    a full exit requires higher volume or a previously filled partial order.
    """
    if index < 1:
        return None
    reached = None
    stage = None
    for event in sorted(events, key=lambda e: e["bar_index"]):
        if event["bar_index"] > index:
            break
        if event["event"] == "wave_projection_invalidated":
            return None
        if event["event"] == "wave_projection_ready":
            reached, stage = event, "two_t"
        elif event["event"] == "wave_projection_target_reached":
            reached, stage = event, event["reached_stage"]
    if reached is None:
        return None
    bar, previous = bars[index], bars[index - 1]
    span = bar.high - bar.low
    if span <= 0:
        return None
    upper = bar.high - max(bar.open, bar.close)
    lower = min(bar.open, bar.close) - bar.low
    body = bar.open - bar.close
    evidence = dict(
        wave_n_date=bars[reached["attack"]].timestamp.date().isoformat(),
        wave_reached_date=bars[reached["bar_index"]].timestamp.date().isoformat(),
        wave_reached_stage=stage,
        wave_reached_price=reached["two_t"] if stage == "two_t" else reached["reached_target"],
        wave_range_fraction=span / previous.close,
        wave_upper_shadow_fraction=upper / span,
        wave_lower_shadow_fraction=lower / span,
        observed_volume=bar.volume,
        previous_volume=previous.volume,
        observed_open=bar.open,
        observed_close=bar.close,
        previous_open=previous.open,
        previous_close=previous.close,
        previous_high=previous.high,
        wave_body_fraction=body / bar.open,
        wave_gap_fraction=(bar.open - previous.high) / previous.high,
        execution_model="same_day_close",
    )
    if (
        previous.close > previous.open
        and body >= config.wave_engulf_min_body * bar.open
        and body >= span * 0.6
        and bar.open >= previous.close
        and bar.close <= previous.open
    ):
        return dict(evidence, reason="wave_bearish_engulf_clear", exit_fraction=1.0)
    # A lower opening can invalidate bullish resistance without engulfing
    # its entire body. Full liquidation must not depend on a prior reduction.
    if index >= 2 and previous.close > previous.open:
        resistance = observe_resistance(
            bars[index - 2], previous, attack_direction=Direction.DOWN, shadow_policy=ShadowPolicy(0.5)
        )
        virtual_low = min(previous.low, bars[index - 2].close)
        if (
            resistance.detected is True
            and body >= config.wave_engulf_min_body * bar.open
            and body >= span * 0.6
            and bar.close < virtual_low
        ):
            return dict(
                evidence,
                reason="wave_bull_resistance_failed_clear",
                exit_fraction=1.0,
                resistance_date=previous.timestamp.date().isoformat(),
                resistance_virtual_low=virtual_low,
            )
    if (
        not reduced
        and bar.volume > previous.volume
        and bar.open > previous.high
        and body >= config.wave_engulf_min_body * bar.open
        and span / previous.close >= config.wave_exhaustion_min_range
        and upper / span <= 0.2
    ):
        return dict(
            evidence,
            reason="wave_gap_reversal_reduce",
            exit_fraction=config.wave_exhaustion_reduction,
            exit_target_fraction=config.wave_exhaustion_reduction,
        )
    if (
        not reduced
        and bar.volume > previous.volume
        and bar.high > previous.high
        and body > 0
        and span / previous.close >= config.wave_exhaustion_min_range
        and upper >= body
        and lower / span <= config.wave_exhaustion_min_shadow
    ):
        # A target-stage rally can fail through its upper wick without a
        # 5% bearish body or a second long shadow. Daily return is irrelevant.
        return dict(
            evidence,
            reason="wave_upper_rejection_reduce",
            exit_fraction=config.wave_exhaustion_reduction,
            exit_target_fraction=config.wave_exhaustion_reduction,
        )
    if (
        not reduced
        and bar.volume > previous.volume
        and span / previous.close >= config.wave_exhaustion_min_range
        and upper / span >= config.wave_exhaustion_min_shadow
        and lower / span >= config.wave_exhaustion_min_shadow
        and abs(body) / span <= 0.3
    ):
        return dict(
            evidence,
            reason="wave_volume_shadows_reduce",
            exit_fraction=config.wave_exhaustion_reduction,
            exit_target_fraction=config.wave_exhaustion_reduction,
        )
    return None
