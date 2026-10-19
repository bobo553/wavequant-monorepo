"""Causal direction certificates shared by all trend hierarchy levels."""

from bisect import bisect_right
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import date
from fractions import Fraction
from math import isfinite
from hashlib import sha256
from sys import getsizeof
from types import MappingProxyType
from typing import Literal, cast

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .n_trend_confirmation import NConfirmationContext, _lazy_n_permissions, prepare_n_confirmation_context, qualify_n_trend
from .trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from .trend_prefix_view import _PrefixView, _VisibleReferences
from .trend_evidence_reuse import _geometry_signature


SAME_LEVEL_KEY_BREAK = "strict_same_level_market_key_break"
SOURCE_CYCLE_CONFIRMATION = "source_key_break_alternation_then_market_turn"


@dataclass(frozen=True, slots=True, eq=False)
class _PrefixSessions(Mapping[str, int]):
    """同次只读行情的日期索引，查询只暴露 cutoff 之前最后一个同日位置。"""

    positions: Mapping[str, tuple[int, ...]]
    end_index: int

    def __getitem__(self, key: str) -> int:
        positions = self.positions[key]
        stop = bisect_right(positions, self.end_index)
        if not stop:
            raise KeyError(key)
        return positions[stop - 1]

    def __iter__(self) -> Iterator[str]:
        return (key for key, positions in self.positions.items() if positions[0] <= self.end_index)

    def __len__(self) -> int:
        return sum(positions[0] <= self.end_index for positions in self.positions.values())


def _market_sessions(market: Sequence[Bar]) -> _PrefixSessions:
    positions: dict[str, list[int]] = {}
    for index, bar in enumerate(market):
        positions.setdefault(session_date(bar), []).append(index)
    return _PrefixSessions(MappingProxyType({key: tuple(value) for key, value in positions.items()}), len(market) - 1)


@dataclass(frozen=True)
class _Reference:
    raw: Mapping[str, object]
    index: int
    ordinal: int
    kind: Literal["H", "L"]
    value: Fraction
    available: str | int
    known: int | None
    _proof_signature: bytes | None = field(default=None, repr=False, compare=False)

    @property
    def order(self) -> tuple[int, int]:
        return self.index, self.ordinal


def _reference(
    raw: Mapping[str, object], session_indices: Mapping[str, int] | None, end_index: int,
) -> _Reference | None:
    index, ordinal = raw.get("index"), raw.get("ordinal", 0)
    kind, value, available = raw.get("kind"), raw.get("value"), raw.get("available_at")
    if (type(index) is not int or index < 0 or index > end_index or type(ordinal) is not int or ordinal < 0
            or kind not in ("H", "L") or not isinstance(value, (int, float)) or isinstance(value, bool)
            or not isfinite(value) or value <= 0 or raw.get("display_only") is True
            or raw.get("state", "confirmed") not in ("confirmed", "reversal", "teaching")):
        return None
    known: int | None
    if type(available) is int:
        if available < index or available > end_index:
            return None
        known = available
    elif isinstance(available, str):
        try:
            if date.fromisoformat(available).isoformat() != available:
                return None
        except ValueError:
            return None
        time = raw.get("time")
        if isinstance(time, str) and available < time:
            return None
        if session_indices is None:
            known = None
        else:
            known = session_indices.get(available)
            if known is None or known < index:
                return None
    else:
        return None
    public = MappingProxyType({key: raw[key] for key in
                               ("index", "ordinal", "time", "kind", "value", "available_at", "label") if key in raw})
    return _Reference(public, index, ordinal, "H" if kind == "H" else "L", Fraction(str(value)), available, known)


