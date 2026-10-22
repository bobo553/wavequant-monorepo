"""完整证据池拒绝后仍按当前不可变图增量保存，旧版本只占临时文件。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
import pickle
from struct import Struct
from sys import getsizeof
from tempfile import TemporaryFile
from typing import cast
from uuid import uuid4

from .owned_evidence import CertifiedSource
from .trend_point_version import PointVersion
from .trend_frozen_state import _SequenceNode


_MAGIC = b'WQ-prepared-graph-1\x00'
_SIZE = Struct('>Q')
_Token = tuple[str, object]


class _NotAdmitted(Exception):
    """未知或超预算图继续使用原完整行协议。"""


@dataclass(slots=True)
class _LiveNode:
    value: object
    reference: int
    children: tuple[int, ...]
    charged: int
    users: int = 0


@dataclass(slots=True)
class _DecodedBlock:
    values: tuple[object, ...]
    left: '_DecodedBlock | None'
    right: '_DecodedBlock | None'


class PreparedGraphHistory:
    """只为新节点写记录；引用计数在当前根替换后释放旧身份。

    ID 是请求专属临时文件偏移，释放内存不改变旧版本的 ID。身份表强引用
    当前图，避免 builtin 容器的 id 重用；没有无界的历史对象表。旧节点
    若以后重新成为输入，可得到新 ID，同一次恢复仍保留当前图的全部别名。
    本类只接收上游已移交的不可变内部图，不能用于外部可变字典。
    """

    def __init__(self, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('graph history memory_bytes must be nonnegative')
        self._limit = memory_bytes
        self._used = 0
        self._namespace = uuid4().bytes
        self._stream = TemporaryFile(mode='w+b')
        self._end = 0
        self._objects: dict[int, _LiveNode] = {}
        self._references: dict[int, _LiveNode] = {}
        self._root: int | None = None
        self.captures = 0
        self.refusals = 0
        self.written_nodes = 0

    @property
    def memory_used(self) -> int:
        return self._used

    @property
    def stored_bytes(self) -> int:
        return self._end

    @property
    def retained_nodes(self) -> int:
        return len(self._objects)

    @staticmethod
    def is_payload(payload: bytes) -> bool:
        return payload.startswith(_MAGIC)

    def _token(self, value: object) -> _Token:
        if type(value) in (int, bool, type(None)):
            return 'value', value
        if type(value) is float:
            if not isfinite(cast(float, value)):
                raise _NotAdmitted
            return 'value', value
        node = self._objects.get(id(value))
        if node is None or node.value is not value:
            raise _NotAdmitted
        return 'ref', node.reference

    @staticmethod
    def _children(value: object) -> tuple[str, list[object]]:
        kind = type(value)
        if kind is dict:
            mapping = cast(dict[object, object], value)
            if any(type(key) not in (str, int, float, bool, type(None)) for key in mapping):
                raise _NotAdmitted
            return 'dict', [child for pair in mapping.items() for child in pair]
        if kind in (PointVersion, CertifiedSource):
            if not cast(PointVersion, value)._sealed:
                raise _NotAdmitted
            points = cast(PointVersion, value)
            return 'points', [points._archive, points._stop, tuple(points._tail)]
        if kind is _SequenceNode:
            block = cast(_SequenceNode, value)
            return 'block', [block.values, block.left, block.right]
        if kind in (list, tuple):
            return ('list' if kind is list else 'tuple'), list(cast(Sequence[object], value))
        if kind in (str, bytes):
            return ('str' if kind is str else 'bytes'), []
        raise _NotAdmitted

    def _release(self, reference: int) -> None:
        pending = [reference]
        while pending:
            node = self._references[pending.pop()]
            node.users -= 1
            if node.users:
                continue
            del self._objects[id(node.value)]
            del self._references[node.reference]
            self._used -= node.charged
            pending.extend(node.children)

    def _rollback(self, created: list[_LiveNode], start: int) -> None:
        new_ids = {node.reference for node in created}
        for node in created:
            for child in node.children:
                if child not in new_ids:
                    self._references[child].users -= 1
            del self._objects[id(node.value)]
            del self._references[node.reference]
            self._used -= node.charged
        self._stream.seek(start)
        self._stream.truncate()
        self._end = start

    def capture(self, value: Mapping[str, object]) -> bytes | None:
        """超限拒绝原子撤销新增记录，不改变已发布历史或现有根。"""
        if self._stream.closed:
            raise ValueError('graph history is closed')
        start = self._end
        self.captures += 1
        created: list[_LiveNode] = []
        visiting: set[int] = set()
        planned: set[int] = set()
        records: list[tuple[object, str, list[object], int]] = []
        reserved = 0
        pending: list[tuple[object, tuple[str, list[object], int] | None, bool]] = [(value, None, False)]
        try:
            while pending:
                item, leaving, internal_block = pending.pop()
                if leaving is not None:
                    kind, children, charged = leaving
                    records.append((item, kind, children, charged))
                    planned.add(id(item))
                    visiting.remove(id(item))
                    continue
                kind = type(item)
                if kind in (int, float, bool, type(None)):
                    self._token(item)
                    continue
                if kind not in (dict, list, tuple, str, bytes, PointVersion, CertifiedSource, _SequenceNode):
                    raise _NotAdmitted
                if kind is _SequenceNode and not internal_block:
                    # 树节点只属于点版本的存储协议，不能扩展外部审计字段白名单。
                    raise _NotAdmitted
                known = self._objects.get(id(item))
                if known is not None and known.value is item:
                    continue
                if id(item) in planned:
                    continue
                if id(item) in visiting:
                    raise _NotAdmitted
                # 在枚举容器前检查其直接存储，拒绝巨型外壳时不额外复制它。
                count = len(item) if kind in (dict, list, tuple) else 3 if kind in (
                    PointVersion, CertifiedSource, _SequenceNode) else 0
                edges = count * 2 if kind is dict else count
                charged = getsizeof(item) + 768 + edges * 24
                if kind in (PointVersion, CertifiedSource):
                    charged += getsizeof(vars(item)) + getsizeof(cast(PointVersion, item)._tail)
                if kind is CertifiedSource:
                    source = cast(CertifiedSource, item)
                    charged += source.signature_bytes + source.keys.storage_bytes + source._costs.storage_bytes
                    charged += len(source._costs) * getsizeof(source.signature_bytes)
                if self._used + reserved + charged > self._limit:
                    raise _NotAdmitted
                record_kind, children = self._children(item)
                # 内嵌标量没有独立身份节点，大整数等实际存储仍须纳入父记录。
                charged += sum(getsizeof(child) for child in children
                               if type(child) in (int, float, bool, type(None)))
                if self._used + reserved + charged > self._limit:
                    raise _NotAdmitted
                reserved += charged
                visiting.add(id(item))
                pending.append((item, (record_kind, children, charged), internal_block))
                pending.extend((children[index], None,
                                record_kind == 'points' and index == 0 or record_kind == 'block' and index > 0)
                               for index in range(len(children) - 1, -1, -1))
            # 整批准入后才写入；超过预算的版本不先写整图再截断文件。
            # records 已按后序排列，写出时只需连接新节点和已有的磁盘 ID。
            for item, kind, children, charged in records:
                tokens = tuple(self._token(child) for child in children)
                record = (kind, item if kind in ('str', 'bytes') else tokens)
                payload = pickle.dumps(record, protocol=5)
                reference = self._end
                self._stream.seek(reference)
                self._stream.write(_SIZE.pack(len(payload)))
                self._stream.write(payload)
                self._end += _SIZE.size + len(payload)
                child_ids = tuple(cast(int, token[1]) for token in tokens if token[0] == 'ref')
                node = _LiveNode(item, reference, child_ids, charged)
                self._objects[id(item)] = node
                self._references[reference] = node
                created.append(node)
                for child in child_ids:
                    self._references[child].users += 1
                self._used += charged
            root = cast(int, self._token(value)[1])
            self._references[root].users += 1
            previous = self._root
            self._root = root
            if previous is not None:
                self._release(previous)
            self.written_nodes += len(created)
            return _MAGIC + self._namespace + _SIZE.pack(root)
        except _NotAdmitted:
            self.refusals += 1
            self._rollback(created, start)
            return None
        except BaseException:
            self._rollback(created, start)
            raise

    def _read(self, reference: int) -> tuple[str, object]:
        if type(reference) is not int or reference < 0 or reference + _SIZE.size > self._end:
            raise ValueError('graph history reference is outside its stream')
        self._stream.seek(reference)
        header = self._stream.read(_SIZE.size)
        if len(header) != _SIZE.size:
            raise ValueError('graph history has an incomplete header')
        length = _SIZE.unpack(header)[0]
        if length > self._end - reference - _SIZE.size:
            raise ValueError('graph history has an incomplete record')
        payload = self._stream.read(length)
        if len(payload) != length:
            raise ValueError('graph history has an incomplete payload')
        record: object = pickle.loads(payload)
        if type(record) is not tuple or len(record) != 2 or record[0] not in (
                'dict', 'list', 'tuple', 'str', 'bytes', 'points', 'block'):
            raise ValueError('graph history requires a typed node')
        return cast(tuple[str, object], record)

    def restore(self, payload: bytes) -> object:
        """每次读取独立恢复；同次读取按磁盘 ID 保留别名和容器类型。"""
        if self._stream.closed:
            raise ValueError('graph history is closed')
        prefix = _MAGIC + self._namespace
        if not payload.startswith(prefix) or len(payload) != len(prefix) + _SIZE.size:
            raise ValueError('graph history payload must belong to its request')
        root = _SIZE.unpack(payload[len(prefix):])[0]
        restored: dict[int, object] = {}
        pending: list[tuple[int, tuple[str, object] | None]] = [(root, None)]
        while pending:
            reference, record = pending.pop()
            if reference in restored:
                continue
            if record is None:
                kind, content = self._read(reference)
                if kind in ('str', 'bytes'):
                    if type(content) is not (str if kind == 'str' else bytes):
                        raise ValueError('graph history scalar type differs')
                    restored[reference] = content
                    continue
                if type(content) is not tuple:
                    raise ValueError('graph history requires child tokens')
                children = cast(tuple[_Token, ...], content)
                pending.append((reference, (kind, children)))
                for token in reversed(children):
                    if type(token) is not tuple or len(token) != 2 or token[0] not in ('ref', 'value'):
                        raise ValueError('graph history contains an invalid token')
                    if token[0] == 'ref':
                        child = token[1]
                        if type(child) is not int or not 0 <= child < reference:
                            raise ValueError('graph history child must precede its parent')
                        pending.append((child, None))
                continue
            kind, content = record
            values = [restored[cast(int, token[1])] if token[0] == 'ref' else token[1]
                      for token in cast(tuple[_Token, ...], content)]
            if kind == 'block':
                if (len(values) != 3 or type(values[0]) is not tuple
                        or any(child is not None and type(child) is not _DecodedBlock for child in values[1:])):
                    raise ValueError('graph history has an invalid frozen block')
                restored[reference] = _DecodedBlock(cast(tuple[object, ...], values[0]),
                    cast(_DecodedBlock | None, values[1]), cast(_DecodedBlock | None, values[2]))
            elif kind == 'points':
                if (len(values) != 3 or type(values[1]) is not int or cast(int, values[1]) < 0
                        or type(values[2]) is not tuple
                        or values[0] is not None and type(values[0]) is not _DecodedBlock):
                    raise ValueError('graph history has an invalid point version')
                points: list[object] = []
                blocks = [cast(_DecodedBlock | None, values[0])]
                while blocks:
                    block = blocks.pop()
                    if block is not None:
                        points.extend(block.values)
                        blocks.extend((block.right, block.left))
                stop = cast(int, values[1])
                if len(points) != stop:
                    raise ValueError('graph history point prefix length differs')
                points.extend(cast(tuple[object, ...], values[2]))
                restored[reference] = points
            elif kind == 'dict':
                if len(values) % 2:
                    raise ValueError('graph history mapping has an incomplete pair')
                restored[reference] = dict(zip(values[::2], values[1::2]))
            else:
                restored[reference] = tuple(values) if kind == 'tuple' else values
        return restored[root]

    def close(self) -> None:
        self.release_current()
        self._stream.close()

    def release_current(self) -> None:
        """忘记当前身份索引；磁盘 ID 仍可恢复全部已发布日期。"""
        self._objects.clear()
        self._references.clear()
        self._root = None
        self._used = 0
