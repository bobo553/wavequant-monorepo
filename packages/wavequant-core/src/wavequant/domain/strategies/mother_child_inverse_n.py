"""Recognize the inverse-N break of a bullish mother-child teaching path."""

from collections.abc import Sequence

from ..market_structure.polyline import PointKind, mother_child_path, observe_bar_relations
from ..models.model import Bar


MOTHER_CHILD_INVERSE_N_LOW_BREAK = "mother_child_inverse_n_low_break"


def mother_child_inverse_n_break(bars: Sequence[Bar], index: int) -> dict[str, float | str] | None:
    """Return close-known evidence when a third bar strictly breaks the child low.

    The lecture's bullish mother-child ordering supplies mother high, child low,
    then child high. A later strict low break confirms the risk at daily close.
    """
    if index < 2 or not observe_bar_relations(bars[index - 2], bars[index - 1]).inside:
        return None
    mother, child, break_bar = bars[index - 2], bars[index - 1], bars[index]
    path = mother_child_path(mother, child, mother_index=index - 2)
    if len(path.vertices) < 3:
        return None
    origin, neckline, pullback = path.vertices[-3:]
    if not (
        origin.index == index - 2 and origin.kind == PointKind.HIGH
        and neckline.index == index - 1 and neckline.kind == PointKind.LOW
        and pullback.index == index - 1 and pullback.kind == PointKind.HIGH
        and break_bar.low < neckline.price
    ):
        return None
    return {
        "mother_date": mother.timestamp.date().isoformat(),
        "mother_high": origin.price,
        "child_date": child.timestamp.date().isoformat(),
        "child_low": neckline.price,
        "child_high": pullback.price,
        "observed_low": break_bar.low,
        "observed_close": break_bar.close,
    }
