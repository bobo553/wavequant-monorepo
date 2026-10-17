"""Reuse complete dated observations within one fixed backtest request.

These observations never cross requests or persisted signal-cache boundaries.
They contain every original prefix, rather than a truncation of final geometry.
"""

from collections.abc import Callable, Mapping, Sequence
from ..market_structure.trend_options import ordinary_completion_enabled
from copy import deepcopy
from dataclasses import dataclass, fields, replace
from datetime import datetime, timezone
from hashlib import sha256
from functools import lru_cache
import pickle
from struct import pack
from sys import getsizeof
from tempfile import TemporaryFile
from threading import get_ident
from types import MappingProxyType, TracebackType
from typing import TYPE_CHECKING, TypedDict, cast
from zoneinfo import ZoneInfo

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature
from wavequant.domain.market_structure.trend_stage_work import TrendStageRunner
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import EntryContext

if TYPE_CHECKING:
    from wavequant.domain.strategies.backtest_analysis import BacktestAnalysis
    from wavequant.domain.strategies.eligible_polyline_n import EligibleNSource
    from wavequant.domain.strategies.integrated_strategy import SystemStrategy


class HierarchyPoint(TypedDict):
    index: int
    ordinal: int
    kind: str
    value: float
    available_at: int


PivotHistory = tuple[
    dict[int, tuple[ReversalPoint, ...]], dict[int, int], dict[int, int], set[int],
]
HierarchyHistory = tuple[dict[int, dict[int, tuple[HierarchyPoint, ...]]], dict[int, int]]
ChartHistory = tuple[dict[int, tuple[EntryContext, ...]], list[dict[str, object]]]
ChartAuditCertificate = tuple[tuple[object, ...], tuple[object, ...]]
ChartAnchors = Mapping[int, Sequence[Mapping[str, object]]]


@dataclass(frozen=True, slots=True)
class MinuteTradingScope:
    """Minute decisions inherit yesterday's qualified sources, never today's close."""

    index: int
    window_start: int
    active_scopes: frozenset[tuple[str, int]]
    sources: tuple['EligibleNSource', ...]
    buy_days: tuple[bool, ...]
    scan_days: tuple[bool, ...]
    source_bars: tuple[Bar, ...]

    @classmethod
    def from_analysis(cls, analysis: 'BacktestAnalysis', *, index: int,
                      window_start: int) -> 'MinuteTradingScope | None':
        if (type(index) is not int or index != len(analysis.bars) - 1
                or type(window_start) is not int or not 0 <= window_start <= index):
            raise ValueError('minute scope requires this completed daily prefix and its window')
        if (index == 0 or window_start == index or len(analysis.buy_days) != index + 1
                or not analysis.buy_days[index] or not analysis.buy_days[index - 1]):
            return None
        scopes = frozenset((interval.source_path, interval.start_index)
            for interval in analysis.trading_intervals
            if interval.start_index < index
            and (interval.end_index is None or index < interval.end_index))
        if not scopes:
            return None
        sources = tuple(source for source in analysis.n_sources
            if source.scope in scopes and window_start <= source.known_at < index
            and all(point.confirmed_index < index for point in source.points))
        days = tuple(window_start <= day and analysis.buy_days[day] for day in range(index + 1))
        return cls(index, window_start, scopes, sources, days, days, tuple(analysis.bars))

    def require_input(self, bars: Sequence[Bar]) -> None:
        if len(bars) != self.index + 1 or tuple(bars[:-1]) != self.source_bars[:-1]:
            raise ValueError('minute scope cannot cross completed market history')
        actual, fixed = bars[-1], self.source_bars[-1]
        partial = replace(fixed, high=actual.high, low=actual.low, close=actual.close, volume=actual.volume)
        if actual != partial:
            raise ValueError('minute scope permits only unfinished price and volume changes')


_REVERSAL_FIELDS = frozenset(('point', 'confirmed_index', 'source'))
_LINE_FIELDS = frozenset(('index', 'ordinal', 'kind', 'price'))
_HIERARCHY_INTERN_BYTES = 8 * 1024 * 1024

_CHART_AUDIT_EVENTS = frozenset({
    'n_consolidation_gap_confirmed', 'squeeze_resumption_observed',
})


class _UncertifiableValue(Exception):
    """Unknown audit metadata must take the original uncached path."""


