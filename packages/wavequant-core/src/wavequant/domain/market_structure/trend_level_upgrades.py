"""在共同趋势定义之外，保留严格突破已知本级高低点的独立升级通道。

高高／低高和低低／高低负责描述趋势方向。本模块只证明另一种事实：
直接下级整波确实结束的端点严格越过此前已知的本级关键位，因此整波起点
与攻击端点可以升入本级。下级自己的结束证明不可省略，本级则不再额外
等待 future holding 点。几何首次出现、下级结束和本级方向是三个独立事实。
"""

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy

from .trend_levels import (
    Moment, _SourceTurn, _candidate_from_source, _later_moment, _reference, _source_turns,
    _stamp_wave_completion, _ordinary_point, _preceding_sources, WaveDirection,
)

from .trend_structure import StructuralTrend, classify_trend_prices

__all__ = ['promote_trend_key_breaks']


def _known_by(known: Moment, cutoff: Moment) -> bool:
    if type(known) is int and type(cutoff) is int:
        return known <= cutoff
    if isinstance(known, str) and isinstance(cutoff, str):
        if known.isdecimal() != cutoff.isdecimal():
            raise ValueError('source and candidate confirmation clocks must match')
        return int(known) <= int(cutoff) if known.isdecimal() else known <= cutoff
    raise ValueError('source and candidate confirmation clocks must match')


def _order(point: _SourceTurn) -> tuple[int, int]:
    return point.index, point.ordinal


def _identity(point: _SourceTurn) -> tuple[int, int, str, float]:
    return point.index, point.ordinal, point.kind, point.value


def _latest_key(left: _SourceTurn, right: _SourceTurn) -> _SourceTurn:
    return left if _order(left) >= _order(right) else right


def _lowest_origin(left: _SourceTurn, right: _SourceTurn) -> _SourceTurn:
    return left if (left.value, _order(left)) <= (right.value, _order(right)) else right


def _highest_origin(left: _SourceTurn, right: _SourceTurn) -> _SourceTurn:
    return left if (-left.value, _order(left)) <= (-right.value, _order(right)) else right


class _PointRange:
    """按价格坐标维护已知点的区间查询，每次更新／查询 O(log n)。

    关键位查询选择攻击左侧最近本级点；整波查询选择指定区间最高／最低
    下级点。相同价格始终保留最早来源，延迟确认也不需要重新扫描整个前缀。
    """

    def __init__(self, count: int, choose: Callable[[_SourceTurn, _SourceTurn], _SourceTurn]) -> None:
        self.size = 1
        while self.size < count:
            self.size *= 2
        self.points: list[_SourceTurn | None] = [None] * (2 * self.size)
        self.choose = choose

    def _combine(self, left: _SourceTurn | None, right: _SourceTurn | None) -> _SourceTurn | None:
        if left is None:
            return right
        if right is None:
            return left
        return self.choose(left, right)

    def update(self, position: int, point: _SourceTurn) -> None:
        cursor = self.size + position
        self.points[cursor] = point
        cursor //= 2
        while cursor:
            self.points[cursor] = self._combine(self.points[2 * cursor], self.points[2 * cursor + 1])
            cursor //= 2

    def query(self, start: int, end: int) -> _SourceTurn | None:
        left, right = self.size + start, self.size + end
        result: _SourceTurn | None = None
        while left < right:
            if left % 2:
                result = self._combine(result, self.points[left])
                left += 1
            if right % 2:
                right -= 1
                result = self._combine(result, self.points[right])
            left //= 2
            right //= 2
        return result


def _store_closed_key(tree: _PointRange, position: int, point: _SourceTurn) -> None:
    """闭合知识独立于最早几何；同坐标的后来证明不能覆盖首次真实结束日。

    earlieropen 没有资格进入树，laterordinary 必须补入；若此前 strictorigin
    已真正结束18，则 laterordinary20 仅增加账本证据，不能把旧 key 拖迟到20。
    单坐标查询仍为 O(log n)，不扫描或重放候选历史。
    """
    existing = tree.query(position, position + 1)
    if existing is None or _known_by(point.available_at, existing.available_at):
        tree.update(position, point)


def _key_known_before_price(key: _SourceTurn, attack: _SourceTurn) -> bool:
    """关键位首次正式可知必须严格早于攻击价格日，同日才知不算旧关键位。"""
    known = key.available_at
    if isinstance(known, int):
        return known < attack.index
    if known.isdecimal() and attack.time.isdecimal():
        return int(known) < int(attack.time)
    return known < attack.time


