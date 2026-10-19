"""步骤缓存内的有界证据编码复用；引用仅在单个自包含 chunk 内有效。"""

from collections import OrderedDict
from collections.abc import Callable, Mapping
from copy import deepcopy
import json
from sys import getsizeof
from typing import cast

from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.market_structure.owned_evidence import CertifiedSource, OwnedEvidence, current_evidence


_FORMAT = 'trend_stroke_refs_v1'
_POINT_FORMAT = 'trend_point_refs_v2'
_Encode = Callable[[object], bytes]


class EvidenceJsonCache:
    """公开值按全文签名；请求内已认证的不可变节点按稳定引用复用。"""

    def __init__(self, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        if type(max_bytes) is not int or max_bytes < 1024:
            raise ValueError('evidence JSON cache requires at least 1024 bytes')
        self.max_bytes = max_bytes
        self.entries: OrderedDict[bytes, bytes] = OrderedDict()
        self.payload_bytes = 0
        self.reused_bytes = 0
        self._owner: OwnedEvidence | None = None
        self._owned_entries: dict[int, bytes] = {}
        self._owned_bytes = 0

    @property
    def memory_bytes(self) -> int:
        return self.payload_bytes + self._owned_bytes + getsizeof(self.entries) + getsizeof(self._owned_entries)

    def clear(self) -> None:
        self.entries.clear()
        self.payload_bytes = 0
        self._owner = None
        self._owned_entries.clear()
        self._owned_bytes = 0

    def encode(self, value: object, encode: _Encode, *, stroke: bool = False) -> bytes:
        if (stroke and type(value) is dict and all(type(key) is str for key in value)
                and type(value.get('points')) in (list, tuple, PointVersion, CertifiedSource)):
            # 路径外壳每日变化；缓存整条路径会挤走其中可复用的历史点，且
            # structural_legs 又重复编码同一批点。只缓存点，外壳逐次重建。
            parts = []
            for key in sorted(value):
                item = value[key]
                if key == 'points':
                    encoded = b'[' + b', '.join(self.encode(point, encode) for point in item) + b']'
                elif key in ('structural_legs', 'confirmed_legs') and type(item) in (list, tuple, PointVersion, CertifiedSource):
                    encoded = b'[' + b', '.join(self.encode(leg, encode, stroke=True) for leg in item) + b']'
                else:
                    encoded = encode(item)
                parts.append(json.dumps(key, ensure_ascii=False).encode() + b': ' + encoded)
            return b'{' + b', '.join(parts) + b'}'
        # 请求证据池已认证不可变节点，稳定引用可直接复用编码；不能为每个旧点
        # 再遍历整份证明生成签名。只准入预算内新项，旧前缀不参与逐日 LRU 抖动。
        owner = current_evidence()
        if owner is not self._owner:
            self._owner = owner
            self._owned_entries.clear()
            self._owned_bytes = 0
        reference = owner.reference(value) if owner is not None else None
        if reference is not None:
            previous = self._owned_entries.get(reference)
            if previous is not None:
                self.reused_bytes += len(previous)
                return previous
            result = encode(value)
            charged = getsizeof(reference) + getsizeof(result) + 128
            if charged + self.memory_bytes + 1024 <= self.max_bytes:
                self._owned_entries[reference] = result
                self._owned_bytes += charged
                if self.memory_bytes > self.max_bytes:
                    del self._owned_entries[reference]
                    self._owned_bytes -= charged
                    if self.memory_bytes > self.max_bytes:
                        self._owned_entries = dict(self._owned_entries)
            return result
        signature = _geometry_signature(value)
        if signature is None:
            return encode(value)
        previous = self.entries.get(signature)
        if previous is not None:
            self.entries.move_to_end(signature)
            self.reused_bytes += len(previous)
            return previous
        result = encode(value)
        charged = getsizeof(signature) + getsizeof(result) + 128
        if charged + 1024 > self.max_bytes:
            return result
        self.entries[signature] = result
        self.payload_bytes += charged
        while self.entries and (self.memory_bytes > self.max_bytes or len(self.entries) > 512):
            old_key, old_value = self.entries.popitem(last=False)
            self.payload_bytes -= getsizeof(old_key) + getsizeof(old_value) + 128
        return result


class TrendChunk:
    """趋势行只替换 strokes 字段，未知形状保留原编码；没有跨块引用依赖。"""

    def __init__(self) -> None:
        self.values: list[bytes] = []
        self.indices: dict[bytes, int] = {}
        self.expanded_bytes = 0

    @property
    def full(self) -> bool:
        # 读接口还原全文，引用压缩不能让一个小块展开成整份历史；单行超限单独落盘。
        return self.expanded_bytes >= 4 * 1024 * 1024 or len(self.values) >= 4096

    def _intern(self, value: bytes) -> int:
        index = self.indices.get(value)
        if index is None:
            index = len(self.values)
            self.values.append(value)
            self.indices[value] = index
        return index

    def _stroke(self, value: object, cache: EvidenceJsonCache, encode: _Encode) -> tuple[bytes, int]:
        if (type(value) is not dict or any(type(key) is not str for key in value)
                or type(value.get('points')) not in (list, tuple, PointVersion, CertifiedSource)):
            encoded = encode(value)
            return b'{"value":' + encoded + b'}', len(encoded)
        # 证明表在当前块内自包含；每日版本和线段共享同一点的 JSON 内容，
        # 不存跨块／跨请求引用，也不丢掉任何旧版或审计字段。
        nested = {'points'} | {key for key in ('structural_legs', 'confirmed_legs')
                              if type(value.get(key)) in (list, tuple, PointVersion, CertifiedSource)}
        metadata = encode({key: item for key, item in value.items() if key not in nested})
        points = [cache.encode(point, encode) for point in value['points']]
        references = [self._intern(point) for point in points]
        expanded = len(metadata) + sum(len(point) + 2 for point in points) + 16
        parts = [b'"row":' + metadata, b'"points":' + json.dumps(references).encode()]
        for key in ('structural_legs', 'confirmed_legs'):
            if key not in nested:
                continue
            legs = [self._stroke(leg, cache, encode) for leg in value[key]]
            parts.append(json.dumps(key).encode() + b':[' + b','.join(leg for leg, _ in legs) + b']')
            expanded += sum(size + 2 for _, size in legs) + len(key) + 6
        return b'{' + b','.join(parts) + b'}', expanded

    def encode(self, row: Mapping[str, object], cache: EvidenceJsonCache, encode: _Encode) -> tuple[bytes, int]:
        if (type(row) is not dict or any(type(key) is not str for key in row)
                or type(row.get('strokes')) not in (list, tuple)):
            value = encode(row)
            result = b'{"value":' + value + b'}'
            self.expanded_bytes += len(value)
            return result, len(result)
        metadata = encode({key: value for key, value in row.items() if key != 'strokes'})
        before = len(self.values)
        strokes = [self._stroke(stroke, cache, encode)
                   for stroke in cast(list[object] | tuple[object, ...], row['strokes'])]
        self.expanded_bytes += len(metadata) + sum(size + 2 for _, size in strokes) + 16
        result = b'{"row":' + metadata + b',"strokes":[' + b','.join(value for value, _ in strokes) + b']}'
        return result, len(result) + sum(len(value) for value in self.values[before:])

    def finish(self, rows: list[bytes]) -> bytes:
        return (b'{"format":"' + _POINT_FORMAT.encode() + b'","values":[' + b','.join(self.values)
                + b'],"rows":[' + b','.join(rows) + b']}')


def _restore_point_stroke(entry: object, values: list[object]) -> object:
    if not isinstance(entry, dict):
        raise ValueError('invalid calculation point stroke')
    if set(entry) == {'value'}:
        return deepcopy(entry['value'])
    if (not {'row', 'points'} <= set(entry) <= {'row', 'points', 'structural_legs', 'confirmed_legs'}
            or not isinstance(entry['row'], dict) or 'points' in entry['row']
            or not isinstance(entry['points'], list)):
        raise ValueError('invalid calculation point row')
    if any(type(index) is not int or not 0 <= index < len(values) for index in entry['points']):
        raise ValueError('invalid calculation point reference')
    restored = deepcopy(entry['row'])
    restored['points'] = [deepcopy(values[index]) for index in entry['points']]
    for key in ('structural_legs', 'confirmed_legs'):
        if key in entry:
            if key in restored or not isinstance(entry[key], list):
                raise ValueError('invalid calculation leg references')
            restored[key] = [_restore_point_stroke(leg, values) for leg in entry[key]]
    return {key: restored[key] for key in sorted(restored)}


def restore_calculation_chunk(value: object) -> object:
    """兼容旧数组；新格式导出与原 JSON 契约相同且每行可独立修改。"""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or value.get('format') not in (_FORMAT, _POINT_FORMAT):
        raise ValueError('unsupported calculation chunk format')
    values, rows = value.get('values'), value.get('rows')
    if not isinstance(values, list) or not isinstance(rows, list):
        raise ValueError('invalid calculation chunk tables')
    result: list[dict[str, object]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            raise ValueError('invalid calculation chunk row')
        if set(entry) == {'value'} and isinstance(entry['value'], dict):
            result.append(deepcopy(entry['value']))
            continue
        if (set(entry) != {'row', 'strokes'} or not isinstance(entry['row'], dict)
                or 'strokes' in entry['row'] or not isinstance(entry['strokes'], list)):
            raise ValueError('invalid calculation stroke row')
        references = entry['strokes']
        point_format = value['format'] == _POINT_FORMAT
        if not point_format and any(type(index) is not int or not 0 <= index < len(values) for index in references):
            raise ValueError('invalid calculation stroke reference')
        row = deepcopy(entry['row'])
        row['strokes'] = ([_restore_point_stroke(stroke, values) for stroke in references] if point_format else
                          [deepcopy(values[index]) for index in references])
        result.append({key: row[key] for key in sorted(row)})
    return result
