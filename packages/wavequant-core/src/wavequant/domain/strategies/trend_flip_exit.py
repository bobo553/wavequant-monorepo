"""Exit adverse candles after a known falling high is broken."""

from collections.abc import Sequence
from typing import TypedDict

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


class _LastFallHighPoint(TypedDict):
    index: int
    kind: str
    value: float
    available_at: int


class _LastFallHighState(TypedDict):
    identity: tuple[int, float]
    key: _LastFallHighPoint
    attack: int
    warning: int | None


def _source_last_fall_high(levels: dict[int, Sequence[_LastFallHighPoint]]) -> _LastFallHighPoint | None:
    """High between the latest two new lows of an unfinished first-level fall."""
    first = levels.get(1, ())
    anchor = next((point for point in reversed(first) if point["kind"] == "H"), None)
    if anchor is None:
        return None
    source = [point for point in levels.get(0, ()) if point["index"] > anchor["index"]]
    record_lows = []
    lowest = float("inf")
    for point in source:
        if point["kind"] == "L" and point["value"] < lowest:
            record_lows.append(point)
            lowest = point["value"]
    if len(record_lows) < 2:
        return None
    left, right = record_lows[-2:]
    highs = [point for point in source if point["kind"] == "H"
             and left["index"] < point["index"] < right["index"]]
    return max(highs, key=lambda point: point["value"], default=None)


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


def _last_fall_high_shadow_history(
    bars: list[Bar], history: dict[int, dict[int, Sequence[_LastFallHighPoint]]], reduction_fraction: float
) -> dict[int, dict]:
    """Stage exits from known source, second-level, or third-level falling highs."""
    risks: dict[int, dict] = {}
    for level in (0, 2, 3):
        state: _LastFallHighState | None = None
        for index in range(1, len(bars)):
            levels = history.get(index - 1, {})
            points = levels.get(level, ())
            # A confirmed low after the high identifies the last falling leg.
            key = (_source_last_fall_high(levels) if level == 0 else
                   points[-2] if len(points) >= 2
                   and points[-2]["kind"] == "H" and points[-1]["kind"] == "L" else None)
            identity = (key["index"], key["value"]) if key is not None else None
            if state is not None and state["warning"] is None and identity != state["identity"]:
                state = None
            bar, previous = bars[index], bars[index - 1]
            crossed = (previous.high <= key["value"] < bar.high if level == 0 else
                       previous.close <= key["value"] < bar.close) if key is not None else False
            if (state is None and key is not None and key["available_at"] < index
                    and crossed):
                state = _LastFallHighState(
                    identity=(key["index"], key["value"]), key=key, attack=index, warning=None
                )
            if state is None:
                continue
            if bar.close < previous.close and bar.low < previous.low:
                warning = state["warning"] if state["warning"] is not None else index
                risks[index] = dict(
                    reason="trend_last_fall_high_breakdown_clear",
                    exit_fraction=1.0,
                    execution_model="same_day_close",
                    trend_level=level,
                    trend_key_date=bars[state["key"]["index"]].timestamp.date().isoformat(),
                    trend_key_high=state["key"]["value"],
                    trend_attack_date=bars[state["attack"]].timestamp.date().isoformat(),
                    trend_attack_index=state["attack"],
                    trend_breakout_basis="high" if level == 0 else "close",
                    trend_warning_date=bars[warning].timestamp.date().isoformat(),
                    trend_warning_index=index,
                    trend_prior_warning_index=warning,
                    observed_close=bar.close,
                    observed_low=bar.low,
                    previous_close=previous.close,
                    previous_low=previous.low,
                )
                state = None
                continue
            span = bar.high - bar.low
            upper = bar.high - max(bar.open, bar.close)
            long_upper = span > 0 and upper > abs(bar.close - bar.open) and upper >= span / 3
            bearish_body = bar.close < bar.open
            upper_warning = long_upper
            if (bearish_body or upper_warning) and risks.get(index, {}).get("exit_fraction", 0) < 1:
                if state["warning"] is None:
                    state["warning"] = index
                risks[index] = dict(
                    reason=("trend_last_fall_high_bearish_reduce" if bearish_body
                            else "trend_last_fall_high_upper_shadow_reduce"),
                    exit_fraction=reduction_fraction,
                    exit_target_fraction=reduction_fraction,
                    execution_model="same_day_close",
                    trend_level=level,
                    trend_key_date=bars[state["key"]["index"]].timestamp.date().isoformat(),
                    trend_key_high=state["key"]["value"],
                    trend_attack_date=bars[state["attack"]].timestamp.date().isoformat(),
                    trend_attack_index=state["attack"],
                    trend_breakout_basis="high" if level == 0 else "close",
                    trend_warning_date=bar.timestamp.date().isoformat(),
                    trend_warning_index=index,
                    trend_upper_shadow_fraction=upper / span,
                    trend_adverse_patterns=(["bearish_body"] if bearish_body else [])
                    + (["long_upper_shadow"] if upper_warning else []),
                    observed_open=bar.open,
                    observed_high=bar.high,
                    observed_low=bar.low,
                    observed_close=bar.close,
                    previous_close=previous.close,
                    previous_low=previous.low,
                )
            elif (state["warning"] is None and index > state["attack"]
                  and bar.close < state["key"]["value"] and bar.high <= state["key"]["value"]):
                state = None
    return risks


def trend_flip_exit_history(bars, history, *, reduction_fraction=0.8):
    risks = _last_fall_high_shadow_history(bars, history, reduction_fraction)
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
                risks.setdefault(i, dict(
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
                ))
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