def _promoted_point(
    source: _SourceTurn, *, attack: _SourceTurn, origin: _SourceTurn,
    key: _SourceTurn, trend_level: int, known: Moment, endpoint: bool,
) -> dict[str, object]:
    source_level = trend_level - 1
    rising = attack.kind == 'H'
    key_name = 'last_fall_high' if rising else 'last_rise_low'
    point_name = 'high' if rising else 'low'
    rule = f'level{source_level}_confirmed_{point_name}_breaks_known_level{trend_level}_{key_name}'
    result = _candidate_from_source(source)
    reference = _reference(attack)
    result.update(
        trend_level=trend_level, source_level=source_level, available_at=known,
        source_label=source.source.get('label', source.kind), broken_key=_reference(key),
        confirmed_by=reference, confirmation_rule=rule if endpoint else rule + '_origin',
        upgrade_evidence=dict(
            direction='up' if rising else 'down', role='endpoint' if endpoint else 'origin', available_at=known,
            confirmation_rule=rule, origin=_reference(origin),
            broken_key=_reference(key), confirmed_by=reference,
        ),
    )
    if source_level == 0:
        result.update(source_turn_position=source.position, confirmed_on_turn=attack.position,
                      source_reversal_available_at=source.available_at)
    else:
        result.update(**{f'source_level{source_level}_position': source.position,
                         f'source_level{source_level}_available_at': source.available_at,
                         f'confirmed_on_level{source_level}': attack.position})
    if endpoint:
        level_name = ('一级', '二级', '三级')[trend_level - 1]
        result.update(flip=level_name + ('末跌高突破升级' if rising else '末升低突破升级'),
                      wave_direction_before='up' if rising else 'down',
                      wave_direction_after='up' if rising else 'down')
    else:
        result.update(flip='翻空为多' if rising else '翻多为空',
                      wave_direction_before='down' if rising else 'up',
                      wave_direction_after='up' if rising else 'down')
    _stamp_wave_completion(result, closed=not endpoint)
    return result


