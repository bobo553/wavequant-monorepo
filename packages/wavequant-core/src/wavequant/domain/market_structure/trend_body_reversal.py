"""下级已翻空后的影线新高例外：收盘不能超过原高点当日收盘。"""

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


def _same_point(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return all(left.get(field, 0 if field == 'ordinal' else None)
               == right.get(field, 0 if field == 'ordinal' else None)
               for field in ('index', 'ordinal', 'kind', 'value', 'time'))


def _prior_bear_flip(anchor: Mapping[str, object], counter: Mapping[str, object],
                     rebound: Mapping[str, object]) -> bool:
    """同一严格翻空可存于高点闭合证明或低点攻击证明，不依赖归并时的优先项。"""
    from .trend_levels import _later_moment, _stamp_wave_completion, _validate_completed_source_point

    level = anchor.get('trend_level')
    if (type(level) is not int or level < 1 or counter.get('trend_level') != level
            or rebound.get('trend_level') != level):
        return False
    paths = [point['source_path'] for point in (anchor, counter, rebound) if 'source_path' in point]
    if any(type(path) is not str for path in paths) or paths and any(path != paths[0] for path in paths):
        return False
    completion = anchor.get('wave_completion_evidence')
    proofs = ((completion.get('upgrade_evidence') if isinstance(completion, Mapping) else None, 'origin'),
              (counter.get('upgrade_evidence'), 'endpoint'))
    for proof, role in proofs:
        if not isinstance(proof, Mapping) or proof.get('direction') != 'down' or proof.get('role') != role:
            continue
        origin, attack = proof.get('origin'), proof.get('confirmed_by')
        if (not isinstance(origin, Mapping) or not isinstance(attack, Mapping)
                or not _same_point(origin, anchor) or not _same_point(attack, counter)):
            continue
        known = proof.get('available_at')
        price_time = rebound.get('index') if type(known) is int else rebound.get('time')
        if (not (type(known) is int or isinstance(known, str) and known)
                or not (type(price_time) is int or isinstance(price_time, str) and price_time)):
            continue
        # 用共同严格破位校验验证这份事实，包括旧关键位、真实坐标和可知日；
        # 临时 origin 只用于校验证据，不发布新节点，也不覆盖原有普通闭合证明。
        closed = dict(origin, available_at=known, trend_level=level, source_level=level - 1,
            wave_direction_before='up', wave_direction_after='down',
            confirmation_rule=proof.get('confirmation_rule'), upgrade_evidence=dict(proof, role='origin'))
        try:
            if known == price_time or _later_moment(known, price_time) != price_time:
                continue
            _stamp_wave_completion(closed, closed=True)
            if _validate_completed_source_point(closed) is not None:
                return True
        except ValueError:
            continue
    return False


def rejects_wick_extension(anchor: Mapping[str, object], counter: Mapping[str, object],
                           rebound: Mapping[str, object]) -> bool:
    """同一高点已由下级严格翻空，影线创新高而收盘未超过原收盘才保留交替。"""
    if (anchor.get('kind') != 'H' or counter.get('kind') != 'L' or rebound.get('kind') != 'H'
            or cast(float, rebound['value']) <= cast(float, anchor['value'])):
        return False
    closing, anchor_closing = point_close(rebound), point_close(anchor)
    return (closing is not None and anchor_closing is not None and closing <= anchor_closing
            and _prior_bear_flip(anchor, counter, rebound))


def body_reference(point: Mapping[str, object]) -> dict[str, object]:
    fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label', 'price_body',
              'trend_level', 'source_level', 'state', 'wave_completion_policy', 'wave_identity_state',
              'wave_completed_at', 'wave_completion_evidence', 'source_geometry_available_at',
              'upgrade_evidence', 'confirmation_rule', 'source_path')
    return {field: point[field] for field in fields if field in point}
