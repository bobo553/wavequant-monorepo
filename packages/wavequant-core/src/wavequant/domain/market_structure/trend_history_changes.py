"""请求内的趋势变化历史：计算按变化推进，行情消费者按日期读取。

变化位置是首次观察到该版本的交易日，不是端点的价格日期。后来的端点
替换、确认和路径关闭都写成新版本，不能从最终折线倒裁历史。
"""

from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import pickle
from types import TracebackType
from typing import Generic, Self, TypeVar, cast

from .trend_evidence_reuse import _geometry_signature
from .trend_staging import TrendStageRows


_Row = TypeVar('_Row')


@dataclass(frozen=True, slots=True)
class _EncodedTrendRow:
    # 仅由同请求的准备阶段生成，不接收外部文件或上传的 pickle。
    payload: bytes


class TrendChangeRows(Generic[_Row]):
    """只存变化版本；日期读取器借用只读版本，不重复恢复相同证明树。

默认每个日期独立拥有结果。内部消费者可显式 shared=True，借用单次
遍历内的只读版本，避免为不变的日期复制同一证明树。
"""

    def __init__(self, decode: Callable[[object], _Row], *, row_count: int,
                 load_encoded: Callable[[bytes], object] = pickle.loads) -> None:
        if type(row_count) is not int or row_count < 0:
            raise ValueError('trend history length must be a nonnegative integer')
        self.row_count = row_count
        self._decode = decode
        self._load_encoded = load_encoded
        self._rows = TrendStageRows(self._entry)
        self._last_position = -1
        self._positions: list[int] = []
        self.read_limit: int | None = None

    def _entry(self, value: object) -> tuple[int, _Row]:
        position, row = cast(tuple[int, object], value)
        if type(row) is _EncodedTrendRow:
            row = self._load_encoded(row.payload)
        return position, self._decode(row)

    @property
    def change_count(self) -> int:
        return self._rows.row_count

    @property
    def closed(self) -> bool:
        return self._rows.closed

    def write_change(self, position: int, row: _Row) -> None:
        if (type(position) is not int or not 0 <= position < self.row_count
                or position <= self._last_position or self._last_position < 0 and position != 0):
            raise ValueError('trend changes require an initial row and strictly increasing dates')
        self._rows.write((position, row))
        self._positions.append(position)
        self._last_position = position

    def _write_encoded_change(self, position: int, payload: bytes) -> None:
        """复用变化识别已生成的可信负载，避免再次遍历完整证明树。"""
        self.write_change(position, cast(_Row, _EncodedTrendRow(payload)))

    def iter_changes(self, *, start_position: int = 0,
                     _borrow_readonly: bool = False) -> Iterator[tuple[int, _Row]]:
        if type(start_position) is not int or not 0 <= start_position <= self.row_count:
            raise ValueError('invalid trend history read position')
        # Each change owns a complete dated state. Seek to the predecessor instead of decoding old proofs.
        start_row = max(0, bisect_right(self._positions, start_position) - 1)
        reader = self._rows.iter_rows(seal=self.read_limit is None, start_row=start_row,
                                      _borrow_readonly=_borrow_readonly)
        try:
            for position, row in reader:
                if self.read_limit is not None and position >= self.read_limit:
                    break
                yield position, row
        finally:
            reader.close()

    def _at(self, position: int) -> _Row:
        """按已知日期借用真实前驱版本，不解码中间变化或下一条未来版本。"""
        stop = self.row_count if self.read_limit is None else self.read_limit
        if type(position) is not int or not 0 <= position < stop:
            raise ValueError('trend history date must be within the readable prefix')
        row = bisect_right(self._positions, position) - 1
        if row < 0:
            raise ValueError('a nonempty trend history requires its initial state')
        reader = cast(Generator[tuple[int, _Row], None, None],
            self._rows.iter_rows(seal=self.read_limit is None, start_row=row, _borrow_readonly=True))
        try:
            return next(reader)[1]
        finally:
            reader.close()

    def _readonly_cursor(self) -> '_TrendDateCursor[_Row]':
        """一次内部消费借用当前目标版本，不向跨读取缓存重复准入整棵证据。"""
        return _TrendDateCursor(self)

    def iter_rows(self, *, shared: bool = False, start_position: int = 0,
                  _reuse_shared: bool = False) -> Iterator[_Row]:
        if _reuse_shared and not shared:
            raise ValueError('cross-reader reuse requires readonly shared rows')
        position = start_position
        current: _Row | None = None
        with_changes = self.iter_changes(start_position=start_position, _borrow_readonly=_reuse_shared)
        try:
            for changed_at, row in with_changes:
                if current is not None:
                    for _ in range(position, changed_at):
                        yield current if shared else deepcopy(current)
                current, position = row, max(start_position, changed_at)
            if current is not None:
                for _ in range(position, self.row_count if self.read_limit is None else self.read_limit):
                    yield current if shared else deepcopy(current)
            elif self.row_count:
                raise ValueError('a nonempty trend history requires its initial state')
        finally:
            with_changes.close()

    def close(self) -> None:
        self._positions.clear()
        self._rows.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()


