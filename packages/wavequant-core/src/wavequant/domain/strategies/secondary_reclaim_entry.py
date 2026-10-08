"""Causal recovery of supply observed on a secondary breakout and its next bar."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


@dataclass(frozen=True)
class SecondaryHigh:
    """A formal secondary high and its real publication session."""

    index: int
    price: float
    known_index: int


@dataclass
class _Episode:
    key: SecondaryHigh
    attack: int
    origin: int
    resistance: bool
    ceiling: float
    ceiling_index: int
    bottom: int
    observed_peak: float


def secondary_reclaim_history(
    bars: Sequence[Bar], highs: Mapping[int, Sequence[SecondaryHigh]],
) -> tuple[list[dict[str, object]], dict[int, dict[str, object]]]:
    """Observe candidates without granting an exception to global entry risks.

    Only the attack and next session define this resistance. A later lower low
    may update the actual pullback stop while the pre-attack origin still holds.
    Consuming the previous session's formal highs prevents future endpoint use.
    """
    events: list[dict[str, object]] = []
    proofs: dict[int, dict[str, object]] = {}
    episode: _Episode | None = None
    attacked: set[tuple[int, float, int]] = set()
    for now in range(1, len(bars)):
        bar, previous = bars[now], bars[now - 1]
        known = [point for point in highs.get(now - 1, ())
                 if 0 <= point.index <= point.known_index < now]
        key = max(known, key=lambda point: (point.index, point.known_index), default=None)
        if episode is not None and bar.low < bars[episode.origin].low:
            events.append(dict(bar_index=now, event="secondary_reclaim_invalidated",
                               secondary_attack_index=episode.attack, reason="secondary_origin_low_broken"))
            episode = None
        if episode is None and key is not None:
            identity = (key.index, key.price, key.known_index)
            if identity not in attacked and previous.high <= key.price < bar.high:
                attacked.add(identity)
                if key.index + 1 >= now:
                    continue
                origin = min(range(key.index + 1, now), key=lambda index: (bars[index].low, index))
                if bars[origin].low >= key.price or bar.low < bars[origin].low:
                    continue
                resisted = observe_resistance(previous, bar, attack_direction=Direction.UP,
                                              shadow_policy=ShadowPolicy(.5)).detected is True
                episode = _Episode(key, now, origin, resisted, bar.high, now, now, bar.high)
                events.append(dict(bar_index=now, event="secondary_reclaim_breakout_observed",
                                   secondary_high_date=bars[key.index].timestamp.date().isoformat(),
                                   secondary_high=key.price, secondary_high_known_index=key.known_index,
                                   secondary_attack_index=now))
        if episode is None:
            continue
        if bar.low < bars[episode.bottom].low:
            episode.bottom = now
        episode.observed_peak = max(episode.observed_peak, bar.high)
        if now == episode.attack + 1:
            episode.resistance |= observe_resistance(
                previous, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5)).detected is True
            if bar.high > episode.ceiling:
                episode.ceiling, episode.ceiling_index = bar.high, now
            if not episode.resistance:
                episode = None
                continue
            events.append(dict(bar_index=now, event="secondary_reclaim_resistance_observed",
                               secondary_attack_index=episode.attack,
                               secondary_resistance_high=episode.ceiling,
                               secondary_resistance_known_index=now))
        if now < episode.attack + 2 or not episode.resistance:
            continue
        body = Fraction(str(bar.close)) - Fraction(str(bar.open))
        open_price = Fraction(str(bar.open))
        body_reclaim = body > open_price * Fraction(2, 100) and bar.close > episode.ceiling
        gap_reclaim = (bar.open > previous.high and bar.low > previous.high
                       and bar.open > episode.ceiling and bar.close > episode.ceiling
                       and body >= open_price * Fraction(3, 100)
                       and body >= (Fraction(str(bar.high)) - Fraction(str(bar.low))) * Fraction(3, 5))
        if previous.volume <= 0 or bar.volume <= previous.volume or not (body_reclaim or gap_reclaim):
            continue
        proof: dict[str, object] = dict(
            definition="secondary_breakout_resistance_reclaim_v1",
            buy_point_type="secondary_resistance_reclaim", trend_level=2,
            secondary_high_date=bars[episode.key.index].timestamp.date().isoformat(),
            secondary_high=episode.key.price,
            secondary_high_known_date=bars[episode.key.known_index].timestamp.date().isoformat(),
            secondary_attack_index=episode.attack,
            secondary_attack_date=bars[episode.attack].timestamp.date().isoformat(),
            secondary_resistance_date=bars[episode.ceiling_index].timestamp.date().isoformat(),
            secondary_resistance_high=episode.ceiling,
            secondary_resistance_known_date=bars[episode.attack + 1].timestamp.date().isoformat(),
            secondary_origin_date=bars[episode.origin].timestamp.date().isoformat(),
            secondary_origin_low=bars[episode.origin].low,
            secondary_pullback_low_date=bars[episode.bottom].timestamp.date().isoformat(),
            secondary_pullback_low=bars[episode.bottom].low,
            secondary_reclaim_type="gap" if gap_reclaim else "body",
            secondary_reclaim_body_fraction=float(body / open_price),
            secondary_reclaim_body_range_fraction=(float(body / (Fraction(str(bar.high)) - Fraction(str(bar.low))))
                                                  if bar.high > bar.low else 0.0),
            secondary_reclaim_unfilled_gap=bar.open > previous.high and bar.low > previous.high,
            secondary_reclaim_close=bar.close,
            secondary_observed_peak=episode.observed_peak,
            previous_volume=previous.volume, breakout_volume=bar.volume,
            breakout_volume_multiple=bar.volume / previous.volume,
            stop=bars[episode.bottom].low,
            counter_ratio=(episode.ceiling - bars[episode.bottom].low)
            / (episode.ceiling - bars[episode.origin].low),
        )
        proofs[now] = proof
        events.append(dict(proof, bar_index=now, event="secondary_reclaim_candidate"))
    return events, proofs