def _immutable_pivot(history: PivotHistory) -> bool:
    """Only the exact frozen scalar value objects can share snapshot references."""
    if (type(history) is not tuple or len(history) != 4
            or any(type(part) is not dict for part in history[:3]) or type(history[3]) is not set):
        return False
    snapshots, epochs, limits, blocked = history
    verified: set[int] = set()
    if (any(type(key) is not int or type(value) is not int for key, value in epochs.items())
            or any(type(key) is not int or type(value) is not int for key, value in limits.items())
            or any(type(index) is not int for index in blocked)):
        return False
    for day, points in snapshots.items():
        if type(day) is not int or type(points) is not tuple:
            return False
        for reversal in points:
            # The complete source history itself keeps these objects alive,
            # so this bounded local identity set cannot see recycled ids.
            if id(reversal) in verified:
                continue
            if (type(reversal) is not ReversalPoint or vars(reversal).keys() != _REVERSAL_FIELDS
                    or type(reversal.confirmed_index) is not int or type(reversal.source) is not str):
                return False
            point = reversal.point
            if (type(point) is not LinePoint or vars(point).keys() != _LINE_FIELDS
                    or type(point.index) is not int or type(point.ordinal) is not int
                    or type(point.kind) is not PointKind or type(point.price) not in (int, float)):
                return False
            if len(verified) < 8192:
                verified.add(id(reversal))
    return True


def _copy_immutable_pivot(history: PivotHistory) -> PivotHistory:
    snapshots, epochs, limits, blocked = history
    return snapshots.copy(), epochs.copy(), limits.copy(), blocked.copy()


@dataclass(frozen=True, slots=True)
class _EncodedHierarchy:
    history: dict[int, dict[int, tuple[bytes, ...]]]
    epochs: dict[int, int]


def _encode_hierarchy(history: HierarchyHistory) -> _EncodedHierarchy | None:
    """Intern full standard point templates; unknown values retain old deepcopy."""
    if (type(history) is not tuple or len(history) != 2
            or type(history[0]) is not dict or type(history[1]) is not dict):
        return None
    original, epochs = history
    if any(type(key) is not int or type(value) is not int for key, value in epochs.items()):
        return None
    encoded: dict[int, dict[int, tuple[bytes, ...]]] = {}
    interned: dict[bytes, bytes] = {}
    memory_used = getsizeof(interned)
    for index, levels in original.items():
        if type(index) is not int or type(levels) is not dict:
            return None
        dated: dict[int, tuple[bytes, ...]] = {}
        for level, points in levels.items():
            if type(level) is not int or type(points) is not tuple:
                return None
            templates: list[bytes] = []
            for point in points:
                if type(point) is not dict:
                    return None
                signature = _geometry_signature(point)
                if signature is None:
                    return None
                shared = interned.get(signature)
                if shared is not None:
                    signature = shared
                else:
                    # Only immutable bytes are retained. The fixed allowance
                    # exceeds an entry's dictionary capacity and reference cost.
                    cost = getsizeof(signature) + 256
                    if memory_used + cost <= _HIERARCHY_INTERN_BYTES:
                        interned[signature] = signature
                        memory_used += cost
                templates.append(signature)
            dated[level] = tuple(templates)
        encoded[index] = dated
    return _EncodedHierarchy(encoded, epochs.copy())


def _decode_hierarchy(history: _EncodedHierarchy) -> HierarchyHistory:
    result: dict[int, dict[int, tuple[HierarchyPoint, ...]]] = {}
    for index, levels in history.history.items():
        dated: dict[int, tuple[HierarchyPoint, ...]] = {}
        for level, templates in levels.items():
            points: list[HierarchyPoint] = []
            for template in templates:
                # These templates were encoded by our exact-builtin Pickler.
                # Each date, point and getter receives fresh mutable containers.
                value: object = pickle.loads(template)
                if type(value) is not dict:
                    raise ValueError('an owned hierarchy point template must decode to a dict')
                points.append(cast(HierarchyPoint, value))
            dated[level] = tuple(points)
        result[index] = dated
    return result, history.epochs.copy()


def _datetime_certificate(value: datetime) -> tuple[str, int]:
    if value.tzinfo is not None and type(value.tzinfo) not in (timezone, ZoneInfo):
        raise _UncertifiableValue
    return value.isoformat(), value.fold


