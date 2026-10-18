"""内部级联的来源投影，只重建变化点及其左侧校验锚点。"""

from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import cast

from .owned_evidence import CertifiedSource, current_evidence, owned_signature
from .trend_levels import _source_turns


@dataclass
class _ProjectedPath:
    points: list[dict[str, object]]
    positions: list[int]
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
                tuple[list[dict[str, object]], list[dict[str, object]] | None, int]]) -> CertifiedSource | None:
        owner = current_evidence()
        if owner is None:
            self.clear()
            return None
        if self.joined is not None and self.joined.owner is not owner:
            self.clear()
        next_paths: dict[str, _ProjectedPath] = {}
        output: list[dict[str, object]] = []
        keys: list[bytes] = []
        changed = 0
        unchanged = True
        for path_position, stroke in enumerate(strokes):
            if stroke.get('display_only') is True:
                continue
            path = stroke.get('id')
            raw = stroke.get('points')
            if type(path) is not str or type(raw) is not list or path in next_paths:
                self.clear()
                return None
            points = cast(list[dict[str, object]], raw)
            previous = self.paths.get(path)
            hint = hints.get(id(points))
            start = len(points) if previous is not None and points is previous.points else (
                hint[2] if previous is not None and hint is not None and hint[0] is points
                and hint[1] is previous.points else 0)
            if previous is not None and start == len(points) == len(previous.points):
                saved = _ProjectedPath(points, previous.positions, previous.source)
            else:
                keep = max(0, bisect_left(previous.positions, start) - 1) if previous is not None else 0
                # 投影可能滤掉未闭合点，必须回到上一个实际来源，而非固定回退一项。
                begin = min(start, previous.positions[keep]) if previous is not None and previous.positions else 0
                tail = [dict(point, source_path=path) for point in points[begin:]]
                level = points[0].get('trend_level', 0) if points else 0
                if type(level) is not int or level not in (1, 2, 3):
                    self.clear()
                    return None
                turns = _source_turns(tail, level, formal_key_attacks=True, _position_offset=begin)
                values = [dict(turn.source) for turn in turns]
                signatures = tuple(owned_signature(point) for point in values)
                if any(key is None for key in signatures):
                    self.clear()
                    return None
                source = CertifiedSource([*(previous.source[:keep] if previous is not None else ()), *values],
                    (*(previous.source.keys[:keep] if previous is not None else ()),
                     *cast(tuple[bytes, ...], signatures)), owner,
                    previous.source if previous is not None else None, _changed_from=keep)
                saved = _ProjectedPath(points,
                    [*(previous.positions[:keep] if previous is not None else ()), *(turn.position for turn in turns)], source)
            next_paths[path] = saved
            if unchanged:
                if (path_position < len(self.order) and self.order[path_position] == path and previous is not None):
                    changed += len(saved.source) if saved.source is previous.source else saved.source.changed_from
                    unchanged = saved.source is previous.source
                else:
                    unchanged = False
            output.extend(saved.source)
            keys.extend(saved.source.keys)
        # 证书和引用只保留当前三层版本；拒绝超额工作集，原入口继续完整校验。
        if sum(path.source.signature_bytes + len(path.source) * 256 for path in next_paths.values()) > 8 * 1024 * 1024:
            self.clear()
            return None
        if self.joined is not None and unchanged and len(output) == len(self.joined):
            self.paths = next_paths
            self.order = list(next_paths)
            return self.joined
        joined = CertifiedSource(output, tuple(keys), owner, self.joined, _changed_from=changed)
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
               allowed: Mapping[str, Mapping[bytes, dict[str, object]]], field: str) -> CertifiedSource | None:
        owner = current_evidence()
        if type(source) is not CertifiedSource or source.owner is not owner or owner is None:
            self.clear()
            return None
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
        start = min(start, len(old) if old is not None else 0)
        values = list(old[:start]) if old is not None else []
        keys = list(old.keys[:start]) if old is not None else []
        for position in range(start, len(source)):
            point = source[position]
            additions = indexed.get((str(point.get('source_path', 'path-0')), self.order(point)))
            value = dict(point, **{field: list(additions)}) if additions else point
            signature = owned_signature(value) if additions else source.keys[position]
            if signature is None:
                self.clear()
                return None
            values.append(value)
            keys.append(signature)
        output = (old if old is not None and start == len(old) == len(source) else
                  CertifiedSource(values, tuple(keys), owner, old, _changed_from=start))
        if output.signature_bytes + len(output) * 512 > 8 * 1024 * 1024:
            self.clear()
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
