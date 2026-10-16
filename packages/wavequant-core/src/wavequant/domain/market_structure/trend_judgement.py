"""以直接下级高低点唯一决定本级结构，交易许可证不参与趋势几何。

一、二、三级共用同一过程：普通有序高低点定义、严格本级高低点突破升级，
再将当时已知的结构事件归并为交替高低点。下一层直接消费 ``points``；
``candidates`` 保存没有进入当前折线的真实结构事件，供审计和独立资格层
使用。本模块不读取 K 线、不计算 N、不调用 publisher，也不借交易证书
删改结构点。市场证书的方向、日期和资格由调用方另行计算。
"""

from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import copy, deepcopy
from dataclasses import dataclass
from gc import get_referents
import json
from math import isfinite
from sys import getsizeof
from threading import get_ident
from types import MappingProxyType
from typing import cast

from .trend_evidence_reuse import _geometry_signature
from .polyline import LinePoint, ReversalPoint
from .trend_level_upgrades import _PointRange, _TrendUpgradeState, promote_trend_key_breaks
from .trend_levels import Moment, _SourceTurn, _TrendReversalState, _later_moment, _source_turns, trend_reversals

__all__ = ['TrendJudgement', 'judge_trend', 'mutable_points']

# 合并解码仅改变模板传输；4 Mi 字符的上界同时限制临时 Unicode 缓冲。
_MUTABLE_POINT_BATCH_CHARACTERS = 4 * 1024 * 1024


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


@dataclass(frozen=True, slots=True, eq=False)
class _FrozenPoint(Mapping[str, object]):
    """只读点及其标准证据导出模板；模板从不参与领域判定。"""

    data: Mapping[str, object]
    json_template: str | None

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
    points: Sequence[Mapping[str, object]], owned: bool,
) -> list[dict[str, object]]:
    """内部调用方移交标准工作集；公共或未知输入仍独立复制所有证据。

    移交后原工作集只能被读取，不再修改或公开导出。tuple 归一化、未知
    deepcopy 钩子以及非有限叶子的当前 identity 仍遵守原公共复制边界。
    """
    if not owned or type(points) not in (list, tuple):
        return mutable_points(points)
    admission = _owned_point_admission.get()
    if admission is None:
        admission = _OwnedPointAdmission()
    if not all(admission.certify(point) for point in points):
        return mutable_points(points)
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
    return _FrozenPoint(frozen, template)


def _frozen_owned_bytes(value: object, seen: set[int]) -> int:
    """计算标准只读快照的真实容器、模板和叶子体积，不执行审计对象钩子。"""
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = getsizeof(value)
    if type(value) is _FrozenPoint:
        point = value
        return size + _frozen_owned_bytes(point.data, seen) + _frozen_owned_bytes(point.json_template, seen)
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


class _FrozenPointCache:
    """单阶段的有界完整点复用；不保存来源判断、行情或可变导出。"""

    def __init__(self, *, memory_bytes: int = 16 * 1024 * 1024) -> None:
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('frozen point memory_bytes must be a positive integer')
        self._limit = memory_bytes
        self._thread = get_ident()
        self._closed = False
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
        return self._payload_bytes + getsizeof(self._points) if self._points else 0

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
        owned = _frozen_owned_bytes(frozen, set()) + getsizeof(signature)
        charged = owned + getsizeof((frozen, owned)) + getsizeof(owned)
        if charged + getsizeof(OrderedDict()) > self._limit:
            self._fallbacks += 1
            return frozen
        self._points[signature] = frozen, charged
        self._payload_bytes += charged
        refused = False
        while self.memory_used > self._limit:
            # 每日顺序扫全窗时 LRU 会逐个淘汰上一日稳定点，超容量后命中
            # 可降为零。优先拒绝刚到达的新版本，保留已有完整快照。
            removed_signature, (_, removed) = self._points.popitem(last=True)
            self._payload_bytes -= removed
            self._evictions += 1
            refused = refused or removed_signature == signature
        if refused:
            self._fallbacks += 1
        if not self._points:
            self._points.clear()
        self._peak_bytes = max(self._peak_bytes, self.memory_used)
        return frozen

    def close(self) -> None:
        self._points.clear()
        self._payload_bytes = 0
        self._closed = True


