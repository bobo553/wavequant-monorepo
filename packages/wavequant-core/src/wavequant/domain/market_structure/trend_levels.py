"""用同一高低点定义递归观察一级、二级和三级趋势。

一级来源为已确认折线反转；普通递归只使用直接下级已结束整波点。
严格关键位通道另可读取直接下级具有完整突破证明的正式端点，不伪造其结束。
正／负反转描述局部折线转向，多／空趋势要求两类同向严格抬高或降低。
本模块产生整波趋势方向证据；突破已知本级高低点的立即升级是独立通道，
普通方向变化不能冒充翻空为多、翻多为空或其他严格关键位突破事件。
"""

from collections.abc import Mapping, Sequence
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass, field
from math import isfinite
from typing import Literal, TypeAlias, cast

from .owned_evidence_producer import PointRecord, current_producer_records
from .owned_evidence_records import RecordRef
from .polyline import LinePoint, PointKind, ReversalPoint
from .trend_structure import StructuralTrend, classify_trend_prices, preceding_turn

Moment: TypeAlias = int | str
WaveDirection: TypeAlias = Literal['up', 'down']

__all__ = ['completed_source_points', 'formal_key_attack_points', 'trend_reversals']

_WAVE_COMPLETION_POLICY = 'whole_wave_v117'


def _proof_order(reference: Mapping[str, object]) -> tuple[int, int]:
    """结束证明也必须保留真实价格坐标；日期迟知不能替代左右位置。"""
    index, ordinal = reference.get('index'), reference.get('ordinal', 0)
    if type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0:
        raise ValueError('wave completion references require valid price coordinates')
    return index, ordinal


def _proof_known(reference: Mapping[str, object]) -> Moment:
    """结束证据的每个子点也要先发生再可知，禁止未来坐标伪装历史证明。"""
    index, _ = _proof_order(reference)
    time, known = reference.get('time'), reference.get('available_at')
    if not isinstance(time, str) or not time:
        raise ValueError('wave completion source requires its price date')
    if type(known) is int:
        if known < index:
            raise ValueError('wave completion source knowledge cannot precede its price index')
        return known
    if isinstance(known, str) and known:
        numeric_clock = known.isdecimal() and time.isdecimal()
        if int(known) < int(time) if numeric_clock else known < time:
            raise ValueError('wave completion source knowledge cannot precede its price date')
        return known
    raise ValueError('wave completion source requires a knowledge clock')


def _completion_evidence(point: Mapping[str, object]) -> dict[str, object]:
    """保存本级整波结束的独立事实，不复制已经继承的下级结束许可证。

    几何端点可以更早出现；本证据的日期表示真正反向趋势首次成立的日期。
    保留四点定义或严格突破的原始证据，使递归门禁能核对方向和坐标，不能
    仅凭一个 ``state='reversal'`` 把尚在延伸的单向升级端点当成已结束整波。
    """
    fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at',
              'wave_direction_before', 'wave_direction_after', 'confirmation_rule',
              'definition_evidence', 'upgrade_evidence', 'lower_wave_identity_evidence', 'body_reversal_evidence')
    result = {field: deepcopy(point[field]) for field in fields if field in point}
    scope = current_producer_records()
    record = scope.lookup(point) if scope is not None else None
    if scope is not None and record is not None:
        # 原逻辑逐字段 deepcopy；各可变字段必须分别进入独立复制域。
        for field, value in result.items():
            reference = record.field_reference(field)
            if reference is not None:
                scope.bind_clone(value, point[field], reference)
        scope.record_point(result)
    return result


def _stamp_wave_completion(point: dict[str, object], *, closed: bool, owned: bool = True) -> None:
    """本级重建结束标记；开放端点只保留几何资格，不取得递归来源资格。"""
    point['wave_completion_policy'] = _WAVE_COMPLETION_POLICY
    point['wave_identity_state'] = 'owned' if owned else 'direction_only'
    scope = current_producer_records()
    if closed and owned:
        point['wave_completed_at'] = point['available_at']
        if scope is not None and scope.tracks(point):
            scope.record_point(point)
        point['wave_completion_evidence'] = _completion_evidence(point)
    if scope is not None and scope.tracks(point):
        scope.record_point(point)


def _completed_source_point(raw: Mapping[str, object]) -> dict[str, object] | None:
    # 门禁只读取当前点的闭合证明；对整棵继承证据做缓存签名比重新核验更贵，
    # 且会随历史增长。直接核验保留迟到修订、未知审计对象和原字段引用语义。
    return _validate_completed_source_point(raw)


