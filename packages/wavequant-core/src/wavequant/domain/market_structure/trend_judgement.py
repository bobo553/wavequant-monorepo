"""以直接下级高低点唯一决定本级结构，交易许可证不参与趋势几何。

一、二、三级共用同一过程：普通有序高低点定义、严格本级高低点突破升级，
再将当时已知的结构事件归并为交替高低点。下一层直接消费 ``points``；
``candidates`` 保存没有进入当前折线的真实结构事件，供审计和独立资格层
使用。本模块不读取 K 线、不计算 N、不调用 publisher，也不借交易证书
删改结构点。市场证书的方向、日期和资格由调用方另行计算。
"""

from collections import OrderedDict
from bisect import bisect_left, insort
from collections.abc import Callable, Iterator, Mapping, MutableMapping, MutableSequence, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from dataclasses import dataclass, field
from gc import get_referents
import json
from math import isfinite
from sys import getsizeof
from threading import get_ident
from types import MappingProxyType
from typing import cast

from .owned_evidence import CertifiedSource, current_evidence, finite_evidence, owned_signature as _geometry_signature
from .owned_evidence_producer import current_producer_records
from .trend_frozen_state import FrozenPrefixList, FrozenPrefixMap, _IndexArchive, _IndexNode, _SequenceNode, _node_children
from .trend_options import ordinary_completion_enabled
from .polyline import LinePoint, ReversalPoint
from .trend_level_upgrades import _PointRange, _TrendUpgradeState, promote_trend_key_breaks
from .trend_levels import (
    Moment, _SourceTurn, _TrendReversalState, _later_moment, _source_record_reference, _source_turns, trend_reversals,
)
from .trend_point_records import PointRecordView
from .trend_upgrade_checkpoint import _UpgradeConsumptionCache

__all__ = ['TrendJudgement', 'judge_trend', 'mutable_points']

# 合并解码仅改变模板传输；4 Mi 字符的上界同时限制临时 Unicode 缓冲。
_MUTABLE_POINT_BATCH_CHARACTERS = 4 * 1024 * 1024
_EventRows = list[dict[str, object]] | FrozenPrefixList[dict[str, object]]


@dataclass(frozen=True, slots=True)
class _JudgementDelta:
    token: object
    parent: object | None
    points_from: int
    candidates_from: int


@dataclass(frozen=True, slots=True)
class TrendJudgement:
    """一个层级的只读结构快照，价格坐标与首次可知日分别保留。

    ``points`` 是当前交替几何，可原样作为下一次 ``judge_trend`` 的首参数。
    ``candidates`` 是本快照普通归并及严格升级的完整候选并集；同向端点被
    更高 H／更低 L 延伸后，原事件仍在这里保留。两个元组的字典及嵌套证据
    容器递归冻结，调用方需要 JSON 或可变工作副本时使用 ``mutable_points``。
    """

    trend_level: int
    source_level: int
    points: tuple[Mapping[str, object], ...]
    candidates: tuple[Mapping[str, object], ...]
    _delta: _JudgementDelta | None = field(default=None, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class _OwnedTrendJudgement(TrendJudgement):
    """已认证内部管线移交的 builtin 只读图，公开入口不返回此类型。

    每个点拥有独立可替换外壳；点及嵌套证明发布后不再原位修改。
    内部消费者只复制外壳，公共消费者仍通过 mutable_points 独立导出。
    此标记说明复制边界，不提供缓存准入或内存费用豁免。
    """


def _judgement_working_points(
    points: Sequence[Mapping[str, object]], *, owned: bool,
) -> list[dict[str, object]]:
    return [dict(point) for point in points] if owned else mutable_points(points)


@dataclass(frozen=True, slots=True, eq=False)
class _FrozenPoint(Mapping[str, object]):
    """只读点及其标准证据导出模板；模板从不参与领域判定。"""

    data: Mapping[str, object]
    json_template: str | None
    _budget_upper_bytes: int | None = field(default=None, init=False, repr=False, compare=False)
    _budget_owner: '_FrozenBudgetOwner | None' = field(default=None, init=False, repr=False, compare=False)

    def __getitem__(self, key: str) -> object:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)


def _json_mapping(value: object) -> dict[object, object]:
    if isinstance(value, MappingProxyType):
        return dict(value)
    raise TypeError('only internally frozen standard evidence may use a JSON template')


def _mutable_value(value: object) -> object:
    """解冻容器并复制其他审计值，避免与输入或不可变快照共用可变引用。"""
    value_type = type(value)
    if value_type in (str, int, float, bool, bytes, complex, type(None)):
        return value
    if value_type in (dict, MappingProxyType) or isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        return {deepcopy(key): _mutable_value(item) for key, item in source.items()}
    if isinstance(value, (tuple, list)):
        return [_mutable_value(item) for item in cast(Sequence[object], value)]
    if isinstance(value, (set, frozenset)):
        # 集合中的tuple是合法审计键，不能按普通证据序列解成不可哈希list。
        return {deepcopy(item) for item in cast(set[object] | frozenset[object], value)}
    return deepcopy(value)


def mutable_points(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """返回可修改、可供既有字典消费者使用的独立点副本。

    MappingProxyType 不能直接被 deepcopy 或 JSON 编码；本函数在这些边界
    递归转回字典／列表，不改变原快照，也不授予任何市场或跨级许可证。
    """
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes, bytearray)):
        raise ValueError('trend points must be a sequence of mappings')
    if type(points) in (list, tuple) and len(points) > 1:
        templates: list[str] = []
        for point in points:
            if type(point) is not _FrozenPoint or point.json_template is None:
                break
            templates.append(point.json_template)
        else:
            # 每批解码共享不可变字段名，点及嵌套容器仍逐个独立生成。
            # 只有本模块的标准模板走批量路径，未知 Mapping／审计值保留旧钩子。
            exported: list[dict[str, object]] = []
            batch: list[str] = []
            characters = 2

            def decode_batch() -> None:
                if len(batch) == 1:
                    decoded: object = json.loads(batch[0])
                    if type(decoded) is not dict:
                        raise ValueError('a frozen trend point requires a mapping template')
                    exported.append(cast(dict[str, object], decoded))
                elif batch:
                    decoded_points: object = json.loads('[' + ','.join(batch) + ']')
                    if (not isinstance(decoded_points, list)
                            or any(type(point) is not dict for point in decoded_points)):
                        raise ValueError('a frozen trend point requires a mapping template')
                    exported.extend(cast(list[dict[str, object]], decoded_points))
                batch.clear()

            for template in templates:
                added = len(template) + 1
                if batch and characters + added > _MUTABLE_POINT_BATCH_CHARACTERS:
                    decode_batch()
                    characters = 2
                batch.append(template)
                characters += added
            decode_batch()
            return exported
    result: list[dict[str, object]] = []
    for point in points:
        if type(point) is _FrozenPoint and point.json_template is not None:
            # 仅从本次冻结的标准模板生成新容器，每个点、每次调用都独立
            # 物化；不缓存可变导出，未知审计对象仍沿用原递归 deepcopy。
            decoded: object = json.loads(point.json_template)
            if not isinstance(decoded, dict):
                raise ValueError('a frozen trend point requires a mapping template')
            result.append(cast(dict[str, object], decoded))
            continue
        if not isinstance(point, Mapping) or any(not isinstance(key, str) for key in point):
            raise ValueError('trend points must be mappings with string keys')
        result.append({key: _mutable_value(value) for key, value in point.items()})
    return result


