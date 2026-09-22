"""A closed volume reversal can defeat earlier supply despite a lower open."""

from collections.abc import Sequence
from typing import TypedDict

from ..models.model import Bar
from .market_regime import RegimeFrame


class VolumeReversalEvidence(TypedDict):
    reversal_record_high: float
    reversal_structural_low: float
    reversal_attack_defense: float
    reversal_low: float
    reversal_close: float
    reversal_volume: float
    reversal_previous_volume: float


def volume_reversal_squeeze(
    bars: Sequence[Bar],
    *,
    attack: int,
    now: int,
    origin_low: float,
    attack_defense: float,
    frame: RegimeFrame,
    offset: int = 0,
) -> VolumeReversalEvidence | None:
    """Require a first-day defense sweep and a decisive close above all supply.

    A broken wave origin or an older defense failure cannot be revived. This
    rule observes only the final close and does not assume an intraday path.
    """
    if now < attack + 2 or frame.first_wave_breach_index is not None:
        return None
    if frame.first_resistance_index is None or frame.first_resistance_index + offset >= now:
        return None
    if frame.first_defense_breach_index is None or frame.first_defense_breach_index + offset != now:
        return None
    bar, prev = bars[now], bars[now - 1]
    record = max(b.high for b in bars[attack:now])
    if not (
        bar.open < prev.close
        and bar.low >= origin_low
        and bar.close > record
        and bar.close > bar.open
        and bar.close - bar.open > (bar.high - bar.low) / 2
        and bar.volume > prev.volume
    ):
        return None
    return dict(
        reversal_record_high=record,
        reversal_structural_low=origin_low,
        reversal_attack_defense=attack_defense,
        reversal_low=bar.low,
        reversal_close=bar.close,
        reversal_volume=bar.volume,
        reversal_previous_volume=prev.volume,
    )