def _validate_completed_source_point(raw: Mapping[str, object]) -> dict[str, object] | None:
    """把本级几何知识投影成直接上级可消费的整波结束知识，不修改原节点。

    旧的人工 confirmed/reversal 输入没有生成标记，继续作为已确认来源。
    新生成的严格同向端点则必须等待同坐标真实反向证明。own available_at
    仍用于绘图；投影副本以 max(本次观察知识, completed_at) 作为来源可知
    时间，另存 earliest 以供审计。重复适配保持幂等，不能用投影日期覆盖
    真实几何首次出现日，也不能用更早结束证明提前本次实际观察知识。
    """
    result = dict(raw)
    identity_state = raw.get('wave_identity_state')
    if identity_state not in (None, 'owned', 'direction_only'):
        raise ValueError('unsupported whole-wave identity state')
    if identity_state is not None and raw.get('wave_completion_policy') != _WAVE_COMPLETION_POLICY:
        raise ValueError('whole-wave identity requires its generated policy')
    if identity_state == 'direction_only':
        if 'wave_completed_at' in raw or 'wave_completion_evidence' in raw:
            raise ValueError('a direction fact cannot carry a whole-wave completion license')
        # 双类方向事实可以成立，但单条下级波尚无本级整波身份；行情/N
        # 许可证也不能反向授予这种身份，所有递归入口在此共同排除。
        return None
    if 'wave_completion_policy' not in raw:
        if 'wave_completed_at' in raw or 'wave_completion_evidence' in raw:
            raise ValueError('wave completion requires its generated policy')
        return result
    if raw['wave_completion_policy'] != _WAVE_COMPLETION_POLICY:
        raise ValueError('unsupported wave completion policy')
    if identity_state != 'owned':
        raise ValueError('generated wave completion requires its own whole-wave identity')
    if 'wave_completed_at' not in raw:
        if 'wave_completion_evidence' in raw:
            raise ValueError('open wave cannot carry completion evidence')
        return None
    completed = raw['wave_completed_at']
    own = raw.get('source_geometry_available_at', raw.get('available_at'))
    if (not (type(completed) is int or isinstance(completed, str) and completed)
            or not (type(own) is int or isinstance(own, str) and own)):
        raise ValueError('wave completion requires a valid knowledge clock')
    completed_clock, own_clock = completed, own
    if _later_moment(own_clock, completed_clock) != completed_clock:
        raise ValueError('wave completion cannot precede its geometry knowledge')
    evidence = raw.get('wave_completion_evidence')
    if not isinstance(evidence, Mapping):
        raise ValueError('completed wave requires independent reversal evidence')
    for field in ('index', 'kind', 'value', 'time'):
        if evidence.get(field) != raw.get(field):
            raise ValueError('wave completion evidence must match its endpoint')
    if evidence.get('ordinal', 0) != raw.get('ordinal', 0) or evidence.get('available_at') != completed:
        raise ValueError('wave completion evidence must retain its coordinate and knowledge date')
    before, after = evidence.get('wave_direction_before'), evidence.get('wave_direction_after')
    expected = ('up', 'down') if raw.get('kind') == 'H' else ('down', 'up')
    if (before, after) != expected:
        raise ValueError('wave completion requires a genuine opposite-direction reversal')
    definition = evidence.get('definition_evidence')
    identity_proof = evidence.get('lower_wave_identity_evidence')
    wave_origin: Mapping[str, object] | None = None
    if identity_proof is not None:
        if not isinstance(identity_proof, Mapping) or identity_proof.get('available_at') != completed:
            raise ValueError('whole-wave identity requires its actual knowledge date')
        # 一级的直接来源是折线反转，严格两高两低已足以确认趋势；折线的一条
        # 价格腿不是“已存在的下级趋势线”，不能为防二/三级重叠再要求虚构内点。
        # 此证明只允许零级折线→一级，递归趋势来源仍必须有真实组合波拓扑。
        polyline_identity = identity_proof.get('basis') == 'polyline_four_point_definition'
        if polyline_identity and (type(raw.get('trend_level')) is not int or raw.get('trend_level') != 1
                or type(raw.get('source_level')) is not int or raw.get('source_level') != 0 or definition is None):
            raise ValueError('polyline identity requires first-level four-point definition evidence')
        if identity_proof.get('basis') not in (None, 'polyline_four_point_definition'):
            raise ValueError('unsupported whole-wave identity basis')
        identity_fields = ('origin', 'endpoint') if polyline_identity else ('origin', 'interior', 'endpoint')
        identity_refs = [identity_proof.get(field) for field in identity_fields]
        if not all(isinstance(ref, Mapping) for ref in identity_refs):
            raise ValueError('ordinary whole-wave identity requires real lower topology')
        references = cast(list[Mapping[str, object]], identity_refs)
        identity_origin, identity_endpoint = references[0], references[-1]
        if (_proof_order(identity_origin) != _proof_order(raw) or identity_origin.get('kind') != raw.get('kind')
                or identity_origin.get('value') != raw.get('value') or identity_origin.get('time') != raw.get('time')
                or not _proof_order(identity_origin) < _proof_order(identity_endpoint)
                or not polyline_identity and not (
                    _proof_order(identity_origin) < _proof_order(references[1]) < _proof_order(identity_endpoint))
                or identity_endpoint.get('kind') == identity_origin.get('kind')):
            raise ValueError('ordinary whole-wave identity requires a composite lower wave')
        if 'wave_origin' in identity_proof:
            phase_origin = identity_proof['wave_origin']
            if not isinstance(phase_origin, Mapping):
                raise ValueError('whole-wave origin requires a real source reference')
            if (phase_origin.get('kind') == raw.get('kind')
                    or not _proof_order(phase_origin) < _proof_order(raw)):
                raise ValueError('whole-wave origin must precede its opposite-kind extreme')
            wave_origin = phase_origin
            references = [*references, wave_origin]
        for ref in references:
            value = ref.get('value')
            if (ref.get('kind') not in ('H', 'L') or type(value) not in (int, float)
                    or not isfinite(cast(float, value)) or cast(float, value) <= 0):
                raise ValueError('whole-wave identity requires finite positive lower prices')
            if _later_moment(_proof_known(ref), completed_clock) != completed_clock:
                raise ValueError('whole-wave identity cannot borrow future lower knowledge')
        if wave_origin is not None:
            wave_sign = 1 if before == 'up' else -1
            if wave_sign * (cast(float, identity_origin['value']) - cast(float, wave_origin['value'])) <= 0:
                raise ValueError('whole-wave origin requires a real directional price leg')
        sign = 1 if after == 'up' else -1
        if sign * (cast(float, identity_endpoint.get('value', 0)) - cast(float, identity_origin.get('value', 0))) <= 0:
            raise ValueError('whole-wave identity requires a real opposite lower leg')
        local_boundary = identity_proof.get('local_definition_boundary')
        if local_boundary is not None:
            if (not isinstance(local_boundary, Mapping) or wave_origin is None
                    or any(local_boundary.get(field, 0) != identity_origin.get(field, 0)
                           for field in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at'))
                    or not isinstance(definition, Sequence)
                    or any(not isinstance(ref, Mapping) or _proof_order(ref) < _proof_order(identity_origin)
                           for ref in definition)):
                raise ValueError('local definition requires all four sources from its whole-wave extreme')
            from .trend_promotion_scope import validate_promotion
            promotion = identity_proof.get('higher_trend_promotion')
            if not isinstance(promotion, Mapping):
                raise ValueError('local definition requires higher-level promotion evidence')
            promoted, released = validate_promotion(promotion, cast(int, raw['trend_level']))
            if (_later_moment(released, completed_clock) != completed_clock
                    or _proof_order(cast(Mapping[str, object], promoted['confirmed_by'])) > _proof_order(identity_origin)):
                raise ValueError('local completion cannot precede its higher promotion')
            promoted_origin = cast(Mapping[str, object], promoted['origin'])
            promoted_end = cast(Mapping[str, object], promoted['confirmed_by'])
            assert wave_origin is not None
            within_wave = (_proof_order(wave_origin) == _proof_order(promoted_origin)
                and _proof_order(identity_origin) == _proof_order(promoted_end)
                or _proof_order(wave_origin) >= _proof_order(promoted_end))
            floor = min(cast(float, promoted_origin['value']), cast(float, promoted_end['value']))
            ceiling = max(cast(float, promoted_origin['value']), cast(float, promoted_end['value']))
            if (promotion.get('_promotion_target_level') != raw['trend_level'] or not within_wave
                    or any(not floor <= cast(float, ref['value']) <= ceiling for ref in references)):
                raise ValueError('local completion must remain within its promoted wave scope')
        reversal_key = identity_proof.get('reversal_key')
        if reversal_key is not None:
            if not isinstance(reversal_key, Mapping):
                raise ValueError('ordinary reversal key requires a source reference')
            key_price = reversal_key.get('value')
            if (type(key_price) not in (int, float) or not isfinite(cast(float, key_price))
                    or cast(float, key_price) <= 0
                    or reversal_key.get('kind') != identity_endpoint.get('kind')
                    or not _proof_order(reversal_key) < _proof_order(identity_origin)
                    or sign * (cast(float, identity_endpoint['value']) - cast(float, key_price)) <= 0):
                raise ValueError('ordinary reversal requires a strict break of its frozen key')
            key_known = _proof_known(reversal_key)
            attack_time = cast(str, identity_endpoint['time'])
            known_before = (key_known < cast(int, identity_endpoint['index']) if isinstance(key_known, int) else
                            int(key_known) < int(attack_time) if key_known.isdecimal() and attack_time.isdecimal()
                            else key_known < attack_time)
            if not known_before or _later_moment(key_known, completed_clock) != completed_clock:
                raise ValueError('ordinary reversal key must be known before its attack')
    if definition is not None and identity_proof is None:
        raise ValueError('ordinary owned completion requires lower-wave identity evidence')
    upgrade = evidence.get('upgrade_evidence')
    body_reversal = evidence.get('body_reversal_evidence')
    if body_reversal is not None:
        from .trend_body_reversal import BODY_REVERSAL_RULE, rejects_wick_extension

        if (not isinstance(body_reversal, Mapping) or evidence.get('confirmation_rule') != BODY_REVERSAL_RULE
                or body_reversal.get('available_at') != completed or expected != ('up', 'down')):
            raise ValueError('body rejection completion requires its own bearish reversal proof')
        body_refs = [body_reversal.get(field) for field in ('anchor', 'counter', 'rebound', 'endpoint')]
        if not all(isinstance(ref, Mapping) for ref in body_refs):
            raise ValueError('body rejection requires four real lower-level points')
        anchor, counter, rebound, endpoint = cast(list[Mapping[str, object]], body_refs)
        if (not _proof_order(anchor) < _proof_order(counter) < _proof_order(rebound) < _proof_order(endpoint)
                or endpoint.get('kind') != 'L' or not rejects_wick_extension(anchor, counter, rebound)
                or any(anchor.get(field, 0) != raw.get(field, 0)
                       for field in ('index', 'ordinal', 'kind', 'value', 'time'))):
            raise ValueError('body rejection must retain the original high and actual lower topology')
        source_level = raw.get('source_level')
        if (type(source_level) is not int or source_level < 1 or raw.get('trend_level') != source_level + 1
                or any(ref.get('trend_level') != source_level for ref in (anchor, counter, rebound, endpoint))):
            raise ValueError('body rejection must use the direct lower trend level')
        for ref in (anchor, counter, rebound, endpoint):
            value = ref.get('value')
            if (type(value) not in (int, float) or not isfinite(cast(float, value)) or cast(float, value) <= 0
                    or _later_moment(_proof_known(ref), completed_clock) != completed_clock):
                raise ValueError('body rejection cannot borrow future or invalid lower prices')
            if _validate_completed_source_point(ref) is None:
                raise ValueError('body rejection requires completed lower waves')
        if cast(float, endpoint['value']) >= cast(float, counter['value']):
            raise ValueError('body rejection requires a strict break of the counter low')
    elif definition is not None:
        if not isinstance(definition, Sequence) or isinstance(definition, (str, bytes)) or len(definition) != 4:
            raise ValueError('ordinary wave completion requires two highs and two lows')
        refs: list[Mapping[str, object]] = []
        for item in definition:
            if not isinstance(item, Mapping):
                raise ValueError('wave completion references must be mappings')
            refs.append(item)
        prices: dict[str, list[float]] = {'H': [], 'L': []}
        previous_orders: dict[str, tuple[int, int]] = {}
        for ref in refs:
            kind, price, known = ref.get('kind'), ref.get('value'), _proof_known(ref)
            if (kind not in prices or not isinstance(price, (int, float)) or isinstance(price, bool)
                    or not isfinite(price) or price <= 0):
                raise ValueError('invalid ordinary wave completion source')
            if _later_moment(known, completed_clock) != completed_clock:
                raise ValueError('wave completion cannot borrow future source knowledge')
            order = _proof_order(ref)
            previous = previous_orders.get(kind)
            if previous is not None and order <= previous:
                raise ValueError('ordinary completion pairs must retain their source order')
            previous_orders[kind] = order
            prices[kind].append(float(price))
        if len(prices['H']) != 2 or len(prices['L']) != 2:
            raise ValueError('ordinary wave completion requires both source kinds')
        # 两高两低决定反向定义，实际波起点决定“本波极值”的价格区间。
        # 严格突破较小旧 key 后，比较基准里可以仍有上一波更高/更低的点；
        # 不能把它归进新波而拒绝合法闭合。本波范围内的反例仍必须拒绝。
        same_kind_prices = prices[cast(str, raw.get('kind'))]
        if wave_origin is not None:
            boundary = _proof_order(wave_origin)
            same_kind_prices = [cast(float, ref['value']) for ref in refs
                                if ref['kind'] == raw.get('kind') and _proof_order(ref) > boundary]
        endpoint_price = raw.get('value')
        if (not isinstance(endpoint_price, (int, float)) or isinstance(endpoint_price, bool)
                or not isfinite(endpoint_price) or endpoint_price <= 0
                or not same_kind_prices
                or raw.get('kind') == 'H' and endpoint_price < max(same_kind_prices)
                or raw.get('kind') == 'L' and endpoint_price > min(same_kind_prices)):
            raise ValueError('ordinary completion must retain its true whole-wave extreme')
        trend = classify_trend_prices(prices['H'], prices['L'], trend_level=1)
        if trend != (StructuralTrend.BULL if after == 'up' else StructuralTrend.BEAR):
            raise ValueError('ordinary wave completion requires strict same-direction pairs')
    elif isinstance(upgrade, Mapping):
        origin, attack, key = upgrade.get('origin'), upgrade.get('confirmed_by'), upgrade.get('broken_key')
        if (upgrade.get('role') != 'origin' or upgrade.get('direction') != after
                or upgrade.get('available_at') != completed
                or not isinstance(origin, Mapping) or not isinstance(attack, Mapping) or not isinstance(key, Mapping)):
            raise ValueError('strict wave completion requires the opposite-direction origin proof')
        if any(origin.get(field, 0 if field == 'ordinal' else None) != raw.get(field, 0 if field == 'ordinal' else None)
               for field in ('index', 'ordinal', 'kind', 'value', 'time')):
            raise ValueError('strict completion must close the true whole-wave extreme')
        sign = 1 if after == 'up' else -1
        if attack.get('kind') != ('H' if after == 'up' else 'L') or key.get('kind') != attack.get('kind'):
            raise ValueError('strict completion requires matching attack and key kinds')
        attack_price, key_price, origin_price = attack.get('value'), key.get('value'), origin.get('value')
        if (not isinstance(attack_price, (int, float)) or isinstance(attack_price, bool)
                or not isinstance(key_price, (int, float)) or isinstance(key_price, bool)
                or not isinstance(origin_price, (int, float)) or isinstance(origin_price, bool)
                or not isfinite(attack_price) or not isfinite(key_price) or not isfinite(origin_price)
                or min(attack_price, key_price, origin_price) <= 0
                or sign * (attack_price - key_price) <= 0 or sign * (key_price - origin_price) <= 0):
            raise ValueError('strict completion requires a strict key break')
        if not (_proof_order(key) < _proof_order(origin) < _proof_order(attack)):
            raise ValueError('strict completion must retain key-origin-attack source order')
        for ref in (origin, attack, key):
            known = _proof_known(ref)
            if _later_moment(known, completed_clock) != completed_clock:
                raise ValueError('strict completion cannot borrow future source knowledge')
        # 和升级器使用同一个“旧关键位”边界：正式知识严格早于攻击价格日，
        # 不是早于攻击确认日。同日新确认的 key 不能由手造结束字段越过门禁。
        key_known = cast(Moment, key['available_at'])
        if isinstance(key_known, int):
            earlier_key = key_known < _proof_order(attack)[0]
        else:
            attack_time = attack.get('time')
            if not isinstance(attack_time, str) or not attack_time:
                raise ValueError('strict completion attack requires its price date')
            earlier_key = (int(key_known) < int(attack_time) if key_known.isdecimal() and attack_time.isdecimal()
                           else key_known < attack_time)
        if not earlier_key:
            raise ValueError('strict completion key must be known before the attack price')
    else:
        raise ValueError('wave completion requires an ordinary or strict reversal proof')
    observed = raw.get('available_at')
    if not (type(observed) is int or isinstance(observed, str) and observed):
        raise ValueError('completed source requires its current observation knowledge')
    # 历史观察器可能在后来重新看到同坐标事实。真实结束证明仍保持23，
    # 本次实际可消费知识却不能早于重新观察27；只有 source_geometry 字段
    # 保存的真实 earliest18 可用于核对结束日，而不能把27倒填回证明日期。
    result.update(available_at=_later_moment(observed, completed_clock), source_geometry_available_at=own_clock)
    return result


def _formal_key_attack_point(raw: Mapping[str, object]) -> dict[str, object] | None:
    """正式端点可证明本级价格突破；开放身份不能作为普通组合波结束证明。

    已闭合普通点仍使用真实结束日。严格升级端点使用自身几何可知日，
    独立校验其起点、旧关键位、攻击坐标及知识链，不伪造 wave_completed_at。
    """
    completed = _validate_completed_source_point(raw)
    upgrade = raw.get('upgrade_evidence')
    if raw.get('wave_identity_state') != 'owned' or not isinstance(upgrade, Mapping):
        return completed
    if upgrade.get('role') != 'endpoint':
        return completed
    level = raw.get('trend_level')
    direction = upgrade.get('direction')
    attack, origin = upgrade.get('confirmed_by'), upgrade.get('origin')
    if (type(level) is not int or level not in (1, 2, 3) or raw.get('source_level') != level - 1
            or direction not in ('up', 'down') or not isinstance(attack, Mapping)
            or not isinstance(origin, Mapping)):
        raise ValueError('formal key attack requires its direct-level strict proof')
    rising = direction == 'up'
    rule = (f'level{level - 1}_confirmed_' + ('high' if rising else 'low')
            + f'_breaks_known_level{level}_' + ('last_fall_high' if rising else 'last_rise_low'))
    if (upgrade.get('confirmation_rule') != rule or raw.get('confirmation_rule') != rule
            or any(attack.get(field, 0 if field == 'ordinal' else None)
                   != raw.get(field, 0 if field == 'ordinal' else None)
                   for field in ('index', 'ordinal', 'kind', 'value', 'time'))
            or origin.get('kind') != ('L' if rising else 'H')
            or raw.get('wave_direction_after') != direction
            or raw.get('wave_direction_before') != direction):
        raise ValueError('formal key attack must retain its actual endpoint and direction')
    known = _proof_known(raw)
    proof_known = upgrade.get('available_at')
    if not (type(proof_known) is int or isinstance(proof_known, str) and proof_known):
        raise ValueError('formal key attack requires a proof clock')
    if _later_moment(proof_known, known) != known:
        raise ValueError('formal key attack cannot borrow future proof')
    key = upgrade.get('broken_key')
    if not isinstance(key, Mapping) or key.get('kind') != attack.get('kind'):
        raise ValueError('formal key attack requires a matching old key')
    sign = 1 if rising else -1
    for ref in (origin, attack, key):
        price = ref.get('value')
        if (type(price) not in (int, float) or not isfinite(cast(float, price)) or cast(float, price) <= 0
                or _later_moment(_proof_known(ref), proof_known) != proof_known):
            raise ValueError('formal key attack requires finite known references')
    # 延伸波的原起点可能已经在旧 key 的突破一侧，不能强加新反向起波的跨价条件。
    # 但真实方向、旧关键位、攻击日前可知与原起点均必须保留。
    if (sign * (cast(float, attack['value']) - cast(float, key['value'])) <= 0
            or sign * (cast(float, attack['value']) - cast(float, origin['value'])) <= 0
            or not _proof_order(key) < _proof_order(attack)
            or not _proof_order(origin) < _proof_order(attack)):
        raise ValueError('formal key attack requires a strict directional break')
    key_known = _proof_known(key)
    attack_time = cast(str, attack['time'])
    earlier = (key_known < cast(int, attack['index']) if isinstance(key_known, int) else
               int(key_known) < int(attack_time) if key_known.isdecimal() and attack_time.isdecimal() else
               key_known < attack_time)
    if not earlier:
        raise ValueError('formal key attack requires a key known before its price date')
    return dict(raw)


def formal_key_attack_points(
    points: Sequence[Mapping[str, object]], *, source_level: int,
) -> list[dict[str, object]]:
    """普通已结束来源加正式严格攻击端点；仅供独立突破路线消费。"""
    if type(source_level) is not int or source_level not in (0, 1, 2, 3):
        raise ValueError('source_level must be 0, 1, 2 or 3')
    return [dict(turn.source) for turn in _source_turns(points, source_level, formal_key_attacks=True)]


def completed_source_points(
    points: Sequence[Mapping[str, object]], *, source_level: int,
) -> list[dict[str, object]]:
    """统一折线适配器和直接递归的来源门禁，仅输出真正完成的直接下级点。"""
    if type(source_level) is not int or source_level not in (0, 1, 2, 3):
        raise ValueError('source_level must be 0, 1, 2 or 3')
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise ValueError('trend source points must be a sequence of mappings')
    return [dict(turn.source) for turn in _source_turns(points, source_level)]


@dataclass(frozen=True)
class _SourceTurn:
    """保留价格坐标、原始来源位置和实际可消费知识，不把绘图日期当结束日期。"""
    source: Mapping[str, object]
    position: int
    index: int
    ordinal: int
    kind: Literal['H', 'L']
    value: float
    time: str
    available_at: Moment
    reversal: ReversalPoint
    source_record: tuple[bytes, int] | None = field(default=None, compare=False, repr=False)

    def __deepcopy__(self, memo: dict[int, object]) -> '_SourceTurn':
        # 真正复制后的证据图具有独立别名域；旧磁盘根只能证明原 source 壳。
        result = object.__new__(type(self))
        memo[id(self)] = result
        for name, value in vars(self).items():
            object.__setattr__(result, name, None if name == 'source_record' else deepcopy(value, memo))
        return result


def _source_record_reference(source: Mapping[str, object]) -> tuple[bytes, int] | None:
    """状态只持有常量引用，不保留临时载体或作用域身份表。"""
    scope = current_producer_records()
    if scope is None or type(source) is not dict:
        return None
    record = scope.record_point(cast(dict[str, object], source))
    return record.reference.namespace, record.reference.offset


def _bind_source_record(turn: _SourceTurn) -> None:
    """旧状态跨作用域被实际消费时恢复直接字段引用，不展开历史子图。"""
    scope = current_producer_records()
    if scope is None or turn.source_record is None:
        return
    namespace, offset = turn.source_record
    if namespace != scope.owner.namespace or type(turn.source) is not dict:
        raise ValueError('source turn records require their original request owner and dictionary')
    if scope.lookup(turn.source) is not None:
        return
    reference = RecordRef(namespace, offset)
    source = cast(dict[str, object], turn.source)
    refs = scope.owner.dictionary_refs(reference)
    fields = tuple((key, pair[1]) for key, pair in zip(source, refs))
    scope.bind(PointRecord(source, reference, fields, scope.owner))


def _source_turns(
    points: Sequence[Mapping[str, object]], source_level: int, *, retain_geometry: bool = False,
    accept_direction_facts: bool = False, _position_offset: int = 0,
    formal_key_attacks: bool = False,
) -> list[_SourceTurn]:
    """统一校验直接下级结束来源；原始位置包含开放点以保持来源可追溯。

    retain_geometry 仅供本级候选的 key 索引保留原事件外壳，解析时钟仍
    遵守完成门禁；普通递归及适配器返回的 source 则是同一个结束投影。
    私有追加账本解析尾部时传入原始位置偏移，并包含上一已校验点以核对边界。
    """
    turns: list[_SourceTurn] = []
    clock: Literal['index', 'numeric_string', 'date_string'] | None = None
    previous_order: tuple[int, int] | None = None
    for position, raw in enumerate(points, _position_offset):
        if not isinstance(raw, Mapping):
            raise ValueError('trend source points must be mappings')
        level = raw.get('trend_level')
        if 'trend_level' in raw and (type(level) is not int or level != source_level):
            raise ValueError('trend source points must belong to the immediately lower level')
        if raw.get('state') in ('seed', 'developing') or raw.get('display_only') is True:
            continue
        geometry = raw
        projected = _formal_key_attack_point(raw) if formal_key_attacks else _completed_source_point(raw)
        if accept_direction_facts and raw.get('wave_identity_state') == 'direction_only':
            # 内部方向账本可以参与审计解析，但也必须先经过共同policy/身份
            # 校验；删除policy的direction_only不能降级成legacy closed key。
            projected = dict(raw)
        if projected is None:
            continue
        raw = projected
        index, ordinal = raw.get('index'), raw.get('ordinal', 0)
        kind, price = raw.get('kind'), raw.get('value')
        time, available = raw.get('time'), raw.get('available_at')
        if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
                or kind not in ('H', 'L') or not isinstance(price, (int, float)) or isinstance(price, bool)
                or not isfinite(price) or price <= 0 or not isinstance(time, str) or not time):
            raise ValueError('ordered finite high/low source points required')
        order = index, ordinal
        if previous_order is not None and order <= previous_order:
            raise ValueError('strictly ordered source points required')
        previous_order = order
        if type(available) is int:
            if available < index:
                raise ValueError('source confirmation cannot precede its price index')
            current_clock: Literal['index', 'numeric_string', 'date_string'] = 'index'
            moment: Moment = available
        elif isinstance(available, str) and available:
            numeric_clock = available.isdecimal() and time.isdecimal()
            if (int(available) < int(time) if numeric_clock else available < time):
                raise ValueError('source confirmation cannot precede its price date')
            current_clock = 'numeric_string' if numeric_clock else 'date_string'
            moment = available
        else:
            raise ValueError('source points require a confirmation index or date')
        if clock is not None and current_clock != clock:
            raise ValueError('source confirmation clocks must match')
        clock = current_clock
        # 公共 preceding_turn 只需要相对位置；内部适配器不伪造确认知识。
        # 真实来源确认时间单独保存，输出时覆盖所有已消费的历史证据。
        reversal = ReversalPoint(
            LinePoint(index, ordinal, PointKind(kind), float(price)), index,
            f'level{source_level}_source_position_{position}',
        )
        source = geometry if retain_geometry else raw
        turns.append(_SourceTurn(source, position, index, ordinal, 'H' if kind == 'H' else 'L',
                                 float(price), time, moment, reversal, _source_record_reference(source)))
    return turns


