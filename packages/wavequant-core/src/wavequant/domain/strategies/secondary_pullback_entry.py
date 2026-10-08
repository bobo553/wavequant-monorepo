"""Observe a deep B recovery after a causally upgraded secondary A high."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from math import isfinite
from typing import cast

from ..market_structure.hierarchical_confirmation import session_date
from ..models.model import Bar


DIRECT_PROMOTION_RULE = "level1_confirmed_high_breaks_known_level2_last_fall_high"


@dataclass(frozen=True)
class _Point:
    index: int
    price: float
    kind: str
    known_index: int


@dataclass(frozen=True)
class SecondaryPullbackCandidate:
    """An observed base-line B, never an invented formal secondary low or N."""

    origin_index: int
    origin_price: float
    origin_known_index: int
    high_index: int
    high_price: float
    high_known_index: int
    key_index: int
    key_price: float
    key_known_index: int
    resistance_index: int
    resistance_price: float
    resistance_known_index: int
    low_index: int
    low_price: float
    low_known_index: int
    retracement_ratio: float
    source_path: str
    source_base_path: str

    @property
    def known_index(self) -> int:
        return max(self.origin_known_index, self.high_known_index, self.key_known_index,
                   self.resistance_known_index, self.low_known_index)

    @property
    def episode(self) -> tuple[str, int, float, int, float]:
        return self.source_path, self.origin_index, self.origin_price, self.high_index, self.high_price

    @property
    def observation(self) -> tuple[str, int, float, int, float, int, float]:
        return (*self.episode, self.low_index, self.low_price)


def _mapping(value: object) -> Mapping[str, object] | None:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else None


def _sequence(value: object) -> Sequence[object]:
    return cast(Sequence[object], value) if isinstance(value, (list, tuple)) else ()


def _point(raw: object, bars: Sequence[Bar], dates: Mapping[str, int], now: int) -> _Point | None:
    point = _mapping(raw)
    if point is None or point.get("state") in ("seed", "developing") or point.get("display_only"):
        return None
    index, kind, price, known = (point.get(name) for name in ("index", "kind", "value", "available_at"))
    if (type(index) is not int or not isinstance(kind, str) or kind not in ("H", "L")
            or not isinstance(price, (int, float)) or isinstance(price, bool) or not isfinite(price)):
        return None
    known_index = dates.get(known) if isinstance(known, str) else known if type(known) is int else None
    if known_index is None or not 0 <= index <= known_index <= now:
        return None
    bar = bars[index]
    if float(price) != (bar.high if kind == "H" else bar.low):
        return None
    return _Point(index, float(price), kind, known_index)


def secondary_pullback_candidates(
    bars: Sequence[Bar], drawing: Mapping[str, object], second: Mapping[str, object],
    dates: Mapping[str, int], now: int, *, first: Mapping[str, object],
) -> tuple[SecondaryPullbackCandidate, ...]:
    """Freeze known H -> L evidence using only prices in the requested prefix.

    The parent A must be a formal direct upgrade over an already-known level-2
    key. The base-line H was known before the B price date, and B is the actual
    deepest low after A. Deep retracement does not grant a new N or alternation.
    """
    if not 0 <= now < len(bars):
        return ()
    candidates: list[SecondaryPullbackCandidate] = []
    first_paths = [_mapping(raw) for raw in _sequence(first.get("strokes"))]
    for raw_path in _sequence(second.get("strokes")):
        path = _mapping(raw_path)
        if path is None or path.get("display_only") or not isinstance(path.get("id"), str):
            continue
        source = next((item for item in first_paths if item is not None
                       and item.get("id") == path.get("source_path") and not item.get("display_only")), None)
        if source is None:
            continue
        declared_paths = _sequence(source.get("source_paths"))
        base_paths = {value for value in declared_paths if isinstance(value, str)}
        single_path = source.get("source_path")
        if not base_paths and isinstance(single_path, str):
            base_paths.add(single_path)
        source_points = [_point(raw, bars, dates, now) for raw in _sequence(source.get("points"))]
        raw_points = _sequence(path.get("points"))
        for raw_origin, raw_high in zip(raw_points, raw_points[1:]):
            high_data = _mapping(raw_high)
            if high_data is None or high_data.get("confirmation_rule") != DIRECT_PROMOTION_RULE:
                continue
            origin = _point(raw_origin, bars, dates, now)
            high = _point(raw_high, bars, dates, now)
            key = _point(high_data.get("broken_key"), bars, dates, now)
            if (origin is None or high is None or key is None or origin.kind != "L" or high.kind != "H"
                    or key.kind != "H" or not key.index < origin.index < high.index < now
                    or key.known_index >= high.index or high.price <= key.price or high.price <= origin.price):
                continue
            if not all(any(point is not None and (point.index, point.kind, point.price) ==
                           (endpoint.index, endpoint.kind, endpoint.price) for point in source_points)
                       for endpoint in (origin, high)):
                continue
            if min(bars[index].low for index in range(origin.index + 1, now + 1)) < origin.price:
                continue
            if max(bars[index].high for index in range(high.index + 1, now + 1)) > high.price:
                continue
            bottom = min(range(high.index + 1, now + 1), key=lambda index: (bars[index].low, index))
            amplitude = Fraction(str(high.price)) - Fraction(str(origin.price))
            retraced = Fraction(str(high.price)) - Fraction(str(bars[bottom].low))
            if not amplitude * Fraction(2, 3) <= retraced < amplitude:
                continue
            for raw_base in _sequence(drawing.get("strokes")):
                base = _mapping(raw_base)
                if (base is None or base.get("display_only") or not isinstance(base.get("id"), str)
                        or base["id"] not in base_paths):
                    continue
                base_points = _sequence(base.get("points"))
                for raw_resistance, raw_low in zip(base_points, base_points[1:]):
                    resistance = _point(raw_resistance, bars, dates, now)
                    low = _point(raw_low, bars, dates, now)
                    if (resistance is None or low is None or resistance.kind != "H" or low.kind != "L"
                            or not high.index < resistance.index < low.index == bottom
                            or resistance.known_index >= low.index or resistance.price <= low.price
                            or resistance.price >= high.price):
                        continue
                    candidates.append(SecondaryPullbackCandidate(
                        origin.index, origin.price, origin.known_index,
                        high.index, high.price, high.known_index, key.index, key.price, key.known_index,
                        resistance.index, resistance.price, resistance.known_index,
                        low.index, low.price, low.known_index, float(retraced / amplitude),
                        cast(str, path["id"]), base["id"]))
    return tuple(candidates)


def _evidence(candidate: SecondaryPullbackCandidate, bars: Sequence[Bar]) -> dict[str, object]:
    def date(index: int) -> str:
        return session_date(bars[index])

    return dict(
        definition="secondary_direct_upgrade_deep_pullback_reclaim_v1",
        buy_point_type="secondary_deep_pullback_reclaim", trend_level=2,
        observed_low=True, formal_alternation=False, requires_positive_n=False,
        origin_index=candidate.origin_index, flip_high_index=candidate.high_index,
        secondary_origin_index=candidate.origin_index, secondary_origin_date=date(candidate.origin_index),
        secondary_origin_low=candidate.origin_price,
        secondary_origin_known_date=date(candidate.origin_known_index),
        secondary_high_index=candidate.high_index, secondary_high_date=date(candidate.high_index),
        secondary_high=candidate.high_price, secondary_high_known_date=date(candidate.high_known_index),
        secondary_high_confirmation_rule=DIRECT_PROMOTION_RULE,
        secondary_key_index=candidate.key_index, secondary_key_date=date(candidate.key_index),
        secondary_key_high=candidate.key_price, secondary_key_known_date=date(candidate.key_known_index),
        secondary_resistance_index=candidate.resistance_index,
        secondary_resistance_date=date(candidate.resistance_index), secondary_resistance_high=candidate.resistance_price,
        secondary_resistance_known_date=date(candidate.resistance_known_index),
        secondary_pullback_low_index=candidate.low_index, secondary_pullback_low_date=date(candidate.low_index),
        secondary_pullback_low=candidate.low_price, secondary_pullback_low_known_date=date(candidate.low_known_index),
        secondary_observation_known_date=date(candidate.known_index),
        source_path=candidate.source_path, source_base_path=candidate.source_base_path,
        counter_ratio=candidate.retracement_ratio, stop=candidate.low_price, target=candidate.high_price,
    )


def secondary_pullback_history(
    bars: Sequence[Bar], candidates: Mapping[int, Sequence[SecondaryPullbackCandidate]],
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    """Publish dated recovery proofs, leaving actual entry permission to trading.

    Confirmation consumes only the previous day's candidate. A failed B may be
    replaced by a newly known deeper B while A's origin holds. A proof does not
    consume A: global risks can reject it, and only an actual LONG retires it.
    """
    events: list[dict[str, object]] = []
    proofs: dict[int, dict[str, object]] = {}
    retired: set[tuple[str, int, float, int, float]] = set()
    observed: set[tuple[str, int, float, int, float, int, float]] = set()
    invalidated_observations: set[tuple[str, int, float, int, float, int, float]] = set()
    for now, bar in enumerate(bars):
        if now:
            known = [candidate for candidate in candidates.get(now - 1, ())
                     if candidate.known_index < now and candidate.episode not in retired
                     and candidate.observation not in invalidated_observations]
            for candidate in sorted(known, key=lambda item: (item.high_index, item.low_index), reverse=True):
                reason = ("secondary_origin_low_broken" if bar.low < candidate.origin_price else
                          "secondary_a_new_high" if bar.high > candidate.high_price else
                          "observed_b_low_broken" if bar.low < candidate.low_price else None)
                if reason is not None:
                    events.append(dict(_evidence(candidate, bars), bar_index=now,
                                       event="secondary_pullback_invalidated", reason=reason))
                    if reason != "observed_b_low_broken":
                        retired.add(candidate.episode)
                    else:
                        invalidated_observations.add(candidate.observation)
                    continue
                if bar.high >= candidate.high_price:
                    continue
                previous = bars[now - 1]
                ceiling_index = max(range(candidate.resistance_index, now), key=lambda index: bars[index].high)
                ceiling = max(candidate.resistance_price, bars[ceiling_index].high)
                body = Fraction(str(bar.close)) - Fraction(str(bar.open))
                opening = Fraction(str(bar.open))
                span = Fraction(str(bar.high)) - Fraction(str(bar.low))
                unfilled_gap = bar.open > previous.high and bar.low > previous.high
                body_reclaim = body > opening * Fraction(2, 100) and body >= span / 2
                gap_reclaim = (unfilled_gap and bar.open > ceiling
                               and body >= opening * Fraction(3, 100) and body >= span * Fraction(3, 5))
                if (previous.volume <= 0 or bar.volume <= previous.volume or bar.close <= ceiling
                        or not (body_reclaim or gap_reclaim) or bar.close <= candidate.low_price):
                    continue
                proof = dict(_evidence(candidate, bars), bar_index=now,
                             reclaim_type="gap" if gap_reclaim else "body", confirmation_close=bar.close,
                             reclaim_body_fraction=float(body / opening),
                             reclaim_body_range_fraction=float(body / span) if span > 0 else 0.0,
                             unfilled_gap=unfilled_gap, previous_volume=previous.volume, breakout_volume=bar.volume,
                             breakout_volume_multiple=bar.volume / previous.volume,
                             reclaim_ceiling=ceiling, reclaim_ceiling_date=session_date(bars[ceiling_index]),
                             gross_reward_risk=(candidate.high_price - bar.close) / (bar.close - candidate.low_price))
                proofs[now] = proof
                events.append(dict(proof, event="secondary_pullback_candidate"))
                break
        for candidate in candidates.get(now, ()):
            if (candidate.known_index > now or candidate.episode in retired or candidate.observation in observed
                    or candidate.observation in invalidated_observations):
                continue
            observed.add(candidate.observation)
            events.append(dict(_evidence(candidate, bars), bar_index=now, event="secondary_pullback_observed"))
    return events, proofs
