"""Exit adverse candles after a resisted close break of a higher-level falling high."""

from collections.abc import Iterator, Sequence
from copy import deepcopy

from ..models.model import Bar
from ..market_structure.observation_window import observation_window_end
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from .history_cursor import HistoryCursor
from .breakout_shadow_exit import BREAKOUT_SHADOW_CLEAR, breakout_shadow_days


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


def _secondary_wave_exhaustion_days(bars, history, *, start_index=0, end_index=None):
    """Close a resisted level-two C wave only after a known equal-leg target fails."""
    risks = {}
    state = None
    shadow_policy = ShadowPolicy(0.25)
    end = observation_window_end(len(bars), start_index, end_index)
    for i in range(max(1, start_index), end + 1):
        yield i, risks
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
    yield end + 1, risks


def _last_fall_high_shadow_days(bars, history, reduction_fraction, level, *, start_index=0, end_index=None):
    risks = {}
    state = None
    attempted = set()
    end = observation_window_end(len(bars), start_index, end_index)
    for index in range(max(1, start_index), end + 1):
        yield index, risks
        points = history.get(index - 1, {}).get(level, ())
        # A confirmed low after the high identifies the last falling leg.
        key = (points[-2] if len(points) >= 2
               and points[-2]["kind"] == "H" and points[-1]["kind"] == "L" else None)
        identity = (key["index"], key["value"]) if key is not None else None
        if state is not None and state["warning"] is None and identity != state["identity"]:
            state = None
        bar, previous = bars[index], bars[index - 1]
        if (state is None and key is not None and key["available_at"] < index
                and identity not in attempted and previous.close <= key["value"] < bar.close):
            attempted.add(identity)
            state = dict(identity=identity, key=key, attack=index, warning=None)
        if state is None:
            continue
        if state["warning"] is not None and bar.close < previous.close:
            warning = state["warning"]
            risks[index] = dict(
                reason="trend_last_fall_high_lower_close_clear",
                exit_fraction=1.0,
                execution_model="same_day_close",
                trend_level=level,
                trend_key_date=bars[state["key"]["index"]].timestamp.date().isoformat(),
                trend_key_high=state["key"]["value"],
                trend_attack_date=bars[state["attack"]].timestamp.date().isoformat(),
                trend_attack_index=state["attack"],
                trend_warning_date=bars[warning].timestamp.date().isoformat(),
                trend_warning_index=warning,
                observed_close=bar.close,
                previous_close=previous.close,
            )
            state = None
            continue
        span = bar.high - bar.low
        upper = bar.high - max(bar.open, bar.close)
        if (span > 0 and upper > abs(bar.close - bar.open) and upper >= span / 3
                and bar.high > state["key"]["value"] and bar.close >= state["key"]["value"]):
            state["warning"] = index
            risks[index] = dict(
                reason="trend_last_fall_high_upper_shadow_reduce",
                exit_fraction=reduction_fraction,
                exit_target_fraction=reduction_fraction,
                execution_model="same_day_close",
                trend_level=level,
                trend_key_date=bars[state["key"]["index"]].timestamp.date().isoformat(),
                trend_key_high=state["key"]["value"],
                trend_attack_date=bars[state["attack"]].timestamp.date().isoformat(),
                trend_attack_index=state["attack"],
                trend_warning_date=bar.timestamp.date().isoformat(),
                trend_warning_index=index,
                trend_upper_shadow_fraction=upper / span,
                observed_open=bar.open,
                observed_high=bar.high,
                observed_low=bar.low,
                observed_close=bar.close,
                previous_close=previous.close,
            )
        elif bar.close < state["key"]["value"]:
            state = None
    yield end + 1, risks



def _trend_flip_resistance_days(bars, history, *, start_index=0, end_index=None):
    risks = {}
    level = 3
    state = None
    attempted = set()
    end = observation_window_end(len(bars), start_index, end_index)
    for i in range(max(1, start_index), end + 1):
        yield i, risks
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
    yield end + 1, risks


def _finish_history(rows: Iterator[tuple[int, dict[int, dict]]], count: int) -> dict[int, dict]:
    cursor = HistoryCursor(rows, {})
    try:
        return cursor.advance(count - 1)
    finally:
        cursor.close()


def _secondary_wave_exhaustion_history(bars, history):
    return _finish_history(_secondary_wave_exhaustion_days(bars, history), len(bars))


def _last_fall_high_shadow_history(bars, history, reduction_fraction):
    risks = {}
    for level in (2, 3):
        risks.update(_finish_history(_last_fall_high_shadow_days(bars, history, reduction_fraction, level), len(bars)))
    return risks


def trend_flip_exit_history(bars, history, *, reduction_fraction=0.8):
    """Batch and live accounts share the same causal reducers and precedence."""
    stream = TrendFlipExitStream(bars, reduction_fraction=reduction_fraction)
    try:
        return deepcopy(stream.advance(len(bars) - 1, history))
    finally:
        stream.close()


