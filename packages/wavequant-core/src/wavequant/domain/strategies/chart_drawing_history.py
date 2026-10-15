"""保存讲义折线的尾部变更；只在消费历史时恢复对应日期的只读折线。"""

from collections.abc import Iterator, Mapping, Sequence
from collections import deque
from copy import deepcopy
from dataclasses import dataclass
from types import TracebackType
from typing import Self, cast

from ..market_structure.trend_staging import TrendStageRows


@dataclass(slots=True)
class ChartDrawingFrame:
    index: int
    epoch: int
    raw: list[dict[str, object]]
    drawing: dict[str, object]


def _change(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


class ChartDrawingHistory:
    """仅接收 lecture_drawing 的尾点替换/追加协议，不接收任意可变快照。

首行保留完整恢复起点，后续只写旧尾点起的替换片段。一次路径关闭只
保存一次闭合副本。每日消费保留发生日与确认日，不能提前使用后续版本。
"""

    def __init__(self, *, start: int, count: int, closed: Sequence[Mapping[str, object]]) -> None:
        if type(start) is not int or start < 0 or type(count) is not int or count < 0:
            raise ValueError('drawing history requires a nonnegative start and length')
        self.row_count = count
        self.start = start
        self._closed = deepcopy([dict(path) for path in closed])
        self._rows = TrendStageRows(_change)
        self._epoch: int | None = None
        self._length = 0
        self._tail: Mapping[str, object] | None = None
        self._next_index = start
        self.read_limit: int | None = None
        self._recent: deque[ChartDrawingFrame] = deque(maxlen=2)

    @property
    def closed(self) -> bool:
        return self._rows.closed

    @property
    def change_count(self) -> int:
        return self._rows.row_count

    def append(self, index: int, epoch: int, raw: Sequence[Mapping[str, object]],
               closed_path: Mapping[str, object] | None) -> dict[str, object] | None:
        if index != self._next_index or index >= self.start + self.row_count:
            raise ValueError('drawing history must observe each date in order')
        self._next_index += 1
        tail = raw[-1] if raw else None
        if (self.change_count and epoch == self._epoch and len(raw) == self._length
                and tail is self._tail and closed_path is None):
            return None
        keep = max(0, self._length - 1) if self.change_count and epoch == self._epoch else 0
        row: dict[str, object] = dict(history_format='drawing_tail_changes_v1', bar_index=index,
            history_end_index=self.start + self.row_count - 1, epoch=epoch, keep=keep,
            points=[dict(point) for point in raw[keep:]], closed_path=closed_path)
        if not self.change_count:
            row['initial_closed'] = self._closed
        self._rows.write(row)
        self._epoch, self._length, self._tail = epoch, len(raw), tail
        return row

    def remember_frame(self, frame: ChartDrawingFrame) -> None:
        # Daily checkpoint consumers need at most yesterday and today; older history stays on disk.
        self._recent.append(deepcopy(frame))

    def iter_rows(self, *, shared: bool = False, start_index: int | None = None) -> Iterator[ChartDrawingFrame]:
        begin = self.start if start_index is None else start_index
        if type(begin) is not int or not self.start <= begin <= self.start + self.row_count:
            raise ValueError('invalid drawing history read position')
        if self.read_limit is not None and self._recent and begin >= self._recent[0].index:
            for frame in self._recent:
                if begin <= frame.index < self.read_limit:
                    yield frame if shared else deepcopy(frame)
            return
        raw: list[dict[str, object]] = []
        closed: list[dict[str, object]] = []
        previous: ChartDrawingFrame | None = None
        position = self.start
        reader = self._rows.iter_rows(seal=self.read_limit is None)
        try:
            for row in reader:
                index = cast(int, row['bar_index'])
                if self.read_limit is not None and index >= self.read_limit:
                    break
                if previous is not None:
                    for day in range(position, index):
                        if day < begin:
                            continue
                        frame = ChartDrawingFrame(day, previous.epoch, previous.raw, previous.drawing)
                        yield frame if shared else deepcopy(frame)
                else:
                    closed = cast(list[dict[str, object]], row['initial_closed'])
                keep = cast(int, row['keep'])
                raw = [*raw[:keep], *cast(list[dict[str, object]], row['points'])]
                closed_path = row['closed_path']
                if closed_path is not None:
                    closed = [*closed, cast(dict[str, object], closed_path)]
                epoch = cast(int, row['epoch'])
                drawing: dict[str, object] = dict(strokes=[*closed,
                    *([dict(id=f'lecture-{epoch}', points=raw)] if len(raw) > 1 else [])])
                previous = ChartDrawingFrame(index, epoch, raw, drawing)
                position = index
            if previous is not None:
                for day in range(max(position, begin), self.start + self.row_count if self.read_limit is None else self.read_limit):
                    frame = ChartDrawingFrame(day, previous.epoch, previous.raw, previous.drawing)
                    yield frame if shared else deepcopy(frame)
            elif self.row_count:
                raise ValueError('a drawing history requires its first dated state')
        finally:
            reader.close()

    def close(self) -> None:
        self._recent.clear()
        self._tail = None
        self._closed = []
        self._rows.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()