_frozen_point_cache: ContextVar[_FrozenPointCache | None] = ContextVar('trend_frozen_point_cache', default=None)


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
) -> tuple[list[dict[str, object]], tuple[int, ...]]:
    """先建立可知来源前缀；类型校验不属于高低点趋势判断。

    可知时间可能晚于价格位置且并非单调，因此不能截取价格位置的简单前缀。
    未完／显示点由共同来源校验排除，其余点按自身真实可知日过滤之后，
    才允许普通定义或严格升级读取。原首参数位置单独保存，迟知点被过滤
    不会令输出 source_position 指向别的来源。
    """
    working = _owned_mutable_points(points, _owned_source)
    turns = _source_turns(working, source_level)
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
        visible.append(point)
        positions.append(turn.position)
        if _validated_turns is not None:
            # 归并器消费可见子序列的局部位置；原输入位置由 positions 单独恢复。
            # 来源绑定本次 visible 的新顶层副本，不将原位置 provenance 混入状态。
            position = len(visible) - 1
            reversal = ReversalPoint(turn.reversal.point, turn.index,
                                     f'level{source_level}_source_position_{position}')
            _validated_turns.append(_SourceTurn(point, position, turn.index, turn.ordinal, turn.kind,
                                                turn.value, turn.time, turn.available_at, reversal))
    return visible, tuple(positions)


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


def _copy_append_states(
    ordinary: _TrendReversalState, upgrade: _TrendUpgradeState, events: list[dict[str, object]],
) -> tuple[_TrendReversalState, _TrendUpgradeState, list[dict[str, object]]]:
    """复制会被追加修改的容器；已发布的来源和证据叶子在状态内保持只读。

    追加器替换 merged 的事件、向 selected/event_sink 追加新事件，不修改
    已保存的事件正文。树的数组及 recent 的内层列表则必须各自拥有副本。
    """
    copies = copy(ordinary), copy(upgrade)
    for original, cloned in zip((ordinary, upgrade), copies):
        for name, value in vars(original).items():
            if type(value) in (dict, list):
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
    events: list[dict[str, object]],
) -> list[object]:
    containers: list[object] = [events]
    for state in (ordinary, upgrade):
        if state is not None:
            containers.extend((state, vars(state)))
            containers.extend(value for value in vars(state).values() if type(value) in (dict, list))
    if upgrade is not None:
        containers.extend(upgrade.recent.values())
        for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]:
            containers.extend((tree, vars(tree), tree.points))
    return containers