class TrendFlipExitStream:
    """Append-only owned history advances once; mutable public history is rechecked."""

    def __init__(
        self, bars: Sequence[Bar], *, reduction_fraction: float = 0.8,
        start_index: int = 0, end_index: int | None = None,
    ) -> None:
        self._bars = tuple(bars)
        self.start_index = start_index
        self.end_index = observation_window_end(len(self._bars), start_index, end_index)
        self._fraction = reduction_fraction
        self._history: dict[int, dict] = {}
        self._index = start_index - 1
        self._owner: object | None = None
        self._closed = False
        self._risks: dict[int, dict] = {}
        self._cursors: list[HistoryCursor[dict[int, dict]]] = []
        self._reset()

    def _reset(self) -> None:
        for cursor in self._cursors:
            cursor.close()
        self._cursors = [
            HistoryCursor(_last_fall_high_shadow_days(
                self._bars, self._history, self._fraction, level,
                start_index=self.start_index, end_index=self.end_index if self._bars else None,
            ), {})
            for level in (2, 3)
        ]
        self._cursors.extend((
            HistoryCursor(_trend_flip_resistance_days(
                self._bars, self._history, start_index=self.start_index,
                end_index=self.end_index if self._bars else None,
            ), {}),
            HistoryCursor(_secondary_wave_exhaustion_days(
                self._bars, self._history, start_index=self.start_index,
                end_index=self.end_index if self._bars else None,
            ), {}),
            HistoryCursor(breakout_shadow_days(
                self._bars, self._history, start_index=self.start_index,
                end_index=self.end_index if self._bars else None,
            ), {}),
        ))
        self._risks = {}
        self._index = self.start_index - 1

    @staticmethod
    def _row(levels: dict) -> dict:
        # Only these fields participate in this observer; complete trend proofs
        # remain owned by the hierarchy and are not copied into another cache.
        result = {level: tuple({key: point[key] for key in
                             ('kind', 'index', 'value', 'available_at', 'state', 'display_only') if key in point}
                             for point in levels.get(level, ())) for level in (1, 2, 3)}
        # 新规则只需要折线最近已确认高点，不能为它逐日复制全部折线历史。
        high = next((point for point in reversed(levels.get(0, ()))
                     if point.get('kind') == 'H' and point.get('state') not in ('seed', 'developing')
                     and point.get('display_only') is not True), None)
        result[0] = (() if high is None else ({key: high[key] for key in
                     ('kind', 'index', 'value', 'available_at', 'state', 'display_only') if key in high},))
        return result

    def advance(self, index: int, history: dict[int, dict], *, _history_owner: object | None = None) -> dict[int, dict]:
        if self._closed:
            raise ValueError('trend risk history stream is closed')
        minimum = self.start_index if self.start_index else -1
        if type(index) is not int or not minimum <= index <= self.end_index:
            raise ValueError('trend risk history requires a date inside its observation window')
        from .hierarchy_stream import HierarchyHistoryStream

        owner = (_history_owner if isinstance(_history_owner, HierarchyHistoryStream)
                 and history is _history_owner.history else None)
        owned = owner is not None and owner is self._owner and index >= self._index
        first_snapshot = max(0, self.start_index - 1)
        start = max(first_snapshot, self._index) if owned else first_snapshot
        changed = index < self._index
        for day in range(start, index):
            row = self._row(history.get(day, {}))
            if day < self._index and self._history.get(day) != row:
                changed = True
            self._history[day] = row
        if changed:
            self._reset()
        previous = self._index
        outcomes = [cursor.advance(index) for cursor in self._cursors]
        # Original batch precedence: level three shadow overrides level two;
        # terminal-high and secondary-wave exits are only fallbacks.
        for day in range(previous + 1, index + 1):
            risk = next((outcome[day] for outcome in (outcomes[1], outcomes[0], outcomes[2], outcomes[3])
                         if day in outcome), None)
            shadow = outcomes[4].get(day)
            if shadow is not None:
                full_clear = next((outcome[day] for outcome in outcomes[:4]
                                   if day in outcome and outcome[day]['exit_fraction'] == 1.0), None)
                if shadow['reason'] == BREAKOUT_SHADOW_CLEAR:
                    risk = shadow
                elif full_clear is not None:
                    risk = full_clear
                elif risk is None or risk['exit_fraction'] < shadow['exit_fraction']:
                    risk = shadow
            if risk is not None:
                self._risks[day] = risk
        self._index, self._owner = index, owner
        return self._risks

    def close(self) -> None:
        for cursor in self._cursors:
            cursor.close()
        self._history.clear()
        self._owner = None
        self._closed = True