@dataclass(frozen=True)
class ConfirmationContext:
    """Immutable parsing snapshot reused only within one path publication.

    The supplied source and market prefix must stay unchanged for that call.
    Identity bindings prevent accidentally reusing another path or clock; this
    snapshot is not a cache of qualification results or a cross-day memo.
    """

    source_identity: int
    bars_identity: int
    end_index: int
    bars: Sequence[Bar] | None
    session_indices: Mapping[str, int] | None
    references: Sequence[_Reference | None]
    ordered: bool
    n_source: Sequence[Mapping[str, object]]
    n_context: NConfirmationContext | None
    n_target_trend_confirmation_enabled: bool
    market_index: _MarketPrefixIndex | None = None
    _prefix_visibility: tuple[int, ...] | None = field(default=None, repr=False, compare=False)
    _proof_prefixes: tuple[bytes, ...] | None = field(default=None, repr=False, compare=False)
    _reference_stops: tuple[int, ...] | None = field(default=None, repr=False, compare=False)
    _visible_stop: int | None = field(default=None, repr=False, compare=False)
    _projected: bool = field(default=False, repr=False, compare=False)


class _SuccessfulConfirmations:
    """复用首证书和已经发生的终止事实，不复用尚在等待的交易资格。"""

    def __init__(self) -> None:
        self.entries: dict[bytes, tuple[int, bytes, dict[str, object], int]] = {}
        self.memory_bytes = 0
        self.prefix_entries: OrderedDict[bytes, tuple[dict[str, object] | None, int]] = OrderedDict()
        self.prefix_bytes = 0
        self.rejections: dict[bytes, tuple[int, bytes, int]] = {}
        self.rejection_bytes = 0

    @staticmethod
    def _prefix(context: ConfirmationContext, cutoff: int) -> bytes | None:
        if (context._prefix_visibility is None or context._proof_prefixes is None
                or context._reference_stops is None or cutoff > context.end_index):
            return None
        stop = context._reference_stops[bisect_right(context._prefix_visibility, cutoff)]
        return context._proof_prefixes[stop]

    def rejected(self, key: bytes, context: ConfirmationContext) -> bool:
        saved = self.rejections.get(key)
        return (saved is not None and saved[1] == self._prefix(context, saved[0]))

    def reject(self, key: bytes, context: ConfirmationContext, failed: int) -> None:
        signature = self._prefix(context, failed)
        if signature is None:
            return
        # 失败日之前全部证据相同、且起点/本级关键位相同，未来来源不能在
        # 失败日之前补出首证书。旧证据修订则整段摘要不匹配，重新走原判断。
        added = getsizeof(key) + getsizeof(signature) + 512
        previous = self.rejections.pop(key, None)
        if previous is not None:
            self.rejection_bytes -= previous[2]
        if self.rejection_bytes + added + 2 * getsizeof(self.rejections) <= 2 * 1024 * 1024:
            self.rejections[key] = failed, signature, added
            self.rejection_bytes += added

    def prefix_key(self, key: bytes, context: ConfirmationContext) -> bytes | None:
        if (not context._projected or context._visible_stop is None
                or context._proof_prefixes is None):
            return None
        return _geometry_signature((key, context.end_index, context._proof_prefixes[context._visible_stop]))

    def get_prefix(self, key: bytes) -> tuple[bool, dict[str, object] | None]:
        saved = self.prefix_entries.get(key)
        if saved is None:
            return False, None
        self.prefix_entries.move_to_end(key)
        return True, deepcopy(saved[0])

    def put_prefix(self, key: bytes, proof: dict[str, object] | None) -> None:
        encoded = _geometry_signature(proof)
        if encoded is None:
            return
        added = getsizeof(key) + 16 * len(encoded) + 1024
        previous = self.prefix_entries.pop(key, None)
        if previous is not None:
            self.prefix_bytes -= previous[1]
        # 拒绝仅复用已锁定历史日的相同事实；新日期仍独立判断。
        while self.prefix_entries and self.prefix_bytes + added + 2 * getsizeof(self.prefix_entries) > 8 * 1024 * 1024:
            _, removed = self.prefix_entries.popitem(last=False)
            self.prefix_bytes -= removed[1]
        if self.prefix_bytes + added + 2 * getsizeof(self.prefix_entries) <= 8 * 1024 * 1024:
            self.prefix_entries[key] = deepcopy(proof), added
            self.prefix_bytes += added

    def get(self, key: bytes, context: ConfirmationContext) -> dict[str, object] | None:
        saved = self.entries.get(key)
        if saved is None or context._prefix_visibility is None or context._proof_prefixes is None:
            return None
        cutoff, signature, proof, _ = saved
        stop = bisect_right(context._prefix_visibility, cutoff)
        if context._reference_stops is not None:
            stop = context._reference_stops[stop]
        else:
            while stop and context.references[stop - 1] is None:
                stop -= 1
        if cutoff > context.end_index or context._proof_prefixes[stop] != signature:
            return None
        return deepcopy(proof)

    def put(self, key: bytes, context: ConfirmationContext, proof: dict[str, object]) -> None:
        trigger = proof.get('confirmed_by')
        if (type(trigger) is not dict or type(trigger.get('index')) is not int
                or context._prefix_visibility is None or context._proof_prefixes is None):
            return
        cutoff = cast(int, trigger['index'])
        if not 0 <= cutoff <= context.end_index:
            return
        encoded = _geometry_signature(proof)
        if encoded is None:
            return
        stop = bisect_right(context._prefix_visibility, cutoff)
        if context._reference_stops is not None:
            stop = context._reference_stops[stop]
        else:
            while stop and context.references[stop - 1] is None:
                stop -= 1
        signature = context._proof_prefixes[stop]
        # 小证书的标准容器开销保守预留；容量满时沿用冷计算，不淘汰逐日版本。
        added = getsizeof(key) + getsizeof(signature) + 16 * len(encoded) + 4096
        previous = self.entries.pop(key, None)
        if previous is not None:
            self.memory_bytes -= previous[3]
        if self.memory_bytes + added + 2 * getsizeof(self.entries) + 4096 <= 4 * 1024 * 1024:
            self.entries[key] = cutoff, signature, deepcopy(proof), added
            self.memory_bytes += added


