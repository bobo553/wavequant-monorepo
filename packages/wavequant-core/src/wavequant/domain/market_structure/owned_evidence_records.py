"""请求内的不可变证据记录；生产者显式连接新记录和已有磁盘引用。"""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from math import isfinite
from struct import Struct
from sys import getsizeof
from tempfile import TemporaryFile
from types import TracebackType
from typing import BinaryIO, TypeAlias, cast
from uuid import uuid4


Scalar: TypeAlias = bool | int | float | str | bytes | None
_INDEX = Struct('>QQB7x')
_OFFSET = Struct('>Q')
_FLOAT = Struct('>d')
_VECTOR_HEADER = Struct('>QQQ')
_NONE, _BOOL, _INT, _FLOAT_KIND, _STR, _BYTES, _LIST, _TUPLE, _DICT, _CLONE, _VECTOR, _DOMAIN, _DOMAIN_REF = range(13)
_EMPTY_CACHE_BYTES = getsizeof(OrderedDict())


@dataclass(frozen=True, slots=True)
class RecordRef:
    """只在原请求存活期间有效；offset 是固定宽度逻辑索引中的偏移。"""

    namespace: bytes
    offset: int


@dataclass(frozen=True, slots=True)
class _DecodedRecord:
    kind: int
    scalar: Scalar = None
    children: tuple[int, ...] = ()


_RecordHeader: TypeAlias = tuple[int, int, int]
_CacheEntry: TypeAlias = tuple[_RecordHeader, _DecodedRecord | None, int]