class _TrendJudgementState:
    """单路径追加状态；保留最近来源翻向及尾部检查点，修订只重放有效后缀。"""

    def __init__(self, trend_level: int, *, memory_bytes: int = 8 * 1024 * 1024,
                 _tail_reuse: bool = False) -> None:
        if type(trend_level) is not int or trend_level not in (1, 2, 3):
            raise ValueError('trend_level must be 1, 2 or 3')
        if type(memory_bytes) is not int or memory_bytes <= 0:
            raise ValueError('judgement memory_bytes must be a positive integer')
        self.trend_level = trend_level
        self._limit = memory_bytes
        self._thread = get_ident()
        self._tail_reuse = _tail_reuse
        self._checkpoint: tuple[tuple[bytes, ...], tuple[bytes, ...], _TrendReversalState,
                                _TrendUpgradeState, list[dict[str, object]]] | None = None
        self._flip_checkpoint: tuple[tuple[bytes, ...], tuple[bytes, ...], _TrendReversalState,
                                     _TrendUpgradeState, list[dict[str, object]]] | None = None
        self.tail_restores = 0
        self.flip_restores = 0
        self._source_keys: tuple[bytes, ...] = ()
        self._regular_keys: tuple[bytes, ...] = ()
        self._ordinary: _TrendReversalState | None = None
        self._upgrade: _TrendUpgradeState | None = None
        self._events: list[dict[str, object]] = []
        self._payload_upper_bytes = 0
        self.memory_used = 0
        self.peak_retained_bytes = 0
        self.peak_observed_bytes = 0
        self.cold_runs = 0
        self.append_runs = 0
        self.deep_budget_walks = 0
        self.budget_rejections = 0

    def _save_tail(self) -> None:
        if self._ordinary is None or self._upgrade is None:
            return
        ordinary, upgrade, events = _copy_append_states(self._ordinary, self._upgrade, self._events)
        copied = _append_state_containers(ordinary, upgrade, events)
        # 回退仅保留一代可变容器。避免每次追加都序列化整段历史和证明树；
        # 证据叶子共享且只读，完整对象图仍受 calculate 的同一内存上限约束。
        if sum(getsizeof(container) for container in copied) <= min(4 * 1024 * 1024, self._limit // 2):
            self._checkpoint = self._source_keys, self._regular_keys, ordinary, upgrade, events
        else:
            self._checkpoint = None

    def remember_source_flip(self) -> None:
        """保存首次可知翻向之后的状态；包括旧关键位、证明及全局来源位置。

        新增点直接追加到当前状态。尾部修订时回到这里，只处理翻向之后的
        新来源；前缀证据有变或容量不足时拒绝复用，不能用截断输入造新趋势。
        """
        if self._ordinary is None or self._upgrade is None:
            return
        if self._flip_checkpoint is not None and self._flip_checkpoint[0] == self._source_keys:
            return
        ordinary, upgrade, events = _copy_append_states(self._ordinary, self._upgrade, self._events)
        self._flip_checkpoint = self._source_keys, self._regular_keys, ordinary, upgrade, events
        measured = self._owned_bytes()
        self.deep_budget_walks += 1
        if measured > self._limit:
            self._flip_checkpoint = None
            measured = self._owned_bytes()
        self.memory_used = measured
        self.peak_retained_bytes = max(self.peak_retained_bytes, measured)
        self._payload_upper_bytes = max(0, measured - self._container_bytes())

    def _restore_tail(self, certified: tuple[bytes, ...], *, flip: bool = False) -> bool:
        saved = self._flip_checkpoint if flip else self._checkpoint
        if (saved is None or len(certified) < len(saved[0])
                or certified[:len(saved[0])] != saved[0]):
            return False
        self._ordinary, self._upgrade, self._events = _copy_append_states(*saved[2:])
        self._source_keys, self._regular_keys = saved[:2]
        if flip:
            self._checkpoint = None
        self.deep_budget_walks += 1
        measured = self._owned_bytes()
        if measured > self._limit:
            self.clear()
            return False
        self.memory_used = measured
        self._payload_upper_bytes = max(0, measured - self._container_bytes())
        if flip:
            self.flip_restores += 1
        else:
            self.tail_restores += 1
        return True

    def clear(self) -> None:
        self._checkpoint = None
        self._flip_checkpoint = None
        self._source_keys = ()
        self._regular_keys = ()
        self._ordinary = None
        self._upgrade = None
        self._events = []
        self._payload_upper_bytes = 0
        self.memory_used = 0

    def _owned_bytes(self) -> int:
        return self._owned_value_bytes([self])

    def _owned_value_bytes(self, roots: Sequence[object]) -> int:
        # 已认证的标准容器及内部状态对象才可进入计数，不调用未知 __sizeof__
        # 或遍历全局函数/类引用。相同来源、证明和叶子只按实际 identity 计一次。
        pending: list[object] = list(roots)
        seen: set[int] = set()
        size = 0
        state_types = (_TrendJudgementState, _TrendReversalState, _TrendUpgradeState,
                       _PointRange, _SourceTurn, LinePoint, ReversalPoint)
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            size += getsizeof(value)
            if type(value) is _TrendJudgementState:
                attributes = vars(value)
                seen.add(id(attributes))
                size += getsizeof(attributes)
                pending.extend(attributes.keys())
                diagnostics = ('memory_used', 'peak_retained_bytes', 'peak_observed_bytes',
                               '_payload_upper_bytes', 'cold_runs', 'append_runs', 'deep_budget_walks', 'budget_rejections',
                               'tail_restores', 'flip_restores')
                pending.extend(item for key, item in attributes.items() if key not in diagnostics)
                # 计数字段自身不能令计数结果变化；为所有计数器分别预留
                # 64 位正整数的完整对象大小，覆盖受支持主机的地址空间。
                size += len(diagnostics) * getsizeof(2 ** 64)
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
        return size

    def calculate(self, source: Sequence[Mapping[str, object]], positions: Sequence[int],
                  *, _validated_turns: list[_SourceTurn] | None = None,
                  _certified_standard: bool = False) -> tuple[
        list[dict[str, object]], list[dict[str, object]], list[dict[str, object]],
    ]:
        if get_ident() != self._thread:
            ordinary = trend_reversals(source, trend_level=self.trend_level)
            events: list[dict[str, object]] = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=self.trend_level, event_sink=events)
            return ordinary, merged, events
        # 私有入口本身也拒绝未知及非有限审计值；不能依赖某一调用方的准入。
        # 先 exact builtin 编码才使用 C JSON，未知对象没有额外序列化钩子。
        # 只有缓存入口已完整认证 exact builtin/finite 的工作集才能传入证书；
        # 公共入口仍独立认证。可知来源解析、逐点全文键和修订门禁始终执行。
        finite = _certified_standard or _geometry_signature(source) is not None
        if finite and not _certified_standard:
            try:
                json.dumps(source, allow_nan=False)
            except (TypeError, ValueError):
                finite = False
        # 每次仍完整执行共同来源校验；价格位置、真实结束时钟与原位置都认证。
        source_keys = tuple(_geometry_signature((position, point)) for position, point in zip(positions, source))
        if not finite or any(key is None for key in source_keys):
            self.clear()
            ordinary = trend_reversals(source, trend_level=self.trend_level)
            events = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=self.trend_level, event_sink=events)
            return ordinary, merged, events
        certified = cast(tuple[bytes, ...], source_keys)
        turns = _validated_turns if _validated_turns is not None else _source_turns(source, self.trend_level - 1)
        appendable = (self._ordinary is not None and self._upgrade is not None
                      and len(certified) >= len(self._source_keys)
                      and certified[:len(self._source_keys)] == self._source_keys)
        restored = False
        if not appendable and self._tail_reuse:
            restored = appendable = self._restore_tail(certified, flip=True)
            if not appendable:
                restored = appendable = self._restore_tail(certified)
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
            # 强引用旧记录直至本次追加计数完成，避免替换后 id 重用隐藏新对象。
            previous_merged = dict(self._upgrade.merged)
            appended = turns[len(self._ordinary.turns):]
            # 公共前驱映射的 key 不能被新来源覆盖；同 K ordinal 保留原规则。
            reversals = {turn.reversal for turn in appended}
            appendable = (len(reversals) == len(appended)
                          and not any(turn.reversal in reversals for turn in self._ordinary.turns))
            if appendable:
                ordinary = self._ordinary.extend(appended)
                regular = _source_turns(ordinary, self.trend_level, retain_geometry=True, accept_direction_facts=True)
                regular_keys = tuple(_geometry_signature(turn.source) for turn in regular)
                appendable = (all(key is not None for key in regular_keys)
                              and len(regular_keys) >= len(self._regular_keys)
                              and regular_keys[:len(self._regular_keys)] == self._regular_keys
                              and self._upgrade.append(appended, regular[len(self._regular_keys):]))
        if not appendable:
            self.clear()
            self._ordinary = _TrendReversalState(self.trend_level)
            ordinary = self._ordinary.extend(turns)
            regular = _source_turns(ordinary, self.trend_level, retain_geometry=True, accept_direction_facts=True)
            regular_keys = tuple(_geometry_signature(turn.source) for turn in regular)
            self._upgrade = _TrendUpgradeState(turns, regular, trend_level=self.trend_level, event_sink=self._events)
            self.cold_runs += 1
        else:
            self.append_runs += 1
        assert self._upgrade is not None
        merged = self._upgrade.advance()
        events = self._events
        certified_regular = cast(tuple[bytes, ...], regular_keys)
        if appendable:
            self._source_keys = (*self._source_keys, *certified[len(self._source_keys):])
            self._regular_keys = (*self._regular_keys, *certified_regular[len(self._regular_keys):])
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
                self._ordinary.direction, self._ordinary.candidate, self._ordinary.prefix_known,
                *(tree.size for tree in [*upgrade.keys_by_kind.values(), *upgrade.origins_by_kind.values()]),
            ]
            for identity, event in upgrade.merged.items():
                previous_event = previous_merged.get(identity)
                if previous_event is not event:
                    roots.append(event)
                if identity not in previous_merged:
                    roots.append(identity)
            # 新证据可引用旧子树，逐批重复计费形成保守上界；不持有逐日版本、
            # object-id memo 或已被替换的证据。可变顶层容器使用其当前真实容量。
            self._payload_upper_bytes += self._owned_value_bytes(roots)
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
        return ordinary, merged, events


