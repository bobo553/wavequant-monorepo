"""严格旁路按实际消费游标恢复；快照共享持久根，不保存每日输入版本链。"""

from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from sys import getsizeof
from typing import cast

from .owned_evidence import CertifiedSource, current_evidence
from .polyline import LinePoint, PointKind, ReversalPoint
from .trend_frozen_state import (
    FrozenPrefixList, FrozenPrefixMap, _IndexArchive, _IndexNode, _SequenceNode, _node_children,
)
from .trend_level_upgrades import _PointRange, _TrendUpgradeState, _known_by
from .trend_levels import Moment, _SourceTurn


_STRIDE = 32
_RUNTIME_FIELDS = (
    'merged', 'merged_order', 'keys_by_kind', 'origins_by_kind', 'regular_cursor', 'prefix_known',
    'owned_direction', 'whole_origin', 'whole_extreme', 'pending_origin', 'pending_extreme',
    'last_fall_high_position', 'last_rise_low_position', 'recent', 'processed_count', 'body_rejection',
    'event_sink', '_consumed_source_count',
)


def _freeze_runtime(state: _TrendUpgradeState) -> None:
    if type(state.merged) is not FrozenPrefixMap:
        state.merged = FrozenPrefixMap(state.merged.items())
    state.merged.freeze()
    for name in ('merged_order', 'event_sink'):
        values = getattr(state, name)
        if values is None:
            continue
        records = values if type(values) is FrozenPrefixList else FrozenPrefixList(values)
        records.freeze(len(records))
        setattr(state, name, records)
    for tree in [*state.keys_by_kind.values(), *state.origins_by_kind.values()]:
        tree.points.freeze()