def _reference(turn: _SourceTurn) -> dict[str, object]:
    """复制可审计的真实来源坐标，缺省 ordinal 不改变同一 K 线上的排序。"""
    return dict(index=turn.index, ordinal=turn.ordinal, kind=turn.kind, value=turn.source['value'],
                time=turn.time, available_at=turn.available_at,
                label=turn.source.get('label', turn.kind))


def _strip_source_license(source: Mapping[str, object]) -> dict[str, object]:
    """新建上级点只继承真实来源几何，许可证与等级必须由本级重新取得。

    普通反转、严格关键位升级和可选N新点共享同一复制边界。下级incoming
    上涨证书尤其不能被上级H当成本级入射腿证据。source_confirmation仅保存
    下级为何已存在的审计引用，不参与本级授权；同坐标已存在的本级结构点
    不走此清理函数，其自身更早证书需在追加独立N证据时继续保留。
    """
    result = dict(source)
    for field in (
        'flip', 'trend_level', 'source_level', 'source_turn_position', 'confirmed_on_turn', 'source_reversal_available_at',
        'source_level1_available_at', 'source_level1_position', 'confirmed_on_level1',
        'source_level2_available_at', 'source_level2_position', 'confirmed_on_level2',
        'incoming_trend_confirmation', 'incoming_trend_state', 'active_trend_confirmation',
        'trend_confirmation', 'trend_confirmation_route', 'n_target_confirmation',
        'incoming_leg_confirmation', 'wave_id', 'confirmed_legs', 'leg_confirmation_policy',
        'wave_display_policy', 'confirmation_policy', 'n_target_trend_confirmation_enabled',
        'structural_confirmation_rule', 'definition_evidence', 'direction_definition', 'upgrade_evidence',
        'wave_completion_policy', 'wave_completed_at', 'wave_completion_evidence', 'source_geometry_available_at',
        'wave_identity_state', 'lower_wave_identity_evidence', 'body_reversal_evidence', '_higher_trend_promotions',
    ):
        result.pop(field, None)
    # 来源的确认链只用于说明这个下级点为何已存在，不能供本级 publisher 授权。
    result['source_confirmation'] = deepcopy(source.get('confirmed_by'))
    scope = current_producer_records()
    if scope is not None:
        record = scope.lookup(source)
        reference = record.field_reference('confirmed_by') if record is not None else None
        if reference is not None:
            scope.bind_clone(result['source_confirmation'], source.get('confirmed_by'), reference)
        scope.record_point(result)
    return result


