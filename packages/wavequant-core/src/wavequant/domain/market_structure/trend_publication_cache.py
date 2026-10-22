"""Request-local publication checkpoints for immutable, causal input versions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from sys import getsizeof
from typing import cast

from .owned_evidence import CertifiedSource, current_evidence
from .trend_frozen_state import FrozenPrefixList
from .trend_market_index import _MarketPrefixIndex
from .trend_point_version import PointVersion
from .trend_readonly_cache import _builtin_size


@dataclass(frozen=True, slots=True)
class _PublicationCheckpoint:
    offset: int
    horizon: int
    result: FrozenPrefixList[dict[str, object]]
    pressure: Mapping[str, object] | None
    pending_origin: Mapping[str, object] | None
    pending_down_origin: Mapping[str, object] | None
    last_low: Mapping[str, object] | None
    active: Mapping[str, object] | None
    extreme: Mapping[str, object] | None


def _publication_size(value: object, limit: int) -> int | None:
    """请求池已持有并计费的不可变证据只计引用，不再遍历或重复收费。"""
    owner = current_evidence()
    if owner is None:
        return _builtin_size(value, limit)
    pending = [value]
    seen: set[int] = set()
    size = 0
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if owner.reference(item) is not None:
            size += 8
        else:
            kind = type(item)
            if kind not in (dict, list, tuple, str, bytes, int, float, bool, type(None)):
                return None
            size += getsizeof(item)
            if kind is dict:
                mapping = cast(dict[object, object], item)
                pending.extend(mapping.keys())
                pending.extend(mapping.values())
            elif kind in (list, tuple):
                pending.extend(cast(Sequence[object], item))
        if size > limit:
            return None
    return size


class _SequenceBudget:
    """只给不可变序列的新后缀计费；替换/截短同时撤销旧后缀费用。

    各记录独立计费形成保守上界，账本自身也计入预算。账本沿用持久前缀，
    不持有逐日版本链，不按每次保存重新遍历旧证据。
    """

    def __init__(self) -> None:
        self.costs = FrozenPrefixList[int]([0])

    @property
    def size(self) -> int:
        return self.costs[-1] + self.costs.storage_bytes + len(self.costs) * 32 + 256

    def update(self, values: FrozenPrefixList[dict[str, object]], start: int, limit: int) -> bool:
        del self.costs[start + 1:]
        for point in values.iter_range(start):
            size = _publication_size(point, limit - self.size)
            if size is None:
                return False
            self.costs.append(self.costs[-1] + size)
            if self.size > limit:
                return False
        self.costs.freeze(len(self.costs))
        return self.size <= limit


class _PublicationCache:
    """Freeze only a prefix whose decisions cannot change on later market dates.

    Unresolved lows stop the frontier. Input edits at or before its knowledge
    horizon invalidate it, including edits to candidates read by lookahead.
    One checkpoint is retained, not one snapshot for every candidate or date.
    """

    def __init__(self, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        self.limit = memory_bytes
        self.checkpoint: _PublicationCheckpoint | None = None
        self._candidates: PointVersion | None = None
        self._source: PointVersion | None = None
        self._owner: object | None = None
        self._level = -1
        self._end = -1
        self._input_bytes = 0
        self._version_bytes = 0
        self._checkpoint_bytes = 0
        self._candidate_budget = _SequenceBudget()
        self._source_budget = _SequenceBudget()
        self._result_budget = _SequenceBudget()

    def clear(self) -> None:
        self.checkpoint = None
        self._candidates = self._source = None
        self._owner = None
        self._level = self._end = -1
        self._input_bytes = 0
        self._version_bytes = 0
        self._checkpoint_bytes = 0
        self._candidate_budget = _SequenceBudget()
        self._source_budget = _SequenceBudget()
        self._result_budget = _SequenceBudget()

    @staticmethod
    def _version(points: Sequence[Mapping[str, object]]) -> PointVersion | None:
        if type(points) not in (PointVersion, CertifiedSource):
            return None
        version = cast(PointVersion, points)
        if not version._sealed:
            return None
        if type(points) is CertifiedSource and points.owner is not current_evidence():
            return None
        return version

    @staticmethod
    def _known(point: Mapping[str, object], market: _MarketPrefixIndex) -> int | None:
        value = point.get('available_at')
        if type(value) is int:
            return value
        if type(value) is str:
            positions = market.owner.positions.get(value)
            if positions is not None and len(positions) == 1:
                return positions[0]
        return None

    def _admit_version(
        self, version: PointVersion, prior: PointVersion | None,
        market: _MarketPrefixIndex, *, candidates: bool,
    ) -> tuple[bool, int]:
        budget = self._candidate_budget if candidates else self._source_budget
        if version is prior:
            return True, budget.size
        start = version.changed_since(prior)
        previous_order: tuple[int, int] | None = None
        previous_known = -1
        if start:
            previous = version[start - 1]
            previous_order = cast(int, previous['index']), cast(int, previous.get('ordinal', 0))
            previous_known = cast(int, self._known(previous, market))
        for point in version.iter_range(start):
            index, ordinal = point.get('index'), point.get('ordinal', 0)
            known = self._known(point, market)
            if (type(index) is not int or type(ordinal) is not int or known is None
                    or not 0 <= index <= known < len(market.bars)):
                return False, 0
            order = index, ordinal
            if previous_order is not None and order <= previous_order:
                return False, 0
            # Source qualification requires ordered knowledge. Candidate
            # publication may have its own earlier/later certificates.
            if not candidates and known < previous_known:
                return False, 0
            previous_order, previous_known = order, known
        if not budget.update(version, start, self.limit):
            return False, 0
        return True, budget.size

    def _checkpoint_covers(self, version: PointVersion, prior: PointVersion | None,
                           market: _MarketPrefixIndex, *, candidates: bool) -> bool:
        checkpoint = self.checkpoint
        if checkpoint is None or version is prior:
            return True
        start = version.changed_since(prior)
        if candidates and start < checkpoint.offset:
            return False
        # 发布器会向后寻找证明；旧知识时刻的后缀修订必须使发布状态失效。
        for values in (prior, version):
            if values is not None:
                for point in values.iter_range(start):
                    known = self._known(point, market)
                    if known is None or known <= checkpoint.horizon:
                        return False
        return True

    def admit(
        self, candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]],
        market: _MarketPrefixIndex | None, source_level: int,
    ) -> bool:
        candidate_version, source_version = self._version(candidates), self._version(source)
        if (market is None or candidate_version is None or source_version is None
                or not market.owner.unique_sessions or not market.owner.exact_prices
                or not market.bars or self.limit <= 0):
            self.clear()
            return False
        end = len(market.bars) - 1
        if self._owner is not market.owner or self._level != source_level or end < self._end:
            self.clear()
        reusable = (self._checkpoint_covers(candidate_version, self._candidates, market, candidates=True)
                    and self._checkpoint_covers(source_version, self._source, market, candidates=False))
        if not reusable:
            # 失效的是发布结论，不是未变输入的校验和容量记录。
            self.checkpoint = None
            self._checkpoint_bytes = 0
            self._result_budget = _SequenceBudget()
        admitted, candidate_bytes = self._admit_version(
            candidate_version, self._candidates, market, candidates=True)
        source_admitted, source_bytes = self._admit_version(
            source_version, self._source, market, candidates=False)
        if not admitted or not source_admitted:
            # Do not partially revalidate a changed dependency against the old
            # checkpoint. The unchanged full publisher is the fallback.
            self.clear()
            return False
        self._input_bytes = candidate_bytes + source_bytes
        self._version_bytes = candidate_version.storage_bytes + source_version.storage_bytes
        if self._input_bytes + self._version_bytes + self._checkpoint_bytes > self.limit:
            self.clear()
            return False
        self._candidates, self._source = candidate_version, source_version
        self._owner, self._level, self._end = market.owner, source_level, end
        return reusable

    @staticmethod
    def rejected_forever(origin: Mapping[str, object], market: _MarketPrefixIndex) -> bool:
        """A missing ordinary proof is final only after the real origin broke."""
        index, price = origin.get('index'), origin.get('value')
        if (type(index) is not int or not 0 <= index < len(market.bars)
                or type(price) not in (int, float) or not isfinite(cast(float, price))
                or type(price) is int and abs(price) > 2 ** 53):
            return False
        return market.first_extreme(index + 1, len(market.bars), cast(float, price), high=False) is not None

    def save(self, checkpoint: _PublicationCheckpoint | None) -> None:
        if checkpoint is None or checkpoint is self.checkpoint:
            return
        previous = self.checkpoint
        start = checkpoint.result.shared_prefix(previous.result) if previous is not None else 0
        remaining = self.limit - self._input_bytes - self._version_bytes
        if not self._result_budget.update(checkpoint.result, start, remaining):
            self.clear()
            return
        frontier = (checkpoint.pressure, checkpoint.pending_origin, checkpoint.pending_down_origin,
                    checkpoint.last_low, checkpoint.active, checkpoint.extreme)
        size = _publication_size(frontier, remaining - self._result_budget.size)
        if size is None:
            self.clear()
            return
        self._checkpoint_bytes = size + self._result_budget.size + checkpoint.result.storage_bytes
        if self._checkpoint_bytes > remaining:
            self.clear()
            return
        self.checkpoint = checkpoint
