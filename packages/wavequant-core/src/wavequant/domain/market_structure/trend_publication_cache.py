"""Request-local publication checkpoints for immutable, causal input versions."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
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

    def clear(self) -> None:
        self.checkpoint = None
        self._candidates = self._source = None
        self._owner = None
        self._level = self._end = -1
        self._input_bytes = 0
        self._version_bytes = 0
        self._checkpoint_bytes = 0

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
        if version is prior:
            return True, 0
        start = version.changed_since(prior)
        checkpoint = self.checkpoint
        if checkpoint is not None:
            if candidates and start < checkpoint.offset:
                return False, 0
            # Forward certificate selection can read beyond the saved frontier.
            # A revised old suffix must therefore invalidate the earlier prefix.
            for values in (prior, version):
                if values is not None:
                    for point in values.iter_range(start):
                        known = self._known(point, market)
                        if known is None or known <= checkpoint.horizon:
                            return False, 0
        suffix = tuple(version.iter_range(start))
        size = _builtin_size(suffix, self.limit - self._input_bytes)
        if size is None:
            return False, 0
        previous_order: tuple[int, int] | None = None
        previous_known = -1
        if start:
            previous = version[start - 1]
            previous_order = cast(int, previous['index']), cast(int, previous.get('ordinal', 0))
            previous_known = cast(int, self._known(previous, market))
        for point in suffix:
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
        return True, size

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
        admitted, candidate_bytes = self._admit_version(
            candidate_version, self._candidates, market, candidates=True)
        source_admitted, source_bytes = self._admit_version(
            source_version, self._source, market, candidates=False)
        if not admitted or not source_admitted:
            # Do not partially revalidate a changed dependency against the old
            # checkpoint. The unchanged full publisher is the fallback.
            self.clear()
            return False
        self._input_bytes += candidate_bytes + source_bytes
        self._version_bytes = candidate_version.storage_bytes + source_version.storage_bytes
        if self._input_bytes + self._version_bytes + self._checkpoint_bytes > self.limit:
            self.clear()
            return False
        self._candidates, self._source = candidate_version, source_version
        self._owner, self._level, self._end = market.owner, source_level, end
        return True

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
        payload = (list(checkpoint.result), checkpoint.pressure, checkpoint.pending_origin,
                   checkpoint.pending_down_origin, checkpoint.last_low, checkpoint.active, checkpoint.extreme)
        remaining = self.limit - self._input_bytes - self._version_bytes
        size = _builtin_size(payload, remaining)
        if size is None or size + checkpoint.result.storage_bytes > remaining:
            self.clear()
            return
        self._checkpoint_bytes = size + checkpoint.result.storage_bytes
        self.checkpoint = checkpoint