def _candidate_from_source(turn: _SourceTurn) -> dict[str, object]:
    """复制真实来源，移除下级许可证；上级必须由本级门禁重新取得发布证书。"""
    _bind_source_record(turn)
    result = _strip_source_license(turn.source)
    result.update(_reference(turn))
    scope = current_producer_records()
    if scope is not None:
        scope.record_point(result)
    return result


def _later_moment(left: Moment, right: Moment) -> Moment:
    if isinstance(left, str) and isinstance(right, str):
        # 既有几何用例用字符串表示 K 线索引，数值 10 必须晚于 9。
        # 保留原时钟表示，日期字符串则按正常日期顺序比较。
        return max(left, right, key=int) if left.isdecimal() else max(left, right)
    if type(left) is int and type(right) is int:
        return max(left, right)
    raise ValueError('source confirmation clocks must match')


def _preceding_sources(turns: Sequence[_SourceTurn]) -> dict[ReversalPoint, _SourceTurn | None]:
    """线性缓存公共末跌高／末升低定义，只取左侧最近异类反转，不取全局极值。"""
    sources = {turn.reversal: turn for turn in turns}
    result: dict[ReversalPoint, _SourceTurn | None] = {}
    latest: dict[str, _SourceTurn] = {}
    for turn in turns:
        opposite = latest.get('L' if turn.kind == 'H' else 'H')
        neighbors = (turn.reversal,) if opposite is None else (opposite.reversal, turn.reversal)
        prior = preceding_turn(neighbors, turn.reversal)
        result[turn.reversal] = sources[prior] if prior is not None else None
        latest[turn.kind] = turn
    return result


