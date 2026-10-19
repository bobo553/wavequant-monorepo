"""已发生的上级严格升级，向同路径下级授予有限的普通结构确认范围。"""

from collections.abc import Mapping, Sequence
from typing import cast

from .trend_levels import Moment, _SourceTurn, _formal_key_attack_point, _proof_known, _proof_order, _later_moment


PROMOTION_FIELD = '_higher_trend_promotions'
PROMOTION_POINT_FIELDS = (
    'index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'trend_level', 'source_level',
    'wave_identity_state', 'wave_completion_policy', 'wave_direction_before', 'wave_direction_after',
    'confirmation_rule', 'upgrade_evidence',
)


def promotion_point(point: Mapping[str, object]) -> dict[str, object]:
    # 仅保留升级当时的严格证据，不把后来结束日或祖先附件带回下级。
    return {field: point[field] for field in PROMOTION_POINT_FIELDS if field in point}


def validate_promotion(point: Mapping[str, object], level: int) -> tuple[Mapping[str, object], Moment]:
    upper = point.get('trend_level')
    proof = point.get('upgrade_evidence')
    if (type(upper) is not int or not level < upper <= 3
            or point.get('wave_identity_state') != 'owned'
            or point.get('state') in ('seed', 'developing') or point.get('display_only') is True
            or not isinstance(proof, Mapping) or proof.get('role') != 'endpoint'):
        raise ValueError('local completion requires an actual higher-level strict promotion')
    _formal_key_attack_point(point)
    return proof, _proof_known(point)


def source_promotions(turns: Sequence[_SourceTurn], level: int) -> list[Mapping[str, object]]:
    by_order = {(turn.index, turn.ordinal): turn for turn in turns}
    result: list[Mapping[str, object]] = []
    for turn in turns:
        records = turn.source.get(PROMOTION_FIELD, ())
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise ValueError('higher trend promotions must be a sequence')
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError('higher trend promotion must be a mapping')
            if (record.get('_promotion_target_level') != level
                    or record.get('_promotion_target_path') != turn.source.get('source_path', 'path-0')):
                raise ValueError('promotion must belong to this lower level and source path')
            proof, known = validate_promotion(record, level)
            for name in ('origin', 'confirmed_by'):
                ref = cast(Mapping[str, object], proof[name])
                actual = by_order.get(_proof_order(ref))
                if (actual is None or actual.kind != ref['kind'] or actual.value != ref['value']
                        or actual.time != ref['time']):
                    raise ValueError('promotion must refer to the actual lower source wave')
                geometry_known = cast(Moment, actual.source.get('source_geometry_available_at', actual.available_at))
                if _later_moment(geometry_known, known) != known:
                    raise ValueError('promotion cannot precede its actual lower source')
            if _proof_order(record) != (turn.index, turn.ordinal):
                raise ValueError('promotion must be attached to its actual endpoint')
            result.append(record)
    return result
