"""Exit adverse candles after a resisted close break of a higher-level falling high."""

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


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
    return risks