class _OwnedPointAdmission:
    """一个阶段只保存标准有限点的全文证书，不持有可变点或审计叶子。"""

    def __init__(self, *, memory_bytes: int = 8 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('owned point memory_bytes must be a positive integer')
        self._limit = memory_bytes
        self._thread = get_ident()
        self._closed = False
        self._points: dict[bytes, None] = {}
        self._key_bytes = 0
        self._calls = 0
        self._hits = 0
        self._misses = 0
        self._refusals = 0
        self._peak_bytes = 0

    @property
    def memory_used(self) -> int:
        # 字节对象和 dict 容量按实际大小计费；固定余量覆盖本实例、属性字典、
        # 键及64位计数器。缓存不保存逐日输入，空缓存没有证据占用。
        return self._key_bytes + getsizeof(self._points) + 4096 if self._points else 0

    def certify(self, point: Mapping[str, object]) -> bool:
        if self._closed or get_ident() != self._thread or type(point) is not dict:
            return False
        self._calls += 1
        # exact builtin 编码先于 get/遍历；未知对象不会进入序列化或相等钩子。
        signature = _geometry_signature(point)
        if signature is None:
            return False
        if signature in self._points:
            self._hits += 1
            return True
        self._misses += 1
        pending: list[object] = [point]
        seen: set[int] = set()
        while pending:
            value = pending.pop()
            if type(value) is dict:
                if id(value) in seen:
                    continue
                seen.add(id(value))
                mapping = cast(dict[object, object], value)
                if any(type(key) is not str for key in mapping):
                    return False
                pending.extend(mapping.values())
            elif type(value) is list:
                if id(value) not in seen:
                    seen.add(id(value))
                    pending.extend(cast(list[object], value))
            elif type(value) not in (str, int, float, bool, type(None)):
                return False
        try:
            # 已完整认证标准类型，C JSON 只检查有限值和无环；不会额外触发
            # 未知钩子，也不会把类型、键序或当前非有限叶子替换成 JSON 身份。
            json.dumps(point, allow_nan=False)
        except (TypeError, ValueError):
            return False
        added = getsizeof(signature)
        if added + self.memory_used + 4096 > self._limit:
            self._refusals += 1
            return True
        self._points[signature] = None
        self._key_bytes += added
        if self.memory_used > self._limit:
            # 稳定准入保留旧证书；dict 扩容后的容量也须计费，拒绝新项后
            # 重建当前表以回收空槽。绝不按每日全窗顺序做 LRU 淘汰。
            del self._points[signature]
            self._key_bytes -= added
            self._points = dict(self._points)
            self._refusals += 1
            if self.memory_used > self._limit:
                self._points.clear()
                self._key_bytes = 0
        self._peak_bytes = max(self._peak_bytes, self.memory_used)
        return True

    def close(self) -> None:
        self._points.clear()
        self._key_bytes = 0
        self._closed = True


_owned_point_admission: ContextVar[_OwnedPointAdmission | None] = ContextVar('owned_point_admission', default=None)


def _owned_mutable_points(
    points: Sequence[Mapping[str, object]], owned: bool, *, _admitted: list[bool] | None = None,
) -> list[dict[str, object]]:
    """内部调用方移交标准工作集；公共或未知输入仍独立复制所有证据。

    移交后原工作集只能被读取，不再修改或公开导出。tuple 归一化、未知
    deepcopy 钩子以及非有限叶子的当前 identity 仍遵守原公共复制边界。
    """
    if _admitted is not None:
        _admitted[0] = False
    if not owned or type(points) not in (list, tuple):
        return mutable_points(points)
    admission = _owned_point_admission.get()
    if admission is None:
        admission = _OwnedPointAdmission()
    if not all(admission.certify(point) for point in points):
        return mutable_points(points)
    if _admitted is not None:
        _admitted[0] = True
    return [dict(point) for point in points]


def _frozen_value(value: object, json_safe: list[bool] | None = None,
                  memo: dict[int, tuple[object, object]] | None = None) -> object:
    """将字典、序列与集合证据容器变为只读快照。"""
    value_type = type(value)
    if value_type in (str, int, float, bool, bytes, complex, type(None)):
        if json_safe is not None and (value_type in (bytes, complex)
                or value_type is float and not isfinite(cast(float, value))):
            json_safe[0] = False
        return value
    previous = memo.get(id(value)) if memo is not None else None
    if previous is not None and previous[0] is value:
        return previous[1]
    safe = [True]
    if value_type in (dict, MappingProxyType) or isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        if value_type not in (dict, MappingProxyType, _FrozenPoint) or any(type(key) is not str for key in source):
            safe[0] = False
        frozen: object = MappingProxyType({deepcopy(key): _frozen_value(item, safe, memo)
                                         for key, item in source.items()})
    elif isinstance(value, (tuple, list)):
        if value_type not in (tuple, list):
            safe[0] = False
        frozen = tuple(_frozen_value(item, safe, memo) for item in cast(Sequence[object], value))
    elif isinstance(value, (set, frozenset)):
        safe[0] = False
        frozen = frozenset(_frozen_value(item, safe, memo) for item in cast(set[object] | frozenset[object], value))
    else:
        safe[0] = False
        frozen = deepcopy(value)
    if safe[0] and memo is not None:
        # 同次判定的标准子树可以共享只读结果；原对象强引用和 is 比较防止
        # id 重用。未知审计对象不进 memo，公开导出仍逐点独立生成容器。
        memo[id(value)] = value, frozen
    if not safe[0] and json_safe is not None:
        json_safe[0] = False
    return frozen


def _freeze_point_uncached(
    point: Mapping[str, object], memo: dict[int, tuple[object, object]] | None = None,
) -> _FrozenPoint:
    safe = [True]
    frozen = cast(Mapping[str, object], _frozen_value(point, safe, memo))
    template: str | None = None
    if safe[0]:
        try:
            template = json.dumps(frozen, default=_json_mapping, allow_nan=False,
                                  ensure_ascii=False, separators=(',', ':'))
        except (ValueError, TypeError):
            # 特大整数等编码边界仍使用原有独立 deepcopy 导出。
            pass
    result = _FrozenPoint(frozen, template)
    if template is not None:
        _frozen_budget_upper(result)
    return result


def _frozen_owned_bytes(value: object, seen: set[int]) -> int:
    """计算标准只读快照的真实容器、模板和叶子体积，不执行审计对象钩子。"""
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = getsizeof(value)
    if type(value) is _FrozenPoint:
        point = value
        # 构造器仍可接收未认证点；只有内部冻结入口会创建尺寸证书。
        certificate = getsizeof(point._budget_upper_bytes) if point._budget_upper_bytes is not None else 0
        return (size + certificate + _frozen_owned_bytes(point.data, seen)
                + _frozen_owned_bytes(point.json_template, seen))
    if type(value) is MappingProxyType:
        # proxy 自身不包含其后方 dict 的容量，直接读取标准容器的 GC 引用。
        return size + sum(_frozen_owned_bytes(child, seen) for child in get_referents(value))
    if type(value) is dict:
        mapping = cast(dict[object, object], value)
        return size + sum(_frozen_owned_bytes(key, seen) + _frozen_owned_bytes(child, seen)
                          for key, child in mapping.items())
    if type(value) is tuple:
        return size + sum(_frozen_owned_bytes(child, seen) for child in cast(tuple[object, ...], value))
    return size


def _frozen_budget_upper(point: _FrozenPoint) -> int:
    """只读标准点的独占上界；不保存对象索引或跨版本强引用。

    追加计费允许重复计入不同点共享的子树，临近限额仍由完整对象图按
    identity 校准。因此缓存不会把共享误差变成直接拒绝，也不依赖某个
    请求池的折扣；同一点在任何请求中该上界都保持有效。
    """
    if point.json_template is None:
        raise ValueError('only standard immutable point evidence has a stable budget')
    retained = point._budget_upper_bytes
    if retained is None:
        # 标准冻结入口在任何状态持有该点之前填充证书。预留完整地址空间
        # 的整数大小，覆盖证书本身；不依赖计算结果恰好落入小整数缓存。
        retained = _frozen_owned_bytes(point, set()) + getsizeof(2 ** 64)
        # 只写与业务值无关的惰性元数据，data 与 json_template 始终只读。
        object.__setattr__(point, '_budget_upper_bytes', retained)
    return retained


@dataclass(slots=True)
class _FrozenBudgetOwner:
    """仅证明一个独立预算仍持有点，不反向持有缓存或任何证据。"""

    alive: bool = True


class _FrozenPointCache:
    """单阶段的有界完整点复用；不保存来源判断、行情或可变导出。"""

    def __init__(self, *, memory_bytes: int = 16 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('frozen point memory_bytes must be a positive integer')
        self._limit = memory_bytes
        self._thread = get_ident()
        self._closed = False
        self._budget_token = _FrozenBudgetOwner()
        self._points: OrderedDict[bytes, tuple[_FrozenPoint, int]] = OrderedDict()
        self._payload_bytes = 0
        self._calls = 0
        self._hits = 0
        self._misses = 0
        self._fallbacks = 0
        self._evictions = 0
        self._peak_bytes = 0

    @property
    def memory_used(self) -> int:
        return (self._payload_bytes + getsizeof(self._points) + getsizeof(self._budget_token)
                if self._points else 0)

    def intern(
        self, point: Mapping[str, object], memo: dict[int, tuple[object, object]] | None = None,
    ) -> _FrozenPoint:
        if self._closed or get_ident() != self._thread:
            return _freeze_point_uncached(point, memo)
        self._calls += 1
        signature = _geometry_signature(point)
        if signature is None:
            self._fallbacks += 1
            return _freeze_point_uncached(point, memo)
        previous = self._points.get(signature)
        if previous is not None:
            self._hits += 1
            return previous[0]
        self._misses += 1
        frozen = _freeze_point_uncached(point, memo)
        if frozen.json_template is None:
            # 未知审计值和非 JSON 标准容器每次沿用原来的复制次数与钩子。
            self._fallbacks += 1
            return frozen
        owned = _frozen_budget_upper(frozen) + getsizeof(signature)
        charged = owned + getsizeof((frozen, owned)) + getsizeof(owned)
        if charged + getsizeof(OrderedDict()) + getsizeof(self._budget_token) > self._limit:
            self._fallbacks += 1
            return frozen
        # 池已满时立即拒绝；试插也不能改变旧表容量，否则拒绝新项后
        # OrderedDict 的扩容余量仍可能迫使旧项被淘汰，使借用凭证失效。
        if self.memory_used + charged > self._limit:
            self._evictions += 1
            self._fallbacks += 1
            return frozen
        # 只复制有界点索引，不复制点及证明；仅成功新增项支付此事务成本，
        # 命中和超容量拒绝均不复制。实际表容量合格后才替换当前索引。
        admitted = self._points.copy()
        admitted[signature] = frozen, charged
        if self._payload_bytes + charged + getsizeof(admitted) + getsizeof(self._budget_token) > self._limit:
            self._evictions += 1
            self._fallbacks += 1
            return frozen
        self._points = admitted
        self._payload_bytes += charged
        object.__setattr__(frozen, '_budget_owner', self._budget_token)
        self._peak_bytes = max(self._peak_bytes, self.memory_used)
        return frozen

    def close(self) -> None:
        self._budget_token.alive = False
        self._points.clear()
        self._payload_bytes = 0
        self._closed = True


_frozen_point_cache: ContextVar[_FrozenPointCache | None] = ContextVar('trend_frozen_point_cache', default=None)


def _current_frozen_budget_owner() -> _FrozenBudgetOwner | None:
    cache = _frozen_point_cache.get()
    if cache is not None and cache._budget_token.alive and cache._thread == get_ident():
        return cache._budget_token
    return None


@contextmanager
def _frozen_point_scope(*, memory_bytes: int = 16 * 1024 * 1024,
                        cache: _FrozenPointCache | None = None,
                        admission: _OwnedPointAdmission | None = None) -> Iterator[_FrozenPointCache]:
    """绑定当前调用的快照缓存；显式借用的请求缓存由其所有者释放。"""
    owned = cache is None
    if cache is None:
        cache = _FrozenPointCache(memory_bytes=memory_bytes)
    owned_admission = admission is None
    if admission is None:
        admission = _OwnedPointAdmission()
    admission_token = _owned_point_admission.set(admission)
    token = _frozen_point_cache.set(cache)
    try:
        yield cache
    finally:
        _frozen_point_cache.reset(token)
        if owned:
            cache.close()
        _owned_point_admission.reset(admission_token)
        if owned_admission:
            admission.close()


def _frozen_point(point: Mapping[str, object], memo: dict[int, tuple[object, object]] | None = None) -> _FrozenPoint:
    cache = _frozen_point_cache.get()
    return cache.intern(point, memo) if cache is not None else _freeze_point_uncached(point, memo)


def _moment_key(moment: Moment) -> tuple[int, int | str]:
    """同一时钟内比较知识顺序，数字字符串的 10 必须晚于 9。"""
    if type(moment) is int and moment >= 0:
        return 0, moment
    if isinstance(moment, str) and moment:
        return (1, int(moment)) if moment.isdecimal() else (2, moment)
    raise ValueError('asof must be a nonnegative confirmation index or nonempty date string')


def _known(point: Mapping[str, object]) -> Moment:
    moment = point['available_at']
    if type(moment) is int or isinstance(moment, str):
        return moment
    raise ValueError('a structural point requires a known confirmation session')


def _order(point: Mapping[str, object]) -> tuple[int, int]:
    return cast(int, point['index']), cast(int, point.get('ordinal', 0))


def _visible_source(
    points: Sequence[Mapping[str, object]], source_level: int, asof: Moment | None,
    *, _validated_turns: list[_SourceTurn] | None = None,
    _owned_source: bool = False,
    _owned_admission: list[bool] | None = None,
    _source_position_offset: int = 0, _validated_position_offset: int = 0,
) -> tuple[list[dict[str, object]], tuple[int, ...]]:
    """先建立可知来源前缀；类型校验不属于高低点趋势判断。

    可知时间可能晚于价格位置且并非单调，因此不能截取价格位置的简单前缀。
    未完／显示点由共同来源校验排除，其余点按自身真实可知日过滤之后，
    才允许普通定义或严格升级读取。原首参数位置单独保存，迟知点被过滤
    不会令输出 source_position 指向别的来源。
    """
    working = _owned_mutable_points(points, _owned_source, _admitted=_owned_admission)
    turns = _source_turns(working, source_level, _position_offset=_source_position_offset)
    cutoff = _moment_key(asof) if asof is not None else None
    visible: list[dict[str, object]] = []
    positions: list[int] = []
    for turn in turns:
        known = _moment_key(turn.available_at)
        if cutoff is not None:
            if known[0] != cutoff[0]:
                raise ValueError('asof and source confirmation clocks must match')
            if asof is not None and _later_moment(turn.available_at, asof) != asof:
                continue
        point = dict(turn.source)
        source_record = _source_record_reference(point)
        visible.append(point)
        positions.append(turn.position)
        if _validated_turns is not None:
            # 归并器消费可见子序列的局部位置；原输入位置由 positions 单独恢复。
            # 来源绑定本次 visible 的新顶层副本，不将原位置 provenance 混入状态。
            position = _validated_position_offset + len(visible) - 1
            reversal = ReversalPoint(turn.reversal.point, turn.index,
                                     f'level{source_level}_source_position_{position}')
            _validated_turns.append(_SourceTurn(point, position, turn.index, turn.ordinal, turn.kind,
                                                turn.value, turn.time, turn.available_at, reversal,
                                                source_record))
    return visible, tuple(positions)


@dataclass(frozen=True)
class _SourceVersion:
    token: object
    parent: object | None
    changed_from: int
    source: FrozenPrefixList[dict[str, object]]
    positions: FrozenPrefixList[int]
    turns: FrozenPrefixList[_SourceTurn]
    certificates: FrozenPrefixList[bytes]
    owned_result: bool = False


class _SourceVersionOwner:
    """在可变来源入场处认证一次，向内部阶段交付版本及已认证的变化后缀。

    内部生产者交付带证书的来源版本及变化边界；公共可变快照仍逐点比较
    全文。解析、结束证明及状态认证只处理已认证的变化后缀，不把外部列表
    的 identity 或长度当版本证书。
    """

    def __init__(self, level: int, limit: int) -> None:
        self.level, self.limit = level, limit
        self.keys: Sequence[bytes] = ()
        self.current: _SourceVersion | None = None
        self._certificate_prefix_bytes: list[int] = [0]
        self._input_version: object | None = None

    def clear(self) -> None:
        self.keys, self.current = (), None
        self._certificate_prefix_bytes = [0]
        self._input_version = None

    def prepare(self, source: Sequence[Mapping[str, object]]) -> _SourceVersion | None:
        certified = type(source) is CertifiedSource and source.owner is current_evidence()
        if type(source) not in (list, tuple) and not certified:
            self.clear()
            return None
        signatures: list[bytes] = []
        signature_bytes = 0
        for point in (() if certified else source):
            if type(point) is not dict:
                self.clear()
                return None
            signature = _geometry_signature(point)
            if signature is None:
                self.clear()
                return None
            signatures.append(signature)
            signature_bytes += getsizeof(signature)
        versioned = cast(CertifiedSource, source) if certified else None
        scope = current_producer_records()
        if (scope is not None and versioned is not None and versioned.record_store is not None
                and versioned.record_store is not scope.owner):
            raise ValueError('source point records must belong to the active request owner')
        if versioned is not None and versioned.token is self._input_version and self.current is not None:
            return self.current
        keys = versioned.keys if versioned is not None else tuple(signatures)
        if versioned is not None:
            signature_bytes = versioned.signature_bytes
        previous = self.current
        changed = (versioned.changed_from if versioned is not None
            and versioned.parent is self._input_version else 0)
        while changed < min(len(keys), len(self.keys)) and keys[changed] == self.keys[changed]:
            changed += 1
        if previous is not None and changed == len(keys) == len(self.keys):
            self._input_version = versioned.token if versioned is not None else None
            return previous
        # 内部不可变节点已经准入；外部可变审计与变化点继续完整检查。
        if not finite_evidence(source[changed:]):
            self.clear()
            return None
        boundary = max(0, bisect_left(previous.positions, changed) - 1) if previous is not None else 0
        # 原首个可见点之前的 display_only／seed 也可能转为有效，不能越过实际修订位置。
        start = min(changed, previous.positions[boundary]) if previous is not None and previous.positions else 0
        if scope is not None and versioned is not None and versioned.record_store is not None:
            # 解析还需要前一可见点校验边界；仅绑定这段实际重读的原始后缀。
            records = cast(PointRecordView, versioned.point_records)
            for record in records.iter_range(start):
                scope.bind(record)
        turns: list[_SourceTurn] = []
        admitted = [False]
        tail, positions = _visible_source(source[start:], self.level - 1, None, _validated_turns=turns,
            _source_position_offset=start, _validated_position_offset=boundary, _owned_source=certified,
            _owned_admission=admitted)
        certificates = tuple(_geometry_signature((position, point)) for position, point in zip(positions, tail))
        if any(certificate is None for certificate in certificates):
            self.clear()
            return None
        certificate_bytes = self._certificate_prefix_bytes[boundary] if previous is not None else 0
        tail_prefix_bytes: list[int] = []
        for certificate in certificates:
            certificate_bytes += getsizeof(certificate)
            tail_prefix_bytes.append(certificate_bytes)
        visible = previous.source.copy() if previous is not None else FrozenPrefixList[dict[str, object]]()
        source_positions = previous.positions.copy() if previous is not None else FrozenPrefixList[int]()
        source_turns = previous.turns.copy() if previous is not None else FrozenPrefixList[_SourceTurn]()
        source_certificates = previous.certificates.copy() if previous is not None else FrozenPrefixList[bytes]()
        for buffer in (visible, source_positions, source_turns, source_certificates):
            del buffer[boundary:]
        visible.extend(tail)
        source_positions.extend(positions)
        source_turns.extend(turns)
        source_certificates.extend(cast(tuple[bytes, ...], certificates))
        for buffer in (visible, source_positions, source_turns, source_certificates):
            buffer.freeze(len(buffer))
        # 未认证的保留前缀继续使用公开冻结；资格随变化后缀传播，不为
        # 恢复共享快路径扫描历史证明。完整替换后才能重新获得严格资格。
        version = _SourceVersion(object(), previous.token if previous is not None else None,
            bisect_left(previous.positions, changed) if previous is not None else 0,
            visible, source_positions, source_turns, source_certificates,
            admitted[0] and (previous is None or start == 0 or previous.owned_result))
        # 不保留版本链；容量不足时调用原公共校验，不截断历史。
        if (signature_bytes + certificate_bytes) * 4 > self.limit:
            self.clear()
            return None
        self.keys, self.current = keys, version
        self._input_version = versioned.token if versioned is not None else None
        # 已保留的证书 bytes 不变；修订只重算后缀费用，仍使用原预算系数和阈值。
        del self._certificate_prefix_bytes[boundary+1:]
        self._certificate_prefix_bytes.extend(tail_prefix_bytes)
        return version


def _restore_source_positions(
    candidates: Sequence[Mapping[str, object]], positions: Sequence[int], source_level: int,
) -> list[dict[str, object]]:
    """公共归并器在可知子序列上运算，公开来源引用仍指向调用方原序列。"""
    fields = (('source_turn_position', 'confirmed_on_turn') if source_level == 0 else
              (f'source_level{source_level}_position', f'confirmed_on_level{source_level}'))
    # 候选已属于本次判定的私有工作集；这里只改顶层来源位置，嵌套证据
    # 直到返回边界统一冻结。重复深复制不会增加隔离，只会反复遍历整棵证明。
    restored = [dict(point) for point in candidates]
    for point in restored:
        for field in fields:
            position = point.get(field)
            if type(position) is int:
                point[field] = positions[position]
    return restored


def _event_key(point: Mapping[str, object]) -> tuple[tuple[int, int], str, str, str | None, str | None, tuple[int, int | str], int | None]:
    """坐标相同不代表同一结构事实；方向、定义及知识日均是证据身份。"""
    trigger = next((value for field in ('confirmed_on_turn', 'confirmed_on_level1', 'confirmed_on_level2')
                    if type(value := point.get(field)) is int), None)
    return (_order(point), cast(str, point['kind']), cast(str, point.get('confirmation_rule', '')),
            cast(str | None, point.get('wave_direction_before')),
            cast(str | None, point.get('wave_direction_after')), _moment_key(_known(point)), trigger)


def _complete_candidates(
    ordinary: Sequence[Mapping[str, object]], upgraded: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    """保留普通整波定义与更早严格升级的各自证据，几何另行归并。

    旧升级器按坐标保留最早事实，以供沿用同一关键位推进升级状态。该坐标
    后来可能又被普通HH→HL／LL→LH定义确认成反向整波起点；不能因此丢掉
    后知证据，也不能把它倒写到较早单向升级端点。审计候选保留两份事实，
    points仍仅取该坐标最早可知的几何节点。线性去重后只排序一次。
    """
    result: list[dict[str, object]] = []
    identities: set[tuple[tuple[int, int], str, str, str | None, str | None, tuple[int, int | str], int | None]] = set()
    for events in (upgraded, ordinary):
        for point in events:
            identity = _event_key(point)
            if identity not in identities:
                result.append(dict(point))
                identities.add(identity)
    return sorted(result, key=lambda point: (_order(point), _moment_key(_known(point)), _event_key(point)[2]))


def _canonical_points(candidates: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """将可知结构事件归并为同一条交替折线，不读取任何许可证。

    先按知识日期纳入候选，再按真实价格位置归并；未知事件没有机会参与
    极值选择。同类 H 保留最高点、L 保留最低点；相等时坐标最早者保留。
    几何首次可知时间和整波结束时间独立：同坐标更晚的真实反向证明只
    追加结束知识，不改写开放端点自身日期，也不由相邻异类点猜测结束。
    替换端点只复制新端点自己的 available_at，绝不把同组更早的知识日
    移给后来才知的更大／更小极值。原端点及严格突破证明仍留在 candidates。
    """
    known_events = sorted(candidates, key=lambda point: (_moment_key(_known(point)), _order(point)))
    # 每个已知事件仅入库一次；随后一次价格排序与线性归并，避免逐事件重放
    # 整个前缀。来源和候选已通过公共校验，同坐标的较晚证据不能倒写早事实。
    by_order: dict[tuple[int, int], dict[str, object]] = {}
    for point in known_events:
        selected = by_order.setdefault(_order(point), dict(point))
        if 'wave_completed_at' in point:
            # 仅 join 独立结束字段，保留早期 geometry available_at、升级证据
            # 与 confirmed_by，防止把 laterclosed23 倒填到 earliest18。
            existing = selected.get('wave_completed_at')
            completed = cast(Moment, point['wave_completed_at'])
            if existing is None or _later_moment(completed, cast(Moment, existing)) == existing:
                for field in ('wave_completion_policy', 'wave_completed_at', 'wave_completion_evidence'):
                    selected[field] = point[field]
    result: list[dict[str, object]] = []
    for point in sorted(by_order.values(), key=_order):
        if result and point['kind'] == result[-1]['kind']:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) > 0:
                result[-1] = point
            continue
        if result:
            sign = 1 if point['kind'] == 'H' else -1
            if sign * (cast(float, point['value']) - cast(float, result[-1]['value'])) <= 0:
                raise ValueError('structural candidates require nonzero alternating high/low legs')
        result.append(point)
    return result


@dataclass
class _CandidateBucket:
    ordinary: list[Mapping[str, object]] = field(default_factory=list)
    events: list[Mapping[str, object]] = field(default_factory=list)
    merged: dict[tuple[int, int, str, float], Mapping[str, object]] = field(default_factory=dict)
    candidates: tuple[Mapping[str, object], ...] = ()
    point: dict[str, object] | None = None
    base: list[Mapping[str, object]] = field(default_factory=list)


class _JudgementLedger:
    """按坐标追加候选账本；旧坐标补充结束证明时仅重整该桶及受影响几何后缀。"""

    def __init__(self, *, _owned_result: bool = False) -> None:
        self._owned_result = _owned_result
        self.ordinary_count = 0
        self.event_count = 0
        self.buckets: MutableMapping[tuple[int, int], _CandidateBucket] = {}
        self.orders: MutableSequence[tuple[int, int]] = []
        self.geometry: MutableSequence[dict[str, object]] = []
        self.geometry_orders: MutableSequence[tuple[int, int]] = []
        self.points: MutableSequence[Mapping[str, object]] = []
        self.counts: MutableSequence[tuple[int, int]] = [(0, 0)]
        self.candidates: MutableSequence[Mapping[str, object]] = []
        self.candidate_offsets: MutableSequence[int] = [0]
        self.revision: object | None = None

    def update(self, ordinary: _TrendReversalState, upgrade: _TrendUpgradeState,
               events: Sequence[dict[str, object]], positions: Sequence[int], *, initial: bool,
               level: int) -> tuple[TrendJudgement, list[object]]:
        dirty: set[tuple[int, int]] = set()
        changed_objects: list[object] = []

        def bucket(point: Mapping[str, object]) -> _CandidateBucket:
            order = _order(point)
            if order not in dirty:
                previous = self.buckets.get(order)
                if previous is None:
                    self.buckets[order] = _CandidateBucket()
                    insort(self.orders, order)
                    changed_objects.append(order)
                else:
                    # 检查点共享已发布桶；每轮只复制实际修订的桶，旧版本不再修改。
                    self.buckets[order] = _CandidateBucket(
                        previous.ordinary.copy(), previous.events.copy(), previous.merged.copy(),
                        previous.candidates, previous.point, previous.base.copy())
                dirty.add(order)
            return self.buckets[order]

        for point in ordinary.selected[self.ordinary_count:]:
            bucket(point).ordinary.append(point)
        for point in events[self.event_count:]:
            bucket(point).events.append(point)
        for turn in (upgrade.merged.values() if initial else upgrade.updated_merged.values()):
            point = turn.source
            bucket(point).merged[turn.index, turn.ordinal, turn.kind, turn.value] = point
        self.ordinary_count, self.event_count = len(ordinary.selected), len(events)
        return self._finish(dirty, positions, level, changed_objects)

    def _finish(self, dirty: set[tuple[int, int]], positions: Sequence[int], level: int,
                changed_objects: list[object]) -> tuple[TrendJudgement, list[object]]:
        point_change, candidate_change = len(self.points), len(self.candidates)
        frozen_memo: dict[int, tuple[object, object]] = {}
        for order in sorted(dirty):
            entry = self.buckets[order]
            upgraded = [*entry.base, *(entry.merged[key] for key in sorted(entry.merged)), *entry.events]
            candidates = _restore_source_positions(_complete_candidates(entry.ordinary, upgraded), positions, level - 1)
            entry.candidates = tuple(dict(point) if self._owned_result else _frozen_point(point, frozen_memo)
                                     for point in candidates)
            owned = _restore_source_positions(_complete_candidates((), upgraded), positions, level - 1)
            canonical = _canonical_points(owned)
            entry.point = canonical[0] if canonical else None
            changed_objects.extend((entry, entry.candidates, entry.point))
        if dirty:
            first = bisect_left(self.orders, min(dirty))
            candidate_change = self.candidate_offsets[first]
            del self.candidates[candidate_change:]
            del self.candidate_offsets[first + 1:]
            for order in self.orders[first:]:
                self.candidates.extend(self.buckets[order].candidates)
                self.candidate_offsets.append(len(self.candidates))
            changed_objects.extend(self.candidate_offsets[first + 1:])
            # 新插入点可能拆开此前被同类极值合并的区间，须从前一已保留端点重放。
            preceding = bisect_left(self.geometry_orders, min(dirty))
            boundary = max(0, preceding - 1)
            point_change = boundary
            start = bisect_left(self.orders, self.geometry_orders[boundary]) if preceding else 0
            del self.geometry[boundary:]
            del self.points[boundary:]
            del self.counts[boundary + 1:]
            for order in self.orders[start:]:
                point = self.buckets[order].point
                if point is None:
                    continue
                if self.geometry and point['kind'] == self.geometry[-1]['kind']:
                    sign = 1 if point['kind'] == 'H' else -1
                    if sign * (cast(float, point['value']) - cast(float, self.geometry[-1]['value'])) > 0:
                        self.geometry[-1] = point
                    continue
                if self.geometry:
                    sign = 1 if point['kind'] == 'H' else -1
                    if sign * (cast(float, point['value']) - cast(float, self.geometry[-1]['value'])) <= 0:
                        raise ValueError('structural candidates require nonzero alternating high/low legs')
                self.geometry.append(point)
            # 起始端点也可能被后续同类极值替换，重新生成它及其后标签。
            high, low = self.counts[-1]
            for point in self.geometry[boundary:]:
                high += point['kind'] == 'H'
                low += point['kind'] == 'L'
                count = (high, low)
                self.counts.append(count)
                changed_objects.append(count)
                labelled = dict(point, state='reversal',
                                label=f"{point['kind']}{high if point['kind'] == 'H' else low}")
                self.points.append(labelled if self._owned_result else _frozen_point(labelled, frozen_memo))
            del self.geometry_orders[boundary:]
            new_orders = [_order(point) for point in self.geometry[boundary:]]
            self.geometry_orders.extend(new_orders)
            changed_objects.extend(new_orders)
            changed_objects.extend(self.points[boundary:])
        delta = _JudgementDelta(object(), self.revision, point_change, candidate_change)
        self.revision = delta.token
        result_type = _OwnedTrendJudgement if self._owned_result else TrendJudgement
        return result_type(level, level - 1, tuple(self.points), tuple(self.candidates), delta), changed_objects

    def reconcile_formal(self, previous_candidates: Sequence[Mapping[str, object]],
                         candidates: Sequence[Mapping[str, object]], candidate_from: int,
                         previous_points: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                         point_from: int, upgrade: _TrendUpgradeState, events: Sequence[dict[str, object]],
                         positions: Sequence[int], level: int) -> tuple[TrendJudgement, list[object]]:
        """正式旁路使用同一候选归并器，只更新普通输入修订和新增升级所在的桶。"""
        dirty: set[tuple[int, int]] = set()
        changed: list[object] = []

        def edit(order: tuple[int, int]) -> _CandidateBucket:
            if order not in dirty:
                previous = self.buckets.get(order)
                if previous is None:
                    self.buckets[order] = _CandidateBucket()
                    insort(self.orders, order)
                else:
                    self.buckets[order] = _CandidateBucket(previous.ordinary.copy(), previous.events.copy(),
                        previous.merged.copy(), previous.candidates, previous.point, previous.base.copy())
                dirty.add(order)
                changed.append(order)
            return self.buckets[order]

        for name, old, new, start in (('ordinary', previous_candidates, candidates, candidate_from),
                                     ('base', previous_points, points, point_from)):
            # 同坐标可有多条证据；边界前的同桶前缀必须保留，不能整桶清空。
            removed: dict[tuple[int, int], int] = {}
            for point in old[start:]:
                order = _order(point)
                removed[order] = removed.get(order, 0) + 1
            for order, count in removed.items():
                records = getattr(edit(order), name)
                del records[len(records) - count:]
            for point in new[start:]:
                getattr(edit(_order(point)), name).append(
                    _judgement_working_points((point,), owned=self._owned_result)[0] if name == 'base' else point)
        for turn in (upgrade.merged.values() if not self.event_count and not self.revision
                     else upgrade.updated_merged.values()):
            point = turn.source
            edit(_order(point)).merged[turn.index, turn.ordinal, turn.kind, turn.value] = point
        for point in events[self.event_count:]:
            edit(_order(point)).events.append(point)
        self.event_count = len(events)
        return self._finish(dirty, positions, level, changed)


def _copy_append_states(
    ordinary: _TrendReversalState, upgrade: _TrendUpgradeState, events: _EventRows,
) -> tuple[_TrendReversalState, _TrendUpgradeState, _EventRows]:
    """复制会被追加修改的容器；已发布的来源和证据叶子在状态内保持只读。

    追加器替换 merged 的事件、向 selected/event_sink 追加新事件，不修改
    已保存的事件正文。树的数组及 recent 的内层列表则必须各自拥有副本。
    """
    copies = copy(ordinary), copy(upgrade)
    for original, cloned in zip((ordinary, upgrade), copies):
        for name, value in vars(original).items():
            if type(value) in (dict, list, FrozenPrefixList, FrozenPrefixMap):
                setattr(cloned, name, value.copy())
    cloned_upgrade = copies[1]
    for name in ('keys_by_kind', 'origins_by_kind'):
        trees: dict[str, _PointRange] = {}
        for kind, tree in getattr(upgrade, name).items():
            cloned_tree = copy(tree)
            cloned_tree.points = tree.points.copy()
            trees[kind] = cloned_tree
        setattr(cloned_upgrade, name, trees)
    cloned_upgrade.recent = {kind: points.copy() for kind, points in upgrade.recent.items()}
    copied_events = events.copy()
    cloned_upgrade.event_sink = copied_events
    return copies[0], cloned_upgrade, copied_events


def _append_state_containers(
    ordinary: _TrendReversalState | None, upgrade: _TrendUpgradeState | None,
    events: _EventRows,
) -> list[object]:
    containers: list[object] = [events]
    for state in (ordinary, upgrade):
        if state is not None:
            containers.extend((state, vars(state)))
            containers.extend(value for value in vars(state).values()
                              if type(value) in (dict, list, FrozenPrefixList, FrozenPrefixMap))
    if upgrade is not None:
        containers.extend(upgrade.recent.values())
        for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]:
            containers.extend((tree, vars(tree), tree.points))
    return containers


def _seal_checkpoint_records(ordinary: _TrendReversalState | None, upgrade: _TrendUpgradeState,
                             events: _EventRows) -> FrozenPrefixList[dict[str, object]]:
    """封存当前知识版本的追加记录，使长时间没有新关键位时仍能廉价存检查点。

    这是可恢复的版本快照，不推进业务固化线。尾部修订恢复旧版本；价格、
    资格和整波状态继续演进，不能因为记录共享而把变化区判为已完成。
    """
    for state in (ordinary, upgrade):
        if state is None:
            continue
        for name, value in list(vars(state).items()):
            if type(value) in (dict, FrozenPrefixMap) and name not in (
                    'keys_by_kind', 'origins_by_kind', 'recent', 'updated_merged'):
                index = value if type(value) is FrozenPrefixMap else FrozenPrefixMap(value.items())
                index.freeze()
                setattr(state, name, index)
        names = ('turns', 'highs', 'lows', 'selected') if state is ordinary else (
            'turns', 'regular', 'orders', 'merged_order')
        for name in names:
            value = getattr(state, name)
            records = value if type(value) is FrozenPrefixList else FrozenPrefixList(value)
            records.freeze(len(records))
            setattr(state, name, records)
    for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]:
        tree.points.freeze()
    saved = events if type(events) is FrozenPrefixList else FrozenPrefixList(events)
    saved.freeze(len(saved))
    upgrade.event_sink = saved
    return saved


class _TrendJudgementState:
    """单路径追加状态；保留最近来源翻向及尾部检查点，修订只重放有效后缀。"""

    def __init__(self, trend_level: int, *, memory_bytes: int = 8 * 1024 * 1024,
                 _tail_reuse: bool = False, ordinary_trend_completion_enabled: bool | None = None) -> None:
        if type(trend_level) is not int or trend_level not in (1, 2, 3):
            raise ValueError('trend_level must be 1, 2 or 3')
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('judgement memory_bytes must be a positive integer')
        self.trend_level = trend_level
        self.ordinary_trend_completion_enabled = (ordinary_completion_enabled()
            if ordinary_trend_completion_enabled is None else ordinary_trend_completion_enabled)
        self._limit = memory_bytes
        self._thread = get_ident()
        self._tail_reuse = _tail_reuse
        self._budget_owner = current_evidence()
        self._frozen_budget_owner = _current_frozen_budget_owner()
        self._checkpoint: tuple[tuple[bytes, ...], tuple[bytes, ...], _TrendReversalState,
                                _TrendUpgradeState, _EventRows] | None = None
        self._flip_checkpoint: tuple[tuple[bytes, ...], tuple[bytes, ...], _TrendReversalState,
                                     _TrendUpgradeState, _EventRows] | None = None
        self._checkpoint_ledger: _JudgementLedger | None = None
        self._flip_checkpoint_ledger: _JudgementLedger | None = None
        self._checkpoint_payload_upper_bytes = 0
        self._flip_payload_upper_bytes = 0
        self._flip_budget_token: object = object()
        self._checkpoint_flip_token: object | None = None
        self.tail_restores = 0
        self.flip_restores = 0
        self._source_keys: tuple[bytes, ...] = ()
        self._source_version: object | None = None
        self._regular_keys: tuple[bytes, ...] = ()
        self._ordinary: _TrendReversalState | None = None
        self._upgrade: _TrendUpgradeState | None = None
        self._events: _EventRows = []
        self._ledger: _JudgementLedger | None = None
        self.frozen_boundary: tuple[int, int] | None = None
        self.frozen_known: Moment | None = None
        self.frozen_advances = 0
        self.frozen_invalidations = 0
        self._payload_upper_bytes = 0
        self.memory_used = 0
        self.peak_retained_bytes = 0
        self.peak_observed_bytes = 0
        self.cold_runs = 0
        self.append_runs = 0
        self.deep_budget_walks = 0
        self.budget_rejections = 0

    @staticmethod
    def _copy_ledger(ledger: _JudgementLedger | None) -> _JudgementLedger | None:
        """检查点复制可变索引，已发布点与证据叶子仍共享不可变版本。"""
        if ledger is None:
            return None
        cloned = copy(ledger)
        for name, value in vars(ledger).items():
            if type(value) in (list, FrozenPrefixList):
                value = value if type(value) is FrozenPrefixList else FrozenPrefixList(value)
                value.freeze(len(value))
                setattr(ledger, name, value)
            elif type(value) in (dict, FrozenPrefixMap):
                value = value if type(value) is FrozenPrefixMap else FrozenPrefixMap(value.items())
                value.freeze()
                setattr(ledger, name, value)
            if type(value) in (dict, list, FrozenPrefixList, FrozenPrefixMap):
                setattr(cloned, name, value.copy())
        return cloned

    def _freeze_prefix(self) -> None:
        """两个当前关键位中取价格位置较新者，确认到达后才推进固化线。

        来源与输出序列只封存 B 之前的记录，B 本身仍在变化区。索引封存
        当前可知版本，后续对旧结构追加结束证明仍写入新的变化覆盖层。
        """
        upgrade = self._upgrade
        if upgrade is None or self._ordinary is None or upgrade.prefix_known is None:
            return
        markers = [position for position in (upgrade.last_fall_high_position, upgrade.last_rise_low_position)
                   if position is not None]
        if not markers:
            return
        boundary = max((upgrade.turns[position].index, upgrade.turns[position].ordinal) for position in markers)
        if boundary == self.frozen_boundary:
            return
        if self.frozen_boundary is not None and boundary < self.frozen_boundary:
            # 冻结整波起点可能重新成为当前攻击 key，不能强行维持更晚的局部 key。
            # 旧记录仍是不可变版本；新版本允许修订变化区，不借边界删除前史。
            self.frozen_invalidations += 1
        self.frozen_boundary, self.frozen_known = boundary, upgrade.prefix_known
        self.frozen_advances += 1

        def seal_sequence(owner: object, name: str, order: Callable[[object], tuple[int, int]]) -> None:
            raw = getattr(owner, name)
            values = raw if type(raw) is FrozenPrefixList else FrozenPrefixList(raw)
            stop = values.frozen_count
            while stop < len(values) and order(values[stop]) < boundary:
                stop += 1
            values.freeze(stop)
            setattr(owner, name, values)

        def turn_order(value: object) -> tuple[int, int]:
            turn = cast(_SourceTurn, value)
            return turn.index, turn.ordinal

        def point_order(value: object) -> tuple[int, int]:
            return _order(cast(Mapping[str, object], value))

        for state in (self._ordinary, upgrade):
            # 这些映射的值为不可变来源／候选；覆盖用尾部版本表达。
            for name, value in list(vars(state).items()):
                if type(value) in (dict, FrozenPrefixMap) and name not in (
                        'keys_by_kind', 'origins_by_kind', 'recent', 'updated_merged'):
                    index = value if type(value) is FrozenPrefixMap else FrozenPrefixMap(value.items())
                    index.freeze()
                    setattr(state, name, index)
            seal_sequence(state, 'turns', turn_order)
        ordinary = self._ordinary
        for name in ('highs', 'lows'):
            seal_sequence(ordinary, name, lambda position: turn_order(ordinary.turns[cast(int, position)]))
        seal_sequence(ordinary, 'selected', point_order)
        seal_sequence(self, '_events', point_order)
        upgrade.event_sink = self._events
        seal_sequence(upgrade, 'regular', turn_order)
        for name in ('orders', 'merged_order'):
            seal_sequence(upgrade, name, lambda value: cast(tuple[int, int], cast(tuple[object, ...], value)[:2]))
        for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]:
            tree.points.freeze()
        if self._ledger is not None:
            ledger = self._ledger
            buckets = ledger.buckets
            if type(buckets) is not FrozenPrefixMap:
                buckets = FrozenPrefixMap(buckets.items())
            buckets.freeze()
            ledger.buckets = buckets
            for name in ('orders', 'geometry_orders'):
                seal_sequence(ledger, name, lambda value: cast(tuple[int, int], value))
            for name in ('geometry', 'points'):
                seal_sequence(ledger, name, point_order)
            seal_sequence(ledger, 'candidates', point_order)
            offsets = ledger.candidate_offsets
            if type(offsets) is not FrozenPrefixList:
                offsets = FrozenPrefixList(offsets)
            offsets.freeze(max(offsets.frozen_count, bisect_left(ledger.orders, boundary)))
            ledger.candidate_offsets = offsets
            counts = ledger.counts
            if type(counts) is not FrozenPrefixList:
                counts = FrozenPrefixList(counts)
            counts.freeze(max(counts.frozen_count, bisect_left(ledger.geometry_orders, boundary)))
            ledger.counts = counts

    @staticmethod
    def _ledger_containers(ledger: _JudgementLedger | None) -> list[object]:
        if ledger is None:
            return []
        containers: list[object] = [ledger, vars(ledger), *vars(ledger).values()]
        return containers

    def _save_tail(self) -> None:
        if self._ordinary is None or self._upgrade is None:
            return
        self._events = _seal_checkpoint_records(self._ordinary, self._upgrade, self._events)
        if self._ledger is not None:
            buckets = self._ledger.buckets
            if type(buckets) is not FrozenPrefixMap:
                buckets = FrozenPrefixMap(buckets.items())
            buckets.freeze()
            self._ledger.buckets = buckets
        ordinary, upgrade, events = _copy_append_states(self._ordinary, self._upgrade, self._events)
        ledger = self._copy_ledger(self._ledger)
        copied = _append_state_containers(ordinary, upgrade, events)
        copied.extend(self._ledger_containers(ledger))
        # 回退仅保留一代可变容器。避免每次追加都序列化整段历史和证明树；
        # 证据叶子共享且只读，完整对象图仍受 calculate 的同一内存上限约束。
        if sum(getsizeof(container) for container in copied) <= min(4 * 1024 * 1024, self._limit // 2):
            self._checkpoint = self._source_keys, self._regular_keys, ordinary, upgrade, events
            self._checkpoint_ledger = ledger
            self._checkpoint_payload_upper_bytes = self._payload_upper_bytes
            self._checkpoint_flip_token = self._flip_budget_token
        else:
            self._checkpoint = None
            self._checkpoint_ledger = None
            self._checkpoint_payload_upper_bytes = 0
            self._checkpoint_flip_token = None

    def remember_source_flip(self) -> None:
        """保存首次可知翻向之后的状态；包括旧关键位、证明及全局来源位置。

        新增点直接追加到当前状态。尾部修订时回到这里，只处理翻向之后的
        新来源；前缀证据有变或容量不足时拒绝复用，不能用截断输入造新趋势。
        """
        if (self._budget_owner is not current_evidence()
                or self._frozen_budget_owner is not _current_frozen_budget_owner()):
            self.clear()
            return
        if self._ordinary is None or self._upgrade is None:
            return
        if self._flip_checkpoint is not None and self._flip_checkpoint[0] == self._source_keys:
            return
        self._events = _seal_checkpoint_records(self._ordinary, self._upgrade, self._events)
        ordinary, upgrade, events = _copy_append_states(self._ordinary, self._upgrade, self._events)
        self._flip_checkpoint = self._source_keys, self._regular_keys, ordinary, upgrade, events
        self._flip_checkpoint_ledger = self._copy_ledger(self._ledger)
        self._flip_budget_token = object()
        # 检查点只新增浅复制容器，证据叶子已在负载上界内；仅上界触及
        # 预算时遍历对象图校准，避免每次翻向都重新读取全部历史证明。
        measured = self._payload_upper_bytes + self._container_bytes() + 4096
        if measured > self._limit:
            measured = self._owned_bytes()
            self.deep_budget_walks += 1
        if measured > self._limit:
            self._flip_checkpoint = None
            self._flip_checkpoint_ledger = None
            measured = self._owned_bytes()
            self.deep_budget_walks += 1
        if measured > self._limit:
            self.budget_rejections += 1
            self.clear()
            return
        self.memory_used = measured
        self.peak_retained_bytes = max(self.peak_retained_bytes, measured)
        self._payload_upper_bytes = max(0, measured - self._container_bytes())
        self._flip_payload_upper_bytes = self._payload_upper_bytes

    def _restore_tail(self, certified: tuple[bytes, ...], *, flip: bool = False) -> bool:
        saved = self._flip_checkpoint if flip else self._checkpoint
        if (saved is None or len(certified) < len(saved[0])
                or certified[:len(saved[0])] != saved[0]):
            return False
        restored_payload = (self._flip_payload_upper_bytes if flip else
            self._checkpoint_payload_upper_bytes if self._checkpoint_flip_token is self._flip_budget_token
            else self._payload_upper_bytes)
        self._ordinary, self._upgrade, self._events = _copy_append_states(*saved[2:])
        # 判定状态和发布账本必须恢复同一来源版本；丢掉账本会让每天的尾点
        # 修订重建所有历史桶，并把整份旧证据再次加入增量内存计费。
        self._ledger = self._copy_ledger(self._flip_checkpoint_ledger if flip else self._checkpoint_ledger)
        self._source_version = None
        self._source_keys, self._regular_keys = saved[:2]
        # 恢复的是旧知识版本；下一次按恢复后的真实关键位重新决定固化线。
        self.frozen_boundary = self.frozen_known = None
        if flip:
            self._checkpoint = None
            self._checkpoint_ledger = None
        # 恢复状态也恢复同版本的容量上界，撤销已丢弃尾部的费用。另一个翻向
        # 检查点发生变化时仍沿用当前保守上界，避免漏计后来保留的证据。
        self._payload_upper_bytes = restored_payload
        measured = self._payload_upper_bytes + self._container_bytes() + 4096
        calibrated = measured > self._limit
        if calibrated:
            self.deep_budget_walks += 1
            measured = self._owned_bytes()
        if measured > self._limit:
            self.clear()
            return False
        self.memory_used = measured
        if calibrated:
            self._payload_upper_bytes = max(0, measured - self._container_bytes())
        if flip:
            self.flip_restores += 1
        else:
            self.tail_restores += 1
        return True

    def clear(self) -> None:
        self._checkpoint = None
        self._flip_checkpoint = None
        self._checkpoint_ledger = None
        self._flip_checkpoint_ledger = None
        self._checkpoint_payload_upper_bytes = self._flip_payload_upper_bytes = 0
        self._flip_budget_token = object()
        self._checkpoint_flip_token = None
        self._source_keys = ()
        self._source_version = None
        self._regular_keys = ()
        self._ordinary = None
        self._upgrade = None
        self._events = []
        self._ledger = None
        self.frozen_boundary = self.frozen_known = None
        self._payload_upper_bytes = 0
        self.memory_used = 0
        self._budget_owner = current_evidence()
        self._frozen_budget_owner = _current_frozen_budget_owner()

    def _owned_bytes(self) -> int:
        return self._owned_value_bytes([self])

    def _owned_value_bytes(self, roots: Sequence[object], *, _incremental: bool = False) -> int:
        # 已认证的标准容器及内部状态对象才可进入计数，不调用未知 __sizeof__
        # 或遍历全局函数/类引用。相同来源、证明和叶子只按实际 identity 计一次。
        pending: list[object] = list(roots)
        seen: set[int] = set()
        owner = current_evidence()
        frozen_owner = _current_frozen_budget_owner()
        size = 0
        state_types = (_TrendJudgementState, _TrendReversalState, _TrendUpgradeState,
                       _PointRange, _SourceTurn, LinePoint, ReversalPoint,
                       _JudgementLedger, _CandidateBucket, FrozenPrefixList, FrozenPrefixMap, _IndexArchive,
                       _IndexNode, _SequenceNode)
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            # 请求池已按完整对象图计费并持有该不可变节点。状态只承担引用，
            # 不在每次追加/容量校准时重新扫描旧证明；池外对象仍完整计费。
            if owner is not None and owner.reference(value) is not None:
                size += 8
                continue
            if (frozen_owner is not None and type(value) is _FrozenPoint
                    and value._budget_owner is frozen_owner):
                size += 8
                continue
            if _incremental and type(value) is _FrozenPoint and value._budget_upper_bytes is not None:
                size += value._budget_upper_bytes
                continue
            size += getsizeof(value)
            if type(value) is _TrendJudgementState:
                attributes = vars(value)
                seen.add(id(attributes))
                size += getsizeof(attributes)
                pending.extend(attributes.keys())
                diagnostics = ('memory_used', 'peak_retained_bytes', 'peak_observed_bytes',
                               '_payload_upper_bytes', '_checkpoint_payload_upper_bytes', '_flip_payload_upper_bytes',
                               'cold_runs', 'append_runs', 'deep_budget_walks', 'budget_rejections',
                               'tail_restores', 'flip_restores', 'frozen_advances', 'frozen_invalidations')
                pending.extend(item for key, item in attributes.items() if key not in diagnostics)
                # 计数字段自身不能令计数结果变化；为所有计数器分别预留
                # 64 位正整数的完整对象大小，覆盖受支持主机的地址空间。
                size += len(diagnostics) * getsizeof(2 ** 64)
            elif type(value) is _FrozenPoint:
                # slots 快照没有 __dict__；证据与导出模板都属于预算。
                if value._budget_upper_bytes is not None:
                    size += getsizeof(value._budget_upper_bytes)
                pending.extend((value.data, value.json_template))
            elif type(value) in (_SequenceNode, _IndexNode):
                pending.extend(_node_children(value))
            elif type(value) in state_types:
                pending.append(vars(value))
            elif type(value) is MappingProxyType:
                pending.extend(get_referents(value))
            elif type(value) is dict:
                mapping = cast(dict[object, object], value)
                pending.extend(mapping.keys())
                pending.extend(mapping.values())
            elif type(value) in (tuple, list, set, frozenset):
                pending.extend(cast(Sequence[object], value))
        return size

    def _container_bytes(self) -> int:
        """可变索引只计当前容量，历史证据对象由冷计数或追加准入单独计费。"""
        containers: list[object] = [vars(self), self._source_keys, self._regular_keys]
        containers.extend(_append_state_containers(self._ordinary, self._upgrade, self._events))
        for ledger in (self._ledger, self._checkpoint_ledger, self._flip_checkpoint_ledger):
            containers.extend(self._ledger_containers(ledger))
        for checkpoint in (self._checkpoint, self._flip_checkpoint):
            if checkpoint is not None:
                containers.extend((checkpoint, checkpoint[0], checkpoint[1]))
                containers.extend(_append_state_containers(*checkpoint[2:]))
        seen: set[int] = set()
        size = 0
        for container in containers:
            if id(container) not in seen:
                seen.add(id(container))
                size += getsizeof(container)
                if type(container) in (FrozenPrefixList, FrozenPrefixMap):
                    size += container.storage_bytes
        # 已发布桶只读，随 changed_objects 计入不可变载荷；这里只统计
        # 可变顶层索引。检查点共享旧桶，不重复遍历或重复扣除其容器费用。
        return size

    def calculate(self, source: Sequence[Mapping[str, object]], positions: Sequence[int],
                  *, _validated_turns: Sequence[_SourceTurn] | None = None,
                  _certified_standard: bool = False, _source_revision: _SourceVersion | None = None) -> tuple[
        list[dict[str, object]], list[dict[str, object]], _EventRows,
    ]:
        if get_ident() != self._thread:
            ordinary = trend_reversals(source, trend_level=self.trend_level)
            events: _EventRows = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=self.trend_level, event_sink=events,
                ordinary_trend_completion_enabled=self.ordinary_trend_completion_enabled)
            return ordinary, merged, events
        if (self._budget_owner is not current_evidence()
                or self._frozen_budget_owner is not _current_frozen_budget_owner()):
            # 旧请求的池已不再为当前状态承担共享载荷，必须重新建立预算。
            self.clear()
        # 私有入口本身也拒绝未知及非有限审计值；不能依赖某一调用方的准入。
        # 先 exact builtin 编码才使用 C JSON，未知对象没有额外序列化钩子。
        # 只有缓存入口已完整认证 exact builtin/finite 的工作集才能传入证书；
        # 公共入口仍独立认证。可知来源解析、逐点全文键和修订门禁始终执行。
        finite = _certified_standard or _geometry_signature(source) is not None
        if finite and not _certified_standard:
            finite = finite_evidence(source)
        # 每次仍完整执行共同来源校验；价格位置、真实结束时钟与原位置都认证。
        source_keys = (_source_revision.certificates if _source_revision is not None else
                       tuple(_geometry_signature((position, point)) for position, point in zip(positions, source)))
        if not finite or (_source_revision is None and any(key is None for key in source_keys)):
            self.clear()
            ordinary = trend_reversals(source, trend_level=self.trend_level)
            events = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=self.trend_level, event_sink=events,
                ordinary_trend_completion_enabled=self.ordinary_trend_completion_enabled)
            return ordinary, merged, events
        certified = cast(tuple[bytes, ...], source_keys)
        turns = _validated_turns if _validated_turns is not None else _source_turns(source, self.trend_level - 1)
        version_append = (_source_revision is not None and _source_revision.parent is self._source_version
                          and self._source_version is not None
                          and _source_revision.changed_from >= len(self._source_keys))
        appendable = (self._ordinary is not None and self._upgrade is not None
                      and len(certified) >= len(self._source_keys)
                      and (version_append or certified[:len(self._source_keys)] == self._source_keys))
        restored = False
        if not appendable and self._tail_reuse:
            # 翻向点可能距今很远；选最近仍完全匹配的检查点，避免优先回退
            # 到旧翻向使每日尾点修订重放整个趋势段。前缀修订仍逐字拒绝。
            checkpoints = sorted(
                ((len(saved[0]), flip) for flip, saved in
                 ((False, self._checkpoint), (True, self._flip_checkpoint)) if saved is not None),
                reverse=True,
            )
            for _, flip in checkpoints:
                if self._restore_tail(certified, flip=flip):
                    restored = appendable = True
                    break
        if appendable and self._tail_reuse and not restored and len(certified) > len(self._source_keys):
            self._save_tail()
        if appendable:
            assert self._ordinary is not None and self._upgrade is not None
            previous_source_count = len(self._ordinary.turns)
            previous_ordinary_count = len(self._ordinary.selected)
            previous_regular_count = len(self._regular_keys)
            previous_orders_count = len(self._upgrade.orders)
            previous_high_count, previous_low_count = len(self._ordinary.highs), len(self._ordinary.lows)
            previous_event_count = len(self._events)
            appended = turns[len(self._ordinary.turns):]
            # 公共前驱映射的 key 不能被新来源覆盖；同 K ordinal 保留原规则。
            reversals = {turn.reversal for turn in appended}
            appendable = (len(reversals) == len(appended)
                          and not any(reversal in self._ordinary._sources_by_reversal for reversal in reversals))
            if appendable:
                ordinary = self._ordinary.extend(appended, _snapshot=False)
                # selected 是本实例只追加的私有账本，旧记录从不改写；来源修订
                # 已由全文 source_keys 拒绝。仅复核最后一个旧点与新增尾部，
                # 保留跨批次排序/时钟门禁以及原始候选位置。
                boundary = max(0, previous_ordinary_count - 1)
                regular_tail = _source_turns(ordinary[boundary:], self.trend_level,
                    retain_geometry=True, accept_direction_facts=True, _position_offset=boundary)
                regular = regular_tail[1:] if previous_ordinary_count else regular_tail
                appended_keys = tuple(_geometry_signature(turn.source) for turn in regular)
                regular_keys = (*self._regular_keys, *appended_keys)
                appendable = (all(key is not None for key in appended_keys)
                              and self._upgrade.append(appended, regular))
        if not appendable:
            self.clear()
            self._ordinary = _TrendReversalState(self.trend_level)
            ordinary = self._ordinary.extend(turns, _snapshot=False)
            regular = _source_turns(ordinary, self.trend_level, retain_geometry=True, accept_direction_facts=True)
            regular_keys = tuple(_geometry_signature(turn.source) for turn in regular)
            self._upgrade = _TrendUpgradeState(turns, regular, trend_level=self.trend_level, event_sink=self._events,
                ordinary_trend_completion_enabled=self.ordinary_trend_completion_enabled)
            self.cold_runs += 1
        else:
            self.append_runs += 1
        assert self._upgrade is not None
        calculated_upgrade = self._upgrade
        merged = calculated_upgrade.advance(_snapshot=False)
        events = self._events
        certified_regular = cast(tuple[bytes, ...], regular_keys)
        if appendable:
            self._source_keys = certified
            self._regular_keys = certified_regular
            assert self._ordinary is not None
            upgrade = self._upgrade
            new_orders = upgrade.orders[previous_orders_count:]
            roots: list[object] = [
                *self._ordinary.turns[previous_source_count:],
                *self._ordinary.selected[previous_ordinary_count:],
                *upgrade.regular[previous_regular_count:], *events[previous_event_count:],
                *self._source_keys[previous_source_count:], *self._regular_keys[previous_regular_count:],
                *self._ordinary.highs[previous_high_count:], *self._ordinary.lows[previous_low_count:],
                *new_orders, *(upgrade.positions[order] for order in new_orders),
                *(upgrade.source_positions[order] for order in new_orders),
                upgrade.regular_cursor, upgrade.processed_count, upgrade.prefix_known, upgrade.owned_direction,
                upgrade.last_fall_high_position, upgrade.last_rise_low_position,
                self._ordinary.direction, self._ordinary.candidate, self._ordinary.prefix_known,
                *(tree.size for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]),
            ]
            roots.extend(upgrade.updated_merged.values())
            roots.extend(upgrade.updated_merged)
            roots.extend(upgrade.new_merged_keys)
            # 新证据可引用旧子树，逐批重复计费形成保守上界；不持有逐日版本、
            # object-id memo 或已被替换的证据。可变顶层容器使用其当前真实容量。
            self._payload_upper_bytes += self._owned_value_bytes(roots, _incremental=True)
            observed_bytes = self._payload_upper_bytes + self._container_bytes()
        else:
            self._source_keys = certified
            self._regular_keys = certified_regular
            observed_bytes = self._owned_bytes()
            self.deep_budget_walks += 1
            self._payload_upper_bytes = observed_bytes - self._container_bytes()
        if appendable and observed_bytes > self._limit:
            # 保守上界靠近预算时重新校准当前完整对象图，避免重复计费误拒绝。
            observed_bytes = self._owned_bytes()
            self.deep_budget_walks += 1
            self._payload_upper_bytes = observed_bytes - self._container_bytes()
        # 一条路径仅保留最近状态，超预算的完整证明沿用冷判定，不保留逐日版本。
        self.peak_observed_bytes = max(self.peak_observed_bytes, observed_bytes)
        if observed_bytes > self._limit:
            self.budget_rejections += 1
            self.clear()
        else:
            self.memory_used = observed_bytes
            self.peak_retained_bytes = max(self.peak_retained_bytes, observed_bytes)
            self._source_version = _source_revision.token if _source_revision is not None else None
        if self._upgrade is None:
            merged = calculated_upgrade.snapshot()
        return ordinary, merged, events

    def finalize(self, ordinary: Sequence[Mapping[str, object]], merged: Sequence[Mapping[str, object]],
                 events: _EventRows, positions: Sequence[int], *, _owned_result: bool = False) -> TrendJudgement:
        if (get_ident() == self._thread and (self._budget_owner is not current_evidence()
                or self._frozen_budget_owner is not _current_frozen_budget_owner())):
            self.clear()
        if self._ordinary is None or self._upgrade is None or get_ident() != self._thread:
            return _finalize_judgement(ordinary, merged, events, positions, self.trend_level,
                                      _owned_result=_owned_result)
        if self._ledger is not None and self._ledger._owned_result != _owned_result:
            self._ledger = None
        initial = self._ledger is None
        if self._ledger is None:
            self._ledger = _JudgementLedger(_owned_result=_owned_result)
        result, changed = self._ledger.update(self._ordinary, self._upgrade, events, positions,
                                              initial=initial, level=self.trend_level)
        self._freeze_prefix()
        self._payload_upper_bytes += self._owned_value_bytes(changed, _incremental=True)
        # 账本含共享来源与可变桶；预留固定对象头余量，临近上限再精确校准。
        observed = self._payload_upper_bytes + self._container_bytes() + 4096
        if observed > self._limit:
            self.deep_budget_walks += 1
            observed = self._owned_bytes()
            self._payload_upper_bytes = max(0, observed - self._container_bytes())
        self.peak_observed_bytes = max(self.peak_observed_bytes, observed)
        if observed > self._limit:
            self.budget_rejections += 1
            self.clear()
        else:
            self.memory_used = observed
            self.peak_retained_bytes = max(self.peak_retained_bytes, observed)
        return result


