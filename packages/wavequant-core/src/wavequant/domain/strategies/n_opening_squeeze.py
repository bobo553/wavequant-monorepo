"""Confirm a known, defended positive N using the current opening price only."""

from typing import Sequence

from ..models.model import Bar


OPENING_SQUEEZE_REASON = 'system_n_opening_gap_squeeze'


def opening_n_squeeze(bars: Sequence[Bar], *, attack: int, known: int, now: int,
                      defense: float, invalidated_at: int | None = None) -> dict[str, str | int | float] | None:
    """Strict gap above yesterday's close; same-session OHLCV cannot confirm or revoke it."""
    if not 0 <= attack <= known < now < len(bars):
        return None
    if invalidated_at is not None and invalidated_at < now:
        return None
    current, previous = bars[now], bars[now - 1]
    if current.open <= previous.close or current.open <= defense:
        return None
    # Every earlier session is complete. A prior defense break permanently
    # retires this N, while a break after today's opening decision is later risk.
    if any(bar.low < defense for bar in bars[attack + 1:now]):
        return None
    return dict(squeeze_confirmation='known_n_opening_gap',
                n_opening_attack=attack, n_opening_known=known,
                n_opening_date=current.timestamp.date().isoformat(),
                n_opening_attack_date=bars[attack].timestamp.date().isoformat(),
                n_opening_known_date=bars[known].timestamp.date().isoformat(),
                prior_bar_date=previous.timestamp.date().isoformat(),
                opening_price=current.open, prior_close=previous.close,
                n_opening_defense=defense,
                decision_timestamp=current.timestamp.replace(hour=9, minute=30, second=0, microsecond=0).isoformat())
