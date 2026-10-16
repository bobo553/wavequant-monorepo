"""下级已翻空后的影线新高例外：只用收盘是否越过原最高价判定。"""

from collections.abc import Mapping, Sequence
from math import isfinite
from typing import cast

from ..models.model import Bar


BODY_REVERSAL_RULE = 'lower_bear_flip_close_rejection_then_lower_low'


def with_price_bodies(points: Sequence[Mapping[str, object]], bars: Sequence[Bar]) -> list[dict[str, object]]:
    """仅附加来源点当日实体，不扫描区间，不改变高低点或其确认日期。"""
    result = []
    for point in points:
        current = dict(point)
        index = point.get('index')
        if type(index) is int and 0 <= index < len(bars):
            bar = bars[index]
            current['price_body'] = dict(index=index, time=bar.timestamp.date().isoformat(),
                                         open=bar.open, close=bar.close, high=bar.high, low=bar.low)
        result.append(current)
    return result


def point_close(point: Mapping[str, object]) -> float | None:
    body = point.get('price_body')
    if (not isinstance(body, Mapping) or body.get('index') != point.get('index')
            or str(body.get('time'))[:10] != str(point.get('time'))[:10]):
        return None
    values = [body.get(field) for field in ('open', 'close', 'high', 'low')]
    if any(type(value) not in (int, float) or not isfinite(cast(float, value)) or cast(float, value) <= 0 for value in values):
        return None
    opening, closing, high, low = (float(cast(float, value)) for value in values)
    if not low <= min(opening, closing) <= max(opening, closing) <= high:
        return None
    if point.get('kind') != 'H' or point.get('value') != high:
        return None
    return closing


def rejects_wick_extension(anchor: Mapping[str, object], counter: Mapping[str, object],
                           rebound: Mapping[str, object]) -> bool:
    """必须先有该高点的下级严格翻空证明；孤立上影线不能阻止正常延伸。"""
    if counter.get('kind') != 'L' or cast(float, rebound['value']) <= cast(float, anchor['value']):
        return False
    completion = anchor.get('wave_completion_evidence')
    if not isinstance(completion, Mapping):
        return False
    proof = completion.get('upgrade_evidence')
    if (not isinstance(proof, Mapping) or proof.get('direction') != 'down'
            or proof.get('role') != 'origin'):
        return False
    origin, attack = proof.get('origin'), proof.get('confirmed_by')
    if not isinstance(origin, Mapping) or not isinstance(attack, Mapping):
        return False
    fields = ('index', 'ordinal', 'kind', 'value')
    if not (all(origin.get(field, 0) == anchor.get(field, 0) for field in fields)
            and all(attack.get(field, 0) == counter.get(field, 0) for field in fields)):
        return False
    closing = point_close(rebound)
    return closing is not None and closing <= cast(float, anchor['value'])


def body_reference(point: Mapping[str, object]) -> dict[str, object]:
    fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label', 'price_body',
              'trend_level', 'source_level', 'state', 'wave_completion_policy', 'wave_identity_state',
              'wave_completed_at', 'wave_completion_evidence', 'source_geometry_available_at')
    return {field: point[field] for field in fields if field in point}
