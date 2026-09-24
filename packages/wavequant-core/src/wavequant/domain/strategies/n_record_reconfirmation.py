"""A later independent N may defeat resistance inside a still-defended older N."""

from collections.abc import Sequence

from ..models.model import Bar


def n_record_reconfirmation(
    bars: Sequence[Bar],
    *,
    attack: int,
    defense: float,
    now: int,
    new_origin: int,
    new_neckline: int,
    new_pullback: int,
    new_known: int,
) -> dict[str, str | int | float] | None:
    """The caller supplies a completed fresh positive N, never just a rising candle."""
    if not (0 <= new_origin <= attack < new_neckline < new_pullback < now < len(bars) and new_known <= now):
        return None
    if min(b.low for b in bars[attack + 1 : now + 1]) < defense:
        return None
    record = max(b.high for b in bars[attack:now])
    if bars[now].close <= record or bars[now].close <= bars[now].open:
        return None
    return dict(
        squeeze_confirmation="fresh_n_defeats_old_n_resistance",
        reconfirmed_by_n=now,
        reconfirmed_n_date=bars[now].timestamp.date().isoformat(),
        confirmation_record_high=record,
        retained_n_defense=defense,
    )