def _copy_runtime(values: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name in _RUNTIME_FIELDS:
        value = values[name]
        if name in ('keys_by_kind', 'origins_by_kind'):
            trees: dict[str, _PointRange] = {}
            for kind, tree in cast(dict[str, _PointRange], value).items():
                cloned = copy(tree)
                cloned.points = tree.points.copy()
                trees[kind] = cloned
            result[name] = trees
        elif name == 'recent':
            result[name] = {kind: points.copy() for kind, points in cast(dict[str, list[_SourceTurn]], value).items()}
        elif type(value) in (list, dict, FrozenPrefixList, FrozenPrefixMap):
            result[name] = value.copy()
        else:
            result[name] = value
    return result


@dataclass(frozen=True, slots=True)
class _ConsumptionCheckpoint:
    processed: int
    attack_position: int
    required_source_count: int
    source_keys: FrozenPrefixList[bytes]
    regular_keys: FrozenPrefixList[str]
    runtime: dict[str, object]
    introduced_ids: tuple[int, ...]
    charged: int


class _UpgradeConsumptionCache:
    """按实际来源依赖保留严格状态检查点；修订只重放受影响的攻击。

    每 32 个实际攻击和最新末端保存一个索引项，不按交易日累计版本。
    旧项只从末端撤销；每个对象 ID 由首次记录它的不可变快照保活，因此
    可按后缀撤销费用，无需逐日扫描旧 DAG 或保留额外的对象所有权表。
    """

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.stats: dict[str, int] = {}
        self.reset()

    def _count(self, name: str, amount: int = 1) -> None:
        self.stats[name] = self.stats.get(name, 0) + amount

    def reset(self) -> None:
        self.frames: list[_ConsumptionCheckpoint] = []
        self._seen: set[int] = set()
        self.memory_used = 0
        self._source_keys = FrozenPrefixList[bytes]()
        self._source_token: object | None = None
        self._regular_keys = FrozenPrefixList[str]()
        self._ordered_regular: list[_SourceTurn] = []
        self._enabled = False
        self._collecting = False
        self._budget_blocked = False

    def roots(self) -> list[object]:
        # source keys 属于当前 formal 输入，不保存其祖先；正式总预算仍计入。
        return [self, vars(self), *(root for frame in self.frames for root in (
            frame.source_keys, frame.regular_keys, frame.runtime, frame.introduced_ids,
            frame.processed, frame.attack_position, frame.required_source_count, frame.charged))]

    def live_roots(self) -> list[object]:
        # 当前输入可能尚未被任何快照消费，必须与现态一起计费。历史快照
        # 已有独占上界，不再逐次放入 formal 的整图扫描。
        return [self._source_keys, self._regular_keys, self._ordered_regular]

    @property
    def retained_upper_bytes(self) -> int:
        fields = vars(self)
        metadata = getsizeof(self) + getsizeof(fields)
        metadata += sum(getsizeof(name) + getsizeof(value) for name, value in fields.items())
        metadata += sum(getsizeof(name) + getsizeof(value) for name, value in self.stats.items())
        # frames/seen 的实际容器容量由 metadata 覆盖；每个 ID 整数与
        # introduced_ids 元组条目已包含在节点128费用，frame对象在1024费用。
        return self.memory_used + metadata

    def prepare(self, source: Sequence[Mapping[str, object]], source_keys: Sequence[bytes],
                candidates: Sequence[str], offsets: Sequence[int], regular: Sequence[_SourceTurn],
                *, owned: bool, appended_from: int | None = None) -> bool:
        self._enabled = (owned and type(source) is CertifiedSource and source.owner is current_evidence()
                         and current_evidence() is not None)
        self._collecting = self._enabled
        if not self._enabled:
            self.reset()
            return False
        assert type(source) is CertifiedSource
        common = (len(self._source_keys) if source.token is self._source_token else
                  source.changed_from if source.parent is self._source_token and self._source_token is not None else 0)
        while common < min(len(source_keys), len(self._source_keys)) and source_keys[common] == self._source_keys[common]:
            common += 1
        if common < len(self._source_keys):
            self._count('source_revisions')
            retained = len(self.frames)
            while retained and self.frames[retained - 1].required_source_count > common:
                retained -= 1
            self._count('source_revision_rejections', len(self.frames) - retained)
            self._count('source_revision_retained_frames', retained)
            self._drop_from(retained)
            # 原来源已改变，旧预算失败不再代表当前依赖图的费用。
            self._budget_blocked = False
        self._source_keys = (source_keys if type(source_keys) is FrozenPrefixList else FrozenPrefixList(source_keys))
        self._source_keys.freeze(len(self._source_keys))
        self._source_token = source.token
        if self._budget_blocked:
            self._count('budget_blocked_calls')
            self._collecting = False
            return False
        # 原 append 已验证新增 regular 严格晚于全部旧消费知识；沿用其
        # 门禁才能只排序本批。修订分支才重建排序视图，不给正常追加增加全扫。
        incremental = appended_from is not None and len(self._ordered_regular) == offsets[appended_from]
        start = cast(int, appended_from) if incremental else 0
        pairs = [(turn, candidates[index]) for index in range(start, len(candidates))
                 for turn in regular[offsets[index]:offsets[index + 1]]]
        numeric = bool(pairs and isinstance(pairs[0][0].available_at, str)
                       and pairs[0][0].available_at.isdecimal())
        pairs.sort(key=lambda pair: int(pair[0].available_at) if numeric else pair[0].available_at)
        keys = [key for _, key in pairs]
        if incremental:
            self._regular_keys.extend(keys)
            self._regular_keys.freeze(len(self._regular_keys))
            self._ordered_regular.extend(turn for turn, _ in pairs)
            return True
        common = 0
        while common < min(len(keys), len(self._regular_keys)) and keys[common] == self._regular_keys[common]:
            common += 1
        updated = self._regular_keys.copy()
        del updated[common:]
        updated.extend(keys[common:])
        updated.freeze(len(updated))
        self._regular_keys = updated
        self._ordered_regular = [turn for turn, _ in pairs]
        return True

    def _drop_from(self, position: int) -> None:
        # 后来的快照不会被更早快照引用；删后缀才能同时撤销其首次引入的 ID。
        for frame in reversed(self.frames[position:]):
            self._seen.difference_update(frame.introduced_ids)
            self.memory_used -= frame.charged
        del self.frames[position:]

    def discard_for_cold(self) -> None:
        self._drop_from(0)

    def disable_for_budget(self) -> None:
        self.discard_for_cold()
        # 关闭期间 prepare 不更新候选视图，不能继续持有过期候选图。
        self._regular_keys = FrozenPrefixList[str]()
        self._ordered_regular = []
        self._budget_blocked = True
        self._collecting = False

    def restore(self, fresh: _TrendUpgradeState) -> bool:
        self._count('restore_attempts')
        if not fresh.strict_only:
            self._count('strict_mode_rejections')
            return False
        if not self._enabled or not self.frames:
            self._count('missing_checkpoints')
            return False
        # 原索引允许未消费的错误外部候选暂留；它们不能改变已保存树的位置布局。
        if len(fresh.orders) != len(fresh.source_by_order):
            self._count('layout_rejections')
            return False
        for position in range(len(self.frames) - 1, -1, -1):
            frame = self.frames[position]
            cursor = len(frame.regular_keys)
            source_common = self._source_keys.shared_prefix(frame.source_keys)
            while (source_common < min(len(self._source_keys), frame.required_source_count)
                    and self._source_keys[source_common] == frame.source_keys[source_common]):
                source_common += 1
            if source_common < frame.required_source_count:
                self._count('source_prefix_rejections')
                continue
            if (frame.processed > len(fresh.turns) or frame.processed
                    and fresh.turns[frame.processed - 1].position != frame.attack_position):
                self._count('attack_layout_rejections')
                continue
            if self._regular_keys.shared_prefix(frame.regular_keys) < cursor:
                self._count('regular_prefix_rejections')
                continue
            known = cast(Moment | None, frame.runtime['prefix_known'])
            if (known is not None and cursor < len(self._ordered_regular)
                    and _known_by(self._ordered_regular[cursor].available_at, known)):
                self._count('backdated_regular_rejections')
                continue
            self._drop_from(position + 1)
            for name, value in _copy_runtime(frame.runtime).items():
                setattr(fresh, name, value)
            # 已消费依赖的完整原始前缀相同，旧坐标位置不变；尾部可以修订。
            for tree in [*fresh.keys_by_kind.values(), *fresh.origins_by_kind.values()]:
                tree.ensure_size(len(fresh.orders))
            self._count('restores')
            self._count('replayed_attacks', len(fresh.turns) - fresh.processed_count)
            return True
        self._count('unmatched_checkpoints')
        return False

    def begin(self, state: _TrendUpgradeState, *, cold: bool) -> bool:
        if not self._enabled:
            return False
        if not state.strict_only:
            self._count('strict_mode_rejections')
            self._collecting = False
            return False
        if cold:
            self._count('cold_replays')
            self._count('cold_attacks', len(state.turns))
            self.discard_for_cold()
        if not self._collecting:
            return False
        if len(state.orders) != len(state.source_by_order):
            self._count('layout_rejections')
            self.discard_for_cold()
            self._collecting = False
            return False
        # 末端索引不是日期历史；进入下一段后仅留下按处理块建立的持久游标。
        if self.frames and self.frames[-1].processed % _STRIDE and self.frames[-1].processed < len(state.turns):
            self._drop_from(len(self.frames) - 1)
        _freeze_runtime(state)
        return self._collecting

    def _new_graph(self, roots: Sequence[object], allowance: int) -> tuple[tuple[int, ...], int] | str | None:
        pending = list(roots)
        added: set[int] = set()
        charged = 0
        owner = current_evidence()
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in self._seen or identity in added:
                continue
            added.add(identity)
            # ID 集合、每个快照的归属 ID 元组及哈希表空槽也在同一预算内。
            charged += 128
            if owner is not None and owner.reference(value) is not None:
                charged += 8
                if charged > allowance:
                    return None
                continue
            charged += getsizeof(value)
            if charged > allowance:
                return None
            kind = type(value)
            if kind is dict:
                pending.extend(child for pair in cast(dict[object, object], value).items() for child in pair)
            elif kind in (list, tuple):
                pending.extend(cast(Sequence[object], value))
            elif kind in (_SequenceNode, _IndexNode):
                pending.extend(_node_children(value))
            elif kind is _PointRange:
                tree = cast(_PointRange, value)
                charged += getsizeof(vars(tree))
                pending.extend((tree.points, tree.size))
            elif kind in (FrozenPrefixList, FrozenPrefixMap, _IndexArchive):
                # 泛型实例自动携带 __orig_class__（types.GenericAlias）；它是
                # 容器类型元数据，不是业务证明，也不应沿类型追踪模块全局图。
                fields = vars(value)
                charged += getsizeof(fields)
                for name, child in fields.items():
                    pending.append(name)
                    if name == '__orig_class__':
                        charged += getsizeof(child)
                    else:
                        pending.append(child)
                if charged > allowance:
                    return None
            elif kind in (_SourceTurn, ReversalPoint, LinePoint):
                pending.append(vars(value))
            elif kind not in (str, bytes, int, float, bool, type(None), PointKind):
                return f'{kind.__module__}.{kind.__qualname__}'
        return tuple(added), charged

    def capture(self, state: _TrendUpgradeState) -> None:
        if not self._collecting:
            return
        cursor = state.processed_count
        if cursor == 0:
            return
        if cursor % _STRIDE and cursor != len(state.turns):
            return
        if self.frames and self.frames[-1].processed == cursor:
            return
        self._count('capture_attempts')
        _freeze_runtime(state)
        runtime = _copy_runtime(vars(state))
        source_keys = self._source_keys.copy()
        del source_keys[state._consumed_source_count:]
        source_keys.freeze(len(source_keys))
        keys = self._regular_keys.copy()
        del keys[state.regular_cursor:]
        keys.freeze(len(keys))
        measured = self._new_graph((runtime, keys, source_keys), self.limit - self.memory_used - 1024)
        if isinstance(measured, str):
            self._count('unsupported_graph_rejections')
            label = f'unsupported_type:{measured[:120]}'
            if label not in self.stats and sum(key.startswith('unsupported_type:') for key in self.stats) >= 16:
                label = 'unsupported_type:other'
            self._count(label)
            self.discard_for_cold()
            self._collecting = False
            return
        if measured is None:
            self._count('budget_rejections')
            # 同一来源 epoch 超预算后不再逐日重建被拒绝的整段索引。
            self.disable_for_budget()
            return
        introduced, size = measured
        charged = size + 1024
        self._seen.update(introduced)
        attack_position = state.turns[cursor - 1].position if cursor else -1
        self.frames.append(_ConsumptionCheckpoint(cursor, attack_position, state._consumed_source_count,
            source_keys, keys, runtime, introduced, charged))
        self.memory_used += charged
        self._count('captures')
        self._count('admitted_bytes', charged)
