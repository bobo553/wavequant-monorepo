"""Close-observed exhaustion risk after the holding's own N reaches a target."""

from ..models.config import StrategyConfig
from ..models.model import Bar
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


def observe_wave_exhaustion(
    bars: list[Bar],
    index: int,
    events: list[dict],
    config: StrategyConfig,
    *,
    reduced: bool = False,
    entry_index: int | None = None,
    signal_index: int | None = None,
) -> dict | None:
    if not events:
        return None
    ordinary = next(
        (
            event
            for event in events
            if event.get("event") == "wave_ordinary_entry"
            and event.get("bar_index") == (signal_index if signal_index is not None else entry_index)
        ),
        None,
    )
    if ordinary is not None:
        return _observe_ordinary_c(bars, index, ordinary, config, reduced=reduced, entry_index=entry_index)
    current = _observe_target_candle(bars, index, events, config, reduced=reduced)
    if current is not None and current.get("exit_fraction") == 1.0:
        return current
    # Long upper-shadow warnings persist to the first later lower close.
    # Failed/rounded partial fills must not prevent that full exit.
    held_from = entry_index if entry_index is not None else signal_index if signal_index is not None else 0
    if index > held_from + 1 and bars[index].close < bars[index - 1].close:
        active_warning = None
        for warning_index in range(max(1, held_from + 1), index):
            if active_warning is not None and bars[warning_index].close < bars[warning_index - 1].close:
                active_warning = None
            warning = _observe_target_candle(bars, warning_index, events, config)
            if warning is None or "exit_target_fraction" not in warning:
                continue
            if warning_index != index - 1 and warning["reason"] != "wave_target_upper_shadow_reduce":
                continue
            active_warning = warning
            abnormal_index = warning_index
        if active_warning is not None:
            return dict(
                {
                    key: active_warning[key]
                    for key in ("wave_n_date", "wave_reached_date", "wave_reached_stage", "wave_reached_price",
                                "wave_n_origin_date", "wave_n_origin_price") if key in active_warning
                },
                reason="wave_abnormal_followthrough_clear",
                exit_fraction=1.0,
                abnormal_date=bars[abnormal_index].timestamp.date().isoformat(),
                abnormal_close=bars[abnormal_index].close,
                observed_open=bars[index].open,
                observed_close=bars[index].close,
                observed_low=bars[index].low,
                observed_high=bars[index].high,
                observed_volume=bars[index].volume,
                previous_volume=bars[index - 1].volume,
                previous_close=bars[index - 1].close,
                execution_model="same_day_close",
            )
    return current


def _observe_ordinary_c(
    bars: list[Bar],
    index: int,
    entry: dict,
    config: StrategyConfig,
    *,
    reduced: bool,
    entry_index: int | None,
) -> dict | None:
    """Freeze an ordinary A at entry and watch its C equal-wave target."""
    held_from = entry_index if entry_index is not None else entry["bar_index"]
    if index <= held_from or not entry["one_p"] <= entry["a_high"] < entry["two_t"]:
        return None
    reached_index = next((j for j in range(held_from + 1, index + 1) if bars[j].high >= entry["target"]), None)
    if reached_index is None:
        return None
    reached = dict(
        entry,
        event="wave_projection_target_reached",
        bar_index=reached_index,
        reached_stage="ordinary_equal",
        reached_target=entry["target"],
    )
    for j in range(reached_index, index):
        warning = _observe_target_candle(bars, j, [reached], config)
        if warning is not None and "exit_target_fraction" in warning:
            if bars[index].close < bars[index - 1].close:
                return dict(
                    {key: value for key, value in warning.items() if key.startswith("wave_")},
                    reason="wave_ordinary_equal_lower_close_clear",
                    exit_fraction=1.0,
                    abnormal_date=bars[j].timestamp.date().isoformat(),
                    abnormal_close=bars[j].close,
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
            return None
    return _observe_target_candle(bars, index, [reached], config, reduced=reduced)


def _observe_target_candle(
    bars: list[Bar], index: int, events: list[dict], config: StrategyConfig, *, reduced: bool = False
) -> dict | None:
    """Caller supplies only this entry N's dated projection events.

    Reaching a target alone never sells. Neither a bearish engulfing candle nor
    a full exit requires higher volume or a previously filled partial order.
    """
    if index < 1:
        return None
    active = {}
    invalidated = set()
    for event in sorted(events, key=lambda e: e["bar_index"]):
        if event["bar_index"] > index:
            break
        key = (event["attack"], event.get("origin_index"))
        if event["event"] == "wave_projection_invalidated":
            invalidated.add(key)
            active.pop(key, None)
            continue
        if key in invalidated:
            continue
        if event["event"] == "wave_projection_ready":
            active[key] = (event, "two_t")
        elif event["event"] in ("wave_projection_target_reached", "wave_n_target_reached"):
            active[key] = (event, event["reached_stage"])
    if not active:
        return None
    stage_rank = {"one_p": 1, "two_t": 2, "five_top": 3, "ten_full": 4}
    reached, stage = max(active.values(), key=lambda item: (
        item[0]["attack"], item[0]["bar_index"], stage_rank.get(item[1], 0)))
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
        wave_reached_price=reached.get("two_t", reached.get("reached_target")) if stage == "two_t" else reached["reached_target"],
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
        wave_body_range_fraction=body / span,
        wave_gap_fraction=(bar.open - previous.high) / previous.high,
        execution_model="same_day_close",
    )
    if reached.get("origin_index") is not None:
        evidence["wave_n_origin_date"] = bars[reached["origin_index"]].timestamp.date().isoformat()
        evidence["wave_n_origin_price"] = bars[reached["origin_index"]].low
    if stage == "ordinary_equal":
        evidence.update(
            wave_a_class="ordinary",
            wave_one_p=reached["one_p"],
            wave_two_t=reached["two_t"],
            wave_a_high=reached["a_high"],
            wave_b_low=reached["b_low"],
            wave_equal_target=reached["target"],
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
    if stage == "ordinary_equal" and not reduced and bar.volume > previous.volume and upper / span >= 0.5:
        return dict(
            evidence,
            reason="wave_ordinary_equal_upper_shadow_reduce",
            exit_fraction=config.wave_exhaustion_reduction,
            exit_target_fraction=config.wave_exhaustion_reduction,
        )
    if stage in ("one_p", "two_t", "five_top", "ten_full") and not reduced and upper / span >= 0.5:
        return dict(
            evidence,
            reason="wave_target_upper_shadow_reduce",
            exit_fraction=config.wave_exhaustion_reduction,
            exit_target_fraction=config.wave_exhaustion_reduction,
        )
    if (
        not reduced
        and bar.volume > previous.volume
        and bar.open > previous.high
        # A wide gap reversal can surrender half its range before its body
        # reaches 5% of the opening price. This is a reduction, not engulfing.
        and (body >= config.wave_engulf_min_body * bar.open or body >= span * 0.5)
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
