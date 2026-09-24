"""Exit adverse candles after a resisted close break of a higher-level falling high."""

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


def _secondary_wave_setup(levels, known_by):
    """The confirmed source A/B points of an unfinished level-two up leg."""
    secondary = levels.get(2, ())
    if not secondary or secondary[-1]["kind"] != "L" or secondary[-1]["available_at"] > known_by:
        return None
    origin = secondary[-1]
    source = levels.get(1, ())
    highs = [p for p in source if p["kind"] == "H" and p["index"] > origin["index"]
             and p["available_at"] <= known_by]
    if not highs:
        return None
    high = max(highs, key=lambda p: (p["value"], -p["index"]))
    lows = [p for p in source if p["kind"] == "L" and p["index"] > high["index"]
            and p["available_at"] <= known_by]
    if not lows:
        return None
    pullback = min(lows, key=lambda p: (p["value"], p["index"]))
    if pullback["value"] <= origin["value"] or high["value"] <= origin["value"]:
        return None
    return origin, high, pullback, pullback["value"] + high["value"] - origin["value"]


def _both_long_shadows(bar):
    span = bar.high - bar.low
    if span <= 0:
        return False
    return (bar.high - max(bar.open, bar.close)) / span >= 0.3 and (
        min(bar.open, bar.close) - bar.low
    ) / span >= 0.3


def _secondary_wave_exhaustion_history(bars, history):
    """Close a resisted level-two C wave only after a known equal-leg target fails."""
    risks = {}
    state = None
    shadow_policy = ShadowPolicy(0.25)
    for i in range(1, len(bars)):
        setup = _secondary_wave_setup(history.get(i - 1, {}), i - 1)
        identity = tuple((p["index"], p["value"]) for p in setup[:3]) if setup else None
        if state is not None and identity != state["identity"]:
            state = None
        if setup is None:
            continue
        origin, high, pullback, target = setup
        bar, previous = bars[i], bars[i - 1]
        resistance = observe_resistance(
            previous, bar, attack_direction=Direction.UP, shadow_policy=shadow_policy
        ).detected is True
        if state is None:
            # An intraday break may precede the next day's close confirmation.
            if previous.high <= high["value"] < bar.high and resistance:
                state = dict(identity=identity, attack=i, resistance=[i])
            continue
        age = i - state["attack"]
        if age == 1:
            if bar.close > high["value"] and resistance:
                state["resistance"].append(i)
            else:
                state = None
        elif age == 2:
            if bar.high >= target and bar.close > high["value"] and _both_long_shadows(bar):
                state["indecision"] = i
            else:
                state = None
        elif age == 3:
            indecision = bars[state["indecision"]]
            if bar.close < bar.open and bar.close < min(indecision.low, high["value"]):
                span = indecision.high - indecision.low
                risks[i] = dict(
                    reason="secondary_wave_target_resistance_clear",
                    exit_fraction=1.0,
                    execution_model="same_day_close",
                    trend_level=2,
                    trend_origin_date=bars[origin["index"]].timestamp.date().isoformat(),
                    trend_origin_low=origin["value"],
                    trend_key_date=bars[high["index"]].timestamp.date().isoformat(),
                    trend_key_high=high["value"],
                    wave_b_date=bars[pullback["index"]].timestamp.date().isoformat(),
                    wave_b_low=pullback["value"],
                    wave_equal_target=target,
                    trend_attack_date=bars[state["attack"]].timestamp.date().isoformat(),
                    trend_resistance_dates=[bars[j].timestamp.date().isoformat() for j in state["resistance"]],
                    trend_indecision_date=indecision.timestamp.date().isoformat(),
                    trend_upper_shadow_fraction=(indecision.high - max(indecision.open, indecision.close)) / span,
                    trend_lower_shadow_fraction=(min(indecision.open, indecision.close) - indecision.low) / span,
                    trend_indecision_low=indecision.low,
                    observed_close=bar.close,
                    observed_low=bar.low,
                    previous_close=previous.close,
                    previous_low=previous.low,
                )
            state = None
        else:
            state = None
    return risks


def trend_flip_exit_history(bars, history):
    risks = {}
    for level in (3,):
        state = None
        attempted = set()
        for i in range(1, len(bars)):
            points = history.get(i - 1, {}).get(level, ())
            # A terminal confirmed high denotes the still-developing falling leg.
            key = points[-1] if points and points[-1]["kind"] == "H" else None
            identity = (key["index"], key["value"]) if key is not None else None
            if state is not None:
                old_key_present = any((p["index"], p["value"]) == state["identity"] for p in points)
                if not old_key_present or (key is not None and identity != state["identity"]):
                    state = None
            bar, prev = bars[i], bars[i - 1]
            resistance = (
                observe_resistance(prev, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5)).detected
                is True
            )
            if (
                state is None
                and key is not None
                and key["available_at"] < i
                and identity not in attempted
                and prev.close <= key["value"] < bar.close
            ):
                attempted.add(identity)
                state = dict(
                    identity=identity,
                    key=key,
                    attack=i,
                    resistance=[],
                    record=bar.high,
                    defense=min(bar.low, prev.close),
                )
            if state is None:
                continue
            attack = state["attack"]
            if i <= attack + 1 and resistance:
                state["resistance"].append(i)
            if i == attack + 1 and not state["resistance"]:
                state = None
                continue
            span = bar.high - bar.low
            upper = bar.high - max(bar.open, bar.close)
            adverse = []
            if bar.close < bar.open:
                adverse.append("bearish_body")
            if bar.close < prev.close:
                adverse.append("close_below_previous")
            if bar.low < prev.low:
                adverse.append("low_below_previous")
            if span > 0 and upper / span >= 0.5:
                adverse.append("long_upper_shadow")
            if state["resistance"] and adverse:
                risks[i] = dict(
                    reason="trend_flip_resistance_adverse_clear",
                    exit_fraction=1.0,
                    execution_model="same_day_close",
                    trend_level=level,
                    trend_key_date=bars[state["key"]["index"]].timestamp.date().isoformat(),
                    trend_key_high=state["key"]["value"],
                    trend_attack_date=bars[attack].timestamp.date().isoformat(),
                    trend_resistance_dates=[bars[j].timestamp.date().isoformat() for j in state["resistance"]],
                    trend_adverse_patterns=adverse,
                    observed_open=bar.open,
                    observed_close=bar.close,
                    observed_low=bar.low,
                    previous_close=prev.close,
                    previous_low=prev.low,
                    trend_upper_shadow_fraction=upper / span if span else 0,
                )
            # A clean renewed advance resolves resistance; a failed defense ends
            # this episode only after the failure candle has emitted its exit.
            resolved = (
                i >= attack + 2
                and not resistance
                and not adverse
                and bar.close > state["record"]
                and bar.low >= state["defense"]
            )
            if resolved or bar.close < state["key"]["value"] or bar.low < state["defense"]:
                state = None
            else:
                state["record"] = max(state["record"], bar.high)
    for index, risk in _secondary_wave_exhaustion_history(bars, history).items():
        risks.setdefault(index, risk)
    return risks
