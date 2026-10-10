"""在共同趋势定义之外，保留严格突破已知本级高低点的独立升级通道。

高高／低高和低低／高低负责描述趋势方向。本模块只证明另一种事实：
已确认下级拐点严格越过此前已知的本级关键位，因此整波起点与攻击端点可以
立即升入本级，不必再等待攻击端点之后的下级 holding 点。两种证据互不冒充。
"""

from collections.abc import Callable, Mapping, Sequence

from .trend_levels import Moment, _SourceTurn, _candidate_from_source, _later_moment, _reference, _source_turns

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
    return result


def promote_trend_key_breaks(
    source: Sequence[Mapping[str, object]], candidates: Sequence[Mapping[str, object]], *, trend_level: int,
) -> list[dict[str, object]]:
    """将严格本级关键位突破与普通有序趋势候选合并，保持高低镜像与因果来源。

    ``source`` 只能来自已经发布的直接下级：一级消费折线，二级消费一级，
    三级消费二级；未完成点和显示尾端由共同来源校验排除。``candidates`` 是
    共同趋势定义已经确认的本级结构，未来才知的候选不能充当历史关键位。

    下级 H 严格高于本级旧 H 时，在旧 H 与攻击 H 之间的已知下级 L 中取
    整波最低点；下级 L 严格低于本级旧 L 时，镜像取整波最高 H。相等极值
    保留最早位置，不用离攻击更近但较高／较低的局部起点缩短整波。

    起点和攻击端点可同日升入本级，知识日覆盖来源、旧关键位以及已消耗
    前缀；不读取攻击之后的下级 holding 点，也不为未来反向波补造拐点。
    当前升级点随后可以成为新的已知本级关键位，继续按同一规则推进。
    来源坐标和原确认日分别保留，返回新的字典，不修改输入。

    返回按价格位置排序的候选并集。独立升级更早确认同一个整波起点时，
    保留该更早证据，不借用普通候选的未来四点证明。此处结构资格仍交给
    原有 publication gate 检查真实行情，升级函数不代替交易许可。
    非法等级、来源、价格、坐标或不一致时钟抛出 ValueError。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    for points in (source, candidates):
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
            raise ValueError('source and candidates must be sequences of mappings')
    turns = _source_turns(source, trend_level - 1)
    regular = _source_turns(candidates, trend_level)
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
    for attack in turns:
        prefix_known = attack.available_at if prefix_known is None else _later_moment(prefix_known, attack.available_at)
        while regular_cursor < len(regular) and _known_by(regular[regular_cursor].available_at, prefix_known):
            point = regular[regular_cursor]
            regular_cursor += 1
            actual = source_by_order.get(_order(point))
            if (actual is None or _identity(actual) != _identity(point) or actual.time != point.time
                    or not _known_by(actual.available_at, point.available_at)):
                raise ValueError('a current-level candidate must retain its actual confirmed source provenance')
            identity = _identity(point)
            if identity not in merged or _known_by(point.available_at, merged[identity].available_at):
                merged[identity] = point
                keys_by_kind[point.kind].update(positions[_order(point)], point)
        attack_position = positions[_order(attack)]
        origins_by_kind[attack.kind].update(attack_position, attack)
        key = keys_by_kind[attack.kind].query(0, attack_position)
        if key is None:
            continue
        rising = attack.kind == 'H'
        sign = 1 if rising else -1
        if sign * (attack.value - key.value) <= 0 or not _key_known_before_price(key, attack):
            continue
        origin_kind = 'L' if rising else 'H'
        origin = origins_by_kind[origin_kind].query(positions[_order(key)] + 1, attack_position)
        if origin is None:
            continue
        if sign * (key.value - origin.value) <= 0:
            continue
        known = _later_moment(prefix_known, _later_moment(key.available_at, origin.available_at))
        for source_point, endpoint in ((origin, False), (attack, True)):
            raw = _promoted_point(source_point, attack=attack, origin=origin, key=key,
                                  trend_level=trend_level, known=known, endpoint=endpoint)
            promoted = _SourceTurn(raw, source_point.position, source_point.index, source_point.ordinal,
                                   source_point.kind, source_point.value, source_point.time, known,
                                   source_point.reversal)
            identity = _identity(promoted)
            existing = merged.get(identity)
            if existing is None or not _known_by(existing.available_at, known):
                merged[identity] = promoted
                keys_by_kind[promoted.kind].update(positions[_order(promoted)], promoted)
    return [dict(point.source) for point in sorted(merged.values(), key=_order)]
