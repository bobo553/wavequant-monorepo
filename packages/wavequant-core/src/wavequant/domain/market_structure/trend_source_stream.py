"""内部级联的来源投影，只重建变化点及其左侧校验锚点。"""

from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import cast

from .owned_evidence import CertifiedSource, current_evidence, owned_signature
from .owned_evidence_producer import PointRecord, ProducerRecordScope, current_producer_records, producer_record_scope
from .owned_evidence_records import RecordRef
from .trend_levels import _source_turns
from .trend_point_version import PointVersion, record_point_version
from .trend_point_records import PointRecordView
from .trend_frozen_state import FrozenPrefixList


@contextmanager
def _source_record_scope() -> Iterator[ProducerRecordScope | None]:
    owner = current_evidence()
    if owner is None or not owner.record_history_enabled:
        yield None
        return
    scope = current_producer_records()
    store = owner.record_store()
    if scope is not None:
        if scope.owner is not store:
            raise ValueError('source production requires its request record owner')
        yield scope
    else:
        with producer_record_scope(store) as opened:
            yield opened


def _record_view(points: Sequence[Mapping[str, object]], scope: ProducerRecordScope) -> PointRecordView:
    if type(points) not in (PointVersion, CertifiedSource):
        raise ValueError('source production requires a complete same-owner point record version')
    version = cast(PointVersion, points)
    if not version._sealed or version.evidence_sequence is None or version.record_store is not scope.owner:
        raise ValueError('source production requires a complete same-owner point record version')
    return cast(PointRecordView, version.point_records)


def _bind_promotion_record(point: dict[str, object], reference: tuple[bytes, int] | None,
                           scope: ProducerRecordScope) -> None:
    if reference is not None and reference[0] != scope.owner.namespace:
        raise ValueError('source promotion records require their original request owner')
    if scope.lookup(point) is not None:
        return
    if reference is None:
        raise ValueError('source promotion requires its actual recorded dictionary root')
    root = RecordRef(*reference)
    refs = scope.owner.dictionary_refs(root)
    scope.bind(PointRecord._from_producer(point, root, refs, scope.owner))


@dataclass
class _ProjectedPath:
    points: Sequence[dict[str, object]]
    positions: FrozenPrefixList[int]
    source: CertifiedSource


