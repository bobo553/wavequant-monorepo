"""Causal candidate and breakout for a shallow higher-level pullback.

This entry does not promote a developing level-two or level-three point into a
formal alternation.  It uses yesterday's confirmed hierarchy and today's
completed bar as an independent, switchable research hypothesis.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from ..models.model import Bar


MIN_RETRACEMENT = Fraction(618, 1000)
MAX_RETRACEMENT = Fraction(2, 3)
MIN_BASE_SESSIONS = 40
MAX_BASE_SESSIONS = 120
BASE_WINDOW = 40
MAX_BASE_WIDTH = Fraction(18, 100)
VOLUME_WINDOW = 20
MIN_VOLUME_MULTIPLE = 2
MIN_BODY_OPEN = Fraction(5, 100)
MIN_BODY_RANGE = Fraction(3, 5)
MIN_CLOSE_LOCATION = Fraction(4, 5)


@dataclass(frozen=True)
class ShallowAlternationCandidate:
    trend_level: int
    origin_index: int
    origin_price: float
    flip_high_index: int
    flip_high_price: float
    pullback_index: int
    pullback_price: float
    known_index: int
    retracement: float

    @property
    def identity(self) -> tuple[int, int, int, int]:
        return self.trend_level, self.origin_index, self.flip_high_index, self.pullback_index


def _fraction(value: float) -> Fraction:
    return Fraction(str(value))


def shallow_candidate_from_geometry(
    levels: Sequence[tuple[dict, dict]],
    dates: Mapping[str, int],
    now: int,
) -> ShallowAlternationCandidate | None:
    """Use the same prefix-confirmed higher A and source-level B as chart entry."""
    from ..market_structure.squeeze_alternation import squeeze_anchors

    candidates = []
    for level, source in levels:
        strokes = {stroke["id"]: stroke for stroke in level["strokes"]}
        source_strokes = {stroke["id"]: stroke for stroke in source["strokes"]}
        for anchor in squeeze_anchors(level, dates, source):
            origin, high = anchor["origin"], anchor["high"]
            path = strokes.get(anchor["source_path"])
            source_path = source_strokes.get(path["source_path"]) if path is not None else None
            if source_path is None or anchor["known_index"] > now:
                continue
            origin_index, high_index = origin["index"], high["index"]
            pullbacks = [
                point
                for point in source_path["points"]
                if point["kind"] == "L"
                and high_index < point["index"] <= now
                and point.get("state") != "developing"
                and not point.get("display_only")
                and dates[point["available_at"]] <= now
            ]
            if not pullbacks:
                continue
            pullback = min(pullbacks, key=lambda point: (point["value"], point["index"]))
            low, peak, counter = (_fraction(point["value"]) for point in (origin, high, pullback))
            if not low < counter < peak or 2 * (pullback["index"] - high_index) >= high_index - origin_index:
                continue
            ratio = (peak - counter) / (peak - low)
            if MIN_RETRACEMENT <= ratio < MAX_RETRACEMENT:
                candidates.append(
                    ShallowAlternationCandidate(
                        anchor["trend_level"],
                        origin_index,
                        float(low),
                        high_index,
                        float(peak),
                        pullback["index"],
                        float(counter),
                        max(anchor["known_index"], dates[pullback["available_at"]]),
                        float(ratio),
                    )
                )
    return max(candidates, key=lambda item: (item.flip_high_index, item.trend_level), default=None)


def shallow_base_history(
    bars: Sequence[Bar],
    candidates: Mapping[int, ShallowAlternationCandidate | None],
    *,
    minimum_reward_risk: float,
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    """Return dated pending-low events and first qualified breakout per episode."""
    events: list[dict[str, object]] = []
    breakouts: dict[int, dict[str, object]] = {}
    seen_candidates: set[tuple[int, int, int, int]] = set()
    used_candidates: set[tuple[int, int, int, int]] = set()
    for now in range(1, len(bars)):
        current = candidates.get(now)
        if current is not None and current.identity not in seen_candidates:
            seen_candidates.add(current.identity)
            events.append(
                dict(
                    bar_index=now,
                    event="shallow_alternation_candidate",
                    trend_level=current.trend_level,
                    origin_index=current.origin_index,
                    flip_high_index=current.flip_high_index,
                    pullback_index=current.pullback_index,
                    pullback_price=current.pullback_price,
                    retracement=current.retracement,
                    status="pending_base_breakout_not_formal_alternation",
                )
            )
        candidate = candidates.get(now - 1)
        if candidate is None or candidate.identity in used_candidates:
            continue
        pullback_index = candidate.pullback_index
        base_sessions = now - pullback_index - 1
        if not MIN_BASE_SESSIONS <= base_sessions <= MAX_BASE_SESSIONS:
            continue
        if min(bar.low for bar in bars[pullback_index : now + 1]) < candidate.pullback_price:
            continue
        base = bars[now - BASE_WINDOW : now]
        base_high = max(bar.high for bar in base)
        base_low = min(bar.low for bar in base)
        if (_fraction(base_high) - _fraction(base_low)) / _fraction(base_low) > MAX_BASE_WIDTH:
            continue
        bar, previous = bars[now], bars[now - 1]
        if bar.close <= max(base_high, *(item.high for item in bars[pullback_index + 1 : now])):
            continue
        spread = bar.high - bar.low
        body = bar.close - bar.open
        if (
            body <= 0
            or spread <= 0
            or _fraction(body) / _fraction(bar.open) < MIN_BODY_OPEN
            or _fraction(body) / _fraction(spread) < MIN_BODY_RANGE
            or _fraction(bar.close - bar.low) / _fraction(spread) < MIN_CLOSE_LOCATION
        ):
            continue
        average_volume = sum(item.volume for item in bars[now - VOLUME_WINDOW : now]) / VOLUME_WINDOW
        if average_volume <= 0 or bar.volume < MIN_VOLUME_MULTIPLE * average_volume or bar.volume <= previous.volume:
            continue
        stop, target = base_low, candidate.flip_high_price
        if not stop < bar.close < target:
            continue
        gross_reward_risk = (target - bar.close) / (bar.close - stop)
        if gross_reward_risk < minimum_reward_risk:
            continue
        used_candidates.add(candidate.identity)
        breakouts[now] = dict(
            buy_point_type="shallow_base_breakout",
            trend_level=candidate.trend_level,
            origin_index=candidate.origin_index,
            origin_price=candidate.origin_price,
            flip_high_index=candidate.flip_high_index,
            flip_high_price=candidate.flip_high_price,
            alternation_low_index=pullback_index,
            alternation_low_price=candidate.pullback_price,
            candidate_known_index=candidate.known_index,
            counter_ratio=candidate.retracement,
            counter_limit=float(MIN_RETRACEMENT),
            base_start_index=now - BASE_WINDOW,
            base_end_index=now - 1,
            base_sessions=base_sessions,
            base_high=base_high,
            base_low=base_low,
            base_width_fraction=(base_high - base_low) / base_low,
            breakout_close=bar.close,
            breakout_body_fraction=body / bar.open,
            breakout_body_range_fraction=body / spread,
            breakout_volume=bar.volume,
            previous_volume=previous.volume,
            base_volume_average=average_volume,
            breakout_volume_multiple=bar.volume / average_volume,
            stop=stop,
            target=target,
            gross_reward_risk=gross_reward_risk,
            attack=now,
            squeeze_confirmation="shallow_base_breakout",
        )
    return events, breakouts
