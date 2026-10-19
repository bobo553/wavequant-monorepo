"""The specific gap that certifies completion of a bullish alternation."""

from collections.abc import Mapping
from math import isfinite


def confirmation_gap(rule: object, proof: object, now: int) -> tuple[int, float, float, str] | None:
    """普通确认不绑定附近缺口；延迟确认不能借用先前已经回补的缺口。"""
    if not isinstance(proof, Mapping):
        return None
    if rule == 'body_gap_volume_bullish_breakout_after_defended_low':
        lower, upper = proof.get('gap_previous_body_high'), proof.get('gap_open')
        index, kind = proof.get('candidate_index'), 'body'
    elif rule == 'positive_n_consolidation_gap_after_qualified_b':
        lower, upper = proof.get('gap_previous_high'), proof.get('gap_low')
        index, kind = proof.get('gap_confirmation_index'), 'full'
    else:
        return None
    if (type(index) is not int or index != now
            or type(lower) not in (int, float) or type(upper) not in (int, float)
            or not isfinite(lower) or not isfinite(upper) or not 0 < lower < upper):
        return None
    return index, float(lower), float(upper), kind