def _finalize_judgement(
    ordinary: Sequence[Mapping[str, object]], merged: Sequence[Mapping[str, object]],
    upgrade_events: Sequence[Mapping[str, object]], positions: Sequence[int], trend_level: int,
    *, _owned_result: bool = False,
) -> TrendJudgement:
    source_level = trend_level - 1
    candidates = _restore_source_positions(_complete_candidates(ordinary, [*merged, *upgrade_events]), positions, source_level)
    # 普通方向账本完整保留，但只有共同整波状态授予身份的事件可递归。
    owned = _restore_source_positions(_complete_candidates([], [*merged, *upgrade_events]), positions, source_level)
    points = _canonical_points(owned)
    counts = {'H': 0, 'L': 0}
    for point in points:
        kind = cast(str, point['kind'])
        counts[kind] += 1
        point.update(state='reversal', label=f'{kind}{counts[kind]}')
    if _owned_result:
        return _OwnedTrendJudgement(trend_level, source_level,
                                    tuple(dict(point) for point in points), tuple(candidates))
    frozen_memo: dict[int, tuple[object, object]] = {}
    return TrendJudgement(trend_level, source_level,
                          tuple(_frozen_point(point, frozen_memo) for point in points),
                          tuple(_frozen_point(point, frozen_memo) for point in candidates))