class _TrendDateCursor(Generic[_Row]):
    """只保留一次消费的最新目标行，稀疏日期不解码间隔或下一版本。"""

    def __init__(self, history: TrendChangeRows[_Row]) -> None:
        self._history = history
        self._row: int | None = None
        self._current: _Row | None = None
        self._closed = False

    def at(self, position: int) -> _Row:
        history = self._history
        if self._closed or history.closed:
            raise RuntimeError('trend history date cursor is closed')
        stop = history.row_count if history.read_limit is None else history.read_limit
        if type(position) is not int or not 0 <= position < stop:
            raise ValueError('trend history date must be within the readable prefix')
        row = bisect_right(history._positions, position) - 1
        if row < 0:
            raise ValueError('a nonempty trend history requires its initial state')
        if row == self._row:
            return cast(_Row, self._current)
        # 实际行不可被原地替换；先关闭底层读取器，才将只读结果交给消费者。
        # 此处不使用只读池，避免每个大版本为一次借用重复扫描完整对象图。
        reader = cast(Generator[tuple[int, _Row], None, None], history._rows.iter_rows(
            seal=history.read_limit is None, start_row=row, _borrow_readonly=False))
        self._row, self._current = None, None
        try:
            current = next(reader)[1]
        finally:
            reader.close()
        self._row, self._current = row, current
        return current

    def close(self) -> None:
        self._row, self._current = None, None
        self._closed = True

    def __enter__(self) -> '_TrendDateCursor[_Row]':
        if self._closed or self._history.closed:
            raise RuntimeError('trend history date cursor is closed')
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()


class TrendSourceChanges:
    """来源只保存最长未变前缀之后的替换；旧确认信息改变同样产生事件。"""

    def __init__(self, *, row_count: int) -> None:
        self._rows = TrendChangeRows(self._entry, row_count=row_count)
        self._keys: tuple[bytes | None, ...] = ()
        self._started = False

    @staticmethod
    def _entry(value: object) -> tuple[int, list[dict[str, object]]]:
        return cast(tuple[int, list[dict[str, object]]], value)

    @property
    def change_count(self) -> int:
        return self._rows.change_count

    def append(self, position: int, points: Sequence[Mapping[str, object]]) -> bool:
        keys = tuple(_geometry_signature(point) for point in points)
        reusable = all(key is not None for key in keys)
        if self._started and reusable and keys == self._keys:
            return False
        keep = 0
        for old, new in zip(self._keys, keys):
            if old is None or new is None or old != new:
                break
            keep += 1
        self._rows.write_change(position, (keep, [dict(point) for point in points[keep:]]))
        self._keys, self._started = keys, True
        return True

    def iter_changes(self) -> Iterator[tuple[int, list[dict[str, object]]]]:
        points: list[dict[str, object]] = []
        for position, (keep, suffix) in self._rows.iter_changes():
            # 只替换容器，不改旧点；晚到修订不会污染已经交给计算器的版本。
            points = [*points[:keep], *suffix]
            yield position, points

    def close(self) -> None:
        self._keys = ()
        self._rows.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()


class PolylineSourceGate:
    """lecture_drawing 的写入约定：旧前缀不改，只替换尾点、追加或换路径。

普通发展尾点同向移动不改变已确认峰谷。只有尾点仍是 developing、
相邻已确认点对象未变、同日投影计数不变且价格相对方向不变，才能跳过
公共峰谷投影。包含关系、确认、修订或路径切换一律重新走公共适配器。
"""

    def __init__(self) -> None:
        self._epoch: int | None = None
        self._length = -1
        self._anchor: Mapping[str, object] | None = None
        self._tail: Mapping[str, object] | None = None

    def changed(self, epoch: int, raw: Sequence[Mapping[str, object]]) -> bool:
        anchor = raw[-2] if len(raw) > 1 else None
        tail = raw[-1] if raw else None
        unchanged = self._epoch == epoch and self._length == len(raw)
        if unchanged and len(raw) > 1:
            previous = self._tail
            unchanged = (anchor is not None and anchor is self._anchor and tail is not None and previous is not None
                and tail.get('state') == previous.get('state') == 'developing'
                and (tail['time'] == anchor['time']) == (previous['time'] == anchor['time'])
                and (cast(float, tail['value']) > cast(float, anchor['value']))
                    == (cast(float, previous['value']) > cast(float, anchor['value']))
                and (cast(float, tail['value']) < cast(float, anchor['value']))
                    == (cast(float, previous['value']) < cast(float, anchor['value'])))
        self._epoch, self._length, self._anchor, self._tail = epoch, len(raw), anchor, tail
        return not unchanged
