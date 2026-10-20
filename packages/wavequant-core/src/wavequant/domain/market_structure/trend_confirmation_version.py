"""Versioned static qualification inputs; market decisions remain per date."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from sys import getsizeof
from typing import Protocol, cast
from weakref import ReferenceType, ref

from ..models.model import Bar
from .owned_evidence import CertifiedSource, current_evidence
from .trend_confirmation import ConfirmationContext, _PrefixSessions, _Reference, _reference, _scalar_reference
from .trend_evidence_reuse import _geometry_signature
from .trend_frozen_state import FrozenPrefixList
from .trend_market_index import _MarketPrefixIndex
from .trend_point_version import PointVersion
from .trend_prefix_view import _PrefixView
from .trend_readonly_cache import _builtin_size


class _Digest(Protocol):
    def copy(self) -> '_Digest': ...
    def update(self, data: bytes) -> None: ...
    def digest(self) -> bytes: ...


@dataclass(frozen=True, slots=True)
class _RejectedVersion:
    token: object
    owner: ReferenceType[object]
    length: int
    limit: int
    end: int


class _ConfirmationVersionCache:
    """Keep one certified source version, never a daily qualification result.

    Only fully known, ordered, scalar references on unique market sessions are
    admitted. Future-known or invalid points use the original parser, so a
    previously invisible point cannot be permanently cached as missing.
    """

    def __init__(self, *, memory_bytes: int = 2 * 1024 * 1024) -> None:
        self.limit = memory_bytes
        self.clear()

    def clear(self) -> None:
        self._rejected: _RejectedVersion | None = None
        self.source: PointVersion | None = None
        self.owner: object | None = None
        self.end = -1
        self.memory_used = 0
        self.references = FrozenPrefixList[_Reference]()
        self.visibility = FrozenPrefixList[int]()
        self.stops = FrozenPrefixList[int]([0])
        self.prefixes = FrozenPrefixList[bytes]([sha256().digest()])
        self.hashes = FrozenPrefixList[_Digest]([sha256()])
        self.costs = FrozenPrefixList[int]([0])

    def _reject(self, source: PointVersion, owner: object, end: int) -> None:
        # Only opaque version metadata survives; failed evidence and market
        # graphs are released. Rejection is never a qualification result.
        rejected = _RejectedVersion(source.token, ref(owner), len(source), self.limit, end)
        self.clear()
        self._rejected = rejected

    def set_limit(self, limit: int) -> None:
        self.limit = limit
        if self.memory_used > limit:
            if self.source is not None and self.owner is not None:
                self._reject(self.source, self.owner, self.end)
            else:
                self.clear()

    def _skip_rejected(self, source: PointVersion, owner: object, end: int) -> bool:
        rejected = self._rejected
        if (rejected is None or rejected.owner() is not owner
                or end < rejected.end or self.limit > rejected.limit):
            return False
        unchanged = source.token is rejected.token
        appended = (source.parent is rejected.token and source.changed_from >= rejected.length
                    and len(source) >= rejected.length)
        if not unchanged and not appended:
            return False
        # Follow only direct, certified appends in O(1). Unknown ancestry retries
        # instead of scanning the old source just to decide whether to cache it.
        self._rejected = replace(rejected, token=source.token, length=len(source), end=end)
        return True

    def prepare(self, source: Sequence[Mapping[str, object]], bars: Sequence[Bar],
                market: _MarketPrefixIndex | None) -> ConfirmationContext | None:
        if (type(source) not in (PointVersion, CertifiedSource)
                or not cast(PointVersion, source)._sealed or market is None
                or not market.matches(bars) or not market.owner.unique_sessions or not bars
                or type(source) is CertifiedSource and source.owner is not current_evidence()):
            self.clear()
            return None
        version = cast(PointVersion, source)
        end = len(bars) - 1
        if self._skip_rejected(version, market.owner, end):
            return None
        if self.owner is not market.owner or end < self.end:
            self.clear()
        start = len(version) if version is self.source else version.changed_since(self.source)
        sessions = _PrefixSessions(market.owner.positions, end)
        references, visibility = self.references.copy(), self.visibility.copy()
        stops, prefixes = self.stops.copy(), self.prefixes.copy()
        hashes, costs = self.hashes.copy(), self.costs.copy()
        del references[start:]
        del visibility[start:]
        for values in (stops, prefixes, hashes, costs):
            del values[start + 1:]
        digest = hashes[-1].copy()
        charged = costs[-1]
        previous = references[-1] if references else None
        for point in version.iter_range(start):
            # Charge the retained input too; nested extension fields can be
            # larger than the scalar reference used by qualification.
            size = _builtin_size(point, self.limit - charged)
            if size is None:
                # Size/admission depends on immutable content, not market time.
                self._reject(version, market.owner, end)
                return None
            reference = _reference(point, sessions, end)
            if (reference is None or not _scalar_reference(reference)
                    or reference.known is None
                    or previous is not None and (reference.order <= previous.order
                        or reference.known < cast(int, previous.known))):
                self.clear()
                return None
            signature = _geometry_signature(dict(reference.raw))
            if signature is None:
                self.clear()
                return None
            reference = replace(reference, _proof_signature=signature)
            charged += (size + getsizeof(signature) + 4096
                        + getsizeof(reference.value.numerator) + getsizeof(reference.value.denominator))
            if charged > self.limit:
                self._reject(version, market.owner, end)
                return None
            digest.update(len(signature).to_bytes(8, 'big'))
            digest.update(signature)
            references.append(reference)
            visibility.append(max(reference.index, cast(int, reference.known)))
            stops.append(len(references))
            prefixes.append(digest.digest())
            hashes.append(digest.copy())
            costs.append(charged)
            previous = reference
        for buffer in (references, visibility, stops, prefixes, hashes, costs):
            buffer.freeze(len(buffer))
        used = charged + version.storage_bytes + 4096 + sum(
            values.storage_bytes for values in (references, visibility, stops, prefixes, hashes, costs))
        if used > self.limit:
            self._reject(version, market.owner, end)
            return None
        self.source, self.owner, self.end = version, market.owner, end
        self.references, self.visibility = references, visibility
        self.stops, self.prefixes, self.hashes, self.costs = stops, prefixes, hashes, costs
        self.memory_used = used
        return ConfirmationContext(
            id(source), id(bars), end, _PrefixView(market.bars, range(end + 1)),
            sessions, references, True, source, None, False, market,
            visibility, prefixes, stops, len(references), False)