def _ordinary_point(
    extreme: _SourceTurn, evidence: Sequence[_SourceTurn], point: _SourceTurn, *,
    trend_level: int, direction: WaveDirection, current: WaveDirection, prefix_known: Moment,
    key: dict[str, object] | None, owned: bool = False,
) -> dict[str, object]:
    """方向账本与本级整波使用同一四点定义；身份只能由独立因果整波状态授予。"""
    source_level = trend_level - 1
    proof = [_reference(turn) for turn in evidence]
    result = _candidate_from_source(extreme)
    # confirmation_rule 的历史协议名称保留，避免改动证据消费者；名称
    # 中的 then 不再是额外门槛，真实充分条件和确认日由四个来源点证明。
    result.update(trend_level=trend_level, source_level=source_level, available_at=prefix_known,
                  wave_direction_before=direction, wave_direction_after=current,
                  direction_definition=StructuralTrend.BULL.value if current == 'up' else StructuralTrend.BEAR.value, broken_key=key,
                  confirmation_rule='ordered_HH_then_HL_or_LL_then_LH_switch',
                  definition_evidence=proof)
    if source_level == 0:
        result.update(source_reversal_available_at=extreme.available_at,
                      source_turn_position=extreme.position,
                      confirmed_on_turn=point.position, confirmed_by=proof)
    else:
        result.update(source_label=extreme.source.get('label', extreme.kind),
                      confirmed_by=_reference(point),
                      **{f'source_level{source_level}_available_at': extreme.available_at,
                         f'source_level{source_level}_position': extreme.position,
                         f'confirmed_on_level{source_level}': point.position})
    scope = current_producer_records()
    if scope is not None:
        scope.record_point(result)
    _stamp_wave_completion(result, closed=owned, owned=owned)
    return result


