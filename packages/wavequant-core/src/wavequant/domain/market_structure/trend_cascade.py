"""层级依赖调度：正式升级证明改变下级规则输入，仍只调用共同趋势核心。"""

from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import cast

from ..models.model import Bar
from .owned_evidence import CertifiedSource, current_evidence, owned_signature as _geometry_signature
from .trend_lines import (
    _ClosedPreparedPaths, _TrendJudgementCache, _prepared_points, _prepared_strokes,
    high_low_points_from_strokes, prepare_trend_lines,
)
from .trend_promotion_scope import PROMOTION_FIELD, promotion_point, validate_promotion
from .trend_readonly_cache import _builtin_size


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
        self._promotion_bytes = 0

    def promotion(self, point: Mapping[str, object], source_level: int) -> tuple[dict[str, object], bytes]:
        key = _geometry_signature(point)
        saved = self._promotions.get((source_level, key)) if key is not None else None
        if saved is not None:
            return saved
        validate_promotion(point, source_level)
        record = promotion_point(point)
        signature = _geometry_signature(record)
        if signature is None:
            raise ValueError('strict promotion must have finite serializable evidence')
        remaining = 8 * 1024 * 1024 - self._promotion_bytes
        size = _builtin_size((key, record, signature), remaining - 512) if remaining > 512 else None
        if key is not None and size is not None:
            self._promotions[source_level, key] = record, signature
            self._promotion_bytes += size + 512
        return record, signature

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
        self._promotion_bytes = 0
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
            versioned = (worker._promotion_source.enrich(current_source, releases[level], PROMOTION_FIELD)
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
                for point in _prepared_points(stroke):
                    proof = point.get('upgrade_evidence')
                    if not isinstance(proof, Mapping) or proof.get('role') != 'endpoint':
                        continue
                    if _owned_source and _round_caches is not None:
                        record, signature = _round_caches.promotion(point, upper - 1)
                    else:
                        validate_promotion(point, upper - 1)
                        record = promotion_point(point)
                        signature = _geometry_signature(record)
                        if signature is None:
                            raise ValueError('strict promotion must have finite serializable evidence')
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
                            records[signature] = dict(record, _promotion_target_level=lower,
                                                      _promotion_target_path=parent_path)
                            added = True
        if not added:
            return [prepared[level] for level in levels]
        round_index += 1
