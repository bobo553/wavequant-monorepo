"""Recognize a bullish child's inverse-N break after strict mother-child containment."""

from collections.abc import Sequence

from ..market_structure.polyline import observe_bar_relations
from ..models.model import Bar


MOTHER_CHILD_INVERSE_N_LOW_BREAK = "mother_child_inverse_n_low_break"


def mother_child_inverse_n_break(bars: Sequence[Bar], index: int) -> dict[str, float | str] | None:
    """Return close-known evidence when a third bar breaks a bullish child's low and close.

    The drawing path can merge the child low after a bearish mother, so the
    observed child prices are authoritative for this exit.
    """
    if index < 2 or not observe_bar_relations(bars[index - 2], bars[index - 1]).inside:
        return None
    mother, child, break_bar = bars[index - 2], bars[index - 1], bars[index]
    if not (
        mother.open != mother.close
        and child.close > child.open
        and break_bar.low < child.low
        and break_bar.close < child.close
    ):
        return None
    return {
        "mother_date": mother.timestamp.date().isoformat(),
        "mother_high": mother.high,
        "child_date": child.timestamp.date().isoformat(),
        "child_low": child.low,
        "child_high": child.high,
        "child_close": child.close,
        "observed_low": break_bar.low,
        "observed_close": break_bar.close,
    }