def _scalar_reference(point: _Reference) -> bool:
    return all(type(value) in (str, int, float, bool, type(None))
               and (type(value) is not float or isfinite(value)) for value in point.raw.values())


def _reference_at_cutoff(
    reference: _Reference | None, session_indices: Mapping[str, int] | None, end_index: int,
) -> _Reference | None:
    """复用静态价格解析，但按当前行情前缀重新取得来源点的真实可知位置。

    完整快照的无效点在更短前缀中仍无效；合法点却可能在较短前缀中尚未
    可知。日期时钟还不能直接沿用完整快照的 known：同一个交易日有多根
    行情时，日期映射保留当前前缀的最后一根，而完整快照可能映射到未来
    的同日行情。仅复用 raw／Fraction 等静态值，日期位置必须重新查询。
    """
    if reference is None or reference.index > end_index:
        return None
    if isinstance(reference.available, int):
        return reference if reference.available <= end_index else None
    known = session_indices.get(reference.available) if session_indices is not None else None
    if session_indices is not None and (known is None or known < reference.index):
        return None
    return reference if known == reference.known else replace(reference, known=known)


class _ReferenceParsingCache:
    """同一只读行情上的标量解析缓存；每次仍按当前知识日过滤。

    缓存价格和日期解析，不缓存交易资格。键包含所有解析输入及缺省字段的
    存在性，旧点修改、标签修改、类型变化和可知日变化都会重新解析。
    """

    def __init__(self) -> None:
        self.entries: dict[bytes, tuple[_Reference, int]] = {}
        self.memory_bytes = 0

    def parse(self, raw: Mapping[str, object], sessions: Mapping[str, int], end_index: int) -> _Reference | None:
        if type(raw) is not dict:
            return _reference(raw, sessions, end_index)
        fields = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label', 'state', 'display_only')
        values = {field: raw[field] for field in fields if field in raw}
        if any(type(value) not in (str, int, float, bool, type(None))
               or type(value) is float and not isfinite(value) for value in values.values()):
            return _reference(raw, sessions, end_index)
        # 保留原编码对字段存在性、精确类型、负零及字符串别名的认证。
        key = _geometry_signature(values)
        if key is None:
            return _reference(raw, sessions, end_index)
        saved = self.entries.get(key)
        if saved is not None:
            return _reference_at_cutoff(saved[0], sessions, end_index)
        reference = _reference(raw, sessions, end_index)
        if reference is not None:
            # raw 已只含认证过的不可变标量。同一解析值的证明摘要不会随
            # 行情截止变化；实际可见位置和市场资格仍由当天上下文独立计算。
            signature = _geometry_signature(dict(reference.raw))
            reference = replace(reference, _proof_signature=signature)
            # 保存的 raw 只有不可变标量；保守计入字典、Fraction 和字符串开销。
            added = getsizeof(key) + getsizeof(signature) + 2048 + sum(getsizeof(value) for value in reference.raw.values())
            if self.memory_bytes + added + 2 * getsizeof(self.entries) <= 4 * 1024 * 1024:
                self.entries[key] = reference, added
                self.memory_bytes += added
        return reference