class SourceProjection:
    def __init__(self) -> None:
        self.paths: dict[str, _ProjectedPath] = {}
        self.joined: CertifiedSource | None = None
        self.order: list[str] = []

    def clear(self) -> None:
        self.paths.clear()
        self.joined = None
        self.order = []

    def project(self, strokes: Sequence[Mapping[str, object]], hints: Mapping[int,
                tuple[Sequence[dict[str, object]], Sequence[dict[str, object]] | None, int]]) -> CertifiedSource | None:
        with _source_record_scope() as scope:
            return self._project(strokes, hints, scope)

    def _project(self, strokes: Sequence[Mapping[str, object]], hints: Mapping[int,
                 tuple[Sequence[dict[str, object]], Sequence[dict[str, object]] | None, int]],
                 scope: ProducerRecordScope | None) -> CertifiedSource | None:
        owner = current_evidence()
        if owner is None:
            self.clear()
            return None
        if self.joined is not None and self.joined.owner is not owner:
            self.clear()
        if scope is not None and self.joined is not None:
            _record_view(self.joined, scope)
        next_paths: dict[str, _ProjectedPath] = {}
        total = 0
        changed = 0
        unchanged = True
        for stroke in strokes:
            if stroke.get('display_only') is True:
                continue
            path = stroke.get('id')
            raw = stroke.get('points')
            if type(path) is not str or type(raw) not in (list, PointVersion) or path in next_paths:
                self.clear()
                if scope is not None:
                    raise ValueError('recorded source projection requires distinct recorded point paths')
                return None
            points = cast(Sequence[dict[str, object]], raw)
            records = _record_view(points, scope) if scope is not None else None
            previous = self.paths.get(path)
            if scope is not None and previous is not None:
                _record_view(previous.source, scope)
            hint = hints.get(id(points))
            start = len(points) if previous is not None and points is previous.points else (
                hint[2] if previous is not None and hint is not None and hint[0] is points
                and hint[1] is previous.points else
                points.changed_since(previous.points) if previous is not None
                and isinstance(points, PointVersion) and isinstance(previous.points, PointVersion) else 0)
            if previous is not None and start == len(points) == len(previous.points):
                saved = _ProjectedPath(points, previous.positions, previous.source)
            else:
                keep = max(0, bisect_left(previous.positions, start) - 1) if previous is not None else 0
                # 投影可能滤掉未闭合点，必须回到上一个实际来源，而非固定回退一项。
                begin = min(start, previous.positions[keep]) if previous is not None and previous.positions else 0
                if records is not None and scope is not None:
                    for record in records.iter_range(begin):
                        scope.bind(record)
                tail = [dict(point, source_path=path) for point in points[begin:]]
                level = points[0].get('trend_level', 0) if points else 0
                if type(level) is not int or level not in (1, 2, 3):
                    self.clear()
                    return None
                turns = _source_turns(tail, level, formal_key_attacks=True, _position_offset=begin)
                values: list[dict[str, object]] = []
                signatures: list[bytes | None] = []
                stable_inputs = (type(points) is PointVersion and points._sealed
                    and previous is not None and type(previous.points) is PointVersion and previous.points._sealed
                    and bool(previous.points) and previous.points[0].get('trend_level', 0) == level)
                for turn in turns:
                    prior = bisect_left(previous.positions, turn.position) if previous is not None else 0
                    if (stable_inputs and previous is not None and prior < len(previous.positions)
                            and previous.positions[prior] == turn.position
                            and turn.position < len(previous.points)
                            and points[turn.position] is previous.points[turn.position]):
                        # 边界锚点仍由共同投影校验；同一只读原点的投影没有变化，
                        # 证据池满后也可沿用它已有的完整证书，不重新编码旧证明。
                        if scope is not None:
                            scope.bind(_record_view(previous.source, scope)[prior])
                        values.append(previous.source[prior])
                        signatures.append(previous.source.keys[prior])
                    else:
                        value = dict(turn.source)
                        values.append(value)
                        signatures.append(owned_signature(value))
                if any(key is None for key in signatures):
                    self.clear()
                    if scope is not None:
                        raise ValueError('recorded source projection requires finite signatures')
                    return None
                retained = 0
                if previous is not None:
                    while (retained < len(values) and keep + retained < len(previous.source)
                            and values[retained] is previous.source[keep + retained]):
                        retained += 1
                source = (previous.source if previous is not None and retained == len(values)
                    and keep + retained == len(previous.source) else
                    CertifiedSource.from_tail(values[retained:], cast(list[bytes], signatures[retained:]), owner,
                        previous.source if previous is not None else None, keep + retained))
                if scope is not None:
                    record_point_version(source, scope)
                positions = previous.positions.copy() if previous is not None else FrozenPrefixList[int]()
                del positions[keep:]
                positions.extend(turn.position for turn in turns)
                positions.freeze(len(positions))
                saved = _ProjectedPath(points, positions, source)
            path_position = len(next_paths)
            next_paths[path] = saved
            if unchanged:
                if (path_position < len(self.order) and self.order[path_position] == path and previous is not None):
                    changed += len(saved.source) if saved.source is previous.source else saved.source.changed_from
                    unchanged = saved.source is previous.source
                else:
                    unchanged = False
            total += len(saved.source)
        # 证书和引用只保留当前三层版本；拒绝超额工作集，原入口继续完整校验。
        projected_bytes = sum(path.source.signature_bytes + len(path.source) * 256 + path.source.record_metadata_bytes
            + (path.points.record_metadata_bytes if isinstance(path.points, PointVersion) else 0)
            for path in next_paths.values())
        if projected_bytes + (self.joined.record_metadata_bytes if self.joined is not None else 0) > 8 * 1024 * 1024:
            self.clear()
            if scope is not None:
                raise ValueError('recorded source projection exceeds its memory limit')
            return None
        if self.joined is not None and unchanged and total == len(self.joined):
            self.paths = next_paths
            self.order = list(next_paths)
            return self.joined
        changed = min(changed, total, len(self.joined) if self.joined is not None else 0)

        tail_points: list[dict[str, object]] = []
        tail_keys: list[bytes] = []
        offset = 0
        for saved in next_paths.values():
            begin = max(0, changed - offset)
            if scope is not None:
                for record in _record_view(saved.source, scope).iter_range(begin):
                    scope.bind(record)
            tail_points.extend(saved.source[begin:])
            tail_keys.extend(saved.source.keys[begin:])
            offset += len(saved.source)
        joined = CertifiedSource.from_tail(tail_points, tail_keys, owner, self.joined, changed)
        if scope is not None:
            record_point_version(joined, scope)
        if projected_bytes + joined.record_metadata_bytes > 8 * 1024 * 1024:
            self.clear()
            if scope is not None:
                raise ValueError('recorded source projection exceeds its memory limit')
            return None
        self.paths, self.order, self.joined = next_paths, list(next_paths), joined
        return joined