class _FormalKeyBreakState:
    """严格突破旁路的单路径状态；原始来源和普通候选均须保持完整证据前缀。

    普通来源状态只消费已结束波，不能直接拿它的 turns 替代正式开放端点。
    本状态独立保留正式来源投影；变化尾部重验相邻边界，旧候选迟知、删除、
    证明修订或追加门禁失败均重新执行原严格状态机，不裁掉冻结关键位。
    """

    def __init__(self, trend_level: int, *, memory_bytes: int = 8 * 1024 * 1024,
                 _consumption_checkpoints: bool = True) -> None:
        if type(trend_level) is not int or trend_level not in (2, 3):
            raise ValueError('formal key break level must be 2 or 3')
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('formal key break memory_bytes must be a positive integer')
        self.trend_level = trend_level
        self._limit = memory_bytes
        self._thread = get_ident()
        self._use_consumption_checkpoints = _consumption_checkpoints
        self._consumption = _UpgradeConsumptionCache(memory_bytes // 2)
        self.clear()

    def clear(self) -> None:
        self._checkpoint: _FormalKeyBreakState | None = None
        self._consumption.reset()
        self._budget_owner = current_evidence()
        self._frozen_budget_owner = _current_frozen_budget_owner()
        self._source_keys: tuple[bytes, ...] = ()
        self._candidate_keys: tuple[str, ...] = ()
        self._turns: list[_SourceTurn] = []
        self._candidates: list[dict[str, object]] = []
        self._regular: list[_SourceTurn] = []
        self._regular_offsets = FrozenPrefixList([0])
        self._upgrade: _TrendUpgradeState | None = None
        self._events: _EventRows = []
        self._payload_upper_bytes = 0
        self.memory_used = 0
        self._ledger: _JudgementLedger | None = None
        self._base_points: Sequence[Mapping[str, object]] = ()
        self._base_token: object | None = None
        self._source_token: object | None = None
        self._early_attack: int | None = None
        self._owned_result_mode: bool | None = None

    @staticmethod
    def _value_bytes(roots: Sequence[object], *, _incremental: bool = False) -> int:
        # 入场已拒绝未知审计值；只遍历本地状态和标准容器，不追踪类／函数全局。
        pending = list(roots)
        seen: set[int] = set()
        owner = current_evidence()
        frozen_owner = _current_frozen_budget_owner()
        size = 0
        while pending:
            value = pending.pop()
            identity = id(value)
            if identity in seen:
                continue
            seen.add(identity)
            # 与普通趋势状态共用请求级所有权边界；不得把共享旧证明
            # 再次计入每条严格突破状态，更不能据此触发整段冷重算。
            if owner is not None and owner.reference(value) is not None:
                size += 8
                continue
            if (frozen_owner is not None and type(value) is _FrozenPoint
                    and value._budget_owner is frozen_owner):
                size += 8
                continue
            if _incremental and type(value) is _FrozenPoint and value._budget_upper_bytes is not None:
                size += value._budget_upper_bytes
                continue
            size += getsizeof(value)
            if type(value) is dict:
                pending.extend(cast(dict[object, object], value).keys())
                pending.extend(cast(dict[object, object], value).values())
            elif type(value) in (list, tuple, set, frozenset):
                pending.extend(cast(Sequence[object], value))
            elif type(value) is _FrozenPoint:
                if value._budget_upper_bytes is not None:
                    size += getsizeof(value._budget_upper_bytes)
                pending.extend((value.data, value.json_template))
            elif type(value) in (_SequenceNode, _IndexNode):
                pending.extend(_node_children(value))
            elif type(value) is MappingProxyType:
                pending.extend(get_referents(value))
            elif type(value) in (_TrendUpgradeState, _PointRange, _SourceTurn, ReversalPoint, LinePoint,
                                FrozenPrefixList, FrozenPrefixMap, _IndexArchive, _IndexNode, _SequenceNode,
                                _JudgementLedger, _CandidateBucket):
                pending.append(vars(value))
        return size

    def _containers(self, *, _include_consumption: bool = True) -> list[object]:
        containers: list[object] = [self._source_keys, self._candidate_keys, self._turns,
            self._candidates, self._regular, self._regular_offsets,
            *_append_state_containers(None, self._upgrade, self._events)]
        containers.extend((self._base_points, *_TrendJudgementState._ledger_containers(self._ledger)))
        if _include_consumption:
            containers.extend(self._consumption.live_roots())
        if self._checkpoint is not None:
            containers.extend((self._checkpoint, vars(self._checkpoint)))
            containers.extend(self._checkpoint._containers(_include_consumption=False))
        return containers

    def _copy_checkpoint(self) -> '_FormalKeyBreakState':
        if self._upgrade is not None:
            self._events = _seal_checkpoint_records(None, self._upgrade, self._events)
        for name in ('_turns', '_candidates', '_regular', '_regular_offsets'):
            raw = getattr(self, name)
            records = raw if type(raw) is FrozenPrefixList else FrozenPrefixList(raw)
            records.freeze(len(records))
            setattr(self, name, records)
        saved = copy(self)
        saved._checkpoint = None
        saved._ledger = _TrendJudgementState._copy_ledger(self._ledger)
        saved._turns, saved._candidates, saved._regular = (
            self._turns.copy(), self._candidates.copy(), self._regular.copy())
        saved._regular_offsets = self._regular_offsets.copy()
        saved._events = self._events.copy()
        if self._upgrade is not None:
            upgrade = saved._upgrade = copy(self._upgrade)
            for name, value in vars(self._upgrade).items():
                if type(value) in (dict, list, FrozenPrefixList, FrozenPrefixMap):
                    setattr(upgrade, name, value.copy())
            for name in ('keys_by_kind', 'origins_by_kind'):
                trees: dict[str, _PointRange] = {}
                for kind, tree in getattr(self._upgrade, name).items():
                    cloned = copy(tree)
                    cloned.points = tree.points.copy()
                    trees[kind] = cloned
                setattr(upgrade, name, trees)
            upgrade.recent = {kind: points.copy() for kind, points in self._upgrade.recent.items()}
            upgrade.event_sink = saved._events
        return saved

    def _resume_checkpoint(self, source_keys: tuple[bytes, ...], candidate_keys: tuple[str, ...],
                           source: Sequence[Mapping[str, object]], result: TrendJudgement) -> None:
        def extends(state: '_FormalKeyBreakState') -> bool:
            source_append = (type(source) is CertifiedSource and source.owner is current_evidence()
                and (source.token is state._source_token or source.parent is state._source_token
                     and source.changed_from >= len(state._source_keys)))
            candidate_append = (result._delta is not None and result._delta.parent is state._base_token
                                and result._delta.candidates_from >= len(state._candidate_keys))
            return ((source_append or source_keys[:len(state._source_keys)] == state._source_keys)
                    and (candidate_append or candidate_keys[:len(state._candidate_keys)] == state._candidate_keys))
        if extends(self):
            if self._source_keys and (source_keys != self._source_keys or candidate_keys != self._candidate_keys):
                self._checkpoint = self._copy_checkpoint()
        elif self._checkpoint is not None and extends(self._checkpoint):
            checkpoint = self._checkpoint
            restored = checkpoint._copy_checkpoint()
            self.__dict__.update(vars(restored))
            self._checkpoint = checkpoint

    def _retain(self, *, appended: bool, added: Sequence[object]) -> None:
        containers = self._containers()
        container_size = sum(getsizeof(value) + (value.storage_bytes
            if type(value) in (FrozenPrefixList, FrozenPrefixMap) else 0)
            for value in {id(value): value for value in containers}.values())
        if appended:
            # 只为新来源、候选、事件和索引项计费；重复引用形成保守上界。
            self._payload_upper_bytes += self._value_bytes(added, _incremental=True)
            current = self._payload_upper_bytes + container_size
        else:
            current = self._value_bytes(containers)
            self._payload_upper_bytes = max(0, current - container_size)
        # 快照独占上界已在新增节点入场时认证，不能每天再次遍历全部旧图。
        # 与当前图的重复引用保守计费；临界时先丢加速快照再校准原状态。
        observed = current + self._consumption.retained_upper_bytes
        if appended and observed > self._limit:
            current = self._value_bytes(containers)
            self._payload_upper_bytes = max(0, current - container_size)
            observed = current + self._consumption.retained_upper_bytes
        if observed > self._limit and self._consumption.frames:
            # 检查点与现态共用原总预算；内存紧张先放弃恢复加速，保留原算法。
            self._consumption._count('total_budget_rejections')
            self._consumption.disable_for_budget()
            containers = self._containers()
            container_size = sum(getsizeof(value) + (value.storage_bytes
                if type(value) in (FrozenPrefixList, FrozenPrefixMap) else 0)
                for value in {id(value): value for value in containers}.values())
            current = self._value_bytes(containers)
            self._payload_upper_bytes = max(0, current - container_size)
            observed = current + self._consumption.retained_upper_bytes
        if observed > self._limit and self._checkpoint is not None:
            self._checkpoint = None
            containers = self._containers()
            container_size = sum(getsizeof(value) + (value.storage_bytes
                if type(value) in (FrozenPrefixList, FrozenPrefixMap) else 0)
                for value in {id(value): value for value in containers}.values())
            current = self._value_bytes(containers)
            self._payload_upper_bytes = max(0, current - container_size)
            observed = current + self._consumption.retained_upper_bytes
        if observed > self._limit:
            self.clear()
        else:
            self.memory_used = observed

    def calculate(self, source: Sequence[Mapping[str, object]], result: TrendJudgement,
                  *, _certified_keys: tuple[bytes, ...] | None = None) -> TrendJudgement | None:
        if get_ident() != self._thread or result.trend_level != self.trend_level:
            self.clear()
            return None
        if (self._budget_owner is not current_evidence()
                or self._frozen_budget_owner is not _current_frozen_budget_owner()):
            self.clear()
        owned_result = type(result) is _OwnedTrendJudgement
        if self._owned_result_mode is not None and self._owned_result_mode != owned_result:
            self.clear()
        self._owned_result_mode = owned_result
        if type(source) not in (list, tuple) and not (type(source) is CertifiedSource
                and source.owner is current_evidence() and _certified_keys is source.keys):
            self.clear()
            return None
        source_keys: list[bytes] = []
        if _certified_keys is not None:
            if len(_certified_keys) != len(source):
                raise ValueError('formal source certificates must cover the complete source')
        else:
            for point in source:
                signature = _geometry_signature(point) if type(point) is dict else None
                if signature is None:
                    self.clear()
                    return None
                source_keys.append(signature)
        candidate_start = (result._delta.candidates_from if result._delta is not None
                           and result._delta.parent is self._base_token else 0)
        candidate_keys: list[str] = list(self._candidate_keys[:candidate_start])
        for point in result.candidates[candidate_start:]:
            if owned_result:
                # 与公开 FrozenPoint 的 JSON 模板完全相同的内容键，不使用
                # 实例/lease 令牌替代相等性；只编码实际变化的候选后缀。
                try:
                    candidate_keys.append(json.dumps(point, allow_nan=False, ensure_ascii=False,
                                                      separators=(',', ':')))
                except (TypeError, ValueError):
                    self.clear()
                    return None
            elif type(point) is not _FrozenPoint or point.json_template is None:
                self.clear()
                return None
            else:
                candidate_keys.append(point.json_template)
        try:
            certified_source = _certified_keys if _certified_keys is not None else tuple(source_keys)
            certified_candidates = tuple(candidate_keys)
            self._resume_checkpoint(certified_source, certified_candidates, source, result)
            return self._calculate(source, result, certified_source, certified_candidates)
        except Exception:
            # 解析或状态推进抛错后不可再复用部分修改的状态；原异常仍向外传播。
            self.clear()
            raise

    def _calculate(
        self, source: Sequence[Mapping[str, object]], result: TrendJudgement,
        source_keys: tuple[bytes, ...], candidate_keys: tuple[str, ...],
    ) -> TrendJudgement:
        source_common = (source.changed_from if type(source) is CertifiedSource
            and source.owner is current_evidence() and source.parent is self._source_token else
            len(source) if type(source) is CertifiedSource and source.token is self._source_token else 0)
        while (source_common < min(len(source_keys), len(self._source_keys))
                and source_keys[source_common] == self._source_keys[source_common]):
            source_common += 1
        candidate_common = (result._delta.candidates_from if result._delta is not None
                            and result._delta.parent is self._base_token else 0)
        while (candidate_common < min(len(candidate_keys), len(self._candidate_keys))
                and candidate_keys[candidate_common] == self._candidate_keys[candidate_common]):
            candidate_common += 1
        source_appended = source_common == len(self._source_keys)
        candidates_appended = candidate_common == len(self._candidate_keys)
        old_turn_count, old_regular_count = len(self._turns), len(self._regular)
        old_event_count = len(self._events)
        previous_candidates = self._candidates
        previous_points = self._base_points
        old_order_count = len(self._upgrade.orders) if self._upgrade is not None else 0
        source_tail: list[_SourceTurn] = []
        if source_common != len(source_keys) or source_common != len(self._source_keys):
            # 前一正式点验证跨边界的坐标与时钟；此前跳过的 seed 等已由证书覆盖。
            boundary = max(0, bisect_left(self._turns, source_common, key=lambda turn: turn.position) - 1)
            start = min(source_common, self._turns[boundary].position) if self._turns else 0
            # 请求拥有的来源保持只读；投影只改外壳，无须再次复制整棵旧证明。
            owned = type(source) is CertifiedSource and source.owner is current_evidence()
            source_tail = _source_turns(_owned_mutable_points(source[start:], owned), self.trend_level - 1,
                formal_key_attacks=True, _position_offset=start)
            self._turns = [*self._turns[:boundary], *source_tail]
            if self._early_attack is None or self._early_attack >= boundary:
                self._early_attack = next((position for position in range(boundary, len(self._turns))
                    if _has_early_formal_attack((self._turns[position],))), None)
        if self._early_attack is None:
            previously_empty = self._upgrade is None and not self._candidates
            self._source_keys = source_keys
            self._candidate_keys = ()
            self._candidates = []
            self._regular = []
            self._regular_offsets = FrozenPrefixList([0])
            self._consumption.reset()
            self._upgrade = None
            self._events = []
            self._ledger = None
            self._base_points, self._base_token = (), None
            self._source_token = source.token if type(source) is CertifiedSource else None
            self._retain(appended=source_appended and previously_empty,
                         added=[*source_tail, *source_keys[source_common:]])
            return result
        owned_result = type(result) is _OwnedTrendJudgement
        new_candidates = _judgement_working_points(result.candidates[candidate_common:], owned=owned_result)
        self._candidates = [*self._candidates[:candidate_common], *new_candidates]
        # 候选可能被投影门禁过滤；保存每个候选后的真实输出长度，修订时
        # 精确保留已完整校验的前缀，不因索引不等长重新扫描全部旧证明。
        regular_start = self._regular_offsets[candidate_common]
        regular_tail: list[_SourceTurn] = []
        del self._regular_offsets[candidate_common + 1:]
        for candidate in new_candidates:
            regular_tail.extend(_source_turns([candidate], self.trend_level,
                retain_geometry=True, accept_direction_facts=True))
            self._regular_offsets.append(regular_start + len(regular_tail))
        del self._regular[regular_start:]
        self._regular.extend(regular_tail)
        appended = (self._upgrade is not None and source_appended and candidates_appended
                    and self._upgrade.append(self._turns[old_turn_count:], self._regular[old_regular_count:]))
        if not appended:
            self._events = []
            self._upgrade = _TrendUpgradeState(self._turns, self._regular, trend_level=self.trend_level,
                                               event_sink=self._events, strict_only=True)
            self._ledger = None
        assert self._upgrade is not None
        checkpointing = self._consumption.prepare(source, source_keys, candidate_keys,
            self._regular_offsets, self._regular, owned=owned_result and self._use_consumption_checkpoints,
            appended_from=candidate_common if appended else None)
        restored = not appended and checkpointing and self._consumption.restore(self._upgrade)
        checkpointing = checkpointing and self._consumption.begin(self._upgrade, cold=not appended and not restored)
        # begin/restore 将事件容器转为可共享的独立根，账本必须读取同一个根。
        assert self._upgrade.event_sink is not None
        self._events = cast(_EventRows, self._upgrade.event_sink)
        self._consumption._count('advance_calls')
        self._consumption._count('attacks_consumed', len(self._upgrade.turns) - self._upgrade.processed_count)
        self._consumption._count('appended_advances' if appended else 'resumed_advances' if restored else 'full_rebuilds')
        self._upgrade.advance(_snapshot=False, _checkpoint=self._consumption.capture if checkpointing else None)
        changed: list[object] = []
        output = result
        if self._ledger is not None and self._ledger._owned_result != owned_result:
            self._ledger = None
        if self._events:
            if self._ledger is None:
                self._ledger = _JudgementLedger(_owned_result=owned_result)
                previous_candidates, previous_points = (), ()
                candidate_common = point_common = 0
            elif result._delta is not None and result._delta.parent is self._base_token:
                point_common = result._delta.points_from
            else:
                point_common = 0
                while (point_common < min(len(previous_points), len(result.points))
                        and previous_points[point_common] is result.points[point_common]):
                    point_common += 1
            output, changed = self._ledger.reconcile_formal(previous_candidates, self._candidates, candidate_common,
                previous_points, result.points, point_common, self._upgrade, self._events,
                range(len(source)), self.trend_level)
        retained_points = result.points[point_common:] if self._events else ()
        self._base_points = result.points if self._events else ()
        self._base_token = result._delta.token if result._delta is not None else None
        self._source_token = source.token if type(source) is CertifiedSource else None
        self._source_keys, self._candidate_keys = source_keys, candidate_keys
        upgrade = self._upgrade
        new_orders = upgrade.orders[old_order_count:] if appended else ()
        self._retain(appended=appended, added=[*changed, *retained_points, *source_tail, *new_candidates, *regular_tail,
            *self._regular_offsets[candidate_common + 1:],
            *source_keys[source_common:], *candidate_keys[candidate_common:], *self._events[old_event_count:],
            *upgrade.updated_merged.values(), *upgrade.updated_merged, *upgrade.new_merged_keys, *new_orders,
            *(upgrade.positions[order] for order in new_orders), *(upgrade.source_positions[order] for order in new_orders),
            upgrade.processed_count, upgrade.regular_cursor, upgrade.prefix_known, upgrade.owned_direction,
            upgrade.last_fall_high_position, upgrade.last_rise_low_position,
            *(tree.size for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()])])
        return output