def _exact_value(value: object) -> object:
    """Keep knowledge dates and scalar types ordinary Python equality conflates."""
    value_type = type(value)
    if value_type is float:
        return float, pack('!d', cast(float, value))
    if value_type is complex:
        number = cast(complex, value)
        return complex, pack('!dd', number.real, number.imag)
    if (value_type is str or value_type is int or value_type is bool
            or value_type is bytes or value_type is type(None)):
        return value_type, value
    if value_type is datetime:
        assert isinstance(value, datetime)
        return type(value), *_datetime_certificate(value)
    if value_type is dict or value_type is MappingProxyType:
        assert isinstance(value, Mapping)
        return type(value), frozenset((_exact_value(key), _exact_value(item)) for key, item in value.items())
    if value_type is list or value_type is tuple:
        assert isinstance(value, (list, tuple))
        return type(value), tuple(_exact_value(item) for item in value)
    if value_type is set or value_type is frozenset:
        assert isinstance(value, (set, frozenset))
        return value_type, frozenset(_exact_value(item) for item in value)
    raise _UncertifiableValue


def _bar_certificate(bars: Sequence[Bar]) -> tuple[tuple[object, ...], ...]:
    if any(type(bar) is not Bar for bar in bars):
        raise _UncertifiableValue
    return tuple(tuple((item.name, _exact_value(getattr(bar, item.name))) for item in fields(Bar)) for bar in bars)


def _strategy_certificate(strategy: 'SystemStrategy') -> tuple[object, ...]:
    from wavequant.domain.strategies.integrated_strategy import SystemStrategy

    if type(strategy) is not SystemStrategy:
        raise _UncertifiableValue
    return tuple((item.name, _exact_value(getattr(strategy, item.name))) for item in fields(strategy))


def _bar_index(row: Mapping[str, object]) -> int:
    index = row.get('bar_index')
    if type(index) is not int:
        raise ValueError('chart audit requires integer bar indices')
    return index


def _audit_token(row: Mapping[str, object], field: str) -> str | None:
    if type(row) is not dict and type(row) is not MappingProxyType:
        raise _UncertifiableValue
    value = row.get(field)
    if value is not None and type(value) is not str:
        raise _UncertifiableValue
    return value


def _chart_audit(audit: Sequence[Mapping[str, object]]) -> ChartAuditCertificate | None:
    # squeeze_alternations passes whole N/confirmation rows to ABC observation.
    # An attack-index set alone cannot certify origin, defense or knowledge time.
    # The reducer selects the last positive N per attack and stably sorts its
    # triggers by date. Ties retain order because the first may claim an A/B.
    try:
        positive = {_bar_index(row): row for row in audit
                    if _audit_token(row, 'event') == 'n_completed' and _audit_token(row, 'direction') == 'up'}
        triggers = sorted((row for row in audit if (
            _audit_token(row, 'event') in _CHART_AUDIT_EVENTS
            or (_audit_token(row, 'event') == 'regime_confirmation'
                and _audit_token(row, 'regime') in ('轧空', '强轧空'))
        )), key=_bar_index)
        return (tuple(_exact_value(positive[index]) for index in sorted(positive)),
                tuple(_exact_value(row) for row in triggers))
    except (_UncertifiableValue, RecursionError):
        return None


def _past_attack_digest(audit: Sequence[Mapping[str, object]], cutoff: int) -> bytes | None:
    # Whole rows, not indices, certify the anchors actually consumed that day.
    try:
        positive = {_bar_index(row): row for row in audit
                    if _audit_token(row, 'event') == 'n_completed' and _audit_token(row, 'direction') == 'up'
                    and _bar_index(row) <= cutoff}
        evidence = tuple(_certificate_bytes(positive[index]) for index in sorted(positive))
    except (_UncertifiableValue, RecursionError):
        return None
    return sha256(pickle.dumps(evidence, protocol=5)).digest()


def _certificate_bytes(value: object) -> bytes:
    kind = type(value)
    if kind is dict or kind is MappingProxyType:
        mapping = cast(Mapping[object, object], value)
        items = sorted((_certificate_bytes(key), _certificate_bytes(item)) for key, item in mapping.items())
        return kind.__name__.encode() + b':' + b''.join(
            pack('!Q', len(key)) + key + pack('!Q', len(item)) + item for key, item in items)
    if kind is list or kind is tuple or kind is set or kind is frozenset:
        sequence_items = [_certificate_bytes(item) for item in cast(Sequence[object], value)]
        if kind is set or kind is frozenset:
            sequence_items.sort()
        return kind.__name__.encode() + b':' + b''.join(pack('!Q', len(item)) + item for item in sequence_items)
    if kind is datetime:
        stamp = cast(datetime, value)
        isoformat, fold = _datetime_certificate(stamp)
        return b'datetime:' + isoformat.encode() + bytes((fold,))
    if kind is float: return b'float:' + pack('!d', cast(float, value))
    if kind is complex:
        number = cast(complex, value)
        return b'complex:' + pack('!dd', number.real, number.imag)
    if kind in (str, int, bool, bytes, type(None)):
        return kind.__name__.encode() + b':' + pickle.dumps(value, protocol=5)
    raise _UncertifiableValue


