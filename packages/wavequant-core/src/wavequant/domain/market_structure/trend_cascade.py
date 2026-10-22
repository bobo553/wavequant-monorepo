"""层级依赖调度：正式升级证明改变下级规则输入，仍只调用共同趋势核心。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from sys import getsizeof
from types import TracebackType
from typing import cast
from weakref import ReferenceType, ref

from ..models.model import Bar
from .owned_evidence import CertifiedSource, OwnedEvidence, current_evidence, owned_signature as _geometry_signature
from .owned_evidence_producer import PointRecord, ProducerRecordScope, producer_record_scope
from .owned_evidence_records import OwnedEvidenceRecords, RecordRef
from .trend_lines import (
    _ClosedPreparedPaths, _TrendJudgementCache, _prepared_points, _prepared_strokes,
    high_low_points_from_strokes, prepare_trend_lines,
)
from .trend_promotion_scope import PROMOTION_FIELD, promotion_point, validate_promotion
from .trend_point_version import PointVersion
from .trend_readonly_cache import _builtin_size


_Promotion = tuple[dict[str, object], bytes]
_PromotionReference = tuple[bytes, int]
_PROMOTION_MEMORY_BYTES = 8 * 1024 * 1024


def _promotion_carrier(point: dict[str, object], reference: _PromotionReference,
                       store: OwnedEvidenceRecords) -> PointRecord:
    """仅恢复同请求、生产后未修改的promotion外壳，不展开其证明。"""
    root = RecordRef(*reference)
    refs = store.dictionary_refs(root)
    return PointRecord._from_producer(point, root, refs, store)


def _promotion_source_record(points: Sequence[Mapping[str, object]], position: int,
                             owner: OwnedEvidence) -> PointRecord:
    if (type(points) is not PointVersion or not points._sealed
            or points.record_store is not owner.record_store()):
        raise ValueError('recorded promotions require their complete owned source version')
    return points.point_records[position]


def _record_targeted_promotion(record: dict[str, object], reference: _PromotionReference, *,
                               level: int, path: str, owner: OwnedEvidence) -> tuple[
    dict[str, object], _PromotionReference,
]:
    store = owner.record_store()
    source = _promotion_carrier(record, reference, store)
    with producer_record_scope(store, inputs=(source,)) as scope:
        targeted = dict(record, _promotion_target_level=level, _promotion_target_path=path)
        carrier = scope.record_point(targeted)
    return targeted, (carrier.reference.namespace, carrier.reference.offset)


@dataclass(frozen=True)
class _PromotionVersion:
    points: ReferenceType[PointVersion]
    owner: ReferenceType[OwnedEvidence]
    published_nodes: int
    # None 表示非升级点，False 表示未获预算准入、下次仍须完整校验。
    entries: tuple[_Promotion | None | bool, ...]
    size: int


class _TrendCascadeCache:
    """请求内按收敛轮次保留判定状态，不跨日保留升级许可证。

    第零轮使用已有状态；最多两轮额外状态各保留一个活动路径。证书及
    只读导出共享原有缓存，每个额外路径沿用 32 MiB 的判定预算，避免
    完整证据超过偏小预算后逐日丢弃状态。该值不是进程总堆上限。超额轮次
    沿用冷判断，不能覆盖第零轮导致下一日期再次从旧证明版本回放。
    """

    def __init__(self, workers: Sequence[_TrendJudgementCache], *, max_rounds: int = 3,
                 state_memory_bytes: int = 32 * 1024 * 1024) -> None:
        if type(max_rounds) is not int or not 1 <= max_rounds <= 3:
            raise ValueError('cascade max_rounds must be between one and three')
        if type(state_memory_bytes) is not int or state_memory_bytes <= 0:
            raise ValueError('cascade state_memory_bytes must be a positive integer')
        self._workers = tuple(workers)
        self._rounds: dict[int, tuple[_TrendJudgementCache, ...]] = {0: self._workers}
        self._max_rounds = max_rounds
        self._state_memory_bytes = state_memory_bytes
        self._closed = False
        self._promotions: dict[tuple[int, bytes], tuple[dict[str, object], bytes]] = {}
        self._promotion_records: dict[int, _PromotionReference] = {}
        self._promotion_bytes = 0
        self._promotion_versions: dict[tuple[int, int, str], _PromotionVersion] = {}
        self._promotion_version_bytes = 0

    def promotion(self, point: Mapping[str, object], source_level: int) -> tuple[dict[str, object], bytes]:
        owner = current_evidence()
        if owner is not None and owner.record_history_enabled:
            raise ValueError('recorded promotions require their owned source version')
        return self._promotion(point, source_level)[0]

    def _promotion(self, point: Mapping[str, object], source_level: int, *,
                   _record_scope: ProducerRecordScope | None = None,
                   _record_references: dict[int, _PromotionReference] | None = None) -> tuple[_Promotion, bool]:
        key = _geometry_signature(point)
        saved = self._promotions.get((source_level, key)) if key is not None else None
        if saved is not None:
            if _record_scope is not None and _record_references is not None:
                root = self._promotion_records.get(id(saved[0]))
                if root is None:
                    raise ValueError('recorded promotion cache requires its production reference')
                _record_scope.owner._validate_ref(RecordRef(*root))
                _record_references[id(saved[0])] = root
            return saved, True
        validate_promotion(point, source_level)
        record = promotion_point(point)
        signature = _geometry_signature(record)
        if signature is None:
            raise ValueError('strict promotion must have finite serializable evidence')
        reference: _PromotionReference | None = None
        reference_bytes = 0
        if _record_scope is not None and _record_references is not None:
            carrier = _record_scope.record_point(record)
            reference = carrier.reference.namespace, carrier.reference.offset
            _record_references[id(record)] = reference
            reference_bytes = 768 + getsizeof(reference) + sum(getsizeof(part) for part in reference)
        remaining = _PROMOTION_MEMORY_BYTES - self._promotion_bytes - self._promotion_version_bytes
        overhead = 512 + reference_bytes
        size = _builtin_size((key, record, signature), remaining - overhead) if remaining > overhead else None
        result = record, signature
        if key is not None and size is not None:
            self._promotions[source_level, key] = result
            if reference is not None:
                self._promotion_records[id(record)] = reference
            self._promotion_bytes += size + overhead
            return result, True
        return result, False

    def promotion_rows(self, points: Sequence[Mapping[str, object]], source_level: int,
                       *, round_index: int, path: str,
                       _record_references: dict[int, _PromotionReference] | None = None) -> list[_Promotion]:
        """只读版本的已校验前缀复用记录；新点与未知容器保持完整证明校验。"""
        owner = current_evidence()
        recording = owner is not None and owner.record_history_enabled
        if recording and _record_references is None:
            _record_references = {}
        versioned = (type(points) is PointVersion and points._sealed and owner is not None
                     and not owner._closed and round_index < self._max_rounds and type(path) is str)
        channel = source_level, round_index, path
        previous = self._promotion_versions.get(channel) if versioned else None
        # 签名字节包含证据池引用；池新增节点时同一图可能改用引用编码，
        # 必须重新取完整签名，不能用旧字节改变本轮升级证明的去重语义。
        prior_points = (previous.points() if previous is not None and previous.owner() is owner
                        and owner is not None and previous.published_nodes == len(owner._nodes) else None)
        start = points.changed_since(prior_points) if versioned else 0
        entries: list[_Promotion | None | bool] = []
        result: list[_Promotion] = []
        for index, point in enumerate(points):
            entry = previous.entries[index] if previous is not None and index < start else False
            if entry is False:
                proof = point.get('upgrade_evidence')
                if not isinstance(proof, Mapping) or proof.get('role') != 'endpoint':
                    entry = None
                else:
                    if recording:
                        assert owner is not None
                        source_record = _promotion_source_record(points, index, owner)
                        with producer_record_scope(owner.record_store(), inputs=(source_record,)) as scope:
                            promotion, retained = self._promotion(point, source_level,
                                _record_scope=scope, _record_references=_record_references)
                    else:
                        promotion, retained = self._promotion(point, source_level)
                    result.append(promotion)
                    entries.append(promotion if retained else False)
                    continue
            elif entry is not None:
                promotion = cast(_Promotion, entry)
                if recording:
                    assert owner is not None and _record_references is not None
                    root = self._promotion_records.get(id(promotion[0]))
                    if root is None:
                        raise ValueError('recorded promotion version requires its production reference')
                    owner.record_store()._validate_ref(RecordRef(*root))
                    _record_references[id(promotion[0])] = root
                result.append(promotion)
            entries.append(entry)
        if versioned:
            assert owner is not None and type(points) is PointVersion
            self._remember_promotion_version(channel, points, owner, tuple(entries))
        return result

    def _remember_promotion_version(self, channel: tuple[int, int, str], points: PointVersion,
                                    owner: OwnedEvidence,
                                    entries: tuple[_Promotion | None | bool, ...]) -> None:
        previous = self._promotion_versions.pop(channel, None)
        if previous is not None:
            self._promotion_version_bytes -= previous.size
        # 记录只引用原 8 MiB 账本已持有的值；另计元数据，不保留点版本或祖先链。
        size = getsizeof(entries) + getsizeof(channel) + getsizeof(channel[2]) + 512
        remaining = _PROMOTION_MEMORY_BYTES - self._promotion_bytes
        if size > remaining:
            return
        while self._promotion_versions and (len(self._promotion_versions) >= 32
                or self._promotion_version_bytes + size > remaining):
            oldest = next(iter(self._promotion_versions))
            self._promotion_version_bytes -= self._promotion_versions.pop(oldest).size
        self._promotion_versions[channel] = _PromotionVersion(ref(points), ref(owner), len(owner._nodes), entries, size)
        self._promotion_version_bytes += size

    def workers_for(self, round_index: int) -> tuple[_TrendJudgementCache, ...] | None:
        if self._closed:
            raise RuntimeError('trend cascade cache is closed')
        if type(round_index) is not int or round_index < 0:
            raise ValueError('cascade round_index must be a nonnegative integer')
        if round_index >= self._max_rounds:
            return None
        workers = self._rounds.get(round_index)
        if workers is None:
            workers = tuple(worker.bounded_sibling(state_memory_bytes=self._state_memory_bytes)
                            for worker in self._workers)
            self._rounds[round_index] = workers
        return workers

    def remember_source_flip(self, trend_level: int, path: str) -> None:
        for workers in self._rounds.values():
            for worker in workers:
                worker.remember_source_flip(trend_level, path)

    def close(self) -> None:
        if self._closed:
            return
        # 第零轮由调用方拥有，额外轮次只能活到本次阶段请求结束。
        for round_index, workers in self._rounds.items():
            if round_index:
                for worker in workers:
                    worker.clear()
        self._rounds.clear()
        self._promotions.clear()
        self._promotion_records.clear()
        self._promotion_bytes = 0
        self._promotion_versions.clear()
        self._promotion_version_bytes = 0
        self._closed = True

    def __enter__(self) -> '_TrendCascadeCache':
        if self._closed:
            raise RuntimeError('trend cascade cache is closed')
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.close()


def _order(point: Mapping[str, object]) -> tuple[int, int]:
    return cast(int, point['index']), cast(int, point.get('ordinal', 0))


def _enrich_promotions(
    source: Sequence[Mapping[str, object]],
    allowed: Mapping[str, Mapping[bytes, dict[str, object]]],
    *, _owned: bool = False,
) -> list[dict[str, object]]:
    """按路径和端点定位证明；保持原插入顺序及真实起点存在性门禁。"""
    if _owned and type(source) is CertifiedSource and source.owner is current_evidence() and not any(allowed.values()):
        return source
    actual_orders = {(str(point.get('source_path', 'path-0')), _order(point)) for point in source}
    indexed: dict[tuple[str, tuple[int, int]], list[dict[str, object]]] = {}
    for path, records in allowed.items():
        for record in records.values():
            endpoint = path, _order(record)
            # 原实现只在存在对应端点时读取 origin，保留这个校验边界。
            if endpoint not in actual_orders:
                continue
            origin = cast(Mapping[str, object], cast(Mapping[str, object], record['upgrade_evidence'])['origin'])
            if (path, _order(origin)) in actual_orders:
                indexed.setdefault(endpoint, []).append(record)
    enriched: list[dict[str, object]] = []
    owner = current_evidence() if _owned else None
    for point in source:
        additions = indexed.get((str(point.get('source_path', 'path-0')), _order(point)))
        item = (point if owner is not None and not additions and type(point) is dict
                and owner.reference(point) is not None else dict(point))
        if additions:
            item = dict(item)
            item[PROMOTION_FIELD] = list(additions)
        enriched.append(item)
    return enriched


def prepare_trend_cascade(
    source: Sequence[Mapping[str, object]], *, symbol: str, bars: Sequence[Bar] = (),
    first: int = 1, last: int = 3,
    caches: Sequence[_TrendJudgementCache] | None = None,
    closed_paths: Sequence[_ClosedPreparedPaths] | None = None,
    _round_caches: _TrendCascadeCache | None = None,
    _owned_source: bool = False,
) -> list[dict[str, object]]:
    """先建立真实升级，再以新证明版本重算受影响下级及其消费者。

    升级证明是不同的规则输入，不是显示层修线。每个来源/证明版本的结果
    在本轮复用，直到没有新升级可接管下级；历史记录只在此过程完成后发布。
    证明账本仅在本次前缀内存在，不跨股票、路径或日期回退保留未来许可证。
    """
    if not 1 <= first <= last <= 3:
        raise ValueError('trend cascade requires ordered levels from one to three')
    levels = range(first, last + 1)
    workers = list(caches) if caches is not None else [_TrendJudgementCache() for _ in levels]
    if len(workers) != len(levels) or closed_paths is not None and len(closed_paths) != len(levels):
        raise ValueError('trend cascade caches must match requested levels')
    if _round_caches is not None and _round_caches.workers_for(0) != tuple(workers):
        raise ValueError('trend cascade round caches must own the requested workers')
    record_owner = current_evidence() if _owned_source else None
    recording = record_owner is not None and record_owner.record_history_enabled
    release_references: dict[int, _PromotionReference] | None = {} if recording else None
    release_reference_bytes = 0
    releases: dict[int, dict[str, dict[bytes, dict[str, object]]]] = {level: {} for level in levels}
    seen: dict[tuple[int, object], dict[str, object]] = {}
    # 同一轮收敛中 prepared 是只读版本；相同版本只展开一次直接下级。
    projections: dict[int, tuple[dict[str, object], list[dict[str, object]]]] = {}
    prepared: dict[int, dict[str, object]] = {}
    round_index = 0
    while True:
        round_workers = _round_caches.workers_for(round_index) if _round_caches is not None else workers
        current_source = source
        for level in levels:
            # 每个证明只附到同路径真实端点，strip_source_license 会在向上
            # 生成新点时清除它，不能继承成其他层的豁免。
            worker = round_workers[level-first] if round_workers is not None else None
            versioned = (worker._promotion_source.enrich(current_source, releases[level], PROMOTION_FIELD,
                _record_references=release_references) if recording and worker is not None else
                worker._promotion_source.enrich(current_source, releases[level], PROMOTION_FIELD)
                if _owned_source and worker is not None else None)
            enriched = versioned if versioned is not None else _enrich_promotions(
                current_source, releases[level], _owned=_owned_source)
            signature = (enriched.token if type(enriched) is CertifiedSource
                and enriched.owner is current_evidence() else _geometry_signature(enriched))
            cached = seen.get((level, signature)) if signature is not None else None
            if cached is None:
                cached = prepare_trend_lines(enriched, trend_level=level, symbol=symbol, bars=bars,
                    _judgement_cache=round_workers[level-first] if round_workers is not None else None,
                    _owned_source=True,
                    _versioned_source=_owned_source,
                    _closed_paths=closed_paths[level-first] if closed_paths is not None else None,
                    _closed_slot=round_index if round_index < 3 else None)
                if signature is not None:
                    seen[level, signature] = cached
            prepared[level] = cached
            if level < last:
                projection = projections.get(level)
                if projection is None or projection[0] is not cached:
                    worker = round_workers[level-first] if round_workers is not None else None
                    incremental = (worker._projection.project(_prepared_strokes(cached, 'strokes'), worker._export_hints)
                        if _owned_source and worker is not None else None)
                    projection = cached, (incremental if incremental is not None else
                        high_low_points_from_strokes(cached['strokes'], include_key_break_attacks=True))
                    projections[level] = projection
                current_source = projection[1]
        added = False
        parents: dict[int, dict[str, Mapping[str, object]]] = {}
        for lower in range(first, last):
            paths: dict[str, Mapping[str, object]] = {}
            for row in _prepared_strokes(prepared[lower], 'candidate_strokes'):
                # 与旧 next(...) 一致，重复 id 时取第一个，不能被末项覆盖。
                paths.setdefault(cast(str, row['id']), row)
            parents[lower] = paths
        for upper in range(first + 1, last + 1):
            for stroke in _prepared_strokes(prepared[upper], 'candidate_strokes'):
                points = _prepared_points(stroke)
                promotion_references: dict[int, _PromotionReference] | None = {} if recording else None
                if _owned_source and _round_caches is not None:
                    promotions = (_round_caches.promotion_rows(points, upper - 1,
                        round_index=round_index, path=cast(str, stroke['id']),
                        _record_references=promotion_references) if recording else
                        _round_caches.promotion_rows(points, upper - 1,
                            round_index=round_index, path=cast(str, stroke['id'])))
                else:
                    promotions = []
                    for position, point in enumerate(points):
                        proof = point.get('upgrade_evidence')
                        if not isinstance(proof, Mapping) or proof.get('role') != 'endpoint':
                            continue
                        if recording:
                            assert record_owner is not None and promotion_references is not None
                            source_record = _promotion_source_record(points, position, record_owner)
                            with producer_record_scope(record_owner.record_store(), inputs=(source_record,)) as scope:
                                validate_promotion(point, upper - 1)
                                record = promotion_point(point)
                                carrier = scope.record_point(record)
                                promotion_references[id(record)] = carrier.reference.namespace, carrier.reference.offset
                        else:
                            validate_promotion(point, upper - 1)
                            record = promotion_point(point)
                        signature = _geometry_signature(record)
                        if signature is None:
                            raise ValueError('strict promotion must have finite serializable evidence')
                        promotions.append((record, signature))
                for record, signature in promotions:
                    parent_path = stroke['source_path']
                    # 路径ID由每一级真实来源串接，逐级回溯，绝不按股票日期
                    # 或字符串前缀猜测跨级归属。三级可同时接管一级与二级。
                    for lower in range(upper - 1, first - 1, -1):
                        parent_stroke = parents[lower].get(cast(str, parent_path))
                        if parent_stroke is None:
                            break
                        parent_path = parent_stroke['source_path']
                        records = releases[lower].setdefault(cast(str, parent_path), {})
                        if signature not in records:
                            if recording:
                                assert (record_owner is not None and promotion_references is not None
                                        and release_references is not None)
                                source_reference = promotion_references.get(id(record))
                                if source_reference is None:
                                    raise ValueError('targeted promotion requires its exact production reference')
                                targeted, reference = _record_targeted_promotion(record, source_reference,
                                    level=lower, path=cast(str, parent_path), owner=record_owner)
                                # 本次收敛的并行表只持有常量根，按实际条目计费并随调用释放。
                                charged = 768 + getsizeof(reference) + sum(getsizeof(part) for part in reference)
                                if release_reference_bytes + charged > _PROMOTION_MEMORY_BYTES:
                                    raise ValueError('promotion release references exceed their scoped memory limit')
                                release_reference_bytes += charged
                                release_references[id(targeted)] = reference
                            else:
                                targeted = dict(record, _promotion_target_level=lower,
                                                _promotion_target_path=parent_path)
                            records[signature] = targeted
                            added = True
        if not added:
            return [prepared[level] for level in levels]
        round_index += 1