def _has_early_formal_attack(turns: Sequence[_SourceTurn]) -> bool:
    for turn in turns:
        proof, completed = turn.source.get('upgrade_evidence'), turn.source.get('wave_completed_at')
        if (turn.source.get('wave_identity_state') == 'owned' and isinstance(proof, Mapping)
                and proof.get('role') == 'endpoint'
                and (completed is None or _later_moment(turn.available_at, cast(Moment, completed)) != turn.available_at)):
            return True
    return False


def _with_formal_key_breaks(
    source: Sequence[Mapping[str, object]], result: TrendJudgement, *, asof: Moment | None = None,
    _append_state: _FormalKeyBreakState | None = None,
    _certified_keys: tuple[bytes, ...] | None = None,
) -> TrendJudgement:
    """在普通闭合判断之外复用严格突破状态，不让开放下级参与普通四点定义。"""
    level = result.trend_level
    if level == 1 or not any(point.get('upgrade_evidence') for point in source):
        if _append_state is not None:
            _append_state.clear()
        return result
    if _append_state is not None and asof is None:
        reused = _append_state.calculate(source, result, _certified_keys=_certified_keys)
        if reused is not None:
            return reused
    elif _append_state is not None:
        _append_state.clear()
    owned_result = type(result) is _OwnedTrendJudgement
    working_source = _owned_mutable_points(list(source), True) if owned_result else mutable_points(source)
    turns = _source_turns(working_source, level - 1, formal_key_attacks=True)
    if asof is not None:
        turns = [turn for turn in turns if _later_moment(turn.available_at, asof) == asof]
    if not _has_early_formal_attack(turns):
        return result
    # 本级候选先恢复原始来源坐标；后续严格状态使用相同原位置，不重排知识日。
    candidates = _judgement_working_points(result.candidates, owned=owned_result)
    regular = [turn for candidate in candidates for turn in _source_turns([candidate], level,
        retain_geometry=True, accept_direction_facts=True)]
    events: list[dict[str, object]] = []
    state = _TrendUpgradeState(turns, regular, trend_level=level, event_sink=events, strict_only=True)
    promoted = state.advance()
    if not events:
        return result
    return _finalize_judgement(candidates, [*_judgement_working_points(result.points, owned=owned_result), *promoted], events,
                               tuple(range(len(source))), level, _owned_result=owned_result)


