"""Block fresh entries on a new low through a causally known inverse neckline."""

from collections.abc import Sequence

from ..market_structure.polyline import PointKind, ReversalPoint
from ..models.model import Bar


INVERSE_N_NEW_LOW_OBSERVATION = "inverse_n_new_low_requires_observation"


def inverse_n_low_entry_risk(
    bars: Sequence[Bar], now: int, points: Sequence[ReversalPoint],
) -> dict[str, str | int | float | bool] | None:
    """Observe a wick break without promoting it to a close-confirmed exit.

    The rebound source must precede today and be known by today. A higher
    intraday rebound below the original high cannot erase the observed low
    break, even when the daily close recovers above the neckline close.
    """
    if not 0 < now < len(bars) or len(points) < 3:
        return None
    bar, previous = bars[now], bars[now - 1]
    if bar.low >= previous.low:
        return None
    origin, neckline, rebound = points[-3:]
    a, b, c = origin.point, neckline.point, rebound.point
    if not (
        a.kind == PointKind.HIGH and b.kind == PointKind.LOW and c.kind == PointKind.HIGH
        and 0 <= a.index < b.index < c.index < now
        and origin.confirmed_index <= neckline.confirmed_index <= rebound.confirmed_index <= now
        and a.price > c.price > b.price
        and a.price == bars[a.index].high
        and b.price == bars[b.index].low
        and c.price == bars[c.index].high
        and bar.low < b.price <= previous.low
    ):
        return None
    # Retired origins, an earlier neckline break or an obsolete rebound cannot
    # be borrowed as a fresh inverse formation. Equality at the low is a touch.
    if (
        max(v.high for v in bars[a.index + 1 : now + 1]) >= a.price
        or min(v.low for v in bars[a.index + 1 : c.index + 1]) != b.price
        or min(v.low for v in bars[b.index + 1 : now]) < b.price
        or max(v.high for v in bars[b.index + 1 : now]) != c.price
    ):
        return None
    return dict(
        reason=INVERSE_N_NEW_LOW_OBSERVATION,
        inverse_origin_date=str(bars[a.index].timestamp.date()),
        inverse_origin_high=a.price,
        inverse_neckline_date=str(bars[b.index].timestamp.date()),
        inverse_neckline_low=b.price,
        inverse_neckline_close=bars[b.index].close,
        inverse_rebound_date=str(bars[c.index].timestamp.date()),
        inverse_rebound_high=c.price,
        inverse_rebound_known_date=str(bars[rebound.confirmed_index].timestamp.date()),
        inverse_observation_date=str(bar.timestamp.date()),
        inverse_previous_low=previous.low,
        inverse_observed_low=bar.low,
        inverse_observed_high=bar.high,
        inverse_observed_close=bar.close,
        inverse_close_break=bar.close < bars[b.index].close,
    )
