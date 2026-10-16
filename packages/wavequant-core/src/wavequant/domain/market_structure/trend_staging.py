"""股票级分阶段计算的有界临时行；只读取本请求自己写出的可信对象。"""

from collections import OrderedDict
from collections.abc import Callable, Iterator
from pickle import HIGHEST_PROTOCOL, dumps, loads
from struct import Struct
from sys import getsizeof
from tempfile import SpooledTemporaryFile
from types import TracebackType
from typing import Generic, Self, TypeVar
from zlib import compress, decompress

from .trend_readonly_cache import _ReadonlySnapshotCache


_Row = TypeVar('_Row')
_STAGE_MEMORY_BYTES = 2 * 1024 * 1024
_HEADER = Struct('>BQ')
_COMPRESS_AFTER = 64 * 1024
_PAYLOAD_CACHE_BYTES = 2 * 1024 * 1024


class TrendStageRows(Generic[_Row]):
    """保存有界证据流；日内消费者可读取已写前缀，再继续追加。

    每个阶段最多在内存保存 2 MiB 序列化内容，超过后由标准库自动移到
    本请求的临时文件。另以最多 2 MiB 保存最近两行的压缩及原始字节，
    避免同一前驱版本跨日反复解压。显式私有只读借用另有 4 MiB 解码缓存，
    默认读取仍恢复独立可变对象。阶段数固定，
    空间由固定缓冲上限及单行直接来源规模限定。

    pickle 仅作为内部对象往返协议：本类创建自己的临时流，只能读取
    write 写入的对象，不接受外部文件、路径、缓存或上传内容。decode
    显式校验恢复对象的领域形状；存储位置永不进入图表结果或 checkpoint。
    调用方必须使用 with／ExitStack，保证成功、判点失败或消费失败都关闭
    临时资源。默认读取封存写入；实时前缀读取恢复写游标。只允许一个读取器。
    """

    def __init__(self, decode: Callable[[object], _Row], *, memory_bytes: int = _STAGE_MEMORY_BYTES,
                 payload_cache_bytes: int = _PAYLOAD_CACHE_BYTES) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('stage memory_bytes must be a positive integer')
        if type(payload_cache_bytes) is not int or payload_cache_bytes < 0:
            raise ValueError('stage payload_cache_bytes must be a nonnegative integer')
        self._decode = decode
        self._stream = SpooledTemporaryFile(max_size=memory_bytes, mode='w+b')
        self._row_count = 0
        self._offsets: list[int] = []
        self._sealed = False
        self._reading = False
        self.raw_bytes = 0
        self.stored_bytes = 0
        self._payload_cache_limit = payload_cache_bytes
        self._payload_cache: OrderedDict[int, tuple[bytes, bytes]] = OrderedDict()
        self._payload_cache_size = 0
        self._readonly_cache = _ReadonlySnapshotCache[_Row](memory_bytes=4 * 1024 * 1024, max_entries=2)

    def _remember_payload(self, row: int, stored: bytes, payload: bytes) -> None:
        # 字节不可变，公开读取仍执行 loads/decode，保留每次还原的类型、
        # 别名和自定义解码行为。超预算单行不驱逐已有的小行。
        charged = getsizeof(stored) + getsizeof(payload) + 512
        if charged + 1024 > self._payload_cache_limit:
            return
        previous = self._payload_cache.pop(row, None)
        if previous is not None:
            self._payload_cache_size -= getsizeof(previous[0]) + getsizeof(previous[1]) + 512
        while self._payload_cache and (len(self._payload_cache) >= 2
                or self._payload_cache_size + charged + 1024 > self._payload_cache_limit):
            _, removed = self._payload_cache.popitem(last=False)
            self._payload_cache_size -= getsizeof(removed[0]) + getsizeof(removed[1]) + 512
        self._payload_cache[row] = stored, payload
        self._payload_cache_size += charged

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def write(self, row: _Row) -> None:
        if self._sealed:
            raise ValueError('a completed trend stage cannot accept additional rows')
        if self._reading:
            raise ValueError('a trend stage cannot append while a reader is active')
        payload = dumps(row, protocol=HIGHEST_PROTOCOL)
        self.raw_bytes += len(payload)
        compressed = compress(payload, level=1) if len(payload) >= _COMPRESS_AFTER else payload
        packed = len(compressed) < len(payload) * .85
        stored = compressed if packed else payload
        # 逐行压缩保留完整 pickle 类型与别名；一次写入避免大证据的多次文件回调。
        self._offsets.append(self._stream.tell())
        self._stream.write(_HEADER.pack(int(packed), len(stored)))
        self._stream.write(stored)
        self.stored_bytes += _HEADER.size + len(stored)
        if packed:
            self._remember_payload(self._row_count, stored, payload)
        self._row_count += 1

    def iter_rows(self, *, seal: bool = True, start_row: int = 0,
                  _borrow_readonly: bool = False) -> Iterator[_Row]:
        if type(start_row) is not int or not 0 <= start_row <= self._row_count:
            raise ValueError('invalid trend stage read offset')
        if self._reading:
            raise ValueError('a trend stage permits only one active sequential reader')
        self._sealed = self._sealed or seal
        self._reading = True
        position = self._stream.tell()
        try:
            if start_row < self._row_count:
                self._stream.seek(self._offsets[start_row])
            for row in range(start_row, self._row_count):
                header = self._stream.read(_HEADER.size)
                if len(header) != _HEADER.size:
                    raise ValueError('owned trend stage has an incomplete row header')
                packed, length = _HEADER.unpack(header)
                payload = self._stream.read(length)
                if len(payload) != length or packed not in (0, 1):
                    raise ValueError('owned trend stage has an incomplete row payload')
                # 仍读取并校验真实行，缓存不能掩盖截断或内容变化。
                token = header + payload if _borrow_readonly else b''
                borrowed = self._readonly_cache.get(row, token) if _borrow_readonly else None
                if borrowed is not None:
                    yield borrowed[0]
                    continue
                if packed:
                    cached = self._payload_cache.get(row)
                    if cached is not None and cached[0] == payload:
                        self._payload_cache.move_to_end(row)
                        payload = cached[1]
                    else:
                        unpacked = decompress(payload)
                        self._remember_payload(row, payload, unpacked)
                        payload = unpacked
                value: object = loads(payload)
                decoded = self._decode(value)
                if _borrow_readonly:
                    self._readonly_cache.put(row, token, decoded)
                yield decoded
        finally:
            if not seal:
                self._stream.seek(position)
            self._reading = False

    def close(self) -> None:
        self._offsets.clear()
        self._payload_cache.clear()
        self._payload_cache_size = 0
        self._readonly_cache.clear()
        self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