def prepare_confirmation_context(
    source: Sequence[Mapping[str, object]], bars: Sequence[Bar] | None, end_index: int,
    *, n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
    parsed_context: ConfirmationContext | None = None,
    _market_index: _MarketPrefixIndex | None = None,
) -> ConfirmationContext:
    """建立当前截止位置的证书上下文，可复用同次发布的静态解析快照。

    parsed_context 只能来自同一只读 source／bars／N 来源及相同开关，且其
    截止位置不得早于当前 cutoff。它提供已冻结的坐标、日期格式和精确价格
    解析，不提供未来的证书资格。较短 cutoff 仍建立真实行情前缀、日期映射、
    可知来源及 ordered 判定；N 上下文也在这个真实前缀中独立重新计算。
    相同 cutoff 的上下文本来就是同一事实，可直接复用。身份或范围不匹配
    显式抛出 ValueError，不能用另一条路径、另一天或另一种 N 模式补资格。
    """
    if type(end_index) is not int or end_index < 0 or bars is not None and end_index >= len(bars):
        raise ValueError("a confirmation context requires an available market prefix")
    n_points = source if n_source is None else n_source
    if _market_index is not None and not _market_index.matches(bars):
        raise ValueError('a market index must match its immutable Bar prefix')
    if parsed_context is not None:
        if (parsed_context.source_identity != id(source) or parsed_context.bars_identity != id(bars)
                or id(parsed_context.n_source) != id(n_points)
                or parsed_context.n_target_trend_confirmation_enabled != n_target_trend_confirmation_enabled
                or type(parsed_context.end_index) is not int or parsed_context.end_index < end_index
                or bars is not None and parsed_context.end_index >= len(bars)
                or len(parsed_context.references) != len(source)
                or parsed_context.market_index is not _market_index):
            raise ValueError("a parsed confirmation context must match its source and cover the market prefix")
        if parsed_context.end_index == end_index:
            return parsed_context
    # 索引已经认证 exact frozen Bar tuple；每个旧确认日只创建 O(1) 窗口。
    # 公共可变行情仍独立冻结，不能借用调用方之后会改写的列表。
    market: Sequence[Bar] | None = (
        _PrefixView(_market_index.bars, range(end_index + 1)) if _market_index is not None else
        tuple(bars[:end_index + 1]) if bars is not None else None)
    sessions: Mapping[str, int] | None = None
    if market is not None:
        previous_sessions = parsed_context.session_indices if parsed_context is not None else None
        sessions = (_PrefixSessions(_market_index.owner.positions, end_index) if _market_index is not None else
                    _PrefixSessions(previous_sessions.positions, end_index)
                    if isinstance(previous_sessions, _PrefixSessions) else _market_sessions(market))
    visibility = parsed_context._prefix_visibility if parsed_context is not None else None
    proof_prefixes = parsed_context._proof_prefixes if parsed_context is not None else None
    reference_stops = parsed_context._reference_stops if parsed_context is not None else None
    visible_stop: int | None = None
    if visibility is not None:
        assert parsed_context is not None
        # 有序来源的可知位置单调，且索引已证明每日唯一；更短前缀只隐藏尾部。
        # 不重复逐点解析与全链排序验证，重复交易日仍走原逐点日期投影。
        stop = bisect_right(visibility, end_index)
        if reference_stops is not None:
            stop = reference_stops[stop]
        visible_stop = stop
        original_references = parsed_context.references
        if isinstance(original_references, _VisibleReferences):
            # 允许在旧日窗口上继续回退，但不累积逐日包装链。
            stop = min(stop, original_references.stop)
            original_references = original_references.source
        references: Sequence[_Reference | None] = _VisibleReferences(original_references, stop)
        ordered = True
    else:
        parsing: _ReferenceParsingCache | None = None
        if (parsed_context is None and sessions is not None and _market_index is not None
                and _market_index.owner.unique_sessions and not n_target_trend_confirmation_enabled):
            if _market_index.owner.reference_reuse is None:
                _market_index.owner.reference_reuse = _ReferenceParsingCache()
            if type(_market_index.owner.reference_reuse) is _ReferenceParsingCache:
                parsing = _market_index.owner.reference_reuse
        references = (tuple(parsing.parse(point, cast(Mapping[str, int], sessions), end_index) for point in source)
                      if parsing is not None else
                      tuple(_reference(point, sessions, end_index) for point in source) if parsed_context is None else
                      tuple(_reference_at_cutoff(point, sessions, end_index) for point in parsed_context.references))
        known = [point for point in references if point is not None]
        ordered = (not any(left.order >= right.order for left, right in zip(known, known[1:]))
                   and not any(left.known is not None and right.known is not None and left.known > right.known
                               for left, right in zip(known, known[1:])))
        if (ordered and _market_index is not None and _market_index.owner.unique_sessions
                and not n_target_trend_confirmation_enabled):
            positions: list[int] = []
            stops = [0]
            latest = -1
            for position, point in enumerate(references):
                if point is not None:
                    assert point.known is not None
                    latest = max(latest, point.index, point.known)
                positions.append(latest)
                stops.append(position + 1 if point is not None else stops[-1])
            visibility = tuple(positions)
            reference_stops = tuple(stops)
            visible_stop = stops[-1]
            digest = sha256()
            prefixes = [digest.digest()]
            for point in references:
                signature = point._proof_signature if point is not None else None
                if signature is None:
                    if point is not None and not _scalar_reference(point):
                        break
                    signature = _geometry_signature(dict(point.raw) if point is not None else None)
                if signature is None:
                    break
                digest.update(len(signature).to_bytes(8, 'big'))
                digest.update(signature)
                prefixes.append(digest.digest())
            else:
                proof_prefixes = tuple(prefixes)
    n_context = (prepare_n_confirmation_context(n_points, bars, end_index)
                 if bars is not None and n_target_trend_confirmation_enabled and not _lazy_n_permissions() else None)
    return ConfirmationContext(id(source), id(bars), end_index, market, sessions, references, ordered,
                               n_points, n_context, n_target_trend_confirmation_enabled, _market_index,
                               visibility, proof_prefixes, reference_stops, visible_stop,
                               parsed_context is not None)


