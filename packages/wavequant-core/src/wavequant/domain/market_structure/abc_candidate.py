"""Observe tertiary ABC transitions without treating a candidate as an order.

The B endpoint is its first lowest low before the positive N attack, never
the later squeeze confirmation. Prices and durations are frozen at that N;
subsequent price action creates separate dated breakout/invalidation events.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import NotRequired, TypedDict

from ..models.model import Bar


@dataclass(frozen=True)
class AbcAnchor:
    origin_index: int
    high_index: int
    known_index: int
    origin_price: float
    high_price: float
    source_path: str


class PullbackEvidence(TypedDict):
    deep_price_path: NotRequired[bool]
    a_origin_index: int
    a_high_index: int
    b_low_index: int
    a_origin_price: float
    a_high_price: float
    b_low_price: float
    b_minimum_close: float
    b_minimum_close_index: int
    two_thirds_price: float
    half_price: float
    retracement_ratio: float
    a_duration: int
    b_duration: int
    duration_unit: str
    price_path: bool
    time_path: bool


class AbcObservation(PullbackEvidence):
    trend_level: int
    source_path: str
    attack: int
    n_origin: int
    n_pullback: int
    candidate_index: int
    regime: str
    event: str
    bar_index: int
    breakout_index: NotRequired[int]


def abc_pullback_evidence(
    bars: Sequence[Bar], anchor: AbcAnchor, end: int, *, allow_deep_pullback: bool = False,
) -> PullbackEvidence | None:
    """Return exact inclusive price thresholds and strict trading-bar duration."""
    start, peak = anchor.origin_index, anchor.high_index
    if not 0 <= start < peak < end < len(bars):
        return None
    low, high = Fraction(str(anchor.origin_price)), Fraction(str(anchor.high_price))
    if high <= low:
        return None
    b_index = min(range(peak + 1, end + 1), key=lambda i: bars[i].low)
    minimum = Fraction(str(bars[b_index].low))
    if minimum <= low or minimum >= high:
        return None
    # The A-high candle belongs to A; its own long wick must not manufacture
    # a below-half closing observation in the subsequent B correction.
    close_index = min(range(peak + 1, b_index + 1), key=lambda i: bars[i].close)
    close = Fraction(str(bars[close_index].close))
    two_thirds = high - (high - low) * Fraction(2, 3)
    half = high - (high - low) / 2
    a_bars, b_bars = peak - start, b_index - peak
    price_path = minimum >= two_thirds
    time_path = b_bars > a_bars and close < half
    # Existing price/time paths keep their original confirmation semantics.
    deep_price_path = allow_deep_pullback and minimum <= two_thirds and not (price_path or time_path)
    if not (price_path or time_path or deep_price_path):
        return None
    return dict(
        a_origin_index=start, a_high_index=peak, b_low_index=b_index,
        a_origin_price=float(low), a_high_price=float(high), b_low_price=float(minimum),
        b_minimum_close=float(close), b_minimum_close_index=close_index,
        two_thirds_price=float(two_thirds), half_price=float(half),
        retracement_ratio=float((high - minimum) / (high - low)),
        a_duration=a_bars, b_duration=b_bars, duration_unit="trading_bars",
        price_path=price_path, time_path=time_path,
        **({'deep_price_path': True} if deep_price_path else {}),
    )


def _index(event: Mapping[str, object], name: str) -> int | None:
    value = event.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def tertiary_abc_observations(
    bars: Sequence[Bar], anchors: Sequence[AbcAnchor], audit: Sequence[Mapping[str, object]],
    *, allow_deep_pullback: bool = False,
) -> list[AbcObservation]:
    """Join confirmed positive Ns to squeeze evidence, independently of LONG filters.

    At most one live C candidate per A. Breaking B invalidates that candidate;
    a subsequent new B/N can be considered. A close above A confirms C, after
    which later Ns belong to a different phase. OHLC ambiguity favors failure.
    """
    positive = {
        event["bar_index"]: event for event in audit
        if event.get("event") == "n_completed" and event.get("direction") == "up"
        and _index(event, "bar_index") is not None
    }
    triggers = []
    for event in audit:
        if event.get("event") == "regime_confirmation" and event.get("regime") in ("轧空", "强轧空"):
            regime = event["regime"]
        elif event.get("event") == "squeeze_resumption_observed":
            regime = "轧空"
        else:
            continue
        attack, confirmed = _index(event, "attack"), _index(event, "bar_index")
        if attack is not None and confirmed is not None and attack in positive:
            triggers.append((confirmed, attack, str(regime), positive[attack]))
    triggers.sort(key=lambda item: (item[0], item[1]))
    observations: list[AbcObservation] = []
    for anchor in anchors:
        busy_until = -1
        used_lows: set[int] = set()
        for confirmed, attack, regime, n in triggers:
            origin, pullback = _index(n, "origin"), _index(n, "pullback")
            known = _index(n, "known_at")
            if (origin is None or pullback is None or known is None
                    or not 0 <= anchor.high_index < origin < pullback <= attack <= confirmed < len(bars)
                    or (pullback == attack and n.get("outside_close_confirmed") is not True)
                    or anchor.known_index > attack or known > confirmed or confirmed <= busy_until):
                continue
            proof = abc_pullback_evidence(bars, anchor, pullback, allow_deep_pullback=allow_deep_pullback)
            if proof is None:
                continue
            b_index = proof["b_low_index"]
            b_price = proof["b_low_price"]
            if b_index in used_lows or b_index > origin:
                continue
            # An intervening lower low retires this B/N chain. A prior A-high
            # breakout cannot be relabeled as a new pre-breakout C candidate.
            if (min(bar.low for bar in bars[b_index:confirmed + 1]) < b_price
                    or any(bar.close > anchor.high_price for bar in bars[b_index:attack])):
                continue
            used_lows.add(b_index)
            common: AbcObservation = {
                **proof, "trend_level": 3, "source_path": anchor.source_path,
                "attack": attack, "n_origin": origin, "n_pullback": pullback,
                "candidate_index": confirmed, "regime": regime,
                "event": "tertiary_c_candidate", "bar_index": confirmed,
            }
            observations.append(common)
            busy_until = len(bars)
            # Squeeze confirmation may lag the attack. A close above A in
            # that interval is already known when B is confirmed; do not lose
            # it and later misclassify a B breach as pre-breakout failure.
            earlier_break = next((j for j in range(attack, confirmed + 1)
                                  if bars[j].close > anchor.high_price), None)
            if earlier_break is not None:
                observations.append({**common, "event": "tertiary_c_breakout", "bar_index": confirmed,
                                     "breakout_index": earlier_break})
                break
            for i in range(confirmed, len(bars)):
                if bars[i].low < b_price:
                    observations.append({**common, "event": "tertiary_c_invalidated", "bar_index": i})
                    busy_until = i
                    break
                if bars[i].close > anchor.high_price:
                    observations.append({**common, "event": "tertiary_c_breakout", "bar_index": i,
                                         "breakout_index": i})
                    break
            if busy_until == len(bars):
                break
    return sorted(observations, key=lambda event: event["bar_index"])
