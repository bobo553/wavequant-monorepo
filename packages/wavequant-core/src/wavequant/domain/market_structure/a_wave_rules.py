"""A strength and lifetime are independent of the earlier N's squeeze defense."""

from math import isfinite
from typing import Literal

AWaveClass = Literal["ordinary", "strong"]


def classify_a_attack(high: float, one_p: float, two_t: float) -> AWaveClass | None:
    """A strictly breaks one-P; equality at two-T upgrades it to strong A."""
    if (any(isinstance(value, bool) or not isfinite(value) or value <= 0 for value in (high, one_p, two_t))
            or two_t <= one_p or high <= one_p):
        return None
    return "strong" if high >= two_t else "ordinary"


def a_origin_broken(low: float, origin: float) -> bool:
    """A wick strictly below the original A low retires that A permanently."""
    return low < origin