def _public(point: _Reference) -> dict[str, object]:
    return {key: point.raw[key] for key in ("index", "ordinal", "time", "kind", "value", "available_at", "label")
            if key in point.raw}


def _known_before_price(key: _Reference, point: _Reference) -> bool:
    if key.known is not None:
        return key.known < point.index
    time = point.raw.get("time")
    return isinstance(key.available, str) and isinstance(time, str) and key.available < time


def _bar_reference(bars: Sequence[Bar], index: int, up: bool, integer_clock: bool) -> dict[str, object]:
    bar = bars[index]
    return dict(index=index, ordinal=0, time=session_date(bar), kind="H" if up else "L",
                value=bar.high if up else bar.low, available_at=index if integer_clock else session_date(bar),
                label="当前高点" if up else "当前低点")


def _turn_reference(bars: Sequence[Bar], index: int, up: bool, integer_clock: bool) -> dict[str, object]:
    bar = bars[index]
    return dict(index=index, ordinal=0, time=session_date(bar), kind="K", value=bar.close,
                available_at=index if integer_clock else session_date(bar), label="转多" if up else "转空",
                close=bar.close, market_high=bar.high, market_low=bar.low)


def _certificate(
    origin: _Reference, key: _Reference, confirmed: dict[str, object], up: bool,
    rule: str, *, flip: _Reference | None = None, counter: _Reference | None = None,
    retracement_origin: _Reference | None = None,
) -> dict[str, object]:
    proof: dict[str, object] = dict(
        available_at=confirmed["available_at"], confirmation_rule=rule, direction="up" if up else "down",
        broken_key=_public(key), origin=_public(origin), confirmed_by=confirmed,
    )
    proof["flip_high" if up else "flip_low"] = _public(flip) if flip is not None else confirmed
    proof["alternation_low" if up else "alternation_high"] = _public(counter) if counter is not None else None
    if retracement_origin is not None:
        proof["retracement_origin"] = _public(retracement_origin)
    return proof


