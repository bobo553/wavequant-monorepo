"""高低点决定趋势线；行情证书提供独立的策略资格视图。

三个级别只改变来源层级和显示名称，单层结构判断始终调用 judge_trend。
不允许资格视图、N 附加证书或显示尾端反过来筛选下一层的结构来源。
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from bisect import bisect_left, bisect_right
from copy import deepcopy
from contextlib import nullcontext
from dataclasses import dataclass
from math import isfinite
from io import BytesIO
import pickle
import json
from sys import getsizeof
from uuid import uuid4
from typing import Protocol, TypedDict, cast

from ..models.model import Bar
from .hierarchical_confirmation import session_date
from .lecture_trend import _annotate
from .n_trend_reversals import n_target_reversals
from .n_trend_confirmation import _NPermissionProofs, _reuse_n_permissions
from .trend_judgement import TrendJudgement, _TrendJudgementState, _owned_mutable_points, judge_trend, mutable_points
from .trend_judgement import _OwnedPointAdmission, _SourceVersionOwner
from .trend_judgement import _FrozenPoint
from .trend_readonly_cache import _ReadonlySnapshotCache
from .trend_evidence_reuse import _geometry_signature
from .trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from .trend_body_reversal import with_price_bodies
from .trend_confirmation import _PrefixSessions
from .trend_judgement import _with_formal_key_breaks
from .trend_levels import formal_key_attack_points
from .trend_levels import Moment, _completed_source_point, _later_moment, _strip_source_license, completed_source_points
from .trend_landmarks import (
    bear_bull_alternation_lows, bear_to_bull_highs, bullish_turn_signals, bearish_turn_signals, post_alternation_bull_highs,
)
from .trend_publication import confirmed_trend_legs, publish_uptrends
from .trend_structure import _trend_level
from .trend_options import ordinary_completion_enabled

STRUCTURAL_LEG_POLICY = 'high_low_point_frozen_wave_key_v119'
STRUCTURAL_RULE = 'point_first_recursive_frozen_wave_key_v119'
_NAMES = {1: '一级', 2: '二级', 3: '三级'}
_KINDS = {1: 'reversal', 2: 'secondary', 3: 'tertiary'}
_GEOMETRY_ANNOTATION_FIELDS = (
    'label', 'trend_level', 'source_level', 'trend', 'window_trend', 'reversal',
    'observations', 'levels', 'preceding_turn', 'lifecycle_context',
)
_ANNOTATION_REFERENCE_FIELDS = frozenset(('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label'))


class _FiniteEvidenceCertificates:
    """有界地认证标准有限证据，不将 JSON 表示作为类型身份或公开输出。"""

    def __init__(self) -> None:
        self._points: set[bytes] = set()
        self._bytes = 0

    def certify(self, value: object, signature: bytes | None = None) -> bool:
        signature = _geometry_signature(value) if signature is None else signature
        if signature is None:
            return False
        if signature in self._points:
            return True
        # 必须先 exact builtin 认证，C JSON 不接触未知对象或其序列化钩子。
        try:
            json.dumps(value, allow_nan=False)
        except (TypeError, ValueError):
            return False
        added = len(signature) + 128
        if self._bytes + added <= 8 * 1024 * 1024:
            self._points.add(signature)
            self._bytes += added
        return True

    def known_points(self, source: Sequence[Mapping[str, object]]) -> bool:
        # 调用方已认证完整 source，才可以逐点建立稳定证书。
        return all(self.certify(point) for point in source)


class _TrendJudgementCache:
    """单次阶段流每条路径仅保留最近来源及其不可变判定，不保存逐日末态。"""

    def __init__(self, *, tail_reuse: bool = False, state_memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(state_memory_bytes) is not int or state_memory_bytes <= 0:
            raise ValueError('state_memory_bytes must be a positive integer')
        self._paths: dict[tuple[int, str], tuple[bytes, TrendJudgement]] = {}
        self._states: dict[tuple[int, str], _TrendJudgementState] = {}
        self._complete_cutoffs: dict[tuple[int, str], int] = {}
        self._finite = _FiniteEvidenceCertificates()
        self._tail_reuse = tail_reuse
        self._ordinary_completion = ordinary_completion_enabled()
        self._state_memory_bytes = state_memory_bytes
        self._source_owners: dict[tuple[int, str], _SourceVersionOwner] = {}
        self._version_results: dict[tuple[int, str], tuple[object, TrendJudgement]] = {}
        self._point_exports = _ReadonlySnapshotCache[dict[str, object]](
            memory_bytes=16 * 1024 * 1024, max_entries=4096, evict=False)

    def export_owned(self, points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        """内部标注只改点外壳；完整冻结证明按内容解码一次，只读借用。

        公共 mutable_points 仍逐次独立导出，未知对象不进入共享边界。
        拒绝超额新项，避免顺序历史扫描造成 LRU 全量抖动。
        """
        result: list[dict[str, object]] = []
        for point in points:
            if type(point) is not _FrozenPoint or point.json_template is None:
                result.extend(mutable_points((point,)))
                continue
            template = point.json_template
            cached = self._point_exports.get(template, None)
            if cached is None:
                exported = mutable_points((point,))[0]
                self._point_exports.put(template, None, exported)
            else:
                exported = cached[0]
            result.append(dict(exported))
        return result

    def remember_source_flip(self, trend_level: int, path: str) -> None:
        """下级翻向首次可知时保存本级状态，不截断仍被关键位引用的前史。"""
        state = self._states.get((trend_level, path))
        if state is not None:
            state.remember_source_flip()

    def judge(self, source: Sequence[Mapping[str, object]], *, path: str, trend_level: int,
              asof: Moment | None = None, _owned_source: bool = False) -> TrendJudgement:
        if self._ordinary_completion != ordinary_completion_enabled():
            # 同一实例切换规则时连来源版本、增量状态和翻向检查点一起失效。
            self.__init__(tail_reuse=self._tail_reuse, state_memory_bytes=self._state_memory_bytes)
        _trend_level(trend_level)
        if _owned_source and asof is None:
            key = trend_level, path
            owner = self._source_owners.get(key)
            if owner is None:
                owner = self._source_owners[key] = _SourceVersionOwner(trend_level, self._state_memory_bytes)
            try:
                version = owner.prepare(source)
                if version is not None:
                    previous_version = self._version_results.get(key)
                    if previous_version is not None and previous_version[0] is version.token:
                        return previous_version[1]
                    state = self._states.get(key)
                    if state is None:
                        state = self._states[key] = _TrendJudgementState(trend_level,
                            _tail_reuse=self._tail_reuse, memory_bytes=self._state_memory_bytes)
                    ordinary, merged, events = state.calculate(version.source, version.positions,
                        _validated_turns=version.turns, _certified_standard=True, _source_revision=version)
                    result = _with_formal_key_breaks(source, state.finalize(ordinary, merged, events, version.positions))
                    self._version_results[key] = version.token, result
                    self._paths.pop(key, None)
                    self._complete_cutoffs.pop(key, None)
                    return result
            except Exception:
                self._source_owners.pop(key, None)
                self._version_results.pop(key, None)
                self._states.pop(key, None)
                self._paths.pop(key, None)
                self._complete_cutoffs.pop(key, None)
                raise
        self._source_owners.pop((trend_level, path), None)
        self._version_results.pop((trend_level, path), None)
        # 原判断会物化全部来源；忽略点上的未知审计值也必须保留其旧复制行为。
        # 先认证整个容器，不能在未知 Mapping／state 上额外调用 get 或相等比较。
        key = trend_level, path
        source_signature = _geometry_signature(source) if type(source) in (list, tuple) else None
        previous = self._paths.get(key)
        complete_signature = b'\x00' + source_signature if source_signature is not None else None
        # 全部来源上次已通过验证，且全文类型、字段顺序和别名都未改变时，
        # 无截止筛选的判断就是同一事实；不重复编码每个嵌套证明。
        complete_cutoff = self._complete_cutoffs.get(key)
        if ((asof is None or type(asof) is int and complete_cutoff is not None and asof >= complete_cutoff)
                and complete_signature is not None and previous is not None
                and previous[0] == complete_signature):
            return previous[1]
        if source_signature is None or not self._finite.known_points(source):
            self._paths.pop(key, None)
            self._states.pop(key, None)
            self._complete_cutoffs.pop(key, None)
            return judge_trend(source, trend_level=trend_level, asof=asof)
        visible = tuple((position, point) for position, point in enumerate(source)
                        if point.get('state') not in ('seed', 'developing') and point.get('display_only') is not True)
        # 即使 seed/developing 不参与判定，原位置和层级校验也不能被缓存隐藏。
        cacheable = all('trend_level' not in point or type(point['trend_level']) is int
                         and point['trend_level'] == trend_level - 1 for point in source)
        if asof is not None:
            # 历史适配器只在全部参与来源及真实结束知识已到达后复用同一判定。
            # 尚有迟知点时，较晚 asof 会改变可见集合，必须走原冷判定及校验。
            cacheable = (cacheable and type(asof) is int and asof >= 0 and all(
                type(point.get('available_at')) is int and cast(int, point['available_at']) <= asof
                and ('wave_completed_at' not in point or type(point['wave_completed_at']) is int
                     and point['wave_completed_at'] <= asof) for _, point in visible))
        signature = None
        if cacheable:
            if len(visible) == len(source):
                signature = complete_signature
            else:
                visible_signature = _geometry_signature(visible)
                signature = b'\x01' + visible_signature if visible_signature is not None else None
        if signature is not None and previous is not None and previous[0] == signature:
            return previous[1]
        if signature is not None:
            state = self._states.get(key)
            if state is None:
                state = _TrendJudgementState(trend_level, _tail_reuse=self._tail_reuse,
                                            memory_bytes=self._state_memory_bytes)
                self._states[key] = state
            try:
                if _owned_source:
                    judgement = judge_trend(source, trend_level=trend_level, asof=asof,
                                            _append_state=state, _owned_source=True, _certified_standard=True)
                else:
                    judgement = judge_trend(source, trend_level=trend_level, asof=asof,
                                            _append_state=state, _certified_standard=True)
            except Exception:
                self._states.pop(key, None)
                self._paths.pop(key, None)
                raise
        else:
            self._states.pop(key, None)
            self._paths.pop(key, None)
            judgement = judge_trend(source, trend_level=trend_level, asof=asof)
        if signature is not None:
            self._paths[key] = signature, judgement
            clocks = [point.get(field, 0) for _, point in visible
                      for field in ('available_at', 'wave_completed_at')]
            if len(visible) == len(source) and all(type(clock) is int for clock in clocks):
                self._complete_cutoffs[key] = max(cast(list[int], clocks), default=0)
            else:
                self._complete_cutoffs.pop(key, None)
        else:
            self._paths.pop(key, None)
            self._complete_cutoffs.pop(key, None)
        return judgement


@dataclass(frozen=True, slots=True)
class _PreparedPathReference:
    owner: str
    index: int


@dataclass(slots=True)
class _ClosedPreparedPath:
    signature: bytes
    cutoff: int
    source_stroke: int
    candidate: int
    stroke: int | None
    developing: int | None


class _ClosedPreparedPaths:
    """阶段流内共享已闭合证据；历史行只保存其首次闭合后才有的引用。

    同请求共享有界的只读字节模板，读取各日期时独立物化证据容器。旧行
    在闭合之前仍保存自己的证据，不能被后来的闭合模板替换。
    """

    _fields = ('source_strokes', 'candidate_strokes', 'structure_strokes', 'strokes', 'developing_strokes')

    def __init__(self, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('closed path memory_bytes must be a positive integer')
        self._owner = uuid4().hex
        self._memory_bytes = memory_bytes
        self._memory_used = 0
        self._paths: dict[tuple[int, str, str], _ClosedPreparedPath] = {}
        self._objects: list[bytes] = []
        self._references: dict[int, _PreparedPathReference] = {}
        self._pending: list[dict[str, object]] = []
        self._finite = _FiniteEvidenceCertificates()
        self._decoded = _ReadonlySnapshotCache[dict[str, object]](
            memory_bytes=8 * 1024 * 1024, max_entries=2048, evict=False)

    def begin_row(self) -> None:
        self._references.clear()
        self._pending.clear()

    def _restore(self, index: int, *, borrow: bool = False) -> dict[str, object]:
        # 公共读取独立解码；私有阶段只读借用已认证的不可变版本。
        cached = self._decoded.get(index, None) if borrow else None
        value: object = cached[0] if cached is not None else pickle.loads(self._objects[index])
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise ValueError('a closed path template requires a mapping')
        result = cast(dict[str, object], value)
        if borrow and cached is None:
            self._decoded.put(index, None, result)
        self._references[id(result)] = _PreparedPathReference(self._owner, index)
        self._pending.append(result)
        return result

    def restore(self, saved: _ClosedPreparedPath, *, borrow: bool = False) -> tuple[
        dict[str, object], dict[str, object], dict[str, object] | None, dict[str, object] | None,
    ]:
        return (self._restore(saved.source_stroke, borrow=borrow), self._restore(saved.candidate, borrow=borrow),
                self._restore(saved.stroke, borrow=borrow) if saved.stroke is not None else None,
                self._restore(saved.developing, borrow=borrow) if saved.developing is not None else None)

    def get(self, level: int, path: str, source: Sequence[Mapping[str, object]],
            cutoff: int | None, *, symbol: str = 'STRUCTURE') -> _ClosedPreparedPath | None:
        previous = self._paths.get((level, path, symbol))
        if previous is None or previous.cutoff != cutoff:
            return None
        signature = _geometry_signature(source)
        return previous if (signature is not None and previous.signature == signature
                            and self._finite.known_points(source)) else None

    def put(self, level: int, path: str, source: Sequence[Mapping[str, object]], cutoff: int,
            source_stroke: dict[str, object], candidate: dict[str, object],
            stroke: dict[str, object] | None, developing: dict[str, object] | None,
            *, symbol: str = 'STRUCTURE') -> None:
        # 每路径只intern首次闭合版本；后来真实修订独立写spool，不累计版本。
        if (level, path, symbol) in self._paths:
            return
        signature = _geometry_signature(source)
        if signature is None or not self._finite.known_points(source):
            return
        items = (source_stroke, candidate, stroke, developing)
        encoded = [_geometry_signature(item) if item is not None else None for item in items]
        if any(item is not None and value is None for item, value in zip(items, encoded)):
            return
        if any(item is not None and not self._finite.certify(item, value)
               for item, value in zip(items, encoded)):
            return
        added = len(signature) + sum(len(value) + 256 for value in encoded if value is not None)
        if self._memory_used + added > self._memory_bytes:
            return
        indices: list[int | None] = []
        for item, value in zip(items, encoded):
            if item is None or value is None:
                indices.append(None)
                continue
            index = len(self._objects)
            self._objects.append(value)
            self._references[id(item)] = _PreparedPathReference(self._owner, index)
            self._pending.append(item)
            indices.append(index)
        saved = _ClosedPreparedPath(signature, cutoff, cast(int, indices[0]), cast(int, indices[1]),
                                    indices[2], indices[3])
        self._paths[(level, path, symbol)] = saved
        self._memory_used += added

    def encode(self, prepared: dict[str, object]) -> dict[str, object]:
        result = dict(prepared)
        for field in self._fields:
            strokes = cast(list[dict[str, object]], prepared[field])
            result[field] = [self._references.get(id(stroke), stroke) for stroke in strokes]
        self.begin_row()
        return result

    def decode(self, value: object) -> dict[str, object]:
        if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
            raise ValueError('a prepared stage requires a mapping')
        result = dict(cast(Mapping[str, object], value))
        for field in self._fields:
            rows = result.get(field)
            if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
                raise ValueError('prepared path references require a sequence')
            restored: list[Mapping[str, object]] = []
            for row in rows:
                if isinstance(row, _PreparedPathReference):
                    if row.owner != self._owner or type(row.index) is not int or not 0 <= row.index < len(self._objects):
                        raise ValueError('prepared path references must belong to this stage')
                    restored.append(self._restore(row.index))
                elif isinstance(row, Mapping) and all(isinstance(key, str) for key in row):
                    restored.append(cast(Mapping[str, object], row))
                else:
                    raise ValueError('a prepared stage requires path mappings')
            result[field] = restored
        self.begin_row()
        return result


@dataclass(slots=True)
class _ClosedPermissionEntry:
    signature: bytes
    market_end: int
    enabled: bool
    payload: bytes
    immutable: tuple[object, ...]
    memory_bytes: int
    n_demand: bool | None = None


def _immutable_copy_leaf(value: object) -> bool:
    pending = [value]
    while pending:
        item = pending.pop()
        if type(item) in (str, int, float, bool, bytes, complex, type(None)):
            continue
        if type(item) is not tuple:
            return False
        pending.extend(cast(tuple[object, ...], item))
    return True


class _PermissionTemplateWriter(pickle.Pickler):
    """只对已经白名单认证的 payload 建模板；保留 deepcopy 的不可变叶子。"""

    def __init__(self, stream: BytesIO) -> None:
        super().__init__(stream, protocol=5)
        self.immutable: list[object] = []
        self.positions: dict[int, int] = {}

    def persistent_id(self, value: object) -> int | None:
        position = self.positions.get(id(value))
        if position is not None:
            return position
        if not _immutable_copy_leaf(value):
            return None
        position = len(self.immutable)
        self.immutable.append(value)
        self.positions[id(value)] = position
        return position


class _PermissionTemplateReader(pickle.Unpickler):
    def __init__(self, payload: bytes, immutable: tuple[object, ...]) -> None:
        super().__init__(BytesIO(payload))
        self.immutable = immutable

    def persistent_load(self, reference: object) -> object:
        if type(reference) is not int or not 0 <= reference < len(self.immutable):
            raise ValueError('permission template references must belong to their own evidence')
        return self.immutable[reference]


def _immutable_template_bytes(values: tuple[object, ...]) -> int:
    pending: list[object] = [values]
    seen: set[int] = set()
    size = 0
    while pending:
        value = pending.pop()
        if id(value) in seen:
            continue
        seen.add(id(value))
        size += getsizeof(value)
        if type(value) is tuple:
            pending.extend(cast(tuple[object, ...], value))
    return size


@dataclass(frozen=True, slots=True)
class _VerifiedMarketPrefix:
    owner: '_TrendPermissionCache'
    length: int


class _TrendPermissionCache:
    """一次历史回放只复用已关闭路径的相同证据，公开许可仍独立复制。"""

    def __init__(self, bars: Sequence[Bar], *, memory_bytes: int = 16 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('permission memory_bytes must be a positive integer')
        self._bars = tuple(bars)
        self._market_index = _MarketRangeIndex.build(self._bars)
        self._paths: dict[tuple[int, str], _ClosedPermissionEntry] = {}
        self._finite = _FiniteEvidenceCertificates()
        self._memory_bytes = memory_bytes
        self._payload_bytes = 0
        self._active_candidates: dict[int, _TrendCandidateCache] = {}
        self._n_permissions: dict[tuple[int, str], _NPermissionProofs] = {}
        self._annotations = _ReadonlySnapshotCache[list[dict[str, object]]](
            memory_bytes=8 * 1024 * 1024, max_entries=256)

    def n_permissions(self, level: int, path: str) -> _NPermissionProofs:
        key = level, path
        if key not in self._n_permissions:
            self._n_permissions[key] = _NPermissionProofs()
        return self._n_permissions[key]

    def candidates(self, level: int) -> '_TrendCandidateCache':
        """每级只保存开放路径的当前只读候选版本，不缓存每日行情结论。"""
        if level not in self._active_candidates:
            self._active_candidates[level] = _TrendCandidateCache()
        return self._active_candidates[level]

    def _discard(self, key: tuple[int, str]) -> None:
        previous = self._paths.pop(key, None)
        if previous is not None:
            self._payload_bytes -= previous.memory_bytes

    def _source_signature(self, source: Sequence[Mapping[str, object]], candidates: Sequence[Mapping[str, object]],
                          qualified_source: Sequence[Mapping[str, object]] | None) -> bytes | None:
        if (type(source) not in (list, tuple) or type(candidates) not in (list, tuple)
                or qualified_source is not None and type(qualified_source) not in (list, tuple)):
            return None
        rows = (list(source), list(candidates), list(qualified_source) if qualified_source is not None else None)
        signature = _geometry_signature(rows)
        if signature is None or any(not self._finite.known_points(points)
                                    for points in rows if points is not None):
            return None
        return signature

    def covers(self, bars: Sequence[Bar]) -> bool:
        return len(bars) <= len(self._bars) and all(bar is self._bars[index] for index, bar in enumerate(bars))

    def prefix(self, bars: Sequence[Bar]) -> _VerifiedMarketPrefix | None:
        return _VerifiedMarketPrefix(self, len(bars)) if self.covers(bars) else None

    def get(
        self, key: tuple[int, str], source: Sequence[Mapping[str, object]],
        candidates: Sequence[Mapping[str, object]], qualified_source: Sequence[Mapping[str, object]] | None,
        prefix: _VerifiedMarketPrefix, market_end: int, enabled: bool, *, n_demand: bool | None = None,
    ) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]] | None:
        previous = self._paths.get(key)
        if (prefix.owner is not self or not 0 <= market_end <= prefix.length <= len(self._bars)
                or previous is None or previous.enabled != enabled or previous.market_end != market_end
                or previous.n_demand != n_demand):
            return None
        signature = self._source_signature(source, candidates, qualified_source)
        if signature is None or signature != previous.signature:
            return None
        # C 解码独立物化容器；不可变叶子重放原引用，保留原 deepcopy 的
        # 标量／不可变 tuple 身份，而非普通 pickle 的新叶子对象。
        restored: object = _PermissionTemplateReader(previous.payload, previous.immutable).load()
        if not isinstance(restored, tuple) or len(restored) != 4:
            raise ValueError('closed permission evidence requires four sequences')
        return cast(tuple[list[dict[str, object]], list[dict[str, object]],
                          list[dict[str, object]], list[dict[str, object]]], restored)

    def put(
        self, key: tuple[int, str], source: Sequence[Mapping[str, object]],
        candidates: Sequence[Mapping[str, object]], qualified_source: Sequence[Mapping[str, object]] | None,
        prefix: _VerifiedMarketPrefix, market_end: int, enabled: bool,
        payload: tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]],
        *, n_demand: bool | None = None,
    ) -> None:
        if prefix.owner is not self or not 0 <= market_end <= prefix.length <= len(self._bars):
            return
        signature = self._source_signature(source, candidates, qualified_source)
        if signature is None:
            self._discard(key)
            return
        payload_template = _geometry_signature(payload)
        if payload_template is None or not self._finite.certify(payload, payload_template):
            self._discard(key)
            return
        self._discard(key)
        if (type(key) is not tuple or len(key) != 2 or type(key[0]) is not int
                or type(key[1]) is not str):
            return
        stream = BytesIO()
        writer = _PermissionTemplateWriter(stream)
        writer.dump(payload)
        encoded = stream.getvalue()
        immutable = tuple(writer.immutable)
        added = (getsizeof(signature) + getsizeof(encoded) + getsizeof(key)
                 + sum(getsizeof(item) for item in key) + _immutable_template_bytes(immutable) + 256)
        # 上界包含 dict 扩容前的保守余量；超限只丢缓存，资格仍走原发布器。
        if self._payload_bytes + added + 2 * getsizeof(self._paths) + 1024 > self._memory_bytes:
            return
        self._paths[key] = _ClosedPermissionEntry(signature, market_end, enabled, encoded, immutable, added, n_demand)
        self._payload_bytes += added


class _DefinitionAnnotator(Protocol):
    def __call__(self, points: list[dict[str, object]], symbol: str, dates: Mapping[object, int],
                 *, trend_level: int) -> None: ...


class _OwnedCandidateOptions(TypedDict, total=False):
    _owned_candidates: bool
    _candidate_cache: '_TrendCandidateCache'


def high_low_points_from_strokes(
    strokes: object, *, include_key_break_attacks: bool = False,
) -> list[dict[str, object]]:
    """将分段中的高低点展开，显式保留路径断点；不读取私有候选或显示尾部。

    source_path 只用来保持输入路径的边界，不参与价格判别。每层的正式
    geometry strokes 的已结束点通过共同门禁成为下一层首参数。严格升级的
    开放端点默认不投影；include_key_break_attacks 供结构递归保留已证明的正式
    突破端点，让 judge 的严格路线独立判断。普通闭合投影及交易来源仍使用
    真实整波结束日，发展尾线始终排除；几何首次可知与结束时钟各自保留。
    """
    if not isinstance(strokes, Sequence) or isinstance(strokes, (str, bytes)):
        raise ValueError('trend strokes must be a sequence')
    result: list[dict[str, object]] = []
    for stroke in strokes:
        if not isinstance(stroke, Mapping) or not isinstance(stroke.get('id'), str):
            raise ValueError('trend strokes require a path id')
        if stroke.get('display_only') is True:
            continue
        points = stroke.get('points')
        if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
            raise ValueError('trend stroke points must be a sequence')
        path_points: list[dict[str, object]] = []
        for point in points:
            if not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point):
                raise ValueError('trend points must be mappings with string keys')
            path_points.append(dict(cast(Mapping[str, object], point), source_path=stroke['id']))
        if path_points:
            level = path_points[0].get('trend_level', 0)
            if type(level) is not int or level not in (0, 1, 2, 3):
                raise ValueError('recursive strokes must belong to a direct source level')
            # 原始高低折线仍需 seed 和最后邻点来提取中间真正峰谷；它不是
            # 已生成的趋势线，不能在三点几何投影之前丢掉这些边界邻点。
            # 一级及以上才有开放升级端点，需要和直接 judge 共用结束门禁。
            project = formal_key_attack_points if include_key_break_attacks else completed_source_points
            result.extend(path_points if level == 0 else project(path_points, source_level=level))
    return result


def polyline_high_low_points(drawing: Mapping[str, object]) -> list[dict[str, object]]:
    """基础折线只在入场时规范化一次，之后各级直接读取前一级输出。

    母子 K 线绘图端点的 kind 不一定是局部峰谷，故以左右相邻价格确定真实
    高低点。等值保留较早位置；seed/developing 没有确认反转，不作为来源。
    此处不判断多空、不升级趋势、不读取行情证书。
    """
    raw = high_low_points_from_strokes(drawing.get('strokes'))
    groups = _source_groups(raw)
    paths = list(groups)
    issues = drawing.get('issues', ())
    if not isinstance(issues, Sequence) or isinstance(issues, (str, bytes)):
        raise ValueError('drawing issues must be a sequence')
    issue_indices: list[int] = []
    for issue in issues:
        index = issue.get('index') if isinstance(issue, Mapping) else None
        if type(index) is not int or index < 0:
            raise ValueError('drawing issues require nonnegative indices')
        issue_indices.append(index)
    issue_indices.sort()
    result: list[dict[str, object]] = []
    for position, (path, points) in enumerate(groups.items()):
        first_index = points[0]['index']
        if type(first_index) is not int or first_index < 0:
            raise ValueError('polyline paths require a nonnegative origin index')
        # lecture ID 记录重启 epoch 的真实起点；无 seed 的教学端点可能稍晚。
        # 普通带 seed 的路径优先使用它自己的原始坐标，不用首个峰谷作起点。
        suffix = path.removeprefix('lecture-')
        origin = (int(suffix) if points[0].get('state') != 'seed' and path.startswith('lecture-')
                  and suffix.isdecimal() else first_index)
        caps: list[int] = []
        for point in points:
            if 'source_path_end_index' in point:
                cap = point['source_path_end_index']
                if type(cap) is not int or cap < 0:
                    raise ValueError('source_path_end_index must be a nonnegative index')
                caps.append(cap)
        issue_position = bisect_right(issue_indices, origin)
        if issue_position < len(issue_indices):
            caps.append(issue_indices[issue_position])
        turns = polyline_turns(points, source_path=path)
        if position+1 < len(paths):
            # 下一路径的 seed 可能比它的首个反转更早，甚至尚无反转。不能因
            # 去掉非峰谷端点而丢失原断点，让旧路径借用新路径的 K 线资格。
            next_seed = groups[paths[position+1]][0]['index']
            if type(next_seed) is not int or next_seed < 0:
                raise ValueError('polyline paths require a nonnegative seed index')
            caps.append(next_seed)
        # 多个空 epoch 没有可展开的后一条 stroke，仍须在首次 issue 关闭旧路径。
        # 显式 cap、下一 raw seed 与首次断路共同取最早值，保留首40而非再重启41。
        if caps:
            cutoff = min(caps)
            turns = [dict(point, source_path_end_index=cutoff) for point in turns]
        result.extend(turns)
    return result


def _polyline_turn(
    left: Mapping[str, object], point: Mapping[str, object], right: Mapping[str, object], *, source_path: str,
) -> dict[str, object] | None:
    if point.get('state') in ('seed', 'developing') or point.get('display_only') is True:
        return None
    value, before, after = (cast(float, item['value']) for item in (point, left, right))
    kind = 'H' if value > max(before, after) else 'L' if value < min(before, after) else None
    if kind is None:
        return None
    return dict(point, kind=kind, source_kind=point['kind'], source_state=point.get('state'),
                state='reversal', trend_level=0, source_path=source_path)


def polyline_turns(points: Sequence[Mapping[str, object]], *, source_path: str) -> list[dict[str, object]]:
    """图表与逐日历史共用的折线峰谷适配，不在历史入口另写一套判别。

    邻价峰谷只负责把绘图端点转成零级高低点；源的可知日保持原值，历史
    调用方可按首次实际观察日冻结时钟。等值点不会凭空制造正／负反转。
    """
    counts = Counter(str(point['time']) for point in points)
    ranks: Counter[str] = Counter()
    compact: list[dict[str, object]] = []
    for point in points:
        time = str(point['time'])
        projected = dict(point, projection_count=counts[time], projection_rank=ranks[time])
        ranks[time] += 1
        if not compact or projected['value'] != compact[-1]['value']:
            compact.append(projected)
    result: list[dict[str, object]] = []
    for left, point, right in zip(compact, compact[1:], compact[2:]):
        turn = _polyline_turn(left, point, right, source_path=source_path)
        if turn is not None:
            result.append(turn)
    return result


class _OwnedPolylineTurns:
    """讲义写入者专用尾部投影；已发布前缀不可原地修改。

    尾点所在的整个日期组可能改变 projection_count，最后一个保留峰谷的
    右邻也可能变化。因此从旧尾日期组开始重投影，并重算前一个邻点，
    不以固定 K 线根数截断，也不改变等价保早、三点峰谷和确认状态规则。
    """

    def __init__(self) -> None:
        self._path: str | None = None
        self._length = 0
        self._tail_start = 0
        self._anchor: Mapping[str, object] | None = None
        self._positions: list[int] = []
        self._compact: list[dict[str, object]] = []
        self._turn_positions: list[int] = []
        self._turns: list[dict[str, object]] = []

    def project(self, points: Sequence[Mapping[str, object]], *, source_path: str) -> list[dict[str, object]]:
        retained = (self._path == source_path and len(points) >= self._length
            and (self._length < 2 or points[self._length - 2] is self._anchor))
        start = self._tail_start if retained else 0
        keep = bisect_left(self._positions, start) if retained else 0
        del self._positions[keep:]
        del self._compact[keep:]
        # 原最后保留点的右邻改变；它之前的转折保持原对象与知识日期。
        affected = self._positions[-1] if self._positions else 0
        turn_keep = bisect_left(self._turn_positions, affected) if retained else 0
        del self._turn_positions[turn_keep:]
        del self._turns[turn_keep:]
        suffix = points[start:]
        counts = Counter(str(point['time']) for point in suffix)
        ranks: Counter[str] = Counter()
        for position, point in enumerate(suffix, start=start):
            time = str(point['time'])
            projected = dict(point, projection_count=counts[time], projection_rank=ranks[time])
            ranks[time] += 1
            if not self._compact or projected['value'] != self._compact[-1]['value']:
                self._positions.append(position)
                self._compact.append(projected)
        for position in range(max(1, keep - 1), len(self._compact) - 1):
            left, point, right = self._compact[position-1:position+2]
            turn = _polyline_turn(left, point, right, source_path=source_path)
            if turn is not None:
                self._turn_positions.append(self._positions[position])
                self._turns.append(turn)
        self._path, self._length = source_path, len(points)
        self._anchor = points[-2] if len(points) > 1 else None
        tail_start = len(points) - 1
        while tail_start > 0 and str(points[tail_start-1]['time']) == str(points[-1]['time']):
            tail_start -= 1
        self._tail_start = max(0, tail_start)
        return list(self._turns)


def _source_groups(points: Sequence[Mapping[str, object]]) -> dict[str, list[dict[str, object]]]:
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        raise ValueError('high_low_points must be a sequence of mappings')
    groups: dict[str, list[dict[str, object]]] = {}
    for point in points:
        if not isinstance(point, Mapping):
            raise ValueError('high_low_points must contain mappings')
        path = point.get('source_path', 'path-0')
        if not isinstance(path, str) or not path:
            raise ValueError('source_path must be a nonempty string')
        groups.setdefault(path, []).append(dict(point))
    return groups


def _definition_dates(points: Sequence[Mapping[str, object]]) -> dict[object, int]:
    """只由来源坐标建立展示用时钟，避免 K 线是否齐全改变结构或确认日期。

    整数/数字字符串保持其原索引。日期输入以来源的实际价格索引映射，
    稀疏来源中没有价格顶点的知识日只需要保持因果顺序和不早于价格位置。
    原 available_at 始终保留，绝不把这个展示适配索引写回结构证据。
    """
    result: dict[object, int] = {}
    ordered = sorted((str(point['time']), cast(int, point['index'])) for point in points)
    times = [time for time, _ in ordered]
    prefix_indices: list[int] = []
    for _, index in ordered:
        prefix_indices.append(max(index, prefix_indices[-1] if prefix_indices else index))
    for point in points:
        moment = point['available_at']
        if type(moment) is int:
            result[moment] = moment
        elif isinstance(moment, str) and moment.isdecimal():
            result[moment] = int(moment)
        elif isinstance(moment, str):
            position = bisect_right(times, moment) - 1
            result[moment] = max(cast(int, point['index']), prefix_indices[position] if position >= 0 else 0)
        else:
            raise ValueError('a confirmed point requires a knowledge index or date')
    return result


def _restore_annotation_references(
    value: object, originals: Mapping[tuple[int, int], Mapping[str, object]],
    *, labels: Mapping[tuple[int, int], str] | None = None,
) -> object:
    """只恢复派生字段中完整点引用的自身可知日，不改命名事件的上下文日期。"""
    if type(value) in (str, int, float, bool, bytes, type(None)):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise ValueError('annotation fields must use string keys')
        fields = cast(Mapping[str, object], value)
        result = {key: _restore_annotation_references(child, originals, labels=labels)
                  for key, child in fields.items()}
        # 旧 _ref 的完整七字段结构才是点引用。观察事件还有 title/basis 等
        # 字段，即使也带坐标，仍必须保留生成事件时累计成立的上下文日期。
        if fields.keys() == _ANNOTATION_REFERENCE_FIELDS:
            index, ordinal = fields['index'], fields['ordinal']
            if type(index) is int and type(ordinal) is int:
                original = originals.get((index, ordinal))
                if original is not None and all(fields[field] == original[field] for field in
                        ('time', 'kind', 'value')):
                    result['available_at'] = original['available_at']
                    if labels is not None:
                        identity = index, ordinal
                        result['label'] = labels[identity] if identity in labels else original['label']
        return result
    if isinstance(value, list):
        return [_restore_annotation_references(child, originals, labels=labels) for child in value]
    if isinstance(value, tuple):
        return tuple(_restore_annotation_references(child, originals, labels=labels) for child in value)
    return value


def _annotate_geometry(
    points: list[dict[str, object]], symbol: str, dates: Mapping[object, int], *, trend_level: int,
    _reference_originals: Mapping[tuple[int, int], Mapping[str, object]] | None = None,
    _reference_labels: Mapping[tuple[int, int], str] | None = None,
) -> None:
    """为几何附加定义注释，原事实时钟与完整上下文时钟各自保留。

    按价格顺序选出的节点可能先有较晚可知的 H，再有较早可知的 L。
    它们各自的日期仍是真实事实；但依赖左侧全部节点的趋势和翻向观察，
    只能在此前节点均已可知后成立。旧标注器要求单调知识日，因此仅把
    最小坐标投影的 available_at 临时设为前缀最大值，不修改原节点。

    临时投影只额外携带本级严格突破三点证明，不携带 source_confirmation、
    definition_evidence 或整份祖先账本。只白名单回写派生字段；完整
    _ref 内恢复对应几何节点自己的日期，观察顶层保留累计日期。它既不能
    倒填事件，也不能把累计上下文日期冒充下一层来源的自身可知日。
    坐标顺序、交替种类及非零腿仍由原标注器严格校验，不放宽其公共契约。
    """
    shadows: list[dict[str, object]] = []
    originals: dict[tuple[int, int], Mapping[str, object]] = {}
    context_known: Moment | None = None
    for point in points:
        own_known = cast(Moment, point['available_at'])
        context_known = own_known if context_known is None else _later_moment(context_known, own_known)
        shadow = dict({field: point[field] for field in ('index', 'ordinal', 'time', 'kind', 'value')},
                      available_at=context_known)
        # 严格突破是独立背景证据，只传本级固定深度的三点证明；不携带
        # 祖先确认链或把未来结束证据注入标注的当时知识。
        if isinstance(point.get('upgrade_evidence'), Mapping):
            shadow.update({field: point[field] for field in (
                'upgrade_evidence', 'confirmation_rule', 'wave_identity_state', 'wave_completion_policy',
                'wave_direction_before', 'wave_direction_after',
            ) if field in point})
        shadows.append(shadow)
        originals[(cast(int, point['index']), cast(int, point['ordinal']))] = point
    annotate = cast(_DefinitionAnnotator, _annotate)
    annotate(shadows, symbol, dates, trend_level=trend_level)
    # 必须等整个影子序列通过原校验才回写，失败时原几何和所有证明保持原样。
    for point, shadow in zip(points, shadows):
        for field in _GEOMETRY_ANNOTATION_FIELDS:
            if field in shadow:
                point[field] = _restore_annotation_references(shadow[field],
                    originals if _reference_originals is None else _reference_originals,
                    labels=_reference_labels)


def _permission_leg(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    """许可账本中的邻点只有真实交替、严格价格腿才共享命名上下文。"""
    return ((cast(int, left['index']), cast(int, left['ordinal'])) <
            (cast(int, right['index']), cast(int, right['ordinal']))
            and left['kind'] != right['kind']
            and (cast(float, right['value']) > cast(float, left['value']) if right['kind'] == 'H'
                 else cast(float, right['value']) < cast(float, left['value'])))


def _legacy_pending_bull_n(
    geometry: Sequence[Mapping[str, object]], bars: Sequence[Bar], *, trend_level: int,
) -> bool:
    """旧整数历史只投影已判定本级证据，不用下级趋势或裸 flip 标签授权 N。

    复用共同的冻结关键位标注和六态事实校验；不重新 judge，不生成几何点。
    真实翻多后到交替/反向/失效前才申请新证明，旧证明由独立缓存继续投影。
    """
    from .trend_lifecycle import _clock, _facts, _records

    _trend_level(trend_level)
    if not bars or not geometry:
        return False
    now = len(bars) - 1
    dates = {session_date(bar): index for index, bar in enumerate(bars)}
    available = [dict(point) for point in geometry
                 if (known := _clock(point.get('available_at'), dates)) is not None and known <= now]
    if not available:
        return False
    annotation_dates: dict[object, int] = dict(dates)
    annotation_dates.update({index: index for index in range(len(bars))})
    annotation_dates.update({str(index): index for index in range(len(bars))})
    _annotate_geometry(available, bars[0].symbol, annotation_dates, trend_level=trend_level)
    events = [(fact.known, fact.point.index, fact.point.ordinal, fact.title)
              for fact in _facts(available, dates, now)
              if fact.title in ('翻空为多', '翻多为空', '空多交替')]
    for point in available:
        for observation in _records(point.get('observations')):
            known = _clock(observation.get('available_at'), dates)
            if observation.get('title') == '翻多观察失效' and known is not None and known <= now:
                events.append((known, cast(int, point['index']), cast(int, point['ordinal']), '翻多观察失效'))
    return bool(events) and sorted(events)[-1][3] == '翻空为多'


def _permission_origin(point: Mapping[str, object], dates: Mapping[object, int]) -> dict[str, object] | None:
    """仅消费本次 incoming 证明的完整真实起点，不借旧 active 压力波的起点。"""
    proof = point.get('incoming_trend_confirmation')
    if point.get('incoming_trend_state') != 'confirmed' or not isinstance(proof, Mapping):
        return None
    origin = proof.get('wave_origin', proof.get('origin'))
    if not isinstance(origin, Mapping) or not _ANNOTATION_REFERENCE_FIELDS.issubset(origin):
        return None
    index, ordinal, price = origin['index'], origin['ordinal'], origin['value']
    if (type(index) is not int or index < 0 or type(ordinal) is not int or ordinal < 0
            or type(price) not in (int, float) or not isfinite(cast(float, price)) or cast(float, price) <= 0
            or origin['kind'] not in ('H', 'L') or not isinstance(origin['time'], str) or not origin['time']
            or not isinstance(origin['label'], str) or not origin['label']):
        return None
    knowns = (origin['available_at'], proof.get('available_at'), point['available_at'])
    indices: list[int] = []
    for known in knowns:
        if not (type(known) is int or isinstance(known, str) and bool(known)):
            return None
        known_index = (int(known) if isinstance(known, str) and known.isdecimal()
                       else dates.get(known))
        if known_index is None or type(known_index) is not int or known_index < 0:
            return None
        indices.append(known_index)
    candidate = {field: origin[field] for field in _ANNOTATION_REFERENCE_FIELDS}
    if (not index <= indices[0] <= indices[1] <= indices[2]
            or proof.get('direction') != ('up' if point['kind'] == 'H' else 'down')
            or not _permission_leg(candidate, point)):
        return None
    return candidate


def _annotate_permissions(
    points: list[dict[str, object]], symbol: str, dates: Mapping[object, int], *, trend_level: int,
    _cache: _ReadonlySnapshotCache[list[dict[str, object]]] | None = None,
    _cache_key: tuple[int, str] | None = None,
) -> None:
    """按合法波段标注策略许可，不能把独立压力点强行拼成一条几何折线。

    发布器可以保留旧压力 H，同时发布从另一真实 H 开始的下跌许可 L，
    两个公开节点之间并不存在下跌价格腿。连续合法段沿用原命名状态；
    不合法连接切断状态，只有完整且当时已知的 incoming 起点可作为本波
    影子上下文。证明不足时从单点开始，不跨断点制造翻向或交替事件。
    影子起点不加入公开许可数组，也不创造新交易资格。

    标注投影仅含坐标和事实日期。原节点、所有证书及自己的可知日保持
    原样；观察用累计上下文日期，嵌套点引用恢复自己的日期。整条路径的
    H/L 编号连续，局部段不会重复命名；清理缺失的派生字段，防止继承的
    preceding_turn 把旧压力伪装成新波起点。源几何仍由严格入口单独校验。
    """
    runs: list[tuple[list[dict[str, object]], dict[str, object] | None]] = []
    labels: dict[tuple[int, int], str] = {}
    counts: Counter[str] = Counter()
    for point in points:
        identity = cast(int, point['index']), cast(int, point['ordinal'])
        kind = cast(str, point['kind'])
        counts[kind] += 1
        labels[identity] = f'{kind}{counts[kind]}'
        if runs and _permission_leg(runs[-1][0][-1], point):
            runs[-1][0].append(point)
        else:
            origin = _permission_origin(point, dates) if runs else None
            runs.append(([point], origin))
    signature: bytes | None = None
    if _cache is not None and _cache_key is not None:
        # 标注只依赖七个点字段、分段边界、全路径编号及这些知识日的映射；
        # incoming 证明仍在上面的起点门禁逐日核验，不缓存任何市场许可证。
        inputs = [[{field: point[field] for field in _ANNOTATION_REFERENCE_FIELDS if field in point}
                   for point in [*([origin] if origin is not None else []), *run]] for run, origin in runs]
        standard = all(type(value) in (str, int, float, bool, type(None))
            and (type(value) is not float or isfinite(value))
            for run in inputs for point in run for value in point.values())
        if standard:
            clocks = [[dates.get(point.get('available_at')) for point in run] for run in inputs]
            signature = _geometry_signature((symbol, trend_level, inputs, clocks, labels,
                                              [origin is not None for _, origin in runs]))
        cached = _cache.get(_cache_key, signature) if signature is not None else None
        if cached is not None:
            for point, fields in zip(points, cached[0], strict=True):
                for field in _GEOMETRY_ANNOTATION_FIELDS:
                    if field in fields:
                        point[field] = fields[field]
                    else:
                        point.pop(field, None)
            return
    derived: list[tuple[dict[str, object], dict[str, object]]] = []
    context_dates = dict(dates)
    for run, origin in runs:
        contexts = [*([origin] if origin is not None else []), *run]
        # 同一坐标可以有公开几何事实与稍晚成立的本波起点证明。引用属于
        # 本段真实上下文，不能被别段同坐标的早日期覆盖；标签仍用全路径表。
        originals: dict[tuple[int, int], Mapping[str, object]] = {
            (cast(int, point['index']), cast(int, point['ordinal'])): point for point in contexts}
        shadows = [{field: point[field] for field in ('index', 'ordinal', 'time', 'kind', 'value', 'available_at')}
                   for point in contexts]
        for shadow in shadows:
            known = shadow['available_at']
            if isinstance(known, str) and known.isdecimal():
                context_dates[known] = int(known)
        _annotate_geometry(shadows, symbol, context_dates, trend_level=trend_level,
            _reference_originals=originals, _reference_labels=labels)
        for point, shadow in zip(run, shadows[1:] if origin is not None else shadows):
            identity = cast(int, point['index']), cast(int, point['ordinal'])
            local_label, label = cast(str, shadow['label']), labels[identity]
            # 几何标注已经按本段真实来源和全路径编号恢复引用，无需再递归
            # 复制一遍同一批派生字段。完整证书不在此白名单中。
            fields = {field: shadow[field] for field in _GEOMETRY_ANNOTATION_FIELDS if field in shadow}
            fields['label'] = label
            # levels 的名称不是点引用，单独修正本点前缀；转换依据名称不变。
            fields['levels'] = [dict(level, name=label + cast(str, level['name'])[len(local_label):])
                if cast(str, level['name']).startswith(local_label + ' 的') else dict(level)
                for level in cast(Sequence[Mapping[str, object]], fields['levels'])]
            derived.append((point, fields))
    # 所有段校验完毕才写回，绝不把许可证字段或未知扩展字段随 dict delta 带入。
    for point, fields in derived:
        for field in _GEOMETRY_ANNOTATION_FIELDS:
            if field in fields:
                point[field] = fields[field]
            else:
                point.pop(field, None)
    if _cache is not None and _cache_key is not None and signature is not None:
        _cache.put(_cache_key, signature, [fields for _, fields in derived])


def _qualification_candidates(
    candidates: Sequence[Mapping[str, object]], owned: bool,
) -> list[dict[str, object]]:
    """私有调用方可移交标准工作副本；公共入口仍独立复制所有证据。

    资格层只改顶层字段，发布器也创建自己的顶层字典。移交后调用方不再
    修改或公开原工作集，因而可借用嵌套证明。tuple 的公开复制会转为 list，
    未知值还可能有 deepcopy 钩子，这些输入必须保持原来的完整复制边界。
    """
    return _owned_mutable_points(candidates, owned)


def _prepare_qualification_candidates(
    candidates: Sequence[Mapping[str, object]], *, owned: bool, _certified_standard: bool = False,
) -> list[dict[str, object]]:
    # 完整账本可在同一坐标保留早升级与晚普通反转。旧资格发布器消费的是
    # 结构节点，不能让晚证明把早已知关键位变成晚知，或重复追加同一节点。
    # 独立事实仍完整保存在 judge.candidates／candidate_strokes 中。
    earliest: dict[tuple[int, int], dict[str, object]] = {}
    completions: dict[tuple[int, int], dict[str, object]] = {}
    working = ([dict(point) for point in candidates] if _certified_standard else
               _qualification_candidates(candidates, owned))
    for point in working:
        # 行情和 N 只核验本级已有波的策略资格，不能授予 pending 方向事实
        # 整波身份，否则资格递归会把同一条下级波重新恢复为上级假波。
        if point.get('wave_identity_state') == 'direction_only':
            continue
        identity = cast(int, point['index']), cast(int, point.get('ordinal', 0))
        if 'wave_completed_at' in point:
            # 只核验、收集真实整波结束事实，不用结束证明替换较早策略候选。
            # 同坐标 earlystrict18 与 lateordinary23 的完整账本是唯一来源。
            _completed_source_point(point)
            previous_completion = completions.get(identity)
            if previous_completion is None or _later_moment(cast(Moment, point['wave_completed_at']),
                    cast(Moment, previous_completion['wave_completed_at'])) == previous_completion['wave_completed_at']:
                completions[identity] = point
        previous = earliest.get(identity)
        if previous is None or (_later_moment(cast(Moment, point['available_at']),
                cast(Moment, previous['available_at'])) == previous['available_at']
                and point['available_at'] != previous['available_at']):
            earliest[identity] = point
    qualified = [earliest[key] for key in sorted(earliest)]
    for point in qualified:
        if 'wave_completion_policy' not in point:
            continue
        # publication 可把 available_at 改为许可证真正可知日27；此前 geometry18
        # 与实际整波结束23必须分别保存，不能拿晚许可证日期反过来否定结构。
        point.setdefault('source_geometry_available_at', point['available_at'])
        completion = completions.get((cast(int, point['index']), cast(int, point.get('ordinal', 0))))
        if completion is not None:
            for field in ('wave_completion_policy', 'wave_completed_at', 'wave_completion_evidence'):
                point[field] = completion[field] if _certified_standard else deepcopy(completion[field])
    return qualified


class _TrendCandidateCache:
    """只复用结构候选整理，行情许可继续按原前缀逐日独立核验。"""

    def __init__(self) -> None:
        self._source: Sequence[Mapping[str, object]] | None = None
        self._prepared: list[dict[str, object]] = []
        self._admission = _OwnedPointAdmission()

    def prepare(self, candidates: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
        if candidates is self._source:
            return self._prepared
        # 候选账本是只读版本；逐点认证同时完成借用准入，避免随后再次对
        # 整个历史账本做 pickle 和 JSON 认证。未知值仍走公共复制边界。
        standard = type(candidates) in (list, tuple) and all(self._admission.certify(point) for point in candidates)
        prepared = _prepare_qualification_candidates(candidates, owned=False, _certified_standard=standard)
        if standard:
            self._source, self._prepared = candidates, prepared
        else:
            self._source, self._prepared = None, []
        return prepared


def qualify_trend_structure(
    candidates: Sequence[Mapping[str, object]], source: Sequence[Mapping[str, object]], bars: Sequence[Bar],
    *, trend_level: int, qualified_source: Sequence[Mapping[str, object]] | None = None,
    n_target_trend_confirmation_enabled: bool = False, n_target_sink: list[dict[str, object]] | None = None,
    _market_index: _MarketPrefixIndex | None = None,
    _owned_candidates: bool = False,
    _candidate_cache: _TrendCandidateCache | None = None,
) -> list[dict[str, object]]:
    """原发布器只建立策略许可，返回值不能再成为趋势线或下一层的输入。

    N 的行情达标证明只追加到这个独立视图。每份许可证仍用本级真实来源、
    知识日及行情前缀重新核验，下级证书不授予上级交易资格。
    """
    source_level = _trend_level(trend_level)
    if not bars:
        return []
    owned = _owned_candidates and not n_target_trend_confirmation_enabled
    qualified = (_candidate_cache.prepare(candidates) if owned and _candidate_cache is not None else
                 _prepare_qualification_candidates(candidates, owned=owned))
    if n_target_trend_confirmation_enabled:
        qualified = n_target_reversals(qualified, source, bars, source_level=source_level,
            qualified_source=qualified_source, target_sink=n_target_sink, n_target_trend_confirmation_enabled=True)
    if _market_index is None:
        published = publish_uptrends(qualified, source, bars, source_level=source_level,
            qualified_source=qualified_source, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled)
    else:
        published = publish_uptrends(qualified, source, bars, source_level=source_level,
            qualified_source=qualified_source, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
            _market_index=_market_index)
    sessions: Mapping[str, int] = (_PrefixSessions(_market_index.owner.positions, len(bars) - 1)
        if _market_index is not None else {session_date(bar): i for i, bar in enumerate(bars)})
    result: list[dict[str, object]] = []
    for point in published:
        known = point['available_at']
        known_index = (known if type(known) is int else int(known) if isinstance(known, str) and known.isdecimal()
                       else sessions.get(cast(str, known), -1))
        # 旧发布器可能保留尚无许可证的压力 H。它的几何归属不受影响，但
        # 超过本路径行情截止的未知压力不能进入策略视图或借下一路径日历。
        if 0 <= cast(int, point['index']) <= known_index < len(bars):
            result.append(point)
    return result


def _structural_legs(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [dict(points=[dict(left), dict(right)], direction='up' if left['kind'] == 'L' else 'down',
                 available_at=right['available_at'], basis=STRUCTURAL_RULE)
            for left, right in zip(points, points[1:])]


def _structural_tail(source: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                     *, path: str, trend_level: int) -> dict[str, object] | None:
    """已知下级极值延伸当前结构尾部，未闭合端点只供显示，不能递归升级。

    各级完全共用同一个极值选择，不等待行情证书。相等极值取早；两个端点
    都已经可知后才显示。源的许可证清除，display_only在路径及端点上明确标记。
    """
    if not points:
        return None
    anchor = points[-1]
    kind = 'H' if anchor['kind'] == 'L' else 'L'
    sign = 1 if kind == 'H' else -1
    eligible = [point for point in source if point.get('state') not in ('seed', 'developing')
        and point.get('display_only') is not True and point['kind'] == kind
        and (cast(int, point['index']), cast(int, point.get('ordinal', 0))) >
            (cast(int, anchor['index']), cast(int, anchor.get('ordinal', 0)))
        and sign * (cast(float, point['value']) - cast(float, anchor['value'])) > 0]
    if not eligible:
        return None
    extreme = eligible[0]
    for point in eligible[1:]:
        if sign * (cast(float, point['value']) - cast(float, extreme['value'])) > 0:
            extreme = point
    endpoint = dict(_strip_source_license(extreme), trend_level=trend_level, source_level=trend_level-1,
        state='developing', display_only=True, label=f'{kind}·发展', source_reversal_available_at=extreme['available_at'],
        available_at=_later_moment(cast(Moment, anchor['available_at']), cast(Moment, extreme['available_at'])))
    return dict(id=f'{_KINDS[trend_level]}-developing-{path}', source_path=path,
        kind=f'{_KINDS[trend_level]}-developing', trend_level=trend_level, source_level=trend_level-1,
        state='candidate', display_only=True, points=[dict(anchor), endpoint],
        confirmed_anchor=dict(anchor), wave_direction='up' if kind == 'H' else 'down',
        source_confirmed_count=1, pending_source_count=0, confirmation_policy=STRUCTURAL_RULE,
        leg_confirmation_policy=STRUCTURAL_LEG_POLICY, confirmed_legs=[])


def prepare_trend_lines(
    high_low_points: Sequence[Mapping[str, object]], *, trend_level: int = 1, symbol: str = 'STRUCTURE',
    bars: Sequence[Bar] = (),
    _judgement_cache: _TrendJudgementCache | None = None,
    _closed_paths: _ClosedPreparedPaths | None = None,
    _owned_source: bool = False,
) -> dict[str, object]:
    """完成本级全部路径的结构判断，结果可直接供下一级准备阶段读取。

    一级来源是全部基础折线高低点，二、三级只读取已经完成的紧邻下级
    strokes。各路径统一调用 judge_trend；六定义标注、候选账本和显示尾部
    都读取这些点。bars仅为来源点附上当日实体，供下级翻空后的影线反弹
    分支使用；没有对应实体证据时仍按原高低价定义，不猜测实体或交易许可。
    调用方可依次准备完整一级、二级、三级，再按同样顺序附加各级资格。

    来源、正式点和候选账本各自拥有独立副本，调用者的嵌套证据不会被
    标注修改。返回的几何数组此后按只读契约复用；资格阶段只复制路径
    外壳，不再次复制全部几何。私有路径截止表保存原始 seed 断点，最后
    一条路径没有截止时用 None 表示开放尾部，绝不依赖尚未传入的行情。
    私有 _owned_source 仅用于阶段流移交其独立恢复的工作集；移交后该
    工作集只能读取，公共可变导出仍在导出边界逐点独立物化。
    """
    source_level = _trend_level(trend_level)
    groups = _source_groups(with_price_bodies(high_low_points, bars) if bars else high_low_points)
    strokes: list[dict[str, object]] = []
    developing_strokes: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    source_strokes: list[dict[str, object]] = []
    path_market_end_indices: dict[str, int | None] = {}
    kind, name = _KINDS[trend_level], _NAMES[trend_level]
    paths = list(groups)
    if _closed_paths is not None:
        _closed_paths.begin_row()
    for path_position, (path, raw_source) in enumerate(groups.items()):
        next_path = paths[path_position+1] if path_position+1 < len(paths) else None
        cutoff = raw_source[0].get('source_path_end_index',
            groups[next_path][0]['index'] if next_path is not None else None)
        if ((cutoff is None and 'source_path_end_index' in raw_source[0])
                or (cutoff is not None and (type(cutoff) is not int or cutoff < 0))):
            raise ValueError('source_path_end_index must be a nonnegative index')
        path_market_end_indices[path] = cutoff
        cached_path = (_closed_paths.get(trend_level, path, raw_source, cutoff, symbol=symbol)
                       if _closed_paths is not None else None)
        if cached_path is not None and _closed_paths is not None:
            cached_source, cached_candidate, cached_stroke, cached_developing = _closed_paths.restore(
                cached_path, borrow=_owned_source)
            source_strokes.append(cached_source)
            candidates.append(cached_candidate)
            if cached_stroke is not None:
                strokes.append(cached_stroke)
            if cached_developing is not None:
                developing_strokes.append(cached_developing)
            continue
        # 历史阶段可移交其独立恢复的私有来源；其他调用保持公共深复制。
        source = (raw_source if _owned_source and _judgement_cache is not None else
                  _owned_mutable_points(raw_source, _owned_source))
        source_stroke: dict[str, object] = dict(id=path, points=source)
        source_strokes.append(source_stroke)
        if _owned_source:
            judgement = (judge_trend(source, trend_level=trend_level, _owned_source=True) if _judgement_cache is None else
                         _judgement_cache.judge(source, path=path, trend_level=trend_level, _owned_source=True))
        else:
            judgement = (judge_trend(source, trend_level=trend_level) if _judgement_cache is None else
                         _judgement_cache.judge(source, path=path, trend_level=trend_level))
        points = (_judgement_cache.export_owned(judgement.points)
            if _owned_source and _judgement_cache is not None else mutable_points(judgement.points))
        events = (_judgement_cache.export_owned(judgement.candidates)
            if _owned_source and _judgement_cache is not None else mutable_points(judgement.candidates))
        identifier = f'{kind}-{path}'
        candidate: dict[str, object] = dict(id=identifier, source_path=path, trend_level=trend_level, points=events)
        candidates.append(candidate)
        # 即使后一条路径尚无可升级点，也保留来源携带的原始断点；资格
        # 阶段只能借用断点之前的行情，不能以筛选后路径的首个反转替代 seed。
        if not points:
            if _closed_paths is not None and cutoff is not None:
                _closed_paths.put(trend_level, path, raw_source, cutoff, source_stroke, candidate, None, None,
                                  symbol=symbol)
            continue
        definition_source = [point for point in source if point.get('state') not in ('seed', 'developing')
                             and point.get('display_only') is not True]
        # 股票名只标识公共定义的结构上下文；确认日始终由来源点映射，
        # 不能因为资格阶段的真实行情日历更密而提前或推迟几何事实。
        _annotate_geometry(points, symbol, _definition_dates([*definition_source, *points]), trend_level=trend_level)
        tail = _structural_tail(source, points, path=path, trend_level=trend_level)
        if tail is not None:
            developing_strokes.append(tail)
        stroke: dict[str, object] = dict(id=identifier, source_path=path, source_paths=[path], kind=kind,
            trend_level=trend_level, points=points, input_turn_count=len(source), key_transitions=[],
            confirmation_policy=STRUCTURAL_RULE, leg_confirmation_policy=STRUCTURAL_LEG_POLICY,
            wave_display_policy='structural_points_independent_strategy_permissions_v119',
            structural_legs=_structural_legs(points), confirmed_legs=[])
        strokes.append(stroke)
        if _closed_paths is not None and cutoff is not None:
            _closed_paths.put(trend_level, path, raw_source, cutoff, source_stroke, candidate, stroke, tail,
                              symbol=symbol)
    return dict(name=f'{name}趋势线', trend_level=trend_level, source_level=source_level, strokes=strokes,
        source_strokes=source_strokes, qualified_strokes=[],
        candidate_strokes=candidates, structure_strokes=candidates, developing_strokes=developing_strokes,
        n_target_observations=[], input_turn_count=sum(len(points) for points in groups.values()),
        confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in strokes),
        strategy_confirmed_wave_count=0, developing_wave_count=len(developing_strokes),
        developing_point_count=2*len(developing_strokes), key_transition_count=0,
        aggregation_rule=STRUCTURAL_RULE, trend_definition_rule=STRUCTURAL_RULE,
        scope='structural_trend_points_with_independent_strategy_permissions',
        bear_to_bull_highs=[], bear_bull_alternation_lows=[], post_alternation_bull_highs=[], bullish_turn_signals=[], bearish_turn_signals=[],
        break_basis='confirmed_lower_level_high_low_points', retracement_threshold=.67,
        _path_market_end_indices=path_market_end_indices,
        note='统一 judge_trend 的首参数为高低点：一级用折线，二级用一级输出，三级用二级输出；'
             '六定义和严格高低点突破升级只读取这个来源。相等不升级，不等待攻击之后的回调。'
             'K线突破、交替和可选N证书只提供策略资格，不筛掉趋势线结构点；路径断点各级一致保留。')


def _prepared_strokes(prepared: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    """校验准备阶段的路径外壳，借用只读点数组，避免全层深复制。"""
    strokes = prepared.get(field)
    if not isinstance(strokes, Sequence) or isinstance(strokes, (str, bytes)):
        raise ValueError(f'prepared {field} must be a sequence')
    result: list[Mapping[str, object]] = []
    for stroke in strokes:
        if not isinstance(stroke, Mapping) or any(not isinstance(key, str) for key in stroke):
            raise ValueError(f'prepared {field} must contain mappings with string keys')
        result.append(cast(Mapping[str, object], stroke))
    return result


def _prepared_points(stroke: Mapping[str, object]) -> Sequence[Mapping[str, object]]:
    points = stroke.get('points')
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)) or any(
        not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point) for point in points
    ):
        raise ValueError('prepared stroke points must be mappings with string keys')
    return cast(Sequence[Mapping[str, object]], points)


def attach_trend_permissions(
    prepared: Mapping[str, object], bars: Sequence[Bar] = (), *,
    n_target_trend_confirmation_enabled: bool = False,
    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
    _permission_cache: _TrendPermissionCache | None = None,
    _owned_candidates: bool = False,
    _n_confirmation_paths: frozenset[str] | None = None,
    _hierarchy_only: bool = False,
) -> dict[str, object]:
    """只给已完成的本级结构附加资格和命名事件，不再判断或归并几何。

    prepared 必须来自 prepare_trend_lines。全部结构路径完成后才调用本函数；
    可重复用不同 N 模式或行情前缀核验同一份几何，结果不会改写 prepared。
    资格按原路径截止重新核验，紧邻下级许可仅是独立核验的依据，不直接
    授予上级交易资格。没有行情或尚未可知的压力点不能进入策略视图。

    返回值复制顶层、路径外壳及资格数组，几何点、候选和来源数组只读共享。
    每条路径需要修改的许可点由资格发布器独立复制，避免为三级历史反复
    深复制全部几何证据。调用者不得通过返回的几何数组修改准备阶段结果；
    需要可变导出时应在导出边界另行复制。私有截止表不进入公开结果。
    私有 _owned_candidates 同样只允许阶段工作集的只读移交；N、未知
    审计值及需要归一化的容器仍沿用原公共复制，公共调用默认不移交。
    私有 _n_confirmation_paths 只授权本级已翻多且待确认的路径计算新 N；
    不在集合中的路径仍可使用来源证书未失效的既有 N 证明。
    私有 _hierarchy_only 只供关闭 N 时的逐日层级历史使用，不生成交易附件；
    消费者不能把此轻量结果放入完整交易视图缓存。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    if _hierarchy_only and n_target_trend_confirmation_enabled:
        raise ValueError('hierarchy-only permissions require N confirmation to be disabled')
    raw_level = prepared.get('trend_level')
    if type(raw_level) is not int:
        raise ValueError('prepared trend_level must be 1, 2 or 3')
    trend_level = raw_level
    _trend_level(trend_level)
    raw_cutoffs = prepared.get('_path_market_end_indices')
    if not isinstance(raw_cutoffs, Mapping) or any(
        not isinstance(path, str) or (cutoff is not None and (type(cutoff) is not int or cutoff < 0))
        for path, cutoff in raw_cutoffs.items()
    ):
        raise ValueError('prepared path cutoffs must contain nonnegative indices or None')
    cutoffs = cast(Mapping[str, int | None], raw_cutoffs)
    source_strokes = _prepared_strokes(prepared, 'source_strokes')
    candidate_strokes = _prepared_strokes(prepared, 'candidate_strokes')
    geometry_strokes = _prepared_strokes(prepared, 'strokes')
    developing_strokes = _prepared_strokes(prepared, 'developing_strokes')
    candidates_by_path = {stroke['source_path']: _prepared_points(stroke) for stroke in candidate_strokes}
    qualified_groups = _source_groups(qualified_source_points) if qualified_source_points is not None else {}
    qualified_strokes: list[dict[str, object]] = []
    legs_by_path: dict[str, list[dict[str, object]]] = {}
    n_targets: list[dict[str, object]] = []
    kind = _KINDS[trend_level]
    verified_prefix = _permission_cache.prefix(bars) if _permission_cache is not None else None
    full_market_index = (_MarketPrefixIndex(_permission_cache._market_index, _permission_cache._bars[:len(bars)])
        if verified_prefix is not None and _permission_cache is not None
        and _permission_cache._market_index is not None else None)
    owned_options: _OwnedCandidateOptions = {'_owned_candidates': True} if _owned_candidates else {}
    for source_stroke in source_strokes:
        path = source_stroke.get('id')
        if not isinstance(path, str) or path not in cutoffs or path not in candidates_by_path:
            raise ValueError('prepared source paths require matching candidates and cutoffs')
        source = _prepared_points(source_stroke)
        cutoff = cutoffs[path]
        # 行情索引仍从原股票的第零根起算，只截断尾部；不能把路径子段
        # 切成从零开始的新市场，也不能把后一路径的突破借给前一路径。
        market = bars if cutoff is None else bars[:cutoff]
        market_index = None
        if full_market_index is not None and len(market) <= len(full_market_index.bars):
            market_index = (full_market_index if len(market) == len(full_market_index.bars) else
                _MarketPrefixIndex(full_market_index.owner, full_market_index.bars[:len(market)]))
            market = market_index.bars
        permission_source = [dict(point, label=point.get('label', point.get('kind', ''))) for point in source]
        qualified_source = qualified_groups.get(path, []) if qualified_source_points is not None else None
        n_demand = (f'{kind}-{path}' in _n_confirmation_paths
                    if n_target_trend_confirmation_enabled and _n_confirmation_paths is not None else None)
        # 开放路径会收到新行情；只有断点已真实到达、原行情和全部来源一致的
        # 关闭路径可复用。缓存不存未来资格，也不把最终证书倒裁成历史。
        cached = (_permission_cache.get((trend_level, path), permission_source, candidates_by_path[path],
                    qualified_source, verified_prefix, len(market), n_target_trend_confirmation_enabled,
                    n_demand=n_demand)
                  if verified_prefix is not None and _permission_cache is not None
                  and cutoff is not None and cutoff <= len(bars) else None)
        if cached is None:
            path_targets: list[dict[str, object]] = []
            candidate_cache = (_permission_cache.candidates(trend_level)
                if cutoff is None and _permission_cache is not None and _owned_candidates else None)
            candidate_options = owned_options.copy()
            if candidate_cache is not None:
                candidate_options['_candidate_cache'] = candidate_cache
            n_scope = (_reuse_n_permissions(
                _permission_cache.n_permissions(trend_level, path)
                    if _permission_cache is not None and verified_prefix is not None
                    and _permission_cache._market_index is not None else _NPermissionProofs(),
                market, allow_new=n_demand is True)
                if n_target_trend_confirmation_enabled and _n_confirmation_paths is not None else nullcontext())
            with n_scope:
                if market_index is None:
                    permissions = qualify_trend_structure(candidates_by_path[path], permission_source, market,
                        trend_level=trend_level, qualified_source=qualified_source,
                        n_target_sink=path_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                        **candidate_options)
                else:
                    permissions = qualify_trend_structure(candidates_by_path[path], permission_source, market,
                        trend_level=trend_level, qualified_source=qualified_source,
                        n_target_sink=path_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                        _market_index=market_index, **candidate_options)
            if _hierarchy_only:
                if permissions:
                    qualified_strokes.append(dict(id=f'{kind}-{path}', source_path=path,
                        trend_level=trend_level, kind=kind, points=permissions))
                continue
            if permissions and market:
                # 命名资格附件只写本次许可，不倒写几何首次可知日。
                permission_dates: dict[object, int] = (
                    {key: value for key, value in _PrefixSessions(market_index.owner.positions, len(market) - 1).items()}
                    if market_index is not None else {session_date(bar): i for i, bar in enumerate(market)})
                permission_dates.update({i: i for i in range(len(market))})
                _annotate_permissions(permissions, market[0].symbol, permission_dates, trend_level=trend_level,
                    _cache=(_permission_cache._annotations
                            if _owned_candidates and _permission_cache is not None else None),
                    _cache_key=(trend_level, path))
            legs = confirmed_trend_legs(permissions, trend_level=trend_level, source_path=path)
            transitions: list[dict[str, object]] = []
            if permissions:
                from .secondary_trend import last_fall_high_reanchors

                reanchors = cast(Callable[..., list[dict[str, object]]], last_fall_high_reanchors)
                # 旧命名事件仅消费交易所日期，整数历史不补造日期资格。
                transitions = reanchors(permissions, market, trend_level=trend_level,
                    _market_index=market_index) if all(
                    isinstance(point['available_at'], str) and not point['available_at'].isdecimal()
                    for point in permissions) else []
            if (verified_prefix is not None and _permission_cache is not None
                    and cutoff is not None and cutoff <= len(bars)):
                _permission_cache.put((trend_level, path), permission_source, candidates_by_path[path],
                    qualified_source, verified_prefix, len(market), n_target_trend_confirmation_enabled,
                    (permissions, legs, transitions, path_targets), n_demand=n_demand)
        else:
            permissions, legs, transitions, path_targets = cached
        n_targets.extend(path_targets)
        legs_by_path[path] = legs
        if permissions:
            qualified_strokes.append(dict(id=f'{kind}-{path}', source_path=path, trend_level=trend_level,
                kind=kind, points=permissions, confirmed_legs=legs, key_transitions=transitions,
                view='strategy_permissions'))
    if _hierarchy_only:
        return dict(trend_level=trend_level, qualified_strokes=qualified_strokes)
    source_shells = [dict(stroke) for stroke in source_strokes]
    candidate_shells = [dict(stroke) for stroke in candidate_strokes]
    strokes = [dict(stroke, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                    confirmed_legs=legs_by_path[cast(str, stroke['source_path'])]) for stroke in geometry_strokes]
    # 这些命名事件只读取本次资格视图；其产物永不进入下一层高低点。
    highs = cast(Callable[..., object], bear_to_bull_highs)
    lows = cast(Callable[..., object], bear_bull_alternation_lows)
    post_highs = cast(Callable[..., object], post_alternation_bull_highs)
    signals = cast(Callable[..., object], bullish_turn_signals)
    landmark_market = full_market_index.bars if full_market_index is not None else bars
    alternation_lows = lows(qualified_strokes, trend_level=trend_level, source_strokes=source_shells,
                           bars=landmark_market, _market_index=full_market_index)
    result = dict(prepared)
    result.pop('_path_market_end_indices')
    result.update(strokes=strokes, source_strokes=source_shells, qualified_strokes=qualified_strokes,
        candidate_strokes=candidate_shells, structure_strokes=candidate_shells,
        developing_strokes=[dict(stroke) for stroke in developing_strokes],
        n_target_observations=n_targets, n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
        strategy_confirmed_wave_count=sum(len(cast(list[object], stroke['points'])) for stroke in qualified_strokes),
        key_transition_count=sum(len(cast(list[object], stroke['key_transitions'])) for stroke in qualified_strokes),
        bear_to_bull_highs=highs(qualified_strokes, trend_level=trend_level),
        bear_bull_alternation_lows=alternation_lows,
        post_alternation_bull_highs=post_highs(qualified_strokes, trend_level=trend_level,
            source_strokes=source_shells, bars=landmark_market, _alternation_lows=alternation_lows),
        bullish_turn_signals=signals(qualified_strokes, landmark_market, trend_level=trend_level,
            source_strokes=source_shells, _alternation_lows=alternation_lows, _market_index=full_market_index),
        bearish_turn_signals=bearish_turn_signals(qualified_strokes, landmark_market, trend_level=trend_level,
            _market_index=full_market_index))
    return result


def trend_lines(
    high_low_points: Sequence[Mapping[str, object]], bars: Sequence[Bar] = (), *, trend_level: int = 1,
    n_target_trend_confirmation_enabled: bool = False,
    qualified_source_points: Sequence[Mapping[str, object]] | None = None,
) -> dict[str, object]:
    """兼容单层入口：先完成所有路径结构，再单独附加行情资格。

    原签名和公开结果字段保持不变。需要股票级完整阶段顺序的入口应直接
    依次调用三层 prepare_trend_lines，再依次调用 attach_trend_permissions；
    下一层始终消费 strokes，qualified_strokes 只用于独立资格核验。
    """
    if type(n_target_trend_confirmation_enabled) is not bool:
        raise ValueError('n_target_trend_confirmation_enabled must be a boolean')
    prepared = prepare_trend_lines(high_low_points, trend_level=trend_level,
                                   symbol=bars[0].symbol if bars else 'STRUCTURE', bars=bars)
    return attach_trend_permissions(prepared, bars,
        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
        qualified_source_points=qualified_source_points)