def _finalize_judgement(
    ordinary: Sequence[Mapping[str, object]], merged: Sequence[Mapping[str, object]],
    upgrade_events: Sequence[Mapping[str, object]], positions: Sequence[int], trend_level: int,
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
    frozen_memo: dict[int, tuple[object, object]] = {}
    return TrendJudgement(trend_level, source_level,
                          tuple(_frozen_point(point, frozen_memo) for point in points),
                          tuple(_frozen_point(point, frozen_memo) for point in candidates))


def judge_trend(
    high_low_points: Sequence[Mapping[str, object]], *, trend_level: int = 1, asof: Moment | None = None,
    _append_state: _TrendJudgementState | None = None,
    _owned_source: bool = False,
    _certified_standard: bool = False,
) -> TrendJudgement:
    """只根据直接下级高低点判断一个层级，并返回可直接递归的结构点。

    一级读取折线高低点，二级读取一级 points，三级读取二级 points；级别
    只校验来源与填写元数据，不选择另一套价格公式。普通方向要求 HH 与 HL／
    LL 与 LH 都已可知，两类证据不限定先后；严格已知本级 H/L 突破升级仍
    共用同一底层实现。严格升级
    仍可同时确认真实整波起点与单向端点；直接下级端点自身必须已经完整
    结束，不能把仅有价格反转的开放趋势尾端递归升级。上涨
    端点的 up→up 或下降端点的 down→down 证据不被改成反向整波许可。

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
    if asof is not None:
        _moment_key(asof)
    source_level = trend_level - 1
    validated_turns: list[_SourceTurn] | None = [] if _append_state is not None else None
    try:
        source, positions = _visible_source(high_low_points, source_level, asof,
                                            _validated_turns=validated_turns, _owned_source=_owned_source)
        if _append_state is None:
            ordinary = trend_reversals(source, trend_level=trend_level)
            upgrade_events: list[dict[str, object]] = []
            merged = promote_trend_key_breaks(source, ordinary, trend_level=trend_level, event_sink=upgrade_events)
        else:
            if _append_state.trend_level != trend_level:
                raise ValueError('append state must belong to the judged trend level')
            ordinary, merged, upgrade_events = _append_state.calculate(source, positions,
                _validated_turns=validated_turns, _certified_standard=_certified_standard)
        return _finalize_judgement(ordinary, merged, upgrade_events, positions, trend_level)
    except Exception:
        # 来源、升级或冻结异常均不能留下半更新状态；原异常继续交给调用方。
        if _append_state is not None and get_ident() == _append_state._thread:
            _append_state.clear()
        raise
