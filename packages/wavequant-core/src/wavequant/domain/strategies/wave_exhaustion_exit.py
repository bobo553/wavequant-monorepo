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
    c_entry = next(
        (event for event in events if event.get("event") == "wave_c_entry"
         and event.get("bar_index") == (signal_index if signal_index is not None else entry_index)),
        None,
    )
    c_exit = (_observe_c_target(bars, index, c_entry, reduced=reduced, entry_index=entry_index)
              if c_entry is not None else None)
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
        if c_entry is not None:
            return c_exit
        return _observe_ordinary_c(bars, index, ordinary, config, reduced=reduced, entry_index=entry_index)
    current = _observe_target_candle(bars, index, events, config, reduced=reduced)
    if current is not None and current.get("exit_fraction") == 1.0:
        return current
    if c_exit is not None and c_exit["exit_fraction"] == 1.0:
        return c_exit
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
    return current or c_exit


def _observe_c_target(
    bars: list[Bar], index: int, entry: dict, *, reduced: bool, entry_index: int | None
) -> dict | None:
    """Use only the entry's frozen C projections and bars closed so far."""
    held_from = entry_index if entry_index is not None else entry["bar_index"]
    if index <= held_from or index < 1:
        return None
    reached = None
    for stage, target in (("c_equal", entry["equal_target"]), ("c_0618", entry["c_0618_target"])):
        first = next((j for j in range(held_from + 1, index + 1) if bars[j].high >= target), None)
        if first is not None:
            reached = stage, target, first
            break
    if reached is None:
        return None
    stage, target, first = reached
    bar, previous = bars[index], bars[index - 1]
    prior_bearish_index = next((j for j in range(index - 1, -1, -1)
                                if bars[j].close < bars[j].open), None)
    evidence = dict(
        wave_reached_stage=stage,
        wave_reached_date=bars[first].timestamp.date().isoformat(),
        wave_reached_price=target,
        wave_c_0618_target=entry["c_0618_target"],
        wave_equal_target=entry["equal_target"],
        wave_a_origin=entry["a_origin"],
        wave_a_high=entry["a_high"],
        wave_b_low=entry["b_low"],
        observed_open=bar.open,
        observed_high=bar.high,
        observed_low=bar.low,
        observed_close=bar.close,
        observed_volume=bar.volume,
        previous_low=previous.low,
        previous_close=previous.close,
        execution_model="same_day_close",
    )
    if (prior_bearish_index is not None and bar.low < previous.low
            and bar.close < previous.close and bar.volume > bars[prior_bearish_index].volume):
        reference = bars[prior_bearish_index]
        return dict(evidence, reason="wave_c_target_lower_low_close_volume_clear", exit_fraction=1.0,
                    bearish_reference_date=reference.timestamp.date().isoformat(),
                    bearish_reference_volume=reference.volume)
    if reduced:
        return None
    resistance = observe_resistance(previous, bar, attack_direction=Direction.UP,
                                    shadow_policy=ShadowPolicy(0.5))
    if resistance.detected is True:
        return dict(evidence, reason="wave_c_target_bearish_reduce", exit_fraction=0.7,
                    exit_target_fraction=0.7, wave_bearish_patterns=list(resistance.reasons))
    return None


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

    Reaching a target alone never sells. Bearish engulfing and failed bullish
    resistance clear regardless of volume or a previously filled reduction.
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
    # A strong A holding can own its parent five/ten milestone and a newer B N.
    # The reached parent milestone governs exits until a higher stage is reached.
    reached, stage = max(active.values(), key=lambda item: (
        stage_rank.get(item[1], 0), item[0]["bar_index"], item[0]["attack"]))
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
    if stage == "ten_full" and body > 0 and bar.high >= reached["reached_target"]:
        prior_bearish = next((bars[j] for j in range(index - 1, -1, -1)
                              if bars[j].close < bars[j].open), None)
        if prior_bearish is not None and bar.volume > prior_bearish.volume:
            return dict(
                evidence,
                reason="wave_ten_full_bearish_volume_clear",
                exit_fraction=1.0,
                bearish_reference_date=prior_bearish.timestamp.date().isoformat(),
                bearish_reference_volume=prior_bearish.volume,
            )
    if stage in ("five_top", "ten_full") and body > 0 and not reduced:
        return dict(
            evidence,
            reason="wave_target_bearish_reduce",
            exit_fraction=0.7,
            exit_target_fraction=0.7,
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