class _TrendReversalState:
    """同一已校验、只追加来源的普通定义状态；批量入口也使用此循环。"""

    def __init__(self, trend_level: int) -> None:
        if type(trend_level) is not int or trend_level not in (1, 2, 3):
            raise ValueError('trend_level must be 1, 2 or 3')
        self.trend_level = trend_level
        self.turns: list[_SourceTurn] = []
        self.highs: list[int] = []
        self.lows: list[int] = []
        self.direction: WaveDirection | None = None
        self.candidate: int | None = None
        self.selected: list[dict[str, object]] = []
        self.prefix_known: Moment | None = None
        self._latest_sources: dict[str, _SourceTurn] = {}
        self._sources_by_reversal: dict[ReversalPoint, _SourceTurn] = {}
        self._preceding_sources: dict[ReversalPoint, _SourceTurn | None] = {}

    def extend(self, appended: Sequence[_SourceTurn], *, _snapshot: bool = True) -> list[dict[str, object]]:
        reversals = {point.reversal for point in appended}
        if self.turns and (len(reversals) != len(appended)
                           or any(reversal in self._sources_by_reversal for reversal in reversals)):
            # 重复 ReversalPoint key 可能改变过去的 preceding 映射；保留
            # 全前缀批量行为，不把此修订当作纯追加。
            combined = [*self.turns, *appended]
            _TrendReversalState.__init__(self, self.trend_level)
            return self.extend(combined, _snapshot=_snapshot)
        start = len(self.turns)
        if not self.turns:
            self._preceding_sources = _preceding_sources(appended)
            self._sources_by_reversal = {point.reversal: point for point in appended}
            for point in appended:
                self._latest_sources[point.kind] = point
        else:
            for point in appended:
                opposite = self._latest_sources.get('L' if point.kind == 'H' else 'H')
                neighbors = (point.reversal,) if opposite is None else (opposite.reversal, point.reversal)
                prior = preceding_turn(neighbors, point.reversal)
                self._preceding_sources[point.reversal] = self._sources_by_reversal.get(prior) if prior is not None else None
                self._sources_by_reversal[point.reversal] = point
                self._latest_sources[point.kind] = point
        self.turns.extend(appended)
        turns, highs, lows = self.turns, self.highs, self.lows
        direction, candidate, prefix_known = self.direction, self.candidate, self.prefix_known
        for position, point in enumerate(appended, start):
            prefix_known = point.available_at if prefix_known is None else _later_moment(prefix_known, point.available_at)
            (highs if point.kind == 'H' else lows).append(position)
            current_trend = classify_trend_prices(
                [turns[index].value for index in highs[-2:]],
                [turns[index].value for index in lows[-2:]],
                trend_level=self.trend_level,
            )
            current: WaveDirection | None = (
                'up' if current_trend == StructuralTrend.BULL else
                'down' if current_trend == StructuralTrend.BEAR else None
            )
            if direction is None:
                if current is None:
                    continue
                direction = current
                candidate = (highs if direction == 'up' else lows)[-1]
                continue
            assert candidate is not None
            target = 'H' if direction == 'up' else 'L'
            sign = 1 if direction == 'up' else -1
            if point.kind == target and sign * (point.value - turns[candidate].value) > 0:
                candidate = position
            if current is None or current == direction:
                continue
            extreme = turns[candidate]
            evidence = [turns[index] for index in [*highs[-2:], *lows[-2:]]]
            preceding = self._preceding_sources[extreme.reversal]
            key = _reference(preceding) if preceding is not None else None
            result = _ordinary_point(extreme, evidence, point, trend_level=self.trend_level,
                direction=direction, current=current, prefix_known=prefix_known, key=key)
            self.selected.append(result)
            direction = current
            pool = highs if direction == 'up' else lows
            tail_start = bisect_right(pool, candidate)
            # 只访问整波极值之后的异类来源索引；历史原点、同类点和价格
            # 坐标仍完整保留，等价极值继续选择原来源顺序中的第一个。
            selected = (max if direction == 'up' else min)(range(tail_start, len(pool)),
                key=lambda offset: turns[pool[offset]].value)
            candidate = pool[selected]
        self.direction, self.candidate, self.prefix_known = direction, candidate, prefix_known
        # 同一判定器内部只读借用追加账本；外部调用仍得到独立列表。
        return list(self.selected) if _snapshot else self.selected