def promote_trend_key_breaks(
    source: Sequence[Mapping[str, object]], candidates: Sequence[Mapping[str, object]], *, trend_level: int,
    event_sink: list[dict[str, object]] | None = None,
) -> list[dict[str, object]]:
    """统一推进本级整波身份和严格升级，方向账本与整波状态各自保留。

    ``source`` 只能来自完整结束的直接下级：一级消费折线，二级消费一级，
    三级消费二级；开放升级点和显示尾端由共同来源校验排除。``candidates`` 是
    共同定义的方向账本，只有取得 owned 身份的闭合点才可成为本级 key。
    普通相反方向若整波退化为一条直接下级波，保持 pending，不改变既有
    owned 方向、起点或极值。以后反向波包含真实内部下级拐点成为组合波，
    或严格突破旧本级 key，才在实际身份可知日关闭和切换本级整波。

    下级 H 严格高于本级旧 H 时，在旧 H 与攻击 H 之间的已知下级 L 中取
    整波最低点；下级 L 严格低于本级旧 L 时，镜像取整波最高 H。相等极值
    保留最早位置，不用离攻击更近但较高／较低的局部起点缩短整波。

    起点和攻击端点可同日升入本级，知识日覆盖来源、旧关键位以及已消耗
    前缀；不读取攻击之后的下级 holding 点，也不为未来反向波补造拐点。
    开放攻击端点仅可显示，不能立即成为已结束本级关键位。同向严格突破
    继续延伸已经存在的整波端点，保留原整波起点，不把每个下级局部回调
    插成本级正／负反转。只有普通双类反向定义或严格反向关键位突破才能
    且取得本级整波身份才能结束此前整波；严格反向起点仍取此前整个区间的最高／最低极值。
    来源坐标和原确认日分别保留，返回新的字典，不修改输入。

    返回按价格位置排序的 owned 几何并集；未升格方向事实仍留在调用方
    的普通账本。裸 confirmed 候选保留原有声明兼容，有生成标记的候选必须
    通过完整身份/闭合/真实来源校验。独立升级更早确认同一个整波起点时，
    保留该更早证据，不借用普通候选的未来四点证明。此处结构资格仍交给
    原有 publication gate 检查真实行情，升级函数不代替交易许可。
    event_sink额外收集每一份真实严格升级证据；同坐标早期事实仍用于推进
    关键位状态，但后知升级不能因此消失。该审计通道不改变默认最早并集，
    不改写已知日期，也不让尚未知的后续证明提前取得资格。
    非法等级、来源、价格、坐标或不一致时钟抛出 ValueError。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    for points in (source, candidates):
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
            raise ValueError('source and candidates must be sequences of mappings')
    turns = _source_turns(source, trend_level - 1)
    # 本级普通事件只借投影时钟进入 key 树，返回 ledger/geometry 必须仍为
    # 原事件自己的副本，不能把“供上级消费”的来源审计字段混回本级输出。
    regular = _source_turns(candidates, trend_level, retain_geometry=True, accept_direction_facts=True)
    if not turns:
        return []
    merged: dict[tuple[int, int, str, float], _SourceTurn] = {}
    orders = sorted({_order(point) for point in [*turns, *regular]})
    positions = {order: position for position, order in enumerate(orders)}
    source_by_order = {_order(point): point for point in turns}
    keys_by_kind = {kind: _PointRange(len(orders), _latest_key) for kind in ('H', 'L')}
    origins_by_kind = {'H': _PointRange(len(orders), _highest_origin),
                       'L': _PointRange(len(orders), _lowest_origin)}
    # 先按知识日期排好本级候选，只在首次已知时入树一次；价格位置仍单独
    # 限定在攻击点左侧，延迟确认不能让未来价格坐标成为旧关键位。
    numeric_clock = isinstance(turns[0].available_at, str) and turns[0].available_at.isdecimal()
    for point in regular:
        _known_by(point.available_at, turns[0].available_at)
    regular.sort(key=lambda point: int(point.available_at) if numeric_clock else point.available_at)
    regular_cursor = 0
    prefix_known: Moment | None = None
    # 方向事实与本级整波身份分别推进。pending 的单条下级反向波不会切换
    # owned 方向，故其后来局部回调也不能被当作新的本级起点或 closed key。
    owned_direction: WaveDirection | None = None
    whole_origin: _SourceTurn | None = None
    whole_extreme: _SourceTurn | None = None
    recent: dict[str, list[_SourceTurn]] = {'H': [], 'L': []}
    source_positions = {_order(point): position for position, point in enumerate(turns)}
    preceding_sources = _preceding_sources(turns)

    def publish_owned(raw: dict[str, object], actual: _SourceTurn, known: Moment, *, closed: bool) -> None:
        event = _SourceTurn(raw, actual.position, actual.index, actual.ordinal, actual.kind,
                            actual.value, actual.time, known, actual.reversal)
        existing = merged.get(_identity(event))
        if existing is None or not _known_by(existing.available_at, known):
            merged[_identity(event)] = event
        if closed:
            _store_closed_key(keys_by_kind[event.kind], positions[_order(event)], event)
        if event_sink is not None:
            event_sink.append(deepcopy(raw))

    for attack in turns:
        prefix_known = attack.available_at if prefix_known is None else _later_moment(prefix_known, attack.available_at)
        while regular_cursor < len(regular) and _known_by(regular[regular_cursor].available_at, prefix_known):
            point = regular[regular_cursor]
            regular_cursor += 1
            actual = source_by_order.get(_order(point))
            # closure14 不能替 geometry10 掩盖来源11才知的越级先出现；结束
            # 投影与本级几何首次出现都必须满足同一个真实下级来源边界。
            geometry_known = point.source.get('source_geometry_available_at', point.source.get('available_at'))
            if (actual is None or _identity(actual) != _identity(point) or actual.time != point.time
                    or not (type(geometry_known) is int or isinstance(geometry_known, str) and geometry_known)
                    or not _known_by(actual.available_at, geometry_known)
                    or not _known_by(actual.available_at, point.available_at)):
                raise ValueError('a current-level candidate must retain its actual confirmed source provenance')
            # 普通方向账本没有整波身份，不可进入 key 树。旧裸 confirmed
            # 候选的声明契约仍兼容；有生成标记的候选必须由本次共同状态重建。
            legacy_candidate = point.source.get('wave_completion_policy') is None
            if legacy_candidate or point.source.get('wave_identity_state') == 'owned':
                identity = _identity(point)
                if identity not in merged or _known_by(point.available_at, merged[identity].available_at):
                    merged[identity] = point
                _store_closed_key(keys_by_kind[point.kind], positions[_order(point)], point)
                # 已校验 owned 候选可补充闭合 key，但正常来源扫描仍掌握
                # whole phase；晚外部证明不得重置极值或把 whole origin 拉退。
                if legacy_candidate and _order(point) < _order(attack):
                    owned_direction = 'down' if point.kind == 'H' else 'up'
                    whole_origin = actual
                    whole_extreme = None
        previous_extreme = whole_extreme
        attack_position = positions[_order(attack)]
        origins_by_kind[attack.kind].update(attack_position, attack)
        recent[attack.kind].append(attack)
        recent[attack.kind] = recent[attack.kind][-2:]
        classification = classify_trend_prices([point.value for point in recent['H']],
                                               [point.value for point in recent['L']], trend_level=trend_level)
        current: WaveDirection | None = ('up' if classification == StructuralTrend.BULL else
                                         'down' if classification == StructuralTrend.BEAR else None)
        if owned_direction is None and current is not None:
            owned_direction = current
            whole_extreme = recent['H' if current == 'up' else 'L'][-1]
        if owned_direction is not None:
            target = 'H' if owned_direction == 'up' else 'L'
            sign = 1 if owned_direction == 'up' else -1
            if attack.kind == target and (whole_extreme is None or sign * (attack.value - whole_extreme.value) > 0):
                whole_extreme = attack
            if current is not None and current != owned_direction and whole_extreme is not None:
                counter_kind = 'H' if current == 'up' else 'L'
                counter = origins_by_kind[counter_kind].query(positions[_order(whole_extreme)] + 1,
                                                              attack_position + 1)
                if counter is not None:
                    left, right = source_positions[_order(whole_extreme)], source_positions[_order(counter)]
                    # 真正组合波须包含两端之间的实际已结束下级拐点。不是最终
                    # 坐标去重，也不按日期/价格阈值猜测；单 child 保留方向事实，
                    # 等组合波身份或本级旧 key 严格突破成立才允许切换 owned。
                    if right - left > 1:
                        opposite = preceding_sources[whole_extreme.reversal]
                        proof = [*recent['H'], *recent['L']]
                        raw = _ordinary_point(whole_extreme, proof, attack, trend_level=trend_level,
                            direction=owned_direction, current=current, prefix_known=prefix_known,
                            key=_reference(opposite) if opposite is not None else None, owned=True)
                        raw['lower_wave_identity_evidence'] = dict(available_at=prefix_known,
                            origin=_reference(whole_extreme), interior=_reference(turns[left + 1]),
                            endpoint=_reference(counter))
                        _stamp_wave_completion(raw, closed=True)
                        publish_owned(raw, whole_extreme, prefix_known, closed=True)
                        whole_origin, whole_extreme, owned_direction = whole_extreme, counter, current
        key = keys_by_kind[attack.kind].query(0, attack_position)
        if key is None:
            continue
        rising = attack.kind == 'H'
        sign = 1 if rising else -1
        if sign * (attack.value - key.value) <= 0 or not _key_known_before_price(key, attack):
            continue
        origin_kind = 'L' if rising else 'H'
        # 当前整波由 owned 状态决定，不能被 pending 普通方向点重置。严格
        # 同向攻击始终保留原 whole origin；反向攻击必须关闭真实整波极值。
        attack_direction: WaveDirection = 'up' if rising else 'down'
        continuing = owned_direction == attack_direction and whole_origin is not None
        origin = (whole_origin if continuing else whole_extreme
                  if owned_direction is not None and owned_direction != attack_direction and whole_extreme is not None
                  else origins_by_kind[origin_kind].query(positions[_order(key)] + 1, attack_position))
        if origin is None:
            continue
        if (continuing and previous_extreme is not None and previous_extreme.kind == attack.kind
                and sign * (attack.value - previous_extreme.value) <= 0):
            # 再次越过同一个旧 key 却没有延伸既有整波极值，只是波内回调。
            # 不能把 H50 的活跃极值改成后来 H48，再用 H48 关闭整波。
            continue
        # 新反向整波必须从旧 key 另一侧起波；已经由真实反转建立的同向
        # 整波可能因跳空或多步抬高整体位于旧 key 以上／以下，此时再强求
        # 起波 cross 会丢掉合法极值延伸。方向已成立仍不等于端点已闭合。
        if not continuing and sign * (key.value - origin.value) <= 0:
            continue
        known = _later_moment(prefix_known, _later_moment(key.available_at, origin.available_at))
        # 同向只新增攻击端点几何；独立 origin 证明仍保留在审计账本，但
        # origin 固定为已有整波起点，绝不改取局部回调 L3/H3，也不重置 key
        # 知识。这样 ordinary14@18 与 laterstrict14@20 均保留，而 H2→L3
        # 的假反向线不会被制造。反向突破才发布整个旧波的真实极值 origin。
        if continuing and event_sink is not None and sign * (key.value - origin.value) > 0:
            event_sink.append(deepcopy(_promoted_point(origin, attack=attack, origin=origin, key=key,
                                                       trend_level=trend_level, known=known, endpoint=False)))
        promoted_points = ((attack, True),) if continuing else ((origin, False), (attack, True))
        for source_point, endpoint in promoted_points:
            raw = _promoted_point(source_point, attack=attack, origin=origin, key=key,
                                  trend_level=trend_level, known=known, endpoint=endpoint)
            publish_owned(raw, source_point, known, closed=not endpoint)
        if not continuing:
            whole_origin = origin
        whole_extreme, owned_direction = attack, attack_direction
    return [dict(point.source) for point in sorted(merged.values(), key=_order)]
