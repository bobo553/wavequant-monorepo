"""有界保存组合 A 实际消费的标量几何，供资格窗口修订后的附件重算。"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
import pickle
from tempfile import TemporaryFile
from typing import cast
from zlib import compress, decompress

from ..market_structure.trend_market_index import _MarketRangeIndex
from ..models.model import Bar
from .backtest_history_reuse import _certificate_bytes
from .combined_a_entry import CombinedAContext, GeometryLevel, combined_a_contexts_from_geometry


_POINT_FIELDS = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'state', 'label', 'flip')


def attack_certificates(audit: Sequence[Mapping[str, object]], cutoff: int) -> dict[int, bytes] | None:
    """认证每个攻击日最后一条完整记录；只留定长摘要，最多 4096 项。"""
    positive = {cast(int, row['bar_index']): row for row in audit
                if row.get('event') == 'n_completed' and row.get('direction') == 'up'
                and cast(int, row['bar_index']) <= cutoff}
    if len(positive) > 4096:
        return None
    return {index: sha256(_certificate_bytes(row)).digest() for index, row in positive.items()}


def later_attack_revision(audit: Sequence[Mapping[str, object]], previous: object, cutoff: int) -> bool:
    """仅接纳新窗口延后知识日期的修订；早期证据修订仍走完整冷重放。"""
    if type(previous) is not dict:
        return False
    positive = {cast(int, row['bar_index']): row for row in audit
                if row.get('event') == 'n_completed' and row.get('direction') == 'up'
                and cast(int, row['bar_index']) < cutoff}
    for index, row in positive.items():
        if previous.get(index) != sha256(_certificate_bytes(row)).digest():
            known = row.get('known_at', index)
            if type(known) is not int or known < cutoff:
                return False
    return True


def _geometry(level: Mapping[str, object]) -> GeometryLevel | None:
    strokes = []
    for stroke in cast('Sequence[Mapping[str, object]]', level.get('strokes', ())):
        projected: dict[str, object] = {}
        for name in ('id', 'source_path'):
            if name in stroke:
                value = stroke[name]
                if type(value) is not str:
                    return None
                projected[name] = value
        points = []
        for point in cast('Sequence[Mapping[str, object]]', stroke.get('points', ())):
            fields = {name: point[name] for name in _POINT_FIELDS if name in point}
            if any(type(value) not in (str, int, float, bool, type(None)) for value in fields.values()):
                return None
            points.append(fields)
        projected['points'] = points
        strokes.append(projected)
    return cast(GeometryLevel, {'strokes': strokes})


class CombinedAReplay:
    """只保存算法声明消费的几何字段，不保存完整证明树或另算趋势。

    单行原始编码不超过 1 MiB；实时请求的几何附件写入专属临时文件，
    内存只保留有界索引。文件由请求作用域关闭，不接收外部路径或负载。
    缺行、未知字段或索引/磁盘超限仍拒绝局部修复，沿用正确的完整重放。
    """

    def __init__(self, *, memory_bytes: int = 4 * 1024 * 1024, spill: bool = False) -> None:
        self._limit = memory_bytes
        self._bytes = 4096
        self._rows: dict[int, bytes | tuple[int, int]] = {}
        try:
            self._stream = TemporaryFile(mode='w+b', buffering=0) if spill else None
        except OSError:
            self._stream = None
        self._disk_bytes = 0
        self._disk_limit = 128 * 1024 * 1024
        self._accepting = memory_bytes >= 4096 + 512

    @classmethod
    def for_replay(cls, previous: object, *, resume_start: int, spill: bool = False) -> 'CombinedAReplay':
        """冷重放不能重置本请求已确认的容量拒绝，否则重复编码同一旧前缀。"""
        if isinstance(previous, cls) and (resume_start or not previous._accepting):
            return previous
        if isinstance(previous, cls):
            previous.close()
        return cls(spill=spill)

    def close(self) -> None:
        self._accepting = False
        self._rows.clear()
        self._bytes = 4096
        if self._stream is not None:
            self._stream.close()

    def _stop_admission(self) -> None:
        # 缺少任一有效日后，完整前缀附件无法局部修复；不再反复编码必定
        # 用不上的后续行。后续冷重放保留拒绝状态，直到请求结束。
        self._accepting = False
        self._rows.clear()
        self._bytes = 4096
        if self._stream is not None:
            self._stream.close()

    def remember(self, index: int, levels: Sequence[Mapping[str, object]]) -> None:
        if not self._accepting:
            return
        previous = self._rows.pop(index, None)
        if previous is not None:
            self._bytes -= (len(previous) if isinstance(previous, bytes) else 0) + 512
        # 上次调用的最后一天可能未完成；重放当天时必须替换其几何。
        geometry = tuple(_geometry(level) for level in levels)
        if any(level is None for level in geometry):
            self._stop_admission()
            return
        payload = pickle.dumps(geometry, protocol=5)
        if len(payload) > 1024 * 1024:
            self._stop_admission()
            return
        payload = compress(payload, level=1)
        charged = (len(payload) if self._stream is None else 0) + 512
        if self._bytes + charged > self._limit:
            self._stop_admission()
            return
        if self._stream is None:
            self._rows[index] = payload
        else:
            if self._disk_bytes + len(payload) > self._disk_limit:
                self._stop_admission()
                return
            try:
                self._stream.seek(0, 2)
                offset = self._stream.tell()
                if self._stream.write(payload) != len(payload):
                    self._stop_admission()
                    return
            except OSError:
                self._stop_admission()
                return
            self._disk_bytes += len(payload)
            self._rows[index] = offset, len(payload)
        self._bytes += charged

    def _read(self, index: int) -> bytes:
        row = self._rows[index]
        if isinstance(row, bytes):
            return row
        if self._stream is None:
            raise RuntimeError('disk replay requires its request stream')
        offset, length = row
        self._stream.seek(offset)
        payload = self._stream.read(length)
        if len(payload) != length:
            raise ValueError('incomplete request-owned geometry row')
        return payload

    def recompute(
        self, bars: Sequence[Bar], dates: Mapping[str, int], entry_days: Sequence[bool], cutoff: int,
        audit: Sequence[Mapping[str, object]], market_index: _MarketRangeIndex | None,
    ) -> dict[int, tuple[CombinedAContext, ...]] | None:
        if not self._accepting:
            return None
        indices = [index for index in range(cutoff) if entry_days[index]]
        if any(index not in self._rows for index in indices):
            return None
        result = {}
        for index in indices:
            try:
                payload = self._read(index)
            except (OSError, ValueError):
                self._stop_admission()
                return None
            drawing, first, second, third = pickle.loads(decompress(payload))
            result[index] = combined_a_contexts_from_geometry(
                bars, drawing, first, second, third, dates, index, audit, _market_index=market_index)
        return result