def trend_reversals(
    points: Sequence[Mapping[str, object]], *, trend_level: int,
) -> list[dict[str, object]]:
    """按高点、低点共同严格升降记录任一级的普通方向事实。

    多头方向由高点与低点同时严格抬高确认，空头方向完全镜像。上涨整波
    结束时必须同时有更低低点和更低高点；下跌整波则必须同时有更高
    高点和更高低点。两类证据不限定先后：HL→HH 与 HH→HL 都能确认多头，
    LH→LL 与 LL→LH 都能确认空头。来源自身必须已经反转确认，单个局部
    正／负反转、相等极值或高低不同向仍不足以改变本级整波方向。

    初始方向只使用最近两类点，不从方向确立前的历史导入极值；方向成立
    后持续保存整波最高 H／最低 L。价格坐标保留原日期，知识日期覆盖
    已消耗来源前缀，旧方向、旧关键位等证据滚出最近四点后仍不能被遗忘。
    普通候选的 broken_key 仅指极值左侧最近反转，不代表价格已经突破它；
    direction_definition 记录多空定义，严格突破升级由独立模块证明。

    未确认尾端和纯显示点不参与任何层级；同日多点由 ordinal 排序。
    输入必须是调用方已发布的直接下级前缀，原 source_position 不因跳过
    未完点而改变。非法等级、来源、价格、排序或混合时钟抛出 ValueError。
    输出保留完整四点方向账本，标为 direction_only；它本身不授予本级
    整波身份、闭合关键位或递归资格。共同整波状态只在反向组合下级波
    真正成立或严格旧 key 突破时发布 owned 几何，K 线/N 仅另行核验交易资格。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise ValueError('trend source points must be a sequence of mappings')
    source_level = trend_level - 1
    turns = _source_turns(points, source_level)
    return _TrendReversalState(trend_level).extend(turns)