@dataclass(frozen=True, slots=True)
class _ChartBaseHistory:
    history: dict[int, tuple[EntryContext, ...]]
    events: list[dict[str, object]]
    anchors_at_attack: dict[int, list[dict[str, object]]]
    attack_certificate: tuple[object, ...]


class _CheckpointUnavailable(Exception):
    """An unsupported value or capacity refusal takes the ordinary replay path."""


@lru_cache(maxsize=1)
def _checkpoint_types() -> tuple[type, ...]:
    # These are internal immutable results, not an arbitrary dataclass protocol.
    from wavequant.domain.market_structure.bottom_n_targets import DeclineStart
    from wavequant.domain.strategies.combined_a_entry import CombinedAContext, CombinedAHigh
    from wavequant.domain.strategies.secondary_pullback_entry import SecondaryPullbackCandidate
    from wavequant.domain.strategies.shallow_base_breakout import ShallowAlternationCandidate
    return (EntryContext, DeclineStart, CombinedAContext,
            SecondaryPullbackCandidate, ShallowAlternationCandidate, CombinedAHigh)


def _restore_checkpoint_value(position: int, values: tuple[object, ...]) -> object:
    constructor = _checkpoint_types()[position]
    return constructor(**{field.name: value for field, value in zip(fields(constructor), values, strict=True)})


class _CheckpointPickler(pickle.Pickler):
    def reducer_override(self, value: object) -> tuple[Callable[..., object], tuple[object, ...]]:
        if value is _restore_checkpoint_value:
            # CPython accepts NotImplemented here; the Pickler stub omits it.
            return cast(tuple[Callable[..., object], tuple[object, ...]], NotImplemented)
        for position, approved in enumerate(_checkpoint_types()):
            if type(value) is approved:
                declared = tuple(field.name for field in fields(cast(EntryContext, value)))
                attributes = vars(value)
                # An extended internal value is no longer the declared result.
                # Reject it before serializing any unknown metadata or dropping it.
                if (any(type(name) is not str for name in attributes)
                        or attributes.keys() != set(declared)):
                    raise _CheckpointUnavailable
                return (_restore_checkpoint_value,
                        (position, tuple(attributes[name] for name in declared)))
        # Exact builtin containers/scalars bypass reducer_override in CPython.
        # Unknown objects are rejected before any user serialization hook.
        raise _CheckpointUnavailable


class _CheckpointWriter:
    def __init__(self, store: '_MinuteCheckpointStore', stream: '_CheckpointStream') -> None:
        self.store, self.stream = store, stream

    def write(self, data: bytes) -> int:
        self.store.make_room(len(data), self.stream.target)
        written = self.stream.file.write(data)
        self.stream.size += written
        self.store.disk_used += written
        return written


class _CheckpointStream:
    def __init__(self, store: '_MinuteCheckpointStore', target: int | None = None) -> None:
        self.store, self.target = store, target
        self.owner = store._owner
        # Unbuffered streams keep per-file hidden buffers out of the permanent
        # metadata budget; pickle itself emits bounded chunks during one dump.
        self.file = TemporaryFile(mode='w+b', buffering=0)
        self.size = 0
        self.closed = False

    def append(self, value: object) -> None:
        self.file.seek(0, 2)
        _CheckpointPickler(_CheckpointWriter(self.store, self), protocol=5).dump(value)

    def close(self) -> None:
        if not self.closed:
            self.store.disk_used -= self.size
            self.file.close()
            self.closed = True