class OwnedEvidenceRecords:
    """单请求、单执行线程拥有的追加存储，不扫描引用所指的旧子图。

    typed 写入口仅接收显式子引用，所有边向前指向已提交记录。磁盘索引验证
    引用的范围和对齐，不需要全历史对象身份表。相等内容也创建不同记录。
    decoded 缓存只持有不可变记录描述；其淘汰不改变任何旧引用或写入行为。

    materialize 每次建立独立的临时 memo，保留本次读取中的容器别名；clone
    将子图放入新的复制域，域内别名保留、域外可变对象隔离。与 builtin
    deepcopy 一样，标量和完全不可变 tuple 跨复制域沿用同一记录的读取值；
    不按值合并不同记录，Python 自身的标量驻留规则除外。公开可变结果从不进入读缓存。
    一次读取的输出和工作 memo 随所请求子图增长，不属于常驻 decoded 预算。
    """

    def __init__(self, *, cache_bytes: int = 1024 * 1024, max_record_bytes: int = 64 * 1024 * 1024,
                 page_bytes: int = 0) -> None:
        if type(cache_bytes) is not int or cache_bytes < 0:
            raise ValueError('record cache_bytes must be nonnegative')
        if type(max_record_bytes) is not int or max_record_bytes < _OFFSET.size:
            raise ValueError('record max_record_bytes must be at least eight')
        if type(page_bytes) is not int or page_bytes < 0 or 0 < page_bytes < _INDEX.size:
            raise ValueError('record page_bytes must be zero or at least one index record')
        if page_bytes and 3 * page_bytes + 2048 > cache_bytes:
            raise ValueError('record pages exceed the shared storage memory limit')
        self._namespace = uuid4().bytes
        self._data_page = _AppendPage(page_bytes) if page_bytes else None
        self._index_page = _AppendPage(page_bytes // _INDEX.size * _INDEX.size) if page_bytes else None
        # 页、持有者及一次页内读取的bytes副本占原读缓存额度。记录不跨页，
        # 因而不存在随历史增长的file/page拼接缓冲，也不增加owner总预算。
        self._page_reserved = (sum(page.memory_used for page in (self._data_page, self._index_page)
                                  if page is not None) + page_bytes + 2048 if page_bytes else 0)
        if self._page_reserved > cache_bytes:
            raise ValueError('record pages exceed the shared storage memory limit')
        self._cache_limit = cache_bytes - self._page_reserved
        self._max_record_bytes = max_record_bytes
        self._cache: OrderedDict[int, _CacheEntry] = OrderedDict()
        self._cache_used = 0
        # 页模式的commit表示整个新图已存在于请求拥有的RAM页或文件，不承诺
        # 磁盘耐久。公共读取仍只见完整已提交图；页满才物理写出。零页模式
        # 保留原同步写语义。临时流绝不交给请求之外的持久化消费者。
        self._data: BinaryIO = TemporaryFile(mode='w+b', buffering=0)
        try:
            self._index: BinaryIO = TemporaryFile(mode='w+b', buffering=0)
        except BaseException:
            self._data.close()
            raise
        self._data_end = self._index_end = 0
        self._committed_data_end = self._committed_index_end = 0
        self._closed = False
        self._writing = False

    @property
    def namespace(self) -> bytes:
        return self._namespace

    @property
    def record_count(self) -> int:
        return self._committed_index_end // _INDEX.size

    @property
    def stored_bytes(self) -> int:
        return self._committed_data_end + self._committed_index_end

    @property
    def cache_memory_used(self) -> int:
        """包括条目及动态索引分配；不包括空缓存持有者的固定开销。"""
        return self._cache_used + max(0, getsizeof(self._cache) - _EMPTY_CACHE_BYTES)

    @property
    def storage_memory_used(self) -> int:
        """读缓存加固定页及其有界读取临时空间的保守上界。"""
        return self.cache_memory_used + self._page_reserved

    def _require_open(self) -> None:
        if self._closed:
            raise ValueError('evidence record store is closed')

    def _validate_ref(self, reference: RecordRef) -> int:
        self._require_open()
        if (type(reference) is not RecordRef or type(reference.namespace) is not bytes
                or reference.namespace != self._namespace or type(reference.offset) is not int
                or reference.offset < 0 or reference.offset % _INDEX.size
                or reference.offset >= self._committed_index_end):
            raise ValueError('evidence reference must belong to a committed record in its request')
        return reference.offset

    @staticmethod
    def _scalar_kind(value: object) -> int:
        kind = type(value)
        if kind is type(None):
            return _NONE
        if kind is bool:
            return _BOOL
        if kind is int:
            return _INT
        if kind is float:
            if not isfinite(cast(float, value)):
                raise ValueError('evidence scalar must be finite')
            return _FLOAT_KIND
        if kind is str:
            return _STR
        if kind is bytes:
            return _BYTES
        raise TypeError('evidence scalar requires an exact builtin scalar type')

    def _scalar_payload(self, value: object) -> tuple[int, bytes]:
        kind = self._scalar_kind(value)
        if kind == _NONE:
            return kind, b''
        if kind == _BOOL:
            return kind, b'\x01' if value else b'\x00'
        if kind == _INT:
            integer = cast(int, value)
            size = max(1, (integer.bit_length() + 8) // 8)
            if size > self._max_record_bytes:
                raise ValueError('evidence record exceeds its size limit')
            return kind, integer.to_bytes(size, 'big', signed=True)
        if kind == _FLOAT_KIND:
            return kind, _FLOAT.pack(cast(float, value))
        if kind == _STR:
            text = cast(str, value)
            if len(text) > self._max_record_bytes:
                raise ValueError('evidence record exceeds its size limit')
            payload = text.encode('utf-8', errors='surrogatepass')
        else:
            payload = cast(bytes, value)
        if len(payload) > self._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        return kind, payload

    @staticmethod
    def _write_all(stream: BinaryIO, payload: bytes | memoryview) -> None:
        remaining = memoryview(payload)
        while remaining:
            written = stream.write(remaining)
            if written is None or written <= 0 or written > len(remaining):
                raise OSError('incomplete evidence record write')
            remaining = remaining[written:]

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._require_open()
        if self._writing:
            raise ValueError('evidence record writes cannot be nested')
        self._writing = True
        data_start, index_start = self._data_end, self._index_end
        try:
            yield
        except BaseException:
            # 同次新图可能已预填完整子记录；回滚后偏移会被复用，不能留下
            # 已撤销的 header 或解码值。旧缓存可被淘汰，无需恢复其命中率。
            self._discard_cached_from(index_start)
            try:
                self._rollback_streams(data_start, index_start)
                self._data_end, self._index_end = data_start, index_start
            except (OSError, ValueError):
                # 无法撤销时令整个请求存储失效，不能继续发布可疑偏移。
                self._close_resources()
                raise
            raise
        else:
            self._committed_data_end = self._data_end
            self._committed_index_end = self._index_end
        finally:
            self._writing = False

    @contextmanager
    def _new_graph_writer(self, *, memory_bytes: int) -> Iterator['_NewGraphWriter']:
        """生产者私有新图批次；完整写入请求页或文件后才发布提交边界。"""
        self._require_open()
        writer = _NewGraphWriter(self, memory_bytes=memory_bytes)
        try:
            with self._transaction():
                yield writer
                writer._flush()
        finally:
            writer.close()

    def _append(self, kind: int, payload: bytes) -> RecordRef:
        if len(payload) > self._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        reference = RecordRef(self._namespace, self._index_end)
        if self._data_page is not None and self._index_page is not None:
            self._data_page.append(self._data, payload)
            self._index_page.append(self._index, _INDEX.pack(self._data_end, len(payload), kind))
        else:
            self._data.seek(self._data_end)
            self._write_all(self._data, payload)
            self._index.seek(self._index_end)
            self._write_all(self._index, _INDEX.pack(self._data_end, len(payload), kind))
        header = self._data_end, len(payload), kind
        self._data_end += len(payload)
        self._index_end += _INDEX.size
        self._cache_written_record(reference.offset, header, payload)
        return reference

    def _rollback_streams(self, data_start: int, index_start: int) -> None:
        for stream, page, start in ((self._data, self._data_page, data_start),
                                    (self._index, self._index_page, index_start)):
            if page is None:
                stream.seek(start)
                stream.truncate(start)
            else:
                page.rollback(stream, start)

    def _read_index(self, offset: int) -> bytes:
        if self._index_page is not None:
            return self._index_page.read(self._index, offset, _INDEX.size)
        self._index.seek(offset)
        return self._index.read(_INDEX.size)

    def _read_payload(self, offset: int, size: int) -> bytes:
        if self._data_page is not None:
            return self._data_page.read(self._data, offset, size)
        self._data.seek(offset)
        return self._data.read(size)

    def flush(self) -> None:
        """显式物理写出已提交页；失败且可截回时，旧根仍可从保留页读取。"""
        self._require_open()
        if self._writing:
            raise ValueError('evidence pages cannot flush through a public active transaction')
        try:
            for stream, page in ((self._data, self._data_page), (self._index, self._index_page)):
                if page is not None:
                    page.flush(stream)
        except BaseException:
            try:
                self._rollback_streams(self._committed_data_end, self._committed_index_end)
            except (OSError, ValueError):
                self._close_resources()
                raise
            raise

    def _cache_written_record(self, offset: int, header: _RecordHeader, payload: bytes) -> None:
        if self._cache_limit:
            # 仅预解码肯定能受缓存预算约束的小记录；宽列表的 offset 整数
            # 和 tuple 会放大 payload，不能先完整分配再判定装不下。
            record = (self._decode_record(offset, header[2], payload)
                      if 512 + len(payload) * 8 <= self._cache_limit else None)
            self._cache_record(offset, record, header)

    def _offset_payload(self, offsets: list[int] | tuple[int, ...]) -> bytes:
        size = len(offsets) * _OFFSET.size
        if size > self._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        payload = bytearray(size)
        for index, offset in enumerate(offsets):
            _OFFSET.pack_into(payload, index * _OFFSET.size, offset)
        return bytes(payload)

    def write_scalar(self, value: Scalar) -> RecordRef:
        """保存一个有限 builtin 标量，不按值去重。"""
        with self._transaction():
            kind, payload = self._scalar_payload(value)
            return self._append(kind, payload)

    def _write_sequence(self, children: list[RecordRef] | tuple[RecordRef, ...], kind: int) -> RecordRef:
        with self._transaction():
            if type(children) not in (list, tuple):
                raise TypeError('evidence children require an exact list or tuple')
            if len(children) * _OFFSET.size > self._max_record_bytes:
                raise ValueError('evidence record exceeds its size limit')
            offsets = [self._validate_ref(child) for child in children]
            return self._append(kind, self._offset_payload(offsets))

    def write_list(self, children: list[RecordRef] | tuple[RecordRef, ...]) -> RecordRef:
        """仅连接已有子引用；不会 materialize 或遍历子图。"""
        return self._write_sequence(children, _LIST)

    def write_tuple(self, children: list[RecordRef] | tuple[RecordRef, ...]) -> RecordRef:
        """仅连接已有子引用，并在公开读取时恢复 tuple 类型。"""
        return self._write_sequence(children, _TUPLE)

    def write_dict(self, items: list[tuple[RecordRef, RecordRef]] | tuple[tuple[RecordRef, RecordRef], ...]) -> RecordRef:
        """按插入顺序保存键值引用；键必须是标量记录，重复键遵从 dict 语义。"""
        with self._transaction():
            if type(items) not in (list, tuple):
                raise TypeError('evidence items require an exact list or tuple')
            if len(items) * 2 * _OFFSET.size > self._max_record_bytes:
                raise ValueError('evidence record exceeds its size limit')
            offsets: list[int] = []
            for item in items:
                if type(item) is not tuple or len(item) != 2:
                    raise TypeError('evidence dictionary item requires an exact key/value tuple')
                key, value = item
                key_offset = self._validate_ref(key)
                if self._record_header(key_offset)[2] > _BYTES:
                    raise TypeError('evidence dictionary keys require scalar records')
                offsets.extend((key_offset, self._validate_ref(value)))
            return self._append(_DICT, self._offset_payload(offsets))

    def clone(self, reference: RecordRef) -> RecordRef:
        """新建复制域引用，不读取原子图；重复使用返回值保留该域中的别名。"""
        with self._transaction():
            offset = self._validate_ref(reference)
            return self._append(_CLONE, _OFFSET.pack(offset))

    def new_read_domain(self) -> RecordRef:
        """一次独立恢复的身份域；少量路径载体共用它，不保存对象身份表。"""
        with self._transaction():
            return self._append(_DOMAIN, b'')

    def domain_ref(self, reference: RecordRef, domain: RecordRef) -> RecordRef:
        """连接域和既有根，不展开历史子图；同域同目标按逻辑身份读取。"""
        target = self._validate_ref(reference)
        marker = self._validate_ref(domain)
        if self._record_header(marker)[2] not in (_DOMAIN, _CLONE):
            raise ValueError('evidence read domain requires its own marker')
        if self._record_header(target)[2] <= _BYTES:
            return reference
        with self._transaction():
            return self._append(_DOMAIN_REF, self._offset_payload((marker, target)))

    def _resolved(self, reference: RecordRef) -> tuple[int, _DecodedRecord, tuple[int, ...]]:
        current = self._validate_ref(reference)
        domains: list[int] = []
        record = self._record(current)
        while record.kind in (_DOMAIN_REF, _CLONE):
            if record.kind == _CLONE:
                marker, current = current, record.children[0]
            else:
                marker, current = record.children
                if self._record_header(marker)[2] not in (_DOMAIN, _CLONE):
                    raise ValueError('evidence domain reference requires its own marker')
            domains.append(marker)
            record = self._record(current)
        return current, record, tuple(domains)

    def _reference_identity(self, reference: RecordRef) -> tuple[int, tuple[int, ...]]:
        """身份比较只读域外壳；宽列表/证明字典的payload与身份无关。"""
        current = self._validate_ref(reference)
        domains: list[int] = []
        while self._record_header(current)[2] in (_DOMAIN_REF, _CLONE):
            record = self._record(current)
            if record.kind == _CLONE:
                marker, current = current, record.children[0]
            else:
                marker, current = record.children
                if self._record_header(marker)[2] not in (_DOMAIN, _CLONE):
                    raise ValueError('evidence domain reference requires its own marker')
            domains.append(marker)
        return current, tuple(domains)

    def same_reference(self, left: RecordRef, right: RecordRef) -> bool:
        """比较逻辑记录身份，不比较值，也不展开其子图。"""
        return self._reference_identity(left) == self._reference_identity(right)

    def record_view(self, reference: RecordRef) -> _DecodedRecord:
        """只解析当前外壳；域化子引用仍为常量记录，不读取证明内容。"""
        _, record, domains = self._resolved(reference)
        if not domains:
            return record

        def scoped(offset: int) -> int:
            value = RecordRef(self._namespace, offset)
            for marker in reversed(domains):
                value = self.domain_ref(value, RecordRef(self._namespace, marker))
            return value.offset

        if record.kind == _VECTOR:
            return _DecodedRecord(_VECTOR, record.scalar, (record.children[0], scoped(record.children[1])))
        if record.kind in (_LIST, _TUPLE, _DICT):
            return _DecodedRecord(record.kind, record.scalar, tuple(scoped(child) for child in record.children))
        if record.kind <= _BYTES:
            return record
        raise ValueError('evidence record view requires a builtin container or vector')

    def dictionary_items(self, reference: RecordRef) -> tuple[tuple[Scalar, RecordRef], ...]:
        """只读根字段引用；不会物化字段指向的历史证明。"""
        return tuple((self._record(key.offset).scalar, value)
                     for key, value in self.dictionary_refs(reference))

    def dictionary_refs(self, reference: RecordRef) -> tuple[tuple[RecordRef, RecordRef], ...]:
        """同时保留键和值的身份引用，供生产者浅复制已有外壳。"""
        offset = self._validate_ref(reference)
        if self._writing:
            raise ValueError('cannot read an evidence record during a write')
        record = self.record_view(reference)
        if record.kind != _DICT:
            raise ValueError('evidence field references require a dictionary record')
        result: list[tuple[RecordRef, RecordRef]] = []
        for index in range(0, len(record.children), 2):
            key = self._record(record.children[index])
            if key.kind > _BYTES:
                raise ValueError('evidence dictionary key must be a scalar record')
            result.append((RecordRef(self._namespace, record.children[index]),
                           RecordRef(self._namespace, record.children[index + 1])))
        return tuple(result)

    def _write_vector(self, root: RecordRef, capacity: int, length: int) -> RecordRef:
        """私有32项叶树的可见根；长度封住旧根中截短后的未来后缀。"""
        with self._transaction():
            offset = self._validate_ref(root)
            if (type(capacity) is not int or not 1 <= capacity <= 2 ** 58
                    or capacity & (capacity - 1) or type(length) is not int
                    or not 0 <= length <= capacity * 32):
                raise ValueError('evidence vector requires a valid capacity and visible length')
            if self._resolved(root)[1].kind != _TUPLE:
                raise ValueError('evidence vector root must be a private tuple node')
            return self._append(_VECTOR, _VECTOR_HEADER.pack(length, capacity, offset))

    def _vector_items(self, record: _DecodedRecord) -> Iterator[tuple[int, tuple[int, ...]]]:
        length = cast(int, record.scalar)
        capacity, root = record.children
        pending: list[tuple[int, int, int, tuple[int, ...]]] = [(root, capacity, 0, ())]
        while pending:
            current, width, start, inherited = pending.pop()
            if start >= length:
                continue
            _, node, local = self._resolved(RecordRef(self._namespace, current))
            domains = inherited + local
            if node.kind != _TUPLE:
                raise ValueError('evidence vector requires private tuple nodes')
            if width == 1:
                visible = min(32, length - start)
                if not visible <= len(node.children) <= 32:
                    raise ValueError('evidence vector has an incomplete visible leaf')
                yield from ((child, domains) for child in node.children[:visible])
            else:
                if len(node.children) != 2:
                    raise ValueError('evidence vector has an incomplete visible branch')
                half = width // 2
                pending.append((node.children[1], half, start + half * 32, domains))
                pending.append((node.children[0], half, start, domains))

    def capture_new(self, value: object, *, memory_bytes: int = 8 * 1024 * 1024) -> RecordRef:
        """仅为一次新 builtin DAG 建立局部身份 memo，已有 RecordRef 作为原子。

        每次调用是新的身份域，退出即释放所有输入强引用。memory_bytes 限制
        本次暂存的保守计费，不是允许长期保存历史对象的额度。循环、未知类型
        和非有限值均拒绝；拒绝会撤销本次全部新增记录，不返回不完整根。
        """
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('new evidence graph memory_bytes must be nonnegative')
        memo: dict[int, tuple[object, RecordRef]] = {}
        active: set[int] = set()
        pending: list[tuple[object, tuple[object, ...] | None]] = [(value, None)]
        charged = 0

        def reference_for(item: object) -> RecordRef:
            if type(item) is RecordRef:
                return cast(RecordRef, item)
            return memo[id(item)][1]

        with self._transaction(), ExitStack() as scope:
            # 异常 traceback 可能被外层保留；离开准入作用域时也主动清空身份表。
            scope.callback(memo.clear)
            scope.callback(active.clear)
            scope.callback(pending.clear)
            while pending:
                item, children = pending.pop()
                if type(item) is RecordRef:
                    self._validate_ref(cast(RecordRef, item))
                    continue
                identity, kind = id(item), type(item)
                if identity in memo:
                    continue
                if children is not None:
                    tag = _DICT if kind is dict else _LIST if kind is list else _TUPLE
                    offsets = [reference_for(child).offset for child in children]
                    memo[identity] = (item, self._append(tag, self._offset_payload(offsets)))
                    active.remove(identity)
                    continue
                if identity in active:
                    raise ValueError('new evidence graph cannot contain a cycle')
                if kind not in (dict, list, tuple):
                    self._scalar_kind(item)
                count = len(cast(dict[object, object] | list[object] | tuple[object, ...], item)) if kind in (
                    dict, list, tuple) else 0
                edges = count * 2 if kind is dict else count
                # 计入 memo/工作栈/临时子引用及编码副本，枚举外壳前先拒绝巨型输入。
                charged += 1536 + getsizeof(item) * 4 + edges * 128
                if charged > memory_bytes:
                    raise ValueError('new evidence graph exceeds its scoped memory budget')
                if kind not in (dict, list, tuple):
                    tag, payload = self._scalar_payload(item)
                    memo[identity] = (item, self._append(tag, payload))
                    continue
                if edges * _OFFSET.size > self._max_record_bytes:
                    raise ValueError('evidence record exceeds its size limit')
                if kind is dict:
                    mapping = cast(dict[object, object], item)
                    for key in mapping:
                        self._scalar_kind(key)
                    children = tuple(child for pair in mapping.items() for child in pair)
                else:
                    children = tuple(cast(list[object] | tuple[object, ...], item))
                active.add(identity)
                pending.append((item, children))
                pending.extend((child, None) for child in reversed(children))
            return reference_for(value)

    def _cache_record(self, offset: int, record: _DecodedRecord | None, header: _RecordHeader) -> None:
        if not self._cache_limit:
            return
        previous = self._cache.get(offset)
        if record is None and previous is not None:
            record = previous[1]
        charged = (getsizeof(offset) + getsizeof(header) + sum(getsizeof(value) for value in header)
                   + getsizeof((header, record, 0)) + getsizeof(0))
        if record is not None:
            charged += (getsizeof(record) + getsizeof(record.scalar) + getsizeof(record.children)
                        + sum(getsizeof(child) for child in record.children))
        if charged > self._cache_limit:
            return
        if previous is not None:
            self._cache_used -= self._cache.pop(offset)[2]
        self._cache[offset] = header, record, charged
        self._cache_used += charged
        while self.cache_memory_used > self._cache_limit and self._cache:
            _, (_, _, removed) = self._cache.popitem(last=False)
            self._cache_used -= removed
        if not self._cache:
            self._cache = OrderedDict()

    def _discard_cached_from(self, offset: int) -> None:
        for current in tuple(self._cache):
            if current >= offset:
                self._cache_used -= self._cache.pop(current)[2]
        if not self._cache:
            self._cache = OrderedDict()

    def _record_header(self, offset: int) -> _RecordHeader:
        self._require_open()
        if not 0 <= offset < self._committed_index_end or offset % _INDEX.size:
            raise ValueError('evidence record index must be committed')
        cached = self._cache.get(offset)
        if cached is not None:
            self._cache.move_to_end(offset)
            header = cached[0]
        else:
            payload = self._read_index(offset)
            if len(payload) != _INDEX.size:
                raise ValueError('incomplete evidence record index')
            header = _INDEX.unpack(payload)
        data_offset, size, kind = header
        if size > self._max_record_bytes or data_offset + size > self._committed_data_end:
            raise ValueError('invalid evidence record size')
        if cached is None:
            self._cache_record(offset, None, header)
        return header

    def _record(self, offset: int) -> _DecodedRecord:
        cached = self._cache.get(offset)
        if cached is not None and cached[1] is not None:
            self._require_open()
            if not 0 <= offset < self._committed_index_end or offset % _INDEX.size:
                raise ValueError('evidence record index must be committed')
            self._cache.move_to_end(offset)
            return cached[1]
        header = self._record_header(offset)
        data_offset, size, kind = header
        payload = self._read_payload(data_offset, size)
        if len(payload) != size:
            raise ValueError('incomplete evidence record payload')
        record = self._decode_record(offset, kind, payload)
        self._cache_record(offset, record, header)
        return record

    def _decode_record(self, offset: int, kind: int, payload: bytes) -> _DecodedRecord:
        size = len(payload)
        if kind in (_LIST, _TUPLE, _DICT, _CLONE, _DOMAIN_REF):
            if size % _OFFSET.size or kind == _DICT and size % (2 * _OFFSET.size):
                raise ValueError('invalid evidence record children')
            if kind == _CLONE and size != _OFFSET.size:
                raise ValueError('invalid evidence clone record')
            if kind == _DOMAIN_REF and size != 2 * _OFFSET.size:
                raise ValueError('invalid evidence domain reference')
            children = tuple(child[0] for child in _OFFSET.iter_unpack(payload))
            if any(child >= offset or child % _INDEX.size for child in children):
                raise ValueError('evidence child must reference an earlier record')
            record = _DecodedRecord(kind, children=children)
        elif kind == _VECTOR and size == _VECTOR_HEADER.size:
            length, capacity, root = _VECTOR_HEADER.unpack(payload)
            if (not 1 <= capacity <= 2 ** 58 or capacity & (capacity - 1)
                    or length > capacity * 32 or root >= offset or root % _INDEX.size):
                raise ValueError('invalid evidence vector root')
            record = _DecodedRecord(kind, length, (capacity, root))
        elif kind in (_NONE, _DOMAIN) and not payload:
            record = _DecodedRecord(kind)
        elif kind == _BOOL and payload in (b'\x00', b'\x01'):
            record = _DecodedRecord(kind, payload == b'\x01')
        elif kind == _INT and payload:
            record = _DecodedRecord(kind, int.from_bytes(payload, 'big', signed=True))
        elif kind == _FLOAT_KIND and size == _FLOAT.size:
            value = _FLOAT.unpack(payload)[0]
            if not isfinite(value):
                raise ValueError('evidence scalar must be finite')
            record = _DecodedRecord(kind, value)
        elif kind == _STR:
            record = _DecodedRecord(kind, payload.decode('utf-8', errors='surrogatepass'))
        elif kind == _BYTES:
            record = _DecodedRecord(kind, payload)
        else:
            raise ValueError('invalid evidence record type or scalar payload')
        return record

    def materialize(self, reference: RecordRef) -> object:
        """独立恢复公开 builtin 图，不向调用方暴露缓存内部对象。"""
        offset = self._validate_ref(reference)
        if self._writing:
            raise ValueError('cannot read an evidence record during a write')
        memo: dict[tuple[int, int], object] = {}
        immutable: dict[int, object] = {}
        pending: list[tuple[int, int, _DecodedRecord | None, tuple[tuple[int, int], ...]]] = [(0, offset, None, ())]
        next_domain = 0
        read_domains: dict[tuple[int, int], int] = {}

        def in_domain(parent: int, marker: int) -> int:
            nonlocal next_domain
            identity = parent, marker
            value = read_domains.get(identity)
            if value is None:
                next_domain += 1
                value = read_domains[identity] = next_domain
            return value

        while pending:
            domain, current, record, children = pending.pop()
            key = (domain, current)
            if key in memo:
                continue
            if current in immutable:
                memo[key] = immutable[current]
                continue
            if record is None:
                record = self._record(current)
                if record.kind == _DOMAIN:
                    raise ValueError('evidence domain markers are not materialized values')
                if record.kind <= _BYTES:
                    memo[key] = record.scalar
                    immutable[current] = record.scalar
                    continue
                if record.kind == _VECTOR:
                    # 域只进入本次memo；读取树叶不能再向磁盘写一遍旧点代理。
                    keys: list[tuple[int, int]] = []
                    for child, markers in self._vector_items(record):
                        child_domain = domain
                        for marker in markers:
                            child_domain = in_domain(child_domain, marker)
                        keys.append((child_domain, child))
                    children = tuple(keys)
                    record = _DecodedRecord(_LIST)
                elif record.kind in (_CLONE, _DOMAIN_REF):
                    if record.kind == _DOMAIN_REF:
                        marker, target = record.children
                        if self._record_header(marker)[2] not in (_DOMAIN, _CLONE):
                            raise ValueError('evidence domain reference requires its own marker')
                    else:
                        target = record.children[0]
                        marker = current
                    children = ((in_domain(domain, marker), target),)
                else:
                    children = tuple((domain, child) for child in record.children)
                pending.append((domain, current, record, children))
                pending.extend((child_domain, child, None, ()) for child_domain, child in reversed(children))
                continue
            if record.kind in (_CLONE, _DOMAIN_REF):
                memo[key] = memo[children[0]]
                if children[0][1] in immutable:
                    immutable[current] = memo[key]
            elif record.kind == _LIST:
                memo[key] = [memo[child] for child in children]
            elif record.kind == _TUPLE:
                memo[key] = tuple(memo[child] for child in children)
                if all(child[1] in immutable for child in children):
                    immutable[current] = memo[key]
            else:
                mapping: dict[Scalar, object] = {}
                for index in range(0, len(children), 2):
                    item_key = memo[children[index]]
                    self._scalar_kind(item_key)
                    mapping[cast(Scalar, item_key)] = memo[children[index + 1]]
                memo[key] = mapping
        return memo[(0, offset)]

    def clear_cache(self) -> None:
        """只释放可重建读缓存；尚未刷出的已提交页必须继续保留。"""
        self._cache = OrderedDict()
        self._cache_used = 0

    def close(self) -> None:
        """关闭请求存储并明确使其全部引用失效；可重复调用。"""
        if self._closed:
            return
        try:
            self.flush()
        finally:
            self._close_resources()

    def _close_resources(self) -> None:
        """失败回滚不可再flush；两句柄都尝试关闭，关闭异常不被吞掉。"""
        if self._closed:
            return
        self._closed = True
        self.clear_cache()
        for page in (self._data_page, self._index_page):
            if page is not None:
                page.close()
        self._page_reserved = 0
        try:
            self._data.close()
        finally:
            self._index.close()

    def __enter__(self) -> 'OwnedEvidenceRecords':
        self._require_open()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()


class _AppendPage:
    """单流固定追加页；一次payload完整落在一页或文件，绝不跨读边界。

    成功写完整页才推进base并复用RAM。失败时原页仍在，事务回滚可保留
    此前已提交的RAM前缀；已经换页的旧提交则一定在物理文件中。
    """

    __slots__ = ('buffer', 'base', 'used')

    def __init__(self, capacity: int) -> None:
        self.buffer = bytearray(capacity)
        self.base = self.used = 0

    @property
    def memory_used(self) -> int:
        return getsizeof(self) + getsizeof(self.buffer) + 512

    def flush(self, stream: BinaryIO) -> None:
        if not self.used:
            return
        stream.seek(self.base)
        OwnedEvidenceRecords._write_all(stream, memoryview(self.buffer)[:self.used])
        self.base += self.used
        self.used = 0

    def append(self, stream: BinaryIO, payload: bytes) -> None:
        size = len(payload)
        if not size:
            return
        if self.used + size > len(self.buffer):
            self.flush(stream)
        if size > len(self.buffer):
            stream.seek(self.base)
            OwnedEvidenceRecords._write_all(stream, payload)
            self.base += size
        else:
            self.buffer[self.used:self.used + size] = payload
            self.used += size

    def rollback(self, stream: BinaryIO, start: int) -> None:
        if start >= self.base:
            if start - self.base > self.used:
                raise ValueError('record page rollback exceeds its retained prefix')
            # 部分write可能已经写出旧RAM和新图；先去掉未完成物理尾，再
            # 留下旧提交RAM前缀。失败页在完整write之前从不clear/reuse。
            stream.seek(self.base)
            stream.truncate(self.base)
            self.used = start - self.base
        else:
            # 旧页已完整落盘，故原提交边界之前的字节不再依赖当前RAM。
            stream.seek(start)
            stream.truncate(start)
            self.base, self.used = start, 0

    def read(self, stream: BinaryIO, offset: int, size: int) -> bytes:
        if not size:
            return b''
        if offset >= self.base:
            position = offset - self.base
            if position + size > self.used:
                raise ValueError('incomplete evidence record page')
            return bytes(memoryview(self.buffer)[position:position + size])
        if offset + size > self.base:
            raise ValueError('evidence record cannot cross its physical page boundary')
        stream.seek(offset)
        return stream.read(size)

    def close(self) -> None:
        # 正在抛出的write异常可能仍持有memoryview，不能resize原buffer。
        self.buffer = bytearray()
        self.used = 0


class _NewGraphWriter:
    """新引用仅在本次私有写者内可连接；页模式直接使用请求的固定页。

    零页模式保留单图双缓冲。header来自固定索引页或原缓冲，不另建随
    节点数增长的表；公共存储入口始终只接受已提交引用。
    """

    __slots__ = ('store', '_data', '_index', '_capacity', '_data_used', '_index_used',
                 '_data_base', '_index_base', '_start_index', '_memory_used', '_closed')

    def __init__(self, store: OwnedEvidenceRecords, *, memory_bytes: int) -> None:
        if type(memory_bytes) is not int or memory_bytes < 0:
            raise ValueError('record batch memory_bytes must be nonnegative')
        paged = store._data_page is not None
        capacity = 0 if paged else min(32 * 1024, max(0, (memory_bytes - 2048) // 2))
        if not paged and capacity < _INDEX.size:
            raise ValueError('record batch exceeds its scoped memory limit')
        self.store = store
        self._data, self._index = bytearray(capacity), bytearray(capacity)
        self._capacity = capacity
        self._data_used = self._index_used = 0
        self._data_base, self._index_base = store._data_end, store._index_end
        self._start_index = store._committed_index_end
        self._closed = False
        # 固定余量包括整数游标、临时header、memoryview和写者的借用引用。
        self._memory_used = getsizeof(self) + getsizeof(self._data) + getsizeof(self._index) + 1536
        if self._memory_used > memory_bytes:
            self.close()
            raise ValueError('record batch exceeds its scoped memory limit')

    @property
    def memory_used(self) -> int:
        return self._memory_used

    def _require_active(self) -> None:
        self.store._require_open()
        if self._closed or not self.store._writing:
            raise ValueError('record batch requires its active write transaction')

    def _reference(self, reference: RecordRef) -> int:
        self._require_active()
        if (type(reference) is not RecordRef or type(reference.namespace) is not bytes
                or reference.namespace != self.store.namespace or type(reference.offset) is not int
                or reference.offset < 0 or reference.offset % _INDEX.size
                or reference.offset >= self.store._index_end):
            raise ValueError('record batch reference must belong to an earlier record in its request')
        # 单写者事务内提交边界固定；上面已完整验证命名空间和当前边界。
        # 旧偏移自然位于已提交区间，无须再次调用相同的公开引用校验。
        return reference.offset

    def _header(self, offset: int) -> _RecordHeader:
        if offset < self._start_index:
            return self.store._record_header(offset)
        if self.store._index_page is not None:
            header = _INDEX.unpack(self.store._read_index(offset))
            if header[1] > self.store._max_record_bytes or header[0] + header[1] > self.store._data_end:
                raise ValueError('invalid record batch payload bounds')
            return header
        position = offset - self._index_base
        if 0 <= position <= self._index_used - _INDEX.size:
            return _INDEX.unpack_from(self._index, position)
        cached = self.store._cache.get(offset)
        if cached is not None:
            self.store._cache.move_to_end(offset)
            return cached[0]
        if offset >= self._index_base:
            raise ValueError('record batch header has not been staged')
        self.store._index.seek(offset)
        payload = self.store._index.read(_INDEX.size)
        if len(payload) != _INDEX.size:
            raise ValueError('incomplete evidence record index')
        header = _INDEX.unpack(payload)
        if header[1] > self.store._max_record_bytes or header[0] + header[1] > self._data_base:
            raise ValueError('invalid record batch payload bounds')
        self.store._cache_record(offset, None, header)
        return header

    def _flush(self) -> None:
        self._require_active()
        if not self._index_used:
            return
        self.store._data.seek(self._data_base)
        self.store._write_all(self.store._data, memoryview(self._data)[:self._data_used])
        self.store._index.seek(self._index_base)
        self.store._write_all(self.store._index, memoryview(self._index)[:self._index_used])
        self._data_used = self._index_used = 0
        self._data_base, self._index_base = self.store._data_end, self.store._index_end

    def _append(self, kind: int, payload: bytes) -> RecordRef:
        self._require_active()
        if self.store._data_page is not None:
            return self.store._append(kind, payload)
        if len(payload) > self.store._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        if len(payload) > self._capacity:
            self._flush()
            reference = self.store._append(kind, payload)
            self._data_base, self._index_base = self.store._data_end, self.store._index_end
            return reference
        if self._data_used + len(payload) > self._capacity or self._index_used + _INDEX.size > self._capacity:
            self._flush()
        reference = RecordRef(self.store.namespace, self.store._index_end)
        header = self.store._data_end, len(payload), kind
        self._data[self._data_used:self._data_used + len(payload)] = payload
        _INDEX.pack_into(self._index, self._index_used, *header)
        self._data_used += len(payload)
        self._index_used += _INDEX.size
        self.store._data_end += len(payload)
        self.store._index_end += _INDEX.size
        self.store._cache_written_record(reference.offset, header, payload)
        return reference

    def write_scalar(self, value: Scalar) -> RecordRef:
        self._require_active()
        kind, payload = self.store._scalar_payload(value)
        return self._append(kind, payload)

    def _write_sequence(self, children: list[RecordRef] | tuple[RecordRef, ...], kind: int) -> RecordRef:
        self._require_active()
        if type(children) not in (list, tuple):
            raise TypeError('evidence children require an exact list or tuple')
        if len(children) * _OFFSET.size > self.store._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        return self._append(kind, self.store._offset_payload([self._reference(child) for child in children]))

    def write_list(self, children: list[RecordRef] | tuple[RecordRef, ...]) -> RecordRef:
        return self._write_sequence(children, _LIST)

    def write_tuple(self, children: list[RecordRef] | tuple[RecordRef, ...]) -> RecordRef:
        return self._write_sequence(children, _TUPLE)

    def write_dict(self, items: list[tuple[RecordRef, RecordRef]] | tuple[tuple[RecordRef, RecordRef], ...]) -> RecordRef:
        self._require_active()
        if type(items) not in (list, tuple):
            raise TypeError('evidence items require an exact list or tuple')
        if len(items) * 2 * _OFFSET.size > self.store._max_record_bytes:
            raise ValueError('evidence record exceeds its size limit')
        offsets: list[int] = []
        for item in items:
            if type(item) is not tuple or len(item) != 2:
                raise TypeError('evidence dictionary item requires an exact key/value tuple')
            key, value = item
            key_offset = self._reference(key)
            if self._header(key_offset)[2] > _BYTES:
                raise TypeError('evidence dictionary keys require scalar records')
            offsets.extend((key_offset, self._reference(value)))
        return self._append(_DICT, self.store._offset_payload(offsets))

    def close(self) -> None:
        self._closed = True
        # 异常traceback可能保留正在写出的memoryview；替换而不resize其缓冲。
        self._data, self._index = bytearray(), bytearray()
        self._memory_used = 0
