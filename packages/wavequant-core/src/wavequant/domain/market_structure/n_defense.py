"""Named virtual-price references from an already completed positive/inverse N.

References retain historical completion even after a later breach. The caller
uses the N observation's timestamps and lifecycle to decide whether it is active.
"""
from __future__ import annotations

from .n_shape import NObservation
from .price_action import _positive

__all__ = ["squeeze_low", "selloff_high"]


def _virtual_defense(observation: NObservation, *, name: str) -> float | None:
    if not isinstance(observation, NObservation):
        raise ValueError("NObservation required")
    completion = observation.completion
    if completion is None:
        return None
    if (completion.bar_index > observation.asof_index
            or not completion.real_break or not completion.virtual_break):
        raise ValueError("causally completed real and virtual N break required")
    if completion.defense_name != name:
        raise ValueError(f"completed {name} N direction required")
    price = completion.virtual_low if name == "轧空低" else completion.virtual_high
    _positive(price, "completed N virtual price")
    return price


def squeeze_low(observation: NObservation) -> float | None:
    """正 N 攻击棒虚低 min(今低, 昨收); no volume prerequisite is imposed.

    The literal candle reference can differ from a staged N's operational
    defense, which deliberately includes more than one attack candle.
    """
    return _virtual_defense(observation, name="轧空低")


def selloff_high(observation: NObservation) -> float | None:
    """倒 N 攻击棒虚高 max(今高, 昨收), frozen at the completion candle."""
    return _virtual_defense(observation, name="杀多高")