class _CheckpointChannel:
    def __init__(self, store: '_MinuteCheckpointStore', kind: str, options: tuple[bool, ...], version: str) -> None:
        self.store, self.kind, self.options = store, kind, options
        self.version = version
        self.journal = _CheckpointStream(store)
        self.states: dict[int, dict[int, _CheckpointStream]] = {}
        self.certificates: dict[int, bytes] = {}
        self.sealed: set[int] = set()
        self.closed = False

    def day(self, level: int, index: int, value: object) -> None:
        if self.closed:
            return
        try:
            self.journal.append((level, index, value))
        except (_CheckpointUnavailable, TypeError, ValueError, OverflowError):
            self.close()

    def state(self, level: int, index: int, value: object, certificate: bytes = b'') -> None:
        allowed = (0,) if self.kind == 'chart' else (0, 1, 2, 3)
        if self.closed or index not in self.store.targets or level not in allowed:
            return
        stream = _CheckpointStream(self.store, index)
        try:
            stream.append(value)
        except (_CheckpointUnavailable, TypeError, ValueError, OverflowError):
            stream.close()
            return
        parts = self.states.setdefault(index, {})
        old = parts.get(level)
        if old is not None:
            old.close()
        parts[level] = stream
        self.certificates[index] = certificate
        if self.kind == 'chart' or all(part in parts for part in (0, 1, 2, 3)):
            self.sealed.add(index)

    def remove(self, index: int) -> None:
        for stream in self.states.pop(index, {}).values():
            stream.close()
        self.certificates.pop(index, None)
        self.sealed.discard(index)

    def read(self, index: int) -> tuple[list[tuple[int, int, object]], dict[int, object]] | None:
        if self.closed or index not in self.sealed:
            return None
        streams = (self.journal, *self.states[index].values())
        if any(stream.store is not self.store or stream.owner is not self.store._owner
               or stream.closed for stream in streams):
            return None
        if any(stream.target != index for stream in self.states[index].values()):
            return None
        self.journal.file.seek(0)
        rows: list[tuple[int, int, object]] = []
        while True:
            try:
                row = pickle.load(self.journal.file)
            except EOFError:
                break
            level, day, value = cast(tuple[int, int, object], row)
            if day <= index:
                rows.append((level, day, value))
        states: dict[int, object] = {}
        for level, stream in self.states[index].items():
            stream.file.seek(0)
            states[level] = pickle.load(stream.file)
        return rows, states

    def close(self) -> None:
        self.journal.close()
        for index in tuple(self.states):
            self.remove(index)
        self.closed = True


class _MinuteCheckpointStore:
    """Keep actual dated reducer states and one journal, never final-state slices."""

    def __init__(self, targets: Sequence[int], *, memory_bytes: int = 2 * 1024 * 1024,
                 disk_bytes: int = 64 * 1024 * 1024) -> None:
        if (len(targets) > 16 or any(type(index) is not int or index < 0 for index in targets)
                or type(memory_bytes) is not int or memory_bytes <= 0
                or type(disk_bytes) is not int or disk_bytes <= 0):
            raise ValueError('minute checkpoints require <=16 dates and positive storage limits')
        self.targets = frozenset(targets)
        self.memory_bytes, self.disk_bytes = memory_bytes, disk_bytes
        self.disk_used = 0
        self._owner = object()
        self.thread = get_ident()
        self.channels: dict[str, _CheckpointChannel] = {}
        self.closed = False
        self.hits = {'chart': 0, 'hierarchy': 0}
        self.misses = {'chart': 0, 'hierarchy': 0}

    def begin(self, kind: str, options: tuple[bool, ...], version: str = '') -> _CheckpointChannel | None:
        if self.closed or self.thread != get_ident() or not self.targets:
            return None
        expected_options = 4 if kind == 'chart' else 3 if kind == 'hierarchy' else 0
        if len(options) != expected_options or any(type(option) is not bool for option in options):
            return None
        if type(version) is not str or len(version) > 256:
            return None
        previous = self.channels.pop(kind, None)
        if previous is not None:
            previous.close()
        # Each date has <=4 hierarchy streams or one chart stream. An 8 KiB
        # allowance per unbuffered stream includes its Python attributes, file
        # object, map entries and certificate; channels/store each get 16 KiB.
        # No serialized proof or reducer state is retained in those maps.
        kinds = (*self.channels, kind)
        metadata_bound = (16 * 1024 + len(kinds) * 16 * 1024
                          + len(self.targets) * sum(1 if item == 'chart' else 4 for item in kinds) * 8192)
        if metadata_bound > self.memory_bytes:
            return None
        channel = _CheckpointChannel(self, kind, options, version)
        self.channels[kind] = channel
        return channel

    def make_room(self, size: int, protected: int | None) -> None:
        if size > self.disk_bytes:
            raise _CheckpointUnavailable
        while self.disk_used + size > self.disk_bytes:
            dates = sorted({date for channel in self.channels.values()
                            for date in channel.states if date != protected})
            if not dates:
                raise _CheckpointUnavailable
            for channel in self.channels.values():
                channel.remove(dates[0])

    def close(self) -> None:
        for channel in self.channels.values():
            channel.close()
        self.channels.clear()
        self.closed = True


