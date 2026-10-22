"""在共同趋势定义之外，保留严格突破已知本级高低点的独立升级通道。

高高／低高和低低／高低负责描述趋势方向。本模块只证明另一种事实：
直接下级整波确实结束的端点严格越过此前已知的本级关键位，因此整波起点
与攻击端点可以升入本级。下级自己的结束证明不可省略，本级则不再额外
等待 future holding 点。几何首次出现、下级结束和本级方向是三个独立事实。
"""

from collections.abc import Callable, Mapping, MutableSequence, Sequence
from bisect import insort
from copy import deepcopy
from typing import cast

from .owned_evidence_producer import current_producer_records
from .trend_levels import (
    Moment, _SourceTurn, _candidate_from_source, _later_moment, _reference, _source_record_reference, _source_turns,
    _stamp_wave_completion, _ordinary_point, _preceding_sources, WaveDirection,
)

from .trend_structure import StructuralTrend, classify_trend_prices, preceding_turn
from .trend_body_reversal import BODY_REVERSAL_RULE, body_reference, rejects_wick_extension
from .trend_promotion_scope import source_promotions, validate_promotion
from .trend_frozen_state import FrozenPrefixMap

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
        # 路径检查点共享已固化的树节点；一次更新仅改 O(log n) 个尾部覆盖。
        self.points: FrozenPrefixMap[int, _SourceTurn | None] = FrozenPrefixMap()
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
            self.points[cursor] = self._combine(self.points.get(2 * cursor), self.points.get(2 * cursor + 1))
            cursor //= 2

    def ensure_size(self, count: int) -> None:
        if count <= self.size:
            return
        previous_size, previous = self.size, self.points
        while self.size < count:
            self.size *= 2
        self.points = FrozenPrefixMap((self.size + position, previous[previous_size + position])
            for position in range(previous_size) if previous_size + position in previous)
        for cursor in range(self.size - 1, 0, -1):
            combined = self._combine(self.points.get(2 * cursor), self.points.get(2 * cursor + 1))
            if combined is not None:
                self.points[cursor] = combined

    def query(self, start: int, end: int) -> _SourceTurn | None:
        left, right = self.size + start, self.size + end
        result: _SourceTurn | None = None
        while left < right:
            if left % 2:
                result = self._combine(result, self.points.get(left))
                left += 1
            if right % 2:
                right -= 1
                result = self._combine(result, self.points.get(right))
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
    _stamp_wave_completion(result, closed=not endpoint, _record_new=True)
    scope = current_producer_records()
    if scope is not None:
        scope.ensure_record(result)
    return result