def _preceding_reference(
    references: Sequence[_Reference | None], start: int, stop: int, kind: str,
) -> _Reference | None:
    for position in range(stop - 1, start - 1, -1):
        point = references[position]
        if point is not None and point.kind == kind:
            return point
    return None


def first_strict_market_break(
    bars: Sequence[Bar], start: int, stop: int, price: float, *, up: bool,
    market_index: _MarketPrefixIndex | _MarketRangeIndex | None = None,
) -> int | None:
    """方向证书与逐日资格共用严格影线突破；stop 为不含端点的因果边界。"""
    if start >= stop:
        return None
    if market_index is not None:
        return market_index.first_extreme(start, stop, price, high=up)
    if up:
        return next((index for index in range(start, stop) if bars[index].high > price), None)
    return next((index for index in range(start, stop) if bars[index].low < price), None)


def _qualify(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *, up: bool,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    if (type(end_index) is not int or end_index < 0 or type(source_position) is not int
            or not 0 <= source_position < len(source) or bars is not None and end_index >= len(bars)):
        return None
    prepared = context if context is not None else prepare_confirmation_context(
        source, bars, end_index, n_source=n_source,
        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
    )
    if (prepared.source_identity != id(source) or prepared.bars_identity != id(bars)
            or prepared.end_index != end_index or id(prepared.n_source) != id(source if n_source is None else n_source)
            or prepared.n_target_trend_confirmation_enabled != n_target_trend_confirmation_enabled
            or prepared.market_index is not None and not prepared.market_index.matches(bars)):
        raise ValueError("a confirmation context must match its source and market prefix")
    supplied = prepared.references[source_position]
    base = supplied if origin is source[source_position] else _reference(origin, prepared.session_indices, end_index)
    if (base is None or supplied is None or base.kind != ("L" if up else "H")
            or (base.order, base.value) != (supplied.order, supplied.value)):
        return None
    references = prepared.references
    if not prepared.ordered:
        return None
    key_kind = "H" if up else "L"
    sign = 1 if up else -1
    own_key = _reference(same_level_key, prepared.session_indices, end_index) if same_level_key is not None else None
    if own_key is not None and (own_key.kind != key_kind or own_key.order >= base.order
                                or sign * (own_key.value - base.value) <= 0):
        own_key = None
    reuse: _SuccessfulConfirmations | None = None
    reuse_key: bytes | None = None
    prefix_key: bytes | None = None
    if (prepared.market_index is not None and prepared._proof_prefixes is not None
            and not n_target_trend_confirmation_enabled and _scalar_reference(base)
            and (own_key is None or _scalar_reference(own_key))):
        owner = prepared.market_index.owner
        if owner.confirmation_reuse is None:
            owner.confirmation_reuse = _SuccessfulConfirmations()
        if type(owner.confirmation_reuse) is _SuccessfulConfirmations:
            reuse = owner.confirmation_reuse
            reuse_key = _geometry_signature((up, source_position, dict(base.raw),
                                             dict(own_key.raw) if own_key is not None else None))
            if reuse_key is not None:
                prefix_key = reuse.prefix_key(reuse_key, prepared)
                if prefix_key is not None:
                    found, prefix_proof = reuse.get_prefix(prefix_key)
                    if found:
                        return prefix_proof
                if reuse.rejected(reuse_key, prepared):
                    return None
                cached = reuse.get(reuse_key, prepared)
                if cached is not None:
                    return cached

    def first_proof(proofs: list[tuple[int, dict[str, object]]]) -> dict[str, object] | None:
        proof = min(proofs, key=lambda item: item[0])[1] if proofs else None
        if proof is not None and reuse is not None and reuse_key is not None:
            reuse.put(reuse_key, prepared, proof)
        elif (proof is None and reuse is not None and reuse_key is not None
              and failed <= end_index):
            reuse.reject(reuse_key, prepared, failed)
        if reuse is not None and prefix_key is not None:
            reuse.put_prefix(prefix_key, proof)
        return proof
    market = prepared.bars
    if market is None:
        if own_key is None:
            return None
        for point in references[source_position + 1:]:
            if point is None:
                continue
            if point.kind == base.kind and sign * (point.value - base.value) < 0:
                return None
            if (point.kind == key_kind and sign * (point.value - own_key.value) > 0
                    and _known_before_price(own_key, point)):
                return _certificate(base, own_key, _public(point), up, SAME_LEVEL_KEY_BREAK, flip=point)
        return None
    assert base.known is not None
    base_price = float(base.value)
    market_index = prepared.market_index
    failed_at = first_strict_market_break(market, base.index + 1, end_index + 1,
                                          base_price, up=not up, market_index=market_index)
    failed: int = end_index + 1 if failed_at is None else failed_at
    proofs: list[tuple[int, dict[str, object]]] = []
    if own_key is not None:
        assert own_key.known is not None
        own_price = float(own_key.value)
        own_start, own_stop = max(base.index + 1, base.known, own_key.known + 1), min(failed, end_index + 1)
        own_break = first_strict_market_break(market, own_start, own_stop, own_price,
                                             up=up, market_index=market_index)
        if own_break is not None:
            confirmed = _bar_reference(market, own_break, up, isinstance(base.available, int))
            proofs.append((own_break, _certificate(base, own_key, confirmed, up, SAME_LEVEL_KEY_BREAK)))
    n_proof = (qualify_n_trend(prepared.n_source, prepared.n_source[source_position], source_position,
                              bars, end_index, up=up, context=prepared.n_context)
               if n_target_trend_confirmation_enabled else None)
    if n_proof is not None:
        trigger = n_proof['confirmed_by']
        assert isinstance(trigger, dict) and isinstance(trigger['index'], int)
        proofs.append((trigger['index'], n_proof))
    frozen = _preceding_reference(references, 0, source_position, key_kind)
    if frozen is None or sign * (frozen.value - base.value) <= 0:
        return first_proof(proofs)
    flips: list[tuple[int, _Reference, _Reference]] = []
    strongest: _Reference | None = None
    causal_limit = min(failed, min((proof[0] for proof in proofs), default=end_index + 1))
    for position in range(source_position + 1, prepared._visible_stop if prepared._visible_stop is not None else len(source)):
        point = references[position]
        # Known times are monotone; these cycles cannot precede failure or the first own-key proof.
        if point is not None and point.known is not None and point.known >= causal_limit:
            break
        if (point is None or point.kind != key_kind or sign * (point.value - frozen.value) <= 0
                or not _known_before_price(frozen, point)
                or strongest is not None and sign * (point.value - strongest.value) <= 0):
            continue
        retracement_origin = ((_preceding_reference(references, source_position, position, base.kind) or base)
                              if strongest is not None else base)
        strongest = point
        flips.append((position, point, retracement_origin))
    for cycle, (position, flip, retracement_origin) in enumerate(flips):
        counter = references[position + 1] if position + 1 < len(references) else None
        if counter is None or counter.kind != base.kind or not flip.order < counter.order:
            continue
        impulse = sign * (flip.value - retracement_origin.value)
        retracement = sign * (flip.value - counter.value)
        if impulse <= 0 or not 0 < retracement < impulse or retracement / impulse >= Fraction(2, 3):
            continue
        assert flip.known is not None and counter.known is not None and retracement_origin.known is not None
        start = max(base.known, flip.known, counter.known, retracement_origin.known) + 1
        next_known = flips[cycle + 1][1].known if cycle + 1 < len(flips) else end_index + 1
        assert next_known is not None
        limit = min(failed, next_known, end_index + 1)
        counter_price, flip_price = float(counter.value), float(flip.value)
        cycle_indices: Sequence[int]
        if market_index is not None:
            if counter.index + 1 >= limit:
                continue
            counter_failure = market_index.first_extreme(counter.index + 1, limit, counter_price, high=not up)
            cycle_stop = min(limit, counter_failure) if counter_failure is not None else limit
            cycle_start = max(counter.index + 1, start)
            crossing = (market_index.first_close_turn(cycle_start, cycle_stop, flip_price, up=up)
                        if cycle_start < cycle_stop else None)
            cycle_indices = [crossing] if crossing is not None else []
        else:
            cycle_indices = range(counter.index + 1, limit)
        for index in cycle_indices:
            adverse = market[index].low if up else market[index].high
            if sign * (adverse - counter_price) < 0:
                break
            if index < start:
                continue
            previous, closing = market[index - 1].close, market[index].close
            if sign * (previous - flip_price) <= 0 and sign * (closing - flip_price) > 0:
                confirmed = _turn_reference(market, index, up, isinstance(base.available, int))
                proof = _certificate(base, frozen, confirmed, up, SOURCE_CYCLE_CONFIRMATION,
                                     flip=flip, counter=counter, retracement_origin=retracement_origin)
                proof.update(retracement_ratio=float(retracement / impulse),
                             source_break_available_at=flip.available, alternation_available_at=counter.available)
                proofs.append((index, proof))
                # Later cycles start after this cycle's exclusive next-known boundary.
                return first_proof(proofs)
    return first_proof(proofs)


def qualify_uptrend(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    """Certify the first own-key break, source cycle or strict N target break.

    Raw hierarchy geometry does not supply permission. Higher source attacks
    may retry a failed pullback with a fresh retracement origin, while the
    original defense and source key remain frozen. Later failures cannot erase
    an earlier certificate. Without bars only a confirmed source own-key break
    can qualify; date-clock inputs must already be a known source prefix.
    """
    return _qualify(source, origin, source_position, same_level_key, bars, end_index, up=True,
                    context=context, n_source=n_source,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)


def qualify_downtrend(
    source: Sequence[Mapping[str, object]], origin: Mapping[str, object], source_position: int,
    same_level_key: Mapping[str, object] | None, bars: Sequence[Bar] | None, end_index: int, *,
    context: ConfirmationContext | None = None,
    n_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False,
) -> dict[str, object] | None:
    """Mirror the same three certificates for a downward direction."""
    return _qualify(source, origin, source_position, same_level_key, bars, end_index, up=False,
                    context=context, n_source=n_source,
                    n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