class BacktestHistoryReuse:
    """Own isolated histories for one exact daily series and strategy.

    Source or strategy mismatches raise instead of borrowing unrelated geometry.
    A different relevant chart audit is a cache miss and must be replayed.
    Consumers receive detached containers and cannot revise stored observations.
    The object is deliberately request-local and is not a concurrent cache.
    """

    def __init__(self, bars: Sequence[Bar], strategy: 'SystemStrategy', *,
                 minute_checkpoint_indices: Sequence[int] | None = None,
                 stage_runner: TrendStageRunner | None = None,
                 stage_runner_factory: Callable[[tuple[Bar, ...]], TrendStageRunner | None] | None = None) -> None:
        strategy_certificate = _strategy_certificate(strategy)
        strategy.validate()
        self._bars = tuple(bars)
        self._bar_certificate = _bar_certificate(self._bars)
        self._strategy = strategy
        self._strategy_certificate = strategy_certificate
        self._pivot: PivotHistory | None = None
        self._pivot_is_immutable = False
        self._hierarchy: HierarchyHistory | _EncodedHierarchy | None = None
        self._chart: ChartHistory | None = None
        self._chart_base: _ChartBaseHistory | None = None
        self._chart_certificate: ChartAuditCertificate | None = None
        targets = (tuple(range(max(0, len(bars) - 17), len(bars) - 1))
                   if minute_checkpoint_indices is None else tuple(minute_checkpoint_indices))
        if any(index >= len(bars) - 1 for index in targets):
            raise ValueError('minute checkpoints must precede the final daily bar')
        self._minute = _MinuteCheckpointStore(targets)
        if stage_runner is not None and stage_runner_factory is not None:
            raise ValueError('a history request has only one stage runner owner')
        self._runner = stage_runner if stage_runner_factory is None else stage_runner_factory(self._bars)

    def _stage_runner(self, bars: Sequence[Bar], *, rows: int) -> TrendStageRunner | None:
        if (self._runner is None or self._minute.closed or self._minute.thread != get_ident()
                or rows < 256 or len(bars) < 512):
            return None
        try:
            self._require_bars(bars)
            if not self._runner.matches(bars):
                return None
        except (_UncertifiableValue, ValueError, RecursionError):
            return None
        return self._runner

    def close(self) -> None:
        """Release all request-owned checkpoint journals, including on failure."""
        try:
            self._minute.close()
        finally:
            if self._runner is not None:
                self._runner.close()

    def __enter__(self) -> 'BacktestHistoryReuse':
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()

    def minute_input_matches(self, bars: Sequence[Bar], strategy: 'SystemStrategy') -> bool:
        """The partial bar may change OHLCV; every other field stays fixed."""
        try:
            return (_strategy_certificate(strategy) == self._strategy_certificate
                    and self._minute_bars_match(bars))
        except (_UncertifiableValue, RecursionError):
            return False

    def _minute_bars_match(self, bars: Sequence[Bar]) -> bool:
        if not bars or len(bars) > len(self._bars):
            return False
        certificate = _bar_certificate(bars)
        if certificate[:-1] != self._bar_certificate[:len(bars) - 1]:
            return False
        mutable = {'high', 'low', 'close', 'volume'}
        current = tuple(field for field in certificate[-1] if cast(tuple[str, object], field)[0] not in mutable)
        original = tuple(field for field in self._bar_certificate[len(bars) - 1]
                         if cast(tuple[str, object], field)[0] not in mutable)
        return current == original

    def _checkpoint_capture(self, kind: str, bars: Sequence[Bar], options: tuple[bool, ...], *,
                            version: str) -> _CheckpointChannel | None:
        try:
            matches = _bar_certificate(bars) == self._bar_certificate
        except (_UncertifiableValue, RecursionError):
            return None
        if (not matches
                or type(options[0]) is not bool
                or options[0] != self._strategy.n_target_trend_confirmation_enabled
                or ordinary_completion_enabled() != self._strategy.ordinary_trend_completion_enabled):
            return None
        return self._minute.begin(kind, options, version)

    def _checkpoint_seed(self, kind: str, bars: Sequence[Bar], options: tuple[bool, ...]
                         ) -> tuple[_CheckpointChannel, int] | None:
        store = self._minute
        channel = store.channels.get(kind)
        if (store.closed or store.thread != get_ident() or channel is None or channel.closed
                or channel.store is not store or channel.kind != kind or channel.options != options
                or any(type(option) is not bool for option in options)
                or not self.minute_input_matches(bars, self._strategy)):
            store.misses[kind] += 1
            return None
        cutoff = len(bars) - 2
        if cutoff not in channel.sealed:
            store.misses[kind] += 1
            return None
        return channel, cutoff

    def _hierarchy_seed(self, bars: Sequence[Bar], *, options: tuple[bool, ...], version: str) -> dict | None:
        match = self._checkpoint_seed('hierarchy', bars, options)
        if match is None:
            return None
        channel, cutoff = match
        if channel.version != version:
            self._minute.misses['hierarchy'] += 1
            return None
        result = channel.read(cutoff)
        if result is None:
            return None
        rows, previous = result
        history: dict = {}; epochs: dict = {}; declines: dict = {}; structure: dict = {}
        for level, index, value in rows:
            if level == 0:
                epochs[index] = value
                history[index] = {}
                if options[2]: structure[index] = {}
            else:
                points, decline, geometry = cast(tuple[object, object, object], value)
                history[index][level] = points
                if level == 1 and options[1]: declines[index] = decline
                if options[2]: structure[index][level] = geometry
        self._minute.hits['hierarchy'] += 1
        return {'key': (version, tuple(bars[:-1]), options[1], options[0], options[2]),
                'checkpoint': (history, epochs, previous, epochs[cutoff], declines, structure)}

    def _chart_seed(self, bars: Sequence[Bar], *, audit: Sequence[Mapping[str, object]],
                    options: tuple[bool, ...], version: str) -> dict | None:
        match = self._checkpoint_seed('chart', bars, options)
        if match is None:
            return None
        channel, cutoff = match
        if channel.version != version:
            self._minute.misses['chart'] += 1
            return None
        certificate = _past_attack_digest(audit, cutoff)
        if certificate is None or certificate != channel.certificates[cutoff]:
            self._minute.misses['chart'] += 1
            return None
        result = channel.read(cutoff)
        if result is None:
            return None
        rows, states = result
        history: dict = {}; events: list = []; shallow: dict = {}; combined: dict = {}; pullback: dict = {}
        for _, index, value in rows:
            contexts, added, sides = cast(tuple[object, list, tuple], value)
            history[index] = contexts
            events.extend(added)
            for output, side in zip((shallow, combined, pullback), sides, strict=True):
                present, candidate = side
                if present: output[index] = candidate
        attacks = frozenset(_bar_index(row) for row in audit if row.get('event') == 'n_completed'
                            and row.get('direction') == 'up' and _bar_index(row) <= cutoff)
        self._minute.hits['chart'] += 1
        return {'key': (tuple(bars[:-1]), attacks, *options[1:], version, options[0], certificate),
                'checkpoint': (history, events, *cast(tuple, states[0]), shallow, combined, pullback)}

    def require_input(self, bars: Sequence[Bar], strategy: 'SystemStrategy') -> None:
        """Reject changed prices, permissions, factors, prefixes or parameters."""
        self._require_bars(bars)
        if _strategy_certificate(strategy) != self._strategy_certificate:
            raise ValueError('backtest history reuse requires the same complete strategy')

    def _require_bars(self, bars: Sequence[Bar]) -> None:
        if _bar_certificate(bars) != self._bar_certificate:
            raise ValueError('backtest history reuse requires the same complete bars')

    def save_pivot(self, bars: Sequence[Bar], strategy: 'SystemStrategy', history: PivotHistory) -> None:
        """Capture all original daily pivot snapshots, including blocked dates."""
        self.require_input(bars, strategy)
        self._pivot_is_immutable = _immutable_pivot(history)
        self._pivot = _copy_immutable_pivot(history) if self._pivot_is_immutable else deepcopy(history)

    def pivot(self, bars: Sequence[Bar], strategy: 'SystemStrategy') -> PivotHistory | None:
        """Return an isolated complete pivot history if it has been computed."""
        self.require_input(bars, strategy)
        if self._pivot is None:
            return None
        return _copy_immutable_pivot(self._pivot) if self._pivot_is_immutable else deepcopy(self._pivot)

    def save_hierarchy(self, bars: Sequence[Bar], strategy: 'SystemStrategy', history: HierarchyHistory) -> None:
        """Capture main dated levels; decline/structure sinks remain separate."""
        self.require_input(bars, strategy)
        encoded = _encode_hierarchy(history)
        self._hierarchy = encoded if encoded is not None else deepcopy(history)

    def hierarchy(self, bars: Sequence[Bar], *, n_target_trend_confirmation_enabled: bool) -> HierarchyHistory | None:
        """Read main levels using the only strategy option this reducer accepts."""
        self._require_bars(bars)
        if (type(n_target_trend_confirmation_enabled) is not bool
                or n_target_trend_confirmation_enabled != self._strategy.n_target_trend_confirmation_enabled
                or ordinary_completion_enabled() != self._strategy.ordinary_trend_completion_enabled):
            raise ValueError('backtest history reuse requires the same trend confirmation mode')
        return (_decode_hierarchy(self._hierarchy) if isinstance(self._hierarchy, _EncodedHierarchy)
                else deepcopy(self._hierarchy))

    def save_chart(self, bars: Sequence[Bar], strategy: 'SystemStrategy',
                   audit: Sequence[Mapping[str, object]], history: ChartHistory) -> None:
        """Capture main entry contexts with their complete relevant audit proof."""
        self.require_input(bars, strategy)
        self._chart_certificate = _chart_audit(audit)
        self._chart = deepcopy(history) if self._chart_certificate is not None else None

    def chart(self, bars: Sequence[Bar], strategy: 'SystemStrategy',
              audit: Sequence[Mapping[str, object]]) -> ChartHistory | None:
        """Reuse only when N and squeeze evidence matches, including trigger ties."""
        self.require_input(bars, strategy)
        certificate = _chart_audit(audit)
        if self._chart is None or certificate is None or certificate != self._chart_certificate:
            return None
        return deepcopy(self._chart)

    def capture_chart_base(
        self, bars: Sequence[Bar], *, n_target_trend_confirmation_enabled: bool,
        audit: Sequence[Mapping[str, object]], history: Mapping[int, tuple[EntryContext, ...]],
        events: Sequence[Mapping[str, object]], anchors_at_attack: ChartAnchors,
    ) -> None:
        """Capture the original dated geometry before any audit squeeze merge.

        Optional combined/shallow/pullback sidecars are not part of this base.
        EntryContext values and their tuples are immutable; only their owning
        map needs detaching. Events and anchors include mutable proof containers.
        """
        self._require_bars(bars)
        if (type(n_target_trend_confirmation_enabled) is not bool
                or n_target_trend_confirmation_enabled != self._strategy.n_target_trend_confirmation_enabled
                or ordinary_completion_enabled() != self._strategy.ordinary_trend_completion_enabled):
            raise ValueError('backtest history reuse requires the same trend confirmation mode')
        certificate = _chart_audit(audit)
        if certificate is None:
            self._chart_base = None
            return
        self._chart_base = _ChartBaseHistory(
            dict(history), deepcopy([dict(row) for row in events]),
            deepcopy({index: [dict(anchor) for anchor in anchors] for index, anchors in anchors_at_attack.items()}),
            certificate[0],
        )

    def refresh_chart(self, bars: Sequence[Bar], strategy: 'SystemStrategy',
                      audit: Sequence[Mapping[str, object]]) -> ChartHistory | None:
        """Replay revised squeeze evidence over the same complete dated base.

        A changed attack or N proof requires geometry replay. A trigger change
        rebuilds all squeeze confirmations, failures and maturity from the base;
        it never accumulates revised evidence on a previously merged result.
        """
        self.require_input(bars, strategy)
        base = self._chart_base
        certificate = _chart_audit(audit)
        if base is None or certificate is None or certificate[0] != base.attack_certificate:
            return None
        from wavequant.domain.market_structure.squeeze_alternation import squeeze_alternations
        from wavequant.domain.strategies.chart_entry_history import merge_squeeze_history

        additional = squeeze_alternations(bars, audit, deepcopy(base.anchors_at_attack))
        history = merge_squeeze_history(bars, dict(base.history), additional)
        result = history, [*deepcopy(base.events), *additional]
        self.save_chart(bars, strategy, audit, result)
        return deepcopy(result)