class _TrendUpgradeState:
    """相同只追加来源的整波状态；新的旧知识候选必须回退完整批量判断。"""

    def __init__(self, turns: Sequence[_SourceTurn], regular: Sequence[_SourceTurn], *, trend_level: int,
                 event_sink: MutableSequence[dict[str, object]] | None = None,
                 ordinary_trend_completion_enabled: bool = False, strict_only: bool = False) -> None:
        if type(trend_level) is not int or trend_level not in (1, 2, 3):
            raise ValueError('trend_level must be 1, 2 or 3')
        self.trend_level = trend_level
        self.strict_only = strict_only
        if type(ordinary_trend_completion_enabled) is not bool:
            raise ValueError('ordinary_trend_completion_enabled must be a boolean')
        self.ordinary_trend_completion_enabled = ordinary_trend_completion_enabled
        self.turns = list(turns)
        self.promotions = source_promotions(turns, trend_level)
        self.regular = list(regular)
        self.event_sink = event_sink
        self.merged: dict[tuple[int, int, str, float], _SourceTurn] = {}
        self.merged_order: list[tuple[int, int, str, float]] = []
        self.updated_merged: dict[tuple[int, int, str, float], _SourceTurn] = {}
        self.new_merged_keys: list[tuple[int, int, str, float]] = []
        self.orders = sorted({_order(point) for point in [*turns, *regular]})
        self.positions = {order: position for position, order in enumerate(self.orders)}
        self.source_by_order = {_order(point): point for point in turns}
        self.source_by_reversal = {point.reversal: point for point in turns}
        self.latest_sources: dict[str, _SourceTurn] = {}
        for point in turns:
            self.latest_sources[point.kind] = point
        self.keys_by_kind = {kind: _PointRange(len(self.orders), _latest_key) for kind in ('H', 'L')}
        self.origins_by_kind = {'H': _PointRange(len(self.orders), _highest_origin),
                               'L': _PointRange(len(self.orders), _lowest_origin)}
        self.numeric_clock = bool(turns and isinstance(turns[0].available_at, str)
                                  and turns[0].available_at.isdecimal())
        for point in regular:
            if turns:
                _known_by(point.available_at, turns[0].available_at)
        self.regular.sort(key=lambda point: int(point.available_at) if self.numeric_clock else point.available_at)
        self.regular_cursor = 0
        self.prefix_known: Moment | None = None
        self.owned_direction: WaveDirection | None = None
        self.whole_origin: _SourceTurn | None = None
        self.whole_extreme: _SourceTurn | None = None
        # 首段价格上下文尚非 owned 波；不能进入闭合 key 或向上递归。
        self.pending_origin: _SourceTurn | None = None
        self.pending_extreme: _SourceTurn | None = None
        # 每个 level/path 只有这两个当前关键位标记，值为完整来源序列的位置。
        # 历史 key 树继续保留审计及修订恢复资料；普通最新 H/L 不更新标记。
        self.last_fall_high_position: int | None = None
        self.last_rise_low_position: int | None = None
        self.recent: dict[str, list[_SourceTurn]] = {'H': [], 'L': []}
        self.source_positions = {_order(point): position for position, point in enumerate(turns)}
        self.preceding_sources = _preceding_sources(turns)
        self.processed_count = 0
        self._consumed_source_count = 0
        self.body_rejection: tuple[_SourceTurn, _SourceTurn, _SourceTurn] | None = None

    def append(self, turns: Sequence[_SourceTurn], regular: Sequence[_SourceTurn]) -> bool:
        # 此门禁必须在写入任何状态前完成；同日新普通事实可能属于已经处理的
        # attack，不能将它补到历史循环末端。调用者同时核对旧来源／候选全文。
        if self.processed_count != len(self.turns) or self.regular_cursor != len(self.regular):
            return False
        if len(self.orders) != len(self.source_by_order):
            return False
        reversals = {point.reversal for point in turns}
        if len(reversals) != len(turns) or any(reversal in self.source_by_reversal for reversal in reversals):
            return False
        if self.turns and any(_order(point) <= _order(self.turns[-1]) for point in turns):
            return False
        if self.prefix_known is not None and any(_known_by(point.available_at, self.prefix_known)
                                                for point in regular):
            return False
        appended_orders = {_order(point) for point in turns}
        if any(_order(point) not in self.source_by_order and _order(point) not in appended_orders for point in regular):
            return False
        for point in regular:
            if self.turns:
                _known_by(point.available_at, self.turns[0].available_at)
        start = len(self.turns)
        if not self.turns and turns:
            self.numeric_clock = isinstance(turns[0].available_at, str) and turns[0].available_at.isdecimal()
        self.turns.extend(turns)
        if any('_higher_trend_promotions' in point.source for point in turns):
            self.promotions = source_promotions(self.turns, self.trend_level)
        for position, point in enumerate(turns, start):
            order = _order(point)
            self.positions[order] = len(self.orders)
            self.orders.append(order)
            self.source_by_order[order] = point
            self.source_positions[order] = position
            opposite = self.latest_sources.get('L' if point.kind == 'H' else 'H')
            neighbors = (point.reversal,) if opposite is None else (opposite.reversal, point.reversal)
            prior = preceding_turn(neighbors, point.reversal)
            self.preceding_sources[point.reversal] = self.source_by_reversal.get(prior) if prior is not None else None
            self.source_by_reversal[point.reversal] = point
            self.latest_sources[point.kind] = point
        for tree in [*self.keys_by_kind.values(), *self.origins_by_kind.values()]:
            tree.ensure_size(len(self.orders))
        # 已消费普通事实不晚于 prefix_known；门禁保证新增事实严格更晚，
        # 因此只对本批排序，旧账本和前驱映射无需重新扫描。
        self.regular.extend(sorted(regular,
            key=lambda point: int(point.available_at) if self.numeric_clock else point.available_at))
        return True

    def _store_merged(self, point: _SourceTurn) -> None:
        identity = _identity(point)
        if identity not in self.merged:
            # 来源价格坐标严格唯一，候选也已核对 actual identity；因此完整
            # identity 顺序与原 (index, ordinal) 稳定顺序相同，不新增同坐标仲裁。
            insort(self.merged_order, identity)
            self.new_merged_keys.append(identity)
        self.merged[identity] = point
        self.updated_merged[identity] = point

    def _attack_key(self, attack: _SourceTurn, *, direction: WaveDirection | None,
                    origin: _SourceTurn | None) -> _SourceTurn | None:
        """从本级当前末跌高／末升低位置续查，反向攻击优先使用冻结整波起点。"""
        tree = self.keys_by_kind[attack.kind]
        attack_position = self.positions[_order(attack)]
        attack_direction = 'up' if attack.kind == 'H' else 'down'
        key: _SourceTurn | None = None
        if direction is not None and direction != attack_direction and origin is not None and origin.kind == attack.kind:
            origin_position = self.positions[_order(origin)]
            if origin_position < attack_position:
                key = tree.query(origin_position, origin_position + 1)
        if key is None:
            marker = self.last_fall_high_position if attack.kind == 'H' else self.last_rise_low_position
            start = self.positions[_order(self.turns[marker])] if marker is not None else 0
            # 迟知来源可能提前把右侧 key 加入树；它不能遮住当前攻击左侧
            # 的合法关键位。检查点或冷重建仍保留原完整历史查询的边界。
            if start >= attack_position:
                start = 0
            key = tree.query(start, attack_position)
        if key is not None and _key_known_before_price(key, attack):
            position = self.source_positions[_order(key)]
            if attack.kind == 'H':
                self.last_fall_high_position = position
            else:
                self.last_rise_low_position = position
        return key

    def _released_promotion(self, origin: _SourceTurn | None, extreme: _SourceTurn,
                            attack: _SourceTurn, known: Moment) -> Mapping[str, object] | None:
        if origin is None:
            return None
        for record in reversed(self.promotions):
            proof, released = validate_promotion(record, self.trend_level)
            if not _known_by(released, known):
                continue
            start = cast(Mapping[str, object], proof['origin'])
            end = cast(Mapping[str, object], proof['confirmed_by'])
            start_order = cast(int, start['index']), cast(int, start.get('ordinal', 0))
            end_order = cast(int, end['index']), cast(int, end.get('ordinal', 0))
            if (_order(extreme) < end_order or not (
                    _order(origin) == start_order and _order(extreme) == end_order
                    or _order(origin) >= end_order)):
                continue
            # 越过已升级段的任一边界后，旧许可证不能在回撤时复活；新延伸
            # 必须重新取得上级升级证明。区间树只含当时已处理的直接来源。
            first, last = self.positions[end_order], self.positions[_order(attack)] + 1
            high = self.origins_by_kind['H'].query(first, last)
            low = self.origins_by_kind['L'].query(first, last)
            ceiling = max(cast(float, start['value']), cast(float, end['value']))
            floor = min(cast(float, start['value']), cast(float, end['value']))
            if high is not None and high.value > ceiling or low is not None and low.value < floor:
                continue
            return record
        return None

    def advance(self, *, _snapshot: bool = True,
                _checkpoint: Callable[['_TrendUpgradeState'], None] | None = None) -> list[dict[str, object]]:
        self.updated_merged = {}
        self.new_merged_keys = []
        turns, regular, trend_level, event_sink = self.turns, self.regular, self.trend_level, self.event_sink
        source_level = trend_level - 1
        merged, positions, source_by_order = self.merged, self.positions, self.source_by_order
        keys_by_kind, origins_by_kind = self.keys_by_kind, self.origins_by_kind
        regular_cursor, prefix_known = self.regular_cursor, self.prefix_known
        owned_direction, whole_origin, whole_extreme = self.owned_direction, self.whole_origin, self.whole_extreme
        recent, source_positions, preceding_sources = self.recent, self.source_positions, self.preceding_sources
        body_rejection = self.body_rejection
        consumed_source_count = self._consumed_source_count
        pending_origin, pending_extreme = self.pending_origin, self.pending_extreme

        def publish_owned(raw: dict[str, object], actual: _SourceTurn, known: Moment, *, closed: bool) -> None:
            event = _SourceTurn(raw, actual.position, actual.index, actual.ordinal, actual.kind,
                                actual.value, actual.time, known, actual.reversal, _source_record_reference(raw))
            existing = merged.get(_identity(event))
            if existing is None or not _known_by(existing.available_at, known):
                self._store_merged(event)
            if closed:
                _store_closed_key(keys_by_kind[event.kind], positions[_order(event)], event)
            if event_sink is not None:
                event_sink.append(deepcopy(raw))

        for cursor in range(self.processed_count, len(turns)):
            # 在消费下一条 attack 之前记录同一个状态机的完整运行态；continue
            # 分支也会落到下一消费边界，不能靠输出点位置推测已经消费的知识。
            if _checkpoint is not None and cursor % 32 == 0:
                self.regular_cursor, self.prefix_known = regular_cursor, prefix_known
                self.owned_direction, self.whole_origin, self.whole_extreme = owned_direction, whole_origin, whole_extreme
                self.body_rejection = body_rejection
                self.pending_origin, self.pending_extreme = pending_origin, pending_extreme
                self.processed_count = cursor
                self._consumed_source_count = consumed_source_count
                _checkpoint(self)
            attack = turns[cursor]
            if self.strict_only:
                consumed_source_count = max(consumed_source_count, attack.position + 1)
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
                if self.strict_only:
                    # regular 可在很早的 attack 消费右侧 actual；该来源也是
                    # 已完成状态的依赖，不能只按攻击坐标选择修订恢复边界。
                    consumed_source_count = max(consumed_source_count, actual.position + 1)
                # 普通方向账本没有整波身份，不可进入 key 树。旧裸 confirmed
                # 候选的声明契约仍兼容；有生成标记的候选必须由本次共同状态重建。
                legacy_candidate = point.source.get('wave_completion_policy') is None
                if legacy_candidate or point.source.get('wave_identity_state') == 'owned':
                    identity = _identity(point)
                    if identity not in merged or _known_by(point.available_at, merged[identity].available_at):
                        self._store_merged(point)
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
            if not self.strict_only and owned_direction is None and not merged:
                if pending_origin is None or pending_extreme is None:
                    previous = preceding_sources[attack.reversal]
                    if previous is not None:
                        pending_origin, pending_extreme = previous, attack
                else:
                    price_sign = 1 if attack.kind == 'H' else -1
                    if attack.kind == pending_extreme.kind:
                        if price_sign * (attack.value - pending_extreme.value) > 0:
                            pending_extreme = attack
                    elif price_sign * (attack.value - pending_origin.value) > 0:
                        # 只有严格越过首段边界才换方向；波内局部四点不能
                        # 把未突破旧高/低的反弹起点抢先发布为本级起点。
                        pending_origin, pending_extreme = pending_extreme, attack
            # 已结束的下级翻空之后，最高价创新高但收盘未超过原高点当日收盘，不能
            # 覆盖该高点。随后再破翻空低点才确认组合下降波，保留真实四点。
            if (not self.strict_only and source_level > 0 and owned_direction == 'up'
                    and whole_extreme is not None and attack.kind == 'H'):
                counter = recent['L'][-1] if recent['L'] else None
                if (counter is not None and _order(whole_extreme) < _order(counter) < _order(attack)
                        and rejects_wick_extension(whole_extreme.source, counter.source, attack.source)):
                    body_rejection = whole_extreme, counter, attack
                    continue
            if body_rejection is not None:
                anchor, counter, rebound = body_rejection
                if owned_direction != 'up':
                    body_rejection = None
                elif attack.kind == 'H':
                    if attack.value > anchor.value:
                        if not rejects_wick_extension(anchor.source, counter.source, attack.source):
                            body_rejection = None
                        else:
                            body_rejection = anchor, counter, attack
                            continue
                elif _order(attack) > _order(rebound) and attack.value < counter.value:
                    reversal_proof = dict(available_at=prefix_known, anchor=body_reference(anchor.source),
                                          counter=body_reference(counter.source), rebound=body_reference(rebound.source),
                                          endpoint=body_reference(attack.source))
                    for actual, endpoint in ((anchor, False), (attack, True)):
                        raw = _candidate_from_source(actual)
                        raw.update(trend_level=trend_level, source_level=source_level, available_at=prefix_known,
                            confirmation_rule=BODY_REVERSAL_RULE, confirmed_by=_reference(attack),
                            wave_direction_before='down' if endpoint else 'up', wave_direction_after='down',
                            body_reversal_evidence=deepcopy(reversal_proof))
                        if source_level == 0:
                            raw.update(source_turn_position=actual.position, confirmed_on_turn=attack.position)
                        else:
                            raw.update({f'source_level{source_level}_position': actual.position,
                                        f'confirmed_on_level{source_level}': attack.position})
                        _stamp_wave_completion(raw, closed=not endpoint)
                        publish_owned(raw, actual, prefix_known, closed=not endpoint)
                    whole_origin, whole_extreme, owned_direction = anchor, attack, 'down'
                    body_rejection = None
                    continue
                if body_rejection is not None:
                    # 交替尚未破位时，不能再让包含该影线的普通四点比较
                    # 生成另一份“最高价极值”结束证明；只等破低或新高的收盘超过原收盘。
                    continue
            classification = classify_trend_prices([point.value for point in recent['H']],
                                                   [point.value for point in recent['L']], trend_level=trend_level)
            current: WaveDirection | None = (None if self.strict_only else
                'up' if classification == StructuralTrend.BULL else
                'down' if classification == StructuralTrend.BEAR else None)
            if owned_direction is None and current is not None:
                target = 'H' if current == 'up' else 'L'
                if not merged:
                    origin, extreme = pending_origin, pending_extreme
                else:
                    extreme = recent[target][-1]
                    origin = origins_by_kind['L' if current == 'up' else 'H'].query(0, positions[_order(extreme)])
                proof = [*recent['H'], *recent['L']]
                if origin is not None and extreme is not None and extreme.kind == target:
                    left = source_positions[_order(origin)]
                    right = source_positions[_order(extreme)]
                    # 证据仍含起点左侧旧拐点时继续等待；后续每次补齐证据
                    # 都可建立同一个边界，不因首次四点不合格而永久丢失。
                    if (all(_order(point) >= _order(origin) for point in proof)
                            and (source_level == 0 or right - left > 1)):
                        raw = _ordinary_point(origin, proof, attack, trend_level=trend_level,
                            direction='down' if current == 'up' else 'up', current=current,
                            prefix_known=prefix_known, key=None, owned=True)
                        initial_identity: dict[str, object] = dict(available_at=prefix_known,
                            origin=_reference(origin), endpoint=_reference(extreme))
                        if source_level == 0:
                            initial_identity['basis'] = 'polyline_four_point_definition'
                        else:
                            initial_identity['interior'] = _reference(turns[left + 1])
                        raw['lower_wave_identity_evidence'] = initial_identity
                        raw['initial_boundary'] = True
                        _stamp_wave_completion(raw, closed=True)
                        publish_owned(raw, origin, prefix_known, closed=True)
                        owned_direction, whole_origin, whole_extreme = current, origin, extreme
                        pending_origin = pending_extreme = None
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
                        opposite = preceding_sources[whole_extreme.reversal]
                        reversal_sign = 1 if current == 'up' else -1
                        # 只有实际被上级严格升级接管的波段，才解除其下级旧key门槛。
                        # 四点定义、拓扑、范围和可知时间仍独立满足；普通未升级波不豁免。
                        proof = [*recent['H'], *recent['L']]
                        promotion = self._released_promotion(whole_origin, whole_extreme, attack, prefix_known)
                        local_definition = (promotion is not None
                            and all(_order(point) >= _order(whole_extreme) for point in proof))
                        ordinary_reversal = (
                            opposite is not None
                            and _key_known_before_price(opposite, counter)
                            and reversal_sign * (counter.value - opposite.value) > 0
                        )
                        # 递归趋势线必须包含实际内部下级拐点，单 child 只能等待
                        # 组合波或严格破旧 key。零级折线只提供价格反转，不是待升格
                        # 的下级趋势线；否则第一条折线腿已是区间极值时，即便以后
                        # 多次出现严格LL/LH或HH/HL，一级也会被永远锁在旧方向。
                        if ((local_definition or ordinary_reversal or self.ordinary_trend_completion_enabled)
                                and (source_level == 0 or right - left > 1)):
                            raw = _ordinary_point(whole_extreme, proof, attack, trend_level=trend_level,
                                direction=owned_direction, current=current, prefix_known=prefix_known,
                                key=_reference(opposite) if opposite is not None else None, owned=True)
                            identity_proof: dict[str, object] = dict(available_at=prefix_known,
                                origin=_reference(whole_extreme), endpoint=_reference(counter))
                            if local_definition:
                                identity_proof['local_definition_boundary'] = _reference(whole_extreme)
                                identity_proof['higher_trend_promotion'] = deepcopy(promotion)
                            if ordinary_reversal:
                                assert opposite is not None
                                identity_proof['reversal_key'] = _reference(opposite)
                            if source_level == 0:
                                identity_proof['basis'] = 'polyline_four_point_definition'
                            else:
                                identity_proof['interior'] = _reference(turns[left + 1])
                            if whole_origin is not None:
                                # 严格突破较小旧 key 可以先开启新波；四点比较仍可能
                                # 含上一个波的更高H/更低L。保留真正整波起点作为极值
                                # 校验边界，不能改取最新局部回调来排除本波反例。
                                identity_proof['wave_origin'] = _reference(whole_origin)
                            raw['lower_wave_identity_evidence'] = identity_proof
                            _stamp_wave_completion(raw, closed=True)
                            publish_owned(raw, whole_extreme, prefix_known, closed=True)
                            whole_origin, whole_extreme, owned_direction = whole_extreme, counter, current
            # 反向严格升级使用当前整波起点已经闭合的本级关键位。波内较晚的
            # 局部 key 不能把冻结末跌高／末升低降格；普通组合波仍由上面的
            # 共同四点定义独立确认，不借六态或交易资格增删几何。
            attack_direction: WaveDirection = 'up' if attack.kind == 'H' else 'down'
            key = self._attack_key(attack, direction=owned_direction, origin=whole_origin)
            if key is None:
                continue
            rising = attack.kind == 'H'
            sign = 1 if rising else -1
            if sign * (attack.value - key.value) <= 0 or not _key_known_before_price(key, attack):
                continue
            origin_kind = 'L' if rising else 'H'
            # 当前整波由 owned 状态决定，不能被 pending 普通方向点重置。严格
            # 同向攻击始终保留原 whole origin；反向攻击必须关闭真实整波极值。
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
            # 局部 key 可被既有同向波突破，但不能倒过来证明其左侧起点闭合。
            # 反向新波需要真实的 key→origin→attack 顺序，缺失时保留旧整波。
            closes_origin = (_order(key) < _order(origin) < _order(attack)
                             and sign * (key.value - origin.value) > 0)
            if not continuing and not closes_origin:
                continue
            known = _later_moment(prefix_known, _later_moment(key.available_at, origin.available_at))
            # 同向只新增攻击端点几何；独立 origin 证明仍保留在审计账本，但
            # origin 固定为已有整波起点，绝不改取局部回调 L3/H3，也不重置 key
            # 知识。这样 ordinary14@18 与 laterstrict14@20 均保留，而 H2→L3
            # 的假反向线不会被制造。反向突破才发布整个旧波的真实极值 origin。
            if continuing and event_sink is not None and closes_origin:
                event_sink.append(deepcopy(_promoted_point(origin, attack=attack, origin=origin, key=key,
                                                           trend_level=trend_level, known=known, endpoint=False)))
            promoted_points = ((attack, True),) if continuing else ((origin, False), (attack, True))
            for source_point, endpoint in promoted_points:
                raw = _promoted_point(source_point, attack=attack, origin=origin, key=key,
                                      trend_level=trend_level, known=known, endpoint=endpoint)
                publish_owned(raw, source_point, known, closed=not endpoint)
            if not continuing:
                whole_origin = origin
                # 关键位可能迟于波内更强的价格点才可用于突破；真实攻击点
                # 负责升级证明，但不能覆盖新起点之后已经观察到的整波极值。
                # 树仅包含当前及此前已消费来源，相等价格仍保留最早坐标。
                whole_extreme = origins_by_kind[attack.kind].query(
                    positions[_order(origin)] + 1, attack_position + 1)
                assert whole_extreme is not None  # 当前 attack 已写入且严格位于 origin 右侧。
            else:
                whole_extreme = attack
            owned_direction = attack_direction
        self.regular_cursor, self.prefix_known = regular_cursor, prefix_known
        self.owned_direction, self.whole_origin, self.whole_extreme = owned_direction, whole_origin, whole_extreme
        self.body_rejection = body_rejection
        self.pending_origin, self.pending_extreme = pending_origin, pending_extreme
        self.processed_count = len(turns)
        self._consumed_source_count = consumed_source_count
        if _checkpoint is not None:
            _checkpoint(self)
        return self.snapshot() if _snapshot else []

    def snapshot(self) -> list[dict[str, object]]:
        """只有公共批量输出或状态预算回退需要物化完整 merged 账本。"""
        return [dict(self.merged[identity].source) for identity in self.merged_order]