class PromotionSource:
    """升级许可证作为独立输入版本；变化端点之前的来源不重新签名。"""

    def __init__(self) -> None:
        self.clear()

    def clear(self) -> None:
        self.source: CertifiedSource | None = None
        self.output: CertifiedSource | None = None
        self.positions: dict[tuple[str, tuple[int, int]], int] = {}
        self.records: dict[tuple[str, tuple[int, int]], tuple[bytes, ...]] = {}

    @staticmethod
    def order(point: Mapping[str, object]) -> tuple[int, int]:
        return cast(int, point['index']), cast(int, point.get('ordinal', 0))

    def enrich(self, source: Sequence[Mapping[str, object]],
               allowed: Mapping[str, Mapping[bytes, dict[str, object]]], field: str, *,
               _record_references: Mapping[int, tuple[bytes, int]] | None = None) -> CertifiedSource | None:
        with _source_record_scope() as scope:
            return self._enrich(source, allowed, field, scope, _record_references)

    def _enrich(self, source: Sequence[Mapping[str, object]],
                allowed: Mapping[str, Mapping[bytes, dict[str, object]]], field: str,
                scope: ProducerRecordScope | None,
                references: Mapping[int, tuple[bytes, int]] | None) -> CertifiedSource | None:
        owner = current_evidence()
        if type(source) is not CertifiedSource or source.owner is not owner or owner is None:
            self.clear()
            if scope is not None:
                raise ValueError('recorded promotion sources require a certified request version')
            return None
        input_records = _record_view(source, scope) if scope is not None else None
        previous = self.source
        start = (len(source) if previous is source else source.changed_from
                 if previous is not None and source.parent is previous.token else 0)
        if previous is not None:
            for point in previous[start:]:
                self.positions.pop((str(point.get('source_path', 'path-0')), self.order(point)), None)
        for position in range(start, len(source)):
            point = source[position]
            key = str(point.get('source_path', 'path-0')), self.order(point)
            if key in self.positions:
                self.clear()
                return None
            self.positions[key] = position
        indexed: dict[tuple[str, tuple[int, int]], list[dict[str, object]]] = {}
        identities: dict[tuple[str, tuple[int, int]], list[bytes]] = {}
        for path, records in allowed.items():
            for signature, record in records.items():
                endpoint = path, self.order(record)
                if endpoint not in self.positions:
                    continue
                origin = cast(Mapping[str, object], cast(Mapping[str, object], record['upgrade_evidence'])['origin'])
                if (path, self.order(origin)) in self.positions:
                    indexed.setdefault(endpoint, []).append(record)
                    identities.setdefault(endpoint, []).append(signature)
        versions = {endpoint: tuple(keys) for endpoint, keys in identities.items()}
        for endpoint in self.records.keys() | versions.keys():
            if self.records.get(endpoint) != versions.get(endpoint):
                start = min(start, self.positions.get(endpoint, len(source)))
        old = self.output
        if old is None or old.owner is not owner:
            start = 0
        elif scope is not None:
            _record_view(old, scope)
        start = min(start, len(old) if old is not None else 0)
        values: list[dict[str, object]] = []
        keys: list[bytes] = []
        for position in range(start, len(source)):
            point = source[position]
            additions = indexed.get((str(point.get('source_path', 'path-0')), self.order(point)))
            if scope is not None and input_records is not None:
                scope.bind(input_records[position])
                for addition in additions or ():
                    reference = references.get(id(addition)) if references is not None else None
                    _bind_promotion_record(addition, reference, scope)
            value = dict(point, **{field: list(additions)}) if additions else point
            signature = owned_signature(value) if additions else source.keys[position]
            if signature is None:
                self.clear()
                if scope is not None:
                    raise ValueError('recorded promotion sources require finite signatures')
                return None
            values.append(value)
            keys.append(signature)
        output = (old if old is not None and start == len(old) == len(source) else
                  CertifiedSource.from_tail(values, keys, owner, old, start))
        if scope is not None:
            record_point_version(output, scope)
        if output.signature_bytes + len(output) * 512 + output.record_metadata_bytes > 8 * 1024 * 1024:
            self.clear()
            if scope is not None:
                raise ValueError('recorded promotion source exceeds its memory limit')
            return None
        self.source, self.output, self.records = source, output, versions
        return output


