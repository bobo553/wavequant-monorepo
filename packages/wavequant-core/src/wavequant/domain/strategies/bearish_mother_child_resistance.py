"""Keep a broken, exceptional-volume bearish mother-child pair as buy resistance."""

from collections.abc import Sequence
from dataclasses import dataclass

from ..market_structure.polyline import observe_bar_relations
from ..market_structure.price_action import Direction, observe_resistance
from ..models.model import Bar


@dataclass(frozen=True)
class _BearishPair:
    mother_index: int
    child_index: int
    mother_high: float
    child_low: float
    child_close: float
    mother_volume: float
    volume_multiple: float


def bearish_mother_child_resistance_history(
    bars: Sequence[Bar], *, volume_lookback: int = 20
) -> dict[int, dict[str, object]]:
    """Return close-known entry blocks until price closes above the mother's high.

    The mother must be a higher-open bearish resistance candle whose volume is
    a new lookback high and at least twice the lookback mean. A bearish inside
    child turns the pair into active resistance only after a later bar trades
    below the child low and closes below the child close.
    """
    if volume_lookback < 1:
        raise ValueError("volume_lookback must be positive")
    blocked: dict[int, dict[str, object]] = {}
    unbroken_pairs: list[_BearishPair] = []
    active: tuple[_BearishPair, int] | None = None
    for index, bar in enumerate(bars):
        if active is not None and bar.close > active[0].mother_high:
            active = None
        waiting: list[_BearishPair] = []
        for pair in unbroken_pairs:
            if bar.close > pair.mother_high:
                continue
            if bar.low < pair.child_low and bar.close < pair.child_close:
                if active is None or pair.mother_high > active[0].mother_high:
                    active = pair, index
            else:
                waiting.append(pair)
        unbroken_pairs = waiting
        if active is not None:
            pair, break_index = active
            blocked[index] = {
                "mother_date": bars[pair.mother_index].timestamp.date().isoformat(),
                "mother_high": pair.mother_high,
                "mother_volume": pair.mother_volume,
                "mother_volume_multiple": pair.volume_multiple,
                "child_date": bars[pair.child_index].timestamp.date().isoformat(),
                "child_low": pair.child_low,
                "child_close": pair.child_close,
                "break_date": bars[break_index].timestamp.date().isoformat(),
                "observed_close": bar.close,
            }
        mother_index = index - 1
        if mother_index >= volume_lookback:
            mother, child = bars[mother_index], bar
            prior_volumes = [prior.volume for prior in bars[mother_index - volume_lookback:mother_index]]
            total_volume = sum(prior_volumes)
            if (
                total_volume > 0
                and mother.volume > max(prior_volumes)
                and mother.volume * volume_lookback >= 2 * total_volume
                and mother.close < mother.open
                and child.close < child.open
                and observe_resistance(
                    bars[mother_index - 1], mother, attack_direction=Direction.UP
                ).opposing_open_and_body
                and observe_bar_relations(mother, child).inside
            ):
                unbroken_pairs.append(_BearishPair(
                    mother_index, index, mother.high, child.low, child.close,
                    mother.volume, mother.volume * volume_lookback / total_volume,
                ))
    return blocked
