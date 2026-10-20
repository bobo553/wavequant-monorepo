"""有界保存组合 A 实际消费的标量几何，供资格窗口修订后的附件重算。"""

from collections.abc import Mapping, Sequence
from hashlib import sha256
import pickle
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

    已序列化字节加固定管理余量受 4 MiB 限制，单行原始编码不超过 1 MiB。
    缺行或超预算时拒绝局部修复，调用方沿用完整重放。读取只接受本实例编码。
    """

    def __init__(self, *, memory_bytes: int = 4 * 1024 * 1024) -> None:
        self._limit = memory_bytes
        self._bytes = 4096
        self._rows: dict[int, bytes] = {}
        self._accepting = memory_bytes >= 4096 + 512

    def _stop_admission(self) -> None:
        # 缺少任一有效日后，完整前缀附件无法局部修复；不再反复编码必定
        # 用不上的后续行。下一次冷重放由调用方建立新的独立记录器。
        self._accepting = False
        self._rows.clear()
        self._bytes = 4096

    def remember(self, index: int, levels: Sequence[Mapping[str, object]]) -> None:
        if not self._accepting:
            return
        previous = self._rows.pop(index, None)
        if previous is not None:
            self._bytes -= len(previous) + 512
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
        charged = len(payload) + 512
        if self._bytes + charged <= self._limit:
            self._rows[index] = payload
            self._bytes += charged
        else:
            self._stop_admission()

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
            drawing, first, second, third = pickle.loads(decompress(self._rows[index]))
            result[index] = combined_a_contexts_from_geometry(
                bars, drawing, first, second, third, dates, index, audit, _market_index=market_index)
        return result
