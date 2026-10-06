"""Close-observed exhaustion risk after a known positive N reaches a target."""

from collections.abc import Sequence
from fractions import Fraction

from ..models.config import StrategyConfig
from ..models.model import Bar
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


FIVE_TOP_CHILD_VOLUME_CLEAR = "wave_five_top_child_volume_clear"
FIVE_TOP_GAP_VOLUME_CLEAR = "wave_five_top_gap_volume_clear"
FIVE_TOP_GAP_UPPER_SHADOW_CLEAR = "wave_five_top_gap_upper_shadow_clear"
FIVE_TOP_BODY_UPPER_SHADOW_CLEAR = "wave_five_top_body_upper_shadow_clear"
FIVE_TOP_UPPER_SHADOW_CLEAR_REASONS = (FIVE_TOP_BODY_UPPER_SHADOW_CLEAR, FIVE_TOP_GAP_UPPER_SHADOW_CLEAR)
C_EQUAL_NEAR_RESISTANCE_REDUCE = "wave_c_equal_near_resistance_reduce"
C_EQUAL_NEAR_VOLUME_CLEAR = "wave_c_equal_near_volume_clear"


def observe_c_equal_near_risk(
    bars: Sequence[Bar], index: int, events: Sequence[dict], *, reduction_fraction: float = 0.8
) -> dict | None:
    """Watch the first one-tick approach to a known strong-A C equal target."""
    if index < 1 or not events:
        return None
    tick = Fraction(1, 100)
    current, previous = bars[index], bars[index - 1]
    lower_day = current.low < previous.low and current.close < previous.close
    last_bearish = (next((bars[j] for j in range(index - 1, -1, -1)
                          if bars[j].close < bars[j].open), None) if lower_day else None)
    clear_possible = (last_bearish is not None and last_bearish.volume > 0
                      and current.volume > last_bearish.volume)
    for event in sorted(events, key=lambda row: row["bar_index"], reverse=True):
        if event.get("event") != "wave_c_equal_target":
            continue
        entry, attack, target = event["bar_index"], event["attack"], event["target"]
        defense = event.get("defense")
        if not 0 <= attack <= entry < index:
            continue
        if defense is None or any(bars[j].low < defense for j in range(entry + 1, index)):
            continue
        target_price = Fraction(str(target))
        today_gap = target_price - Fraction(str(current.high))
        if not clear_possible and not 0 <= today_gap <= tick:
            continue
        warning_index = next(
            (j for j in range(entry + 1, index + 1)
             if Fraction(str(bars[j].high)) >= target_price - tick), None
        )
        if warning_index is None:
            continue
        warning = bars[warning_index]
        gap = target_price - Fraction(str(warning.high))
        if not 0 <= gap <= tick:
            continue
        resistance = observe_resistance(
            bars[warning_index - 1], warning,
            attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5),
        )
        if resistance.detected is not True:
            continue
        evidence = dict(
            wave_n_date=bars[attack].timestamp.date().isoformat(),
            wave_c_equal_target=target, wave_target_gap=float(gap),
            target_warning_date=warning.timestamp.date().isoformat(),
            target_warning_high=warning.high, target_warning_low=warning.low,
            target_warning_close=warning.close,
            wave_upper_shadow_fraction=resistance.shadow_range_fraction,
            target_resistance_patterns=list(resistance.reasons),
            execution_model="same_day_close",
        )
        if warning_index == index:
            return dict(
                evidence, reason=C_EQUAL_NEAR_RESISTANCE_REDUCE,
                exit_fraction=reduction_fraction, exit_target_fraction=reduction_fraction,
                observed_open=warning.open, observed_high=warning.high,
                observed_low=warning.low, observed_close=warning.close,
                observed_volume=warning.volume,
            )
        reference = next((bars[j] for j in range(warning_index, -1, -1)
                          if bars[j].close < bars[j].open), None)
        for day in range(warning_index + 1, index + 1):
            bar, previous = bars[day], bars[day - 1]
            if (reference is not None and reference.volume > 0 and bar.low < previous.low
                    and bar.close < previous.close and bar.volume > reference.volume):
                if day == index:
                    return dict(
                        evidence, reason=C_EQUAL_NEAR_VOLUME_CLEAR, exit_fraction=1.0,
                        observed_open=bar.open, observed_high=bar.high,
                        observed_low=bar.low, observed_close=bar.close,
                        observed_volume=bar.volume, previous_low=previous.low,
                        previous_close=previous.close,
                        bearish_reference_date=reference.timestamp.date().isoformat(),
                        bearish_reference_volume=reference.volume,
                    )
                break
            if bar.close < bar.open:
                reference = bar
    return None