def promote_trend_key_breaks(
    source: Sequence[Mapping[str, object]], candidates: Sequence[Mapping[str, object]], *, trend_level: int,
    event_sink: MutableSequence[dict[str, object]] | None = None,
    ordinary_trend_completion_enabled: bool = False,
) -> list[dict[str, object]]:
    """统一推进本级整波身份和严格升级，方向账本与整波状态各自保留。

    ``source`` 只能来自完整结束的直接下级：一级消费折线，二级消费一级，
    三级消费二级；开放升级点和显示尾端由共同来源校验排除。``candidates`` 是
    共同定义的方向账本，只有取得 owned 身份的闭合点才可成为本级 key。
    只有实际升级到更高级别的波段，才允许其下级在升级可知后凭独立
    HH/HL 或 LL/LH 确认局部反向结构。未升级、异路径或越界后的旧证明
    不豁免旧关键位；显式普通结束选项继续保持独立用户配置。
    首段先保存独立的待确认价格边界，局部四点与该方向一致、且完整位于
    边界内部后才发布 owned 起点；证据不足时保留边界等待后续补齐。
    待确认边界只有严格价格越界才切换，不能作为闭合 key 向上消费。
    严格升级的突破日必须晚于关键位可知日。
    普通相反方向若整波退化为一条直接下级趋势波，保持 pending，不改变既有
    owned 方向、起点或极值。以后反向波包含真实内部下级拐点成为组合波，
    或严格突破旧本级 key，才在实际身份可知日关闭和切换本级整波。
    一级来源是折线反转，直接以同一严格四点定义确认，不能把二/三级的
    趋势段升格门禁施加给折线。此边界只区分来源身份，不改变价格公式。

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
    source_level = trend_level - 1
    turns = _source_turns(source, source_level)
    # 本级普通事件只借投影时钟进入 key 树，返回 ledger/geometry 必须仍为
    # 原事件自己的副本，不能把“供上级消费”的来源审计字段混回本级输出。
    regular = _source_turns(candidates, trend_level, retain_geometry=True, accept_direction_facts=True)
    if not turns:
        return []
    return _TrendUpgradeState(turns, regular, trend_level=trend_level, event_sink=event_sink,
        ordinary_trend_completion_enabled=ordinary_trend_completion_enabled).advance()