class SourceExtremes:
    """发展尾部的范围摘要；长期没有新关键位也只推进变化来源。"""

    def __init__(self) -> None:
        self.token: object | None = None
        self.orders: list[tuple[int, int]] = []
        self.valid = [True]
        self.capacity = 1
        self.high: list[Mapping[str, object] | None] = [None, None]
        self.low: list[Mapping[str, object] | None] = [None, None]

    @staticmethod
    def choose(left: Mapping[str, object] | None, right: Mapping[str, object] | None,
               sign: int) -> Mapping[str, object] | None:
        if left is None:
            return right
        if right is None:
            return left
        difference = sign * (cast(float, left['value']) - cast(float, right['value']))
        if difference != 0:
            return left if difference > 0 else right
        return left if PromotionSource.order(left) <= PromotionSource.order(right) else right

    def extreme(self, source: Sequence[Mapping[str, object]], anchor: Mapping[str, object]) -> tuple[
            bool, Mapping[str, object] | None]:
        if (type(source) is not CertifiedSource or source.owner is not current_evidence()
                or len(source) * 256 > 8 * 1024 * 1024):
            self.__init__()
            return False, None
        if source.token is not self.token:
            start = source.changed_from if source.parent is self.token else 0
            old_length = len(self.orders)
            start = min(start, old_length, len(source))
            del self.orders[start:]
            del self.valid[start + 1:]
            for point in source[start:]:
                index, ordinal = point.get('index'), point.get('ordinal', 0)
                valid = type(index) is int and type(ordinal) is int and index >= 0 and ordinal >= 0
                order = (cast(int, index), cast(int, ordinal)) if valid else (0, 0)
                self.valid.append(self.valid[-1] and valid and (not self.orders or self.orders[-1] < order))
                self.orders.append(order)
            if len(source) > self.capacity:
                while len(source) > self.capacity:
                    self.capacity *= 2
                self.high, self.low = [None] * (2 * self.capacity), [None] * (2 * self.capacity)
                start = 0
            for position in range(start, max(old_length, len(source))):
                point = source[position] if position < len(source) else None
                visible = (point is not None and point.get('state') not in ('seed', 'developing')
                           and point.get('display_only') is not True)
                cursor = self.capacity + position
                self.high[cursor] = point if visible and point is not None and point['kind'] == 'H' else None
                self.low[cursor] = point if visible and point is not None and point['kind'] == 'L' else None
                cursor //= 2
                while cursor:
                    self.high[cursor] = self.choose(self.high[2 * cursor], self.high[2 * cursor + 1], 1)
                    self.low[cursor] = self.choose(self.low[2 * cursor], self.low[2 * cursor + 1], -1)
                    cursor //= 2
            self.token = source.token
        if not self.valid[-1]:
            return False, None
        sign = 1 if anchor['kind'] == 'L' else -1
        tree = self.high if sign == 1 else self.low
        left, right = self.capacity + bisect_right(self.orders, PromotionSource.order(anchor)), self.capacity + len(source)
        result = None
        while left < right:
            if left % 2:
                result = self.choose(result, tree[left], sign)
                left += 1
            if right % 2:
                right -= 1
                result = self.choose(result, tree[right], sign)
            left //= 2
            right //= 2
        if result is not None and sign * (cast(float, result['value']) - cast(float, anchor['value'])) <= 0:
            result = None
        return True, result