def observe_five_top_child_volume_clear(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Expose a known five-top break from chronologically sorted projection events."""
    if not projection_events:
        return None
    child_break = _five_top_child_volume_break(bars, index)
    if child_break is None:
        return None
    reached = _known_five_top(projection_events, index)
    if reached is None:
        return None
    bar, previous = bars[index], bars[index - 1]
    return dict(
        child_break,
        wave_n_date=bars[reached["attack"]].timestamp.date().isoformat(),
        wave_reached_date=bars[reached["bar_index"]].timestamp.date().isoformat(),
        wave_reached_stage=reached["reached_stage"], wave_reached_price=reached["reached_target"],
        observed_open=bar.open, observed_close=bar.close, observed_volume=bar.volume,
        previous_close=previous.close, previous_volume=previous.volume,
        execution_model="same_day_close",
    )


def _known_five_top(projection_events: Sequence[dict], index: int) -> dict | None:
    """Select a live five-top known before this close; current invalidation wins."""
    reached_by_n: dict[tuple[int, int | None], dict] = {}
    invalidated: set[tuple[int, int | None]] = set()
    for event in projection_events:
        if event["bar_index"] > index:
            break
        key = (event["attack"], event.get("origin_index"))
        if event["event"] == "wave_projection_invalidated":
            reached_by_n.pop(key, None)
            invalidated.add(key)
        elif (key not in invalidated and event["event"] == "wave_projection_target_reached"
              and event["bar_index"] < index
              and event.get("reached_stage") in ("five_top", "ten_full")):
            reached_by_n[key] = event
    if not reached_by_n:
        return None
    rank = {"five_top": 1, "ten_full": 2}
    return max(reached_by_n.values(), key=lambda event: (
        rank[event["reached_stage"]], event["bar_index"], event["attack"]))


def observe_five_top_gap_volume_clear(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Clear after a prior five-top on a lower open, prior-open break and bearish-volume expansion."""
    if index < 1 or not projection_events:
        return None
    bar, previous = bars[index], bars[index - 1]
    if bar.open >= previous.close or bar.close >= previous.open:
        return None
    reference = next((bars[j] for j in range(index - 1, -1, -1)
                      if bars[j].close < bars[j].open), None)
    if reference is None or reference.volume <= 0 or bar.volume <= reference.volume:
        return None
    reached = _known_five_top(projection_events, index)
    if reached is None:
        return None
    return dict(
        reason=FIVE_TOP_GAP_VOLUME_CLEAR, exit_fraction=1.0,
        wave_n_date=bars[reached["attack"]].timestamp.date().isoformat(),
        wave_reached_date=bars[reached["bar_index"]].timestamp.date().isoformat(),
        wave_reached_stage=reached["reached_stage"], wave_reached_price=reached["reached_target"],
        observed_open=bar.open, observed_close=bar.close, observed_volume=bar.volume,
        previous_open=previous.open, previous_close=previous.close,
        bearish_reference_date=reference.timestamp.date().isoformat(),
        bearish_reference_volume=reference.volume,
        execution_model="same_day_close",
    )


def _five_top_upper_shadow_evidence(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Freeze a prior live milestone and a dominant upper shadow on the observed candle."""
    if index < 1 or not projection_events:
        return None
    bar, previous = bars[index], bars[index - 1]
    span = Fraction(str(bar.high)) - Fraction(str(bar.low))
    upper = Fraction(str(bar.high)) - Fraction(str(max(bar.open, bar.close)))
    if span <= 0 or 2 * upper < span:
        return None
    reached = _known_five_top(sorted(projection_events, key=lambda event: event["bar_index"]), index)
    if reached is None:
        return None
    return dict(
        exit_fraction=1.0,
        wave_n_date=bars[reached["attack"]].timestamp.date().isoformat(),
        wave_reached_date=bars[reached["bar_index"]].timestamp.date().isoformat(),
        wave_reached_stage=reached["reached_stage"], wave_reached_price=reached["reached_target"],
        wave_upper_shadow_fraction=float(upper / span),
        observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
        observed_close=bar.close, observed_volume=bar.volume, previous_close=previous.close,
        execution_model="same_day_close",
    )


def observe_five_top_gap_upper_shadow_clear(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Clear a prior live five-top on a weak lower open with at least half-range upper shadow."""
    if index < 1 or bars[index].open >= bars[index - 1].close or bars[index].close > bars[index].open:
        return None
    evidence = _five_top_upper_shadow_evidence(bars, index, projection_events)
    return dict(evidence, reason=FIVE_TOP_GAP_UPPER_SHADOW_CLEAR) if evidence is not None else None


def observe_five_top_body_upper_shadow_clear(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Clear a body child with a long upper shadow, including a doji or bullish child."""
    if index < 1:
        return None
    mother, child = bars[index - 1], bars[index]
    mother_low, mother_high = sorted((Fraction(str(mother.open)), Fraction(str(mother.close))))
    child_low, child_high = sorted((Fraction(str(child.open)), Fraction(str(child.close))))
    if (child_low < mother_low or child_high > mother_high
            or (child_low == mother_low and child_high == mother_high)):
        return None
    evidence = _five_top_upper_shadow_evidence(bars, index, projection_events)
    if evidence is None:
        return None
    return dict(evidence, reason=FIVE_TOP_BODY_UPPER_SHADOW_CLEAR,
                mother_date=mother.timestamp.date().isoformat(), child_date=child.timestamp.date().isoformat(),
                previous_open=mother.open, mother_body_low=float(mother_low), mother_body_high=float(mother_high),
                child_body_low=float(child_low), child_body_high=float(child_high))


def observe_five_top_upper_shadow_clear(
    bars: Sequence[Bar], index: int, projection_events: Sequence[dict]
) -> dict | None:
    """Prefer the explicit body-child proof when both independently authorized shapes qualify."""
    return (observe_five_top_body_upper_shadow_clear(bars, index, projection_events)
            or observe_five_top_gap_upper_shadow_clear(bars, index, projection_events))


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
        decision = _observe_ordinary_c(bars, index, ordinary, config, reduced=reduced, entry_index=entry_index)
        if decision is not None and decision.get("exit_fraction") == 1.0:
            return decision
        shadow_clear = observe_five_top_upper_shadow_clear(bars, index, events)
        if shadow_clear is not None:
            return observe_five_top_gap_volume_clear(
                bars, index, sorted(events, key=lambda event: event["bar_index"])) or shadow_clear
        return decision
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
    shadow_clear = observe_five_top_upper_shadow_clear(bars, index, events)
    if shadow_clear is not None:
        # Retain the established full-clear reason when both gap rules apply.
        return observe_five_top_gap_volume_clear(
            bars, index, sorted(events, key=lambda event: event["bar_index"])) or shadow_clear
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
    """Freeze an ordinary A at entry and watch its C projection targets."""
    held_from = entry_index if entry_index is not None else entry["bar_index"]
    if index <= held_from or not entry["one_p"] <= entry["a_high"] < entry["two_t"]:
        return None
    c_0618_reduction = None
    target_0618 = entry.get("target_0618")
    if target_0618 is not None:
        reached_0618_index = next(
            (j for j in range(held_from + 1, index + 1) if bars[j].high >= target_0618), None
        )
        if reached_0618_index is not None:
            evidence = dict(
                wave_a_class="ordinary",
                wave_n_date=bars[entry["attack"]].timestamp.date().isoformat(),
                wave_reached_date=bars[reached_0618_index].timestamp.date().isoformat(),
                wave_reached_stage="c_0618",
                wave_reached_price=target_0618,
                wave_a_high=entry["a_high"],
                wave_b_low=entry["b_low"],
                wave_c_0618_target=target_0618,
                execution_model="same_day_close",
            )
            warning_index = next(
                (j for j in range(reached_0618_index, index)
                 if _c_0618_bearish_shadow(bars, j)), None
            )
            bar = bars[index]
            if (warning_index is not None and bar.close < bars[index - 1].close
                    and bar.low < bars[warning_index].low and bar.close < bars[warning_index].close):
                shadow_warning = bars[warning_index]
                return dict(
                    evidence, reason="wave_c_0618_shadow_break_clear", exit_fraction=1.0,
                    abnormal_date=shadow_warning.timestamp.date().isoformat(),
                    abnormal_low=shadow_warning.low, abnormal_close=shadow_warning.close,
                    observed_low=bar.low, observed_close=bar.close,
                    previous_close=bars[index - 1].close,
                )
            if not reduced and _c_0618_bearish_shadow(bars, index):
                span = bar.high - bar.low
                c_0618_reduction = dict(
                    evidence, reason="wave_c_0618_upper_shadow_reduce",
                    exit_fraction=config.wave_exhaustion_reduction,
                    exit_target_fraction=config.wave_exhaustion_reduction,
                    wave_upper_shadow_fraction=(bar.high - max(bar.open, bar.close)) / span,
                    observed_open=bar.open, observed_close=bar.close,
                    observed_low=bar.low, observed_high=bar.high,
                    previous_close=bars[index - 1].close,
                )
    reached_index = next((j for j in range(held_from + 1, index + 1) if bars[j].high >= entry["target"]), None)
    if reached_index is None:
        return c_0618_reduction
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
            return c_0618_reduction
    decision = _observe_target_candle(bars, index, [reached], config, reduced=reduced)
    if decision is not None and decision["exit_fraction"] == 1.0:
        return decision
    return c_0618_reduction or decision


def _c_0618_bearish_shadow(bars: list[Bar], index: int) -> bool:
    """Require a lower close and a dominant upper shadow after C reaches 0.618."""
    if index < 1:
        return False
    bar = bars[index]
    span = bar.high - bar.low
    return (span > 0 and bar.close < bars[index - 1].close
            and 2 * (bar.high - max(bar.open, bar.close)) >= span)


def _observe_target_candle(
    bars: list[Bar], index: int, events: list[dict], config: StrategyConfig, *, reduced: bool = False
) -> dict | None:
    """Caller supplies only this entry N's dated projection events.

    Reaching a target alone never sells. Bearish engulfing and failed bullish
    resistance clear regardless of volume or a previously filled reduction.
    """
    if index < 1:
        return None
    active: dict[tuple[int, int | None], tuple[dict, str]] = {}
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
    if stage in ("five_top", "ten_full") and reached["bar_index"] < index:
        child_break = _five_top_child_volume_break(bars, index)
        if child_break is not None:
            return dict(evidence, **child_break, reason=FIVE_TOP_CHILD_VOLUME_CLEAR, exit_fraction=1.0)
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


def _five_top_child_volume_break(bars: Sequence[Bar], index: int) -> dict | None:
    """The child two sessions ago may be bullish; compare volume to the last bearish bar."""
    if index < 3:
        return None
    mother, child, previous, bar = bars[index - 3:index + 1]
    if (
        mother.close == mother.open
        or child.high > mother.high
        or child.low < mother.low
        or (child.high == mother.high and child.low == mother.low)
        or bar.close >= bar.open
        or bar.low >= previous.low
        or bar.close >= previous.close
        or bar.low >= child.low
    ):
        return None
    reference = next((bars[j] for j in range(index - 1, -1, -1)
                      if bars[j].close < bars[j].open), None)
    if reference is None or reference.volume <= 0 or bar.volume <= reference.volume:
        return None
    return dict(
        mother_date=mother.timestamp.date().isoformat(), mother_high=mother.high, mother_low=mother.low,
        child_date=child.timestamp.date().isoformat(), child_high=child.high, child_low=child.low,
        child_close=child.close, previous_low=previous.low, observed_low=bar.low,
        bearish_reference_date=reference.timestamp.date().isoformat(), bearish_reference_volume=reference.volume,
    )
