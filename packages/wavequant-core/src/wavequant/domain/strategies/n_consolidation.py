"""A fresh volume gap can reactivate a defended larger N after consolidation."""

from collections.abc import Sequence

from ..models.model import Bar


def consolidation_gap(bars: Sequence[Bar], *, attack: int, now: int, defense: float) -> dict[str, str | float] | None:
    if not 0 <= attack < now - 2 < len(bars) or now >= len(bars):
        return None
    original, bar, prev = bars[attack], bars[now], bars[now - 1]
    if min(b.low for b in bars[attack + 1 : now + 1]) < defense:
        return None
    if not (
        bar.low > prev.high
        and bar.open > prev.high
        and bar.close > original.high
        and bar.close > bar.open
        and bar.volume > prev.volume
        and bars[now - 2].close <= original.high
        and prev.close <= original.high
    ):
        return None
    return dict(
        consolidation_n_date=original.timestamp.date().isoformat(),
        consolidation_high=original.high,
        consolidation_defense=defense,
        gap_previous_high=prev.high,
        gap_low=bar.low,
        gap_volume=bar.volume,
        gap_previous_volume=prev.volume,
    )
