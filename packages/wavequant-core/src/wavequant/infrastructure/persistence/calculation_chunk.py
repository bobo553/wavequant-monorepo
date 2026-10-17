"""步骤缓存内的有界证据编码复用；引用仅在单个自包含 chunk 内有效。"""

from collections import OrderedDict
from collections.abc import Callable, Mapping
from copy import deepcopy
import json
from sys import getsizeof
from typing import cast

from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature


_FORMAT = 'trend_stroke_refs_v1'
_Encode = Callable[[object], bytes]


class EvidenceJsonCache:
    """仅按完整标准值签名复用 JSON 字节，不按可变对象身份猜测未变化。"""

    def __init__(self, *, max_bytes: int = 8 * 1024 * 1024) -> None:
        if type(max_bytes) is not int or max_bytes < 1024:
            raise ValueError('evidence JSON cache requires at least 1024 bytes')
        self.max_bytes = max_bytes
        self.entries: OrderedDict[bytes, bytes] = OrderedDict()
        self.payload_bytes = 0
        self.reused_bytes = 0

    @property
    def memory_bytes(self) -> int:
        return self.payload_bytes + getsizeof(self.entries)

    def clear(self) -> None:
        self.entries.clear()
        self.payload_bytes = 0

    def encode(self, value: object, encode: _Encode, *, stroke: bool = False) -> bytes:
        signature = _geometry_signature(value)
        if signature is None:
            return encode(value)
        previous = self.entries.get(signature)
        if previous is not None:
            self.entries.move_to_end(signature)
            self.reused_bytes += len(previous)
            return previous
        if (stroke and type(value) is dict and all(type(key) is str for key in value)
                and type(value.get('points')) in (list, tuple)):
            # 活跃尾段会变，已封闭点的完整证明仍可复用，避免重编码整条来源树。
            parts = []
            for key in sorted(value):
                item = value[key]
                encoded = (b'[' + b', '.join(self.encode(point, encode) for point in item) + b']'
                           if key == 'points' else encode(item))
                parts.append(json.dumps(key, ensure_ascii=False).encode() + b': ' + encoded)
            result = b'{' + b', '.join(parts) + b'}'
        else:
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

    def encode(self, row: Mapping[str, object], cache: EvidenceJsonCache, encode: _Encode) -> tuple[bytes, int]:
        if (type(row) is not dict or any(type(key) is not str for key in row)
                or type(row.get('strokes')) not in (list, tuple)):
            value = encode(row)
            result = b'{"value":' + value + b'}'
            self.expanded_bytes += len(value)
            return result, len(result)
        metadata = encode({key: value for key, value in row.items() if key != 'strokes'})
        encoded_strokes = [cache.encode(stroke, encode, stroke=True)
                           for stroke in cast(list[object] | tuple[object, ...], row['strokes'])]
        self.expanded_bytes += len(metadata) + sum(len(value) + 2 for value in encoded_strokes) + 16
        references: list[int] = []
        added = 0
        for value in encoded_strokes:
            index = self.indices.get(value)
            if index is None:
                index = len(self.values)
                self.values.append(value)
                self.indices[value] = index
                added += len(value)
            references.append(index)
        result = b'{"row":' + metadata + b',"strokes":' + json.dumps(references).encode() + b'}'
        return result, len(result) + added

    def finish(self, rows: list[bytes]) -> bytes:
        return (b'{"format":"' + _FORMAT.encode() + b'","values":[' + b','.join(self.values)
                + b'],"rows":[' + b','.join(rows) + b']}')


def restore_calculation_chunk(value: object) -> object:
    """兼容旧数组；新格式导出与原 JSON 契约相同且每行可独立修改。"""
    if isinstance(value, list):
        return value
    if not isinstance(value, dict) or value.get('format') != _FORMAT:
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
        if any(type(index) is not int or not 0 <= index < len(values) for index in references):
            raise ValueError('invalid calculation stroke reference')
        row = deepcopy(entry['row'])
        row['strokes'] = [deepcopy(values[index]) for index in references]
        result.append({key: row[key] for key in sorted(row)})
    return result