def judge_trend(
    high_low_points: Sequence[Mapping[str, object]], *, trend_level: int = 1, asof: Moment | None = None,
    ordinary_trend_completion_enabled: bool | None = None,
    _append_state: _TrendJudgementState | None = None,
    _owned_source: bool = False,
    _certified_standard: bool = False,
    _owned_result: bool = False,
) -> TrendJudgement:
    """只根据直接下级高低点判断一个层级，并返回可直接递归的结构点。

    一级读取折线高低点，二级读取一级 points，三级读取二级 points；级别
    只校验来源与填写元数据，不选择另一套价格公式。普通方向要求 HH 与 HL／
    LL 与 LH 都已可知，两类证据不限定先后；方向账本成为整波结束前，还须
    严格突破攻击日前已知的反向关键位。仅已实际升级波段的下级可凭
    已知升级证据及独立四点定义豁免旧key；显式普通结束选项另行保留。严格本级 H/L 突破升级仍
    共用同一底层实现。严格升级可读取具有完整突破证明的直接下级正式端点，
    不等待下级未来反向结束；普通四点路线仍只读已结束来源。开放端点没有
    本级旧关键位严格突破时不能升格，发展尾线和仅价格反转不能借此进入。
    up→up/down→down 不改成下级反向结束许可；本级独立取得其突破证明。

    asof 必须在源点判断之前过滤知识日期：完整输入中的未来点不能先
    改变归并结果，再靠末端过滤隐藏。发生坐标与可知日期都保留，原来源
    位置不会因为筛选或跳过未完点而改变。下级交易证书由共享复制边界
    清除，只保存 source_confirmation 审计引用；K 线和 N 证书不参与。

    几何首次可知日与 wave_completed_at 分别保留，只有真实反向证明才
    产生结束知识。一级的折线来源按同一严格四点定义获得身份；折线腿不能
    被误当作待升格的下级趋势线。二/三级普通方向转变若只是单条直接下级
    趋势波，保留 direction_only 账本；真正组合波或合法严格旧 key 突破才
    授予 owned 身份。下一层先投影结束时钟再判断，同一来源规则用于三级。
    没有普通闭合证据或严格升级证据时返回空结构，也
    不制造缺失尾端。纯显示发展线和独立策略资格由外部通用流程负责。
    输入不会被修改，结果及嵌套证据容器只读；mutable_points 可取得副本。
    """
    if type(trend_level) is not int or trend_level not in (1, 2, 3):
        raise ValueError('trend_level must be 1, 2 or 3')
    ordinary_enabled = (ordinary_completion_enabled() if ordinary_trend_completion_enabled is None
                        else ordinary_trend_completion_enabled)
    if type(ordinary_enabled) is not bool:
        raise ValueError('ordinary_trend_completion_enabled must be a boolean')
    if _append_state is not None and _append_state.ordinary_trend_completion_enabled != ordinary_enabled:
        _append_state.clear()
        _append_state.ordinary_trend_completion_enabled = ordinary_enabled
    if asof is not None:
        _moment_key(asof)
    source_level = trend_level - 1
    validated_turns: list[_SourceTurn] | None = [] if _append_state is not None else None
    admitted = [False]
    try:
        source, positions = _visible_source(high_low_points, source_level, asof,
            _validated_turns=validated_turns, _owned_source=_owned_source, _owned_admission=admitted)
        # 有限证据允许 tuple／非字符串键等审计类型；内部共享还须通过
        # 入场复制边界的严格 builtin 认证，不能只凭调用方的意愿标记。
        owned_result = _owned_result and _owned_source and _certified_standard and admitted[0]
        if _append_state is None:
            ordinary = trend_reversals(source, trend_level=trend_level)
            upgrade_events: _EventRows = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=trend_level, event_sink=upgrade_events,
                ordinary_trend_completion_enabled=ordinary_enabled)
        else:
            if _append_state.trend_level != trend_level:
                raise ValueError('append state must belong to the judged trend level')
            ordinary, merged, upgrade_events = _append_state.calculate(source, positions,
                _validated_turns=validated_turns, _certified_standard=_certified_standard)
        result = (_append_state.finalize(ordinary, merged, upgrade_events, positions, _owned_result=owned_result)
                  if _append_state is not None else
                  _finalize_judgement(ordinary, merged, upgrade_events, positions, trend_level,
                                      _owned_result=owned_result))
        return _with_formal_key_breaks(high_low_points, result, asof=asof)
    except Exception:
        # 来源、升级或冻结异常均不能留下半更新状态；原异常继续交给调用方。
        if _append_state is not None and get_ident() == _append_state._thread:
            _append_state.clear()
        raise
