"""股票级分阶段计算的有界临时行；只读取本请求自己写出的可信对象。"""

from collections.abc import Callable, Iterator
from pickle import HIGHEST_PROTOCOL, dump, load
from tempfile import SpooledTemporaryFile
from types import TracebackType
from typing import Generic, Self, TypeVar


_Row = TypeVar('_Row')
_STAGE_MEMORY_BYTES = 2 * 1024 * 1024


class TrendStageRows(Generic[_Row]):
    """顺序写完一个阶段，再逐行读取，避免保留全历史完整证据树。

    每个阶段最多在内存保存 2 MiB 序列化内容，超过后由标准库自动移到
    本请求的临时文件；读取时只持有当前行。阶段数固定，因此空间由固定
    缓冲上限及单行直接来源规模限定，不再是全部日期三层证据之和。

    pickle 仅作为内部对象往返协议：本类创建自己的临时流，只能读取
    write 写入的对象，不接受外部文件、路径、缓存或上传内容。decode
    显式校验恢复对象的领域形状；存储位置永不进入图表结果或 checkpoint。
    调用方必须使用 with／ExitStack，保证成功、判点失败或消费失败都关闭
    临时资源。读阶段封存写入；只允许一个顺序读取器，避免隐式共享游标。
    """

    def __init__(self, decode: Callable[[object], _Row], *, memory_bytes: int = _STAGE_MEMORY_BYTES) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('stage memory_bytes must be a positive integer')
        self._decode = decode
        self._stream = SpooledTemporaryFile(max_size=memory_bytes, mode='w+b')
        self._row_count = 0
        self._sealed = False
        self._reading = False

    @property
    def row_count(self) -> int:
        return self._row_count

    @property
    def closed(self) -> bool:
        return self._stream.closed

    def write(self, row: _Row) -> None:
        if self._sealed:
            raise ValueError('a completed trend stage cannot accept additional rows')
        dump(row, self._stream, protocol=HIGHEST_PROTOCOL)
        self._row_count += 1

    def iter_rows(self) -> Iterator[_Row]:
        if self._reading:
            raise ValueError('a trend stage permits only one active sequential reader')
        self._sealed = True
        self._reading = True
        try:
            self._stream.seek(0)
            for _ in range(self._row_count):
                value: object = load(self._stream)
                yield self._decode(value)
        finally:
            self._reading = False

    def close(self) -> None:
        self._stream.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
