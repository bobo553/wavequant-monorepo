"""层级依赖调度：正式升级证明改变下级规则输入，仍只调用共同趋势核心。"""

from collections.abc import Mapping, Sequence
from typing import cast

from ..models.model import Bar
from .trend_evidence_reuse import _geometry_signature
from .trend_lines import (
    _ClosedPreparedPaths, _TrendJudgementCache, _prepared_points, _prepared_strokes,
    high_low_points_from_strokes, prepare_trend_lines,
)
from .trend_promotion_scope import PROMOTION_FIELD, promotion_point, validate_promotion


def _order(point: Mapping[str, object]) -> tuple[int, int]:
    return cast(int, point['index']), cast(int, point.get('ordinal', 0))


def prepare_trend_cascade(
    source: Sequence[Mapping[str, object]], *, symbol: str, bars: Sequence[Bar] = (),
    first: int = 1, last: int = 3,
    caches: Sequence[_TrendJudgementCache] | None = None,
    closed_paths: Sequence[_ClosedPreparedPaths] | None = None,
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
    releases: dict[int, dict[str, dict[bytes, dict[str, object]]]] = {level: {} for level in levels}
    seen: dict[tuple[int, bytes], dict[str, object]] = {}
    prepared: dict[int, dict[str, object]] = {}
    while True:
        current_source = source
        for level in levels:
            allowed = releases[level]
            # 每个证明只附到同路径真实端点，strip_source_license 会在向上
            # 生成新点时清除它，不能继承成其他层的豁免。
            enriched: list[dict[str, object]] = []
            actual_orders = {(str(point.get('source_path', 'path-0')), _order(point)) for point in current_source}
            for point in current_source:
                item = dict(point)
                path = str(point.get('source_path', 'path-0'))
                additions = [record for record in allowed.get(path, {}).values()
                    if _order(record) == _order(point)
                    and (path, _order(cast(Mapping[str, object],
                         cast(Mapping[str, object], record['upgrade_evidence'])['origin']))) in actual_orders]
                if additions:
                    item[PROMOTION_FIELD] = additions
                enriched.append(item)
            signature = _geometry_signature(enriched)
            cached = seen.get((level, signature)) if signature is not None else None
            if cached is None:
                cached = prepare_trend_lines(enriched, trend_level=level, symbol=symbol, bars=bars,
                    _judgement_cache=workers[level-first], _owned_source=True,
                    _closed_paths=closed_paths[level-first] if closed_paths is not None else None)
                if signature is not None:
                    seen[level, signature] = cached
            prepared[level] = cached
            if level < last:
                current_source = high_low_points_from_strokes(cached['strokes'], include_key_break_attacks=True)
        added = False
        for upper in range(first + 1, last + 1):
            for stroke in _prepared_strokes(prepared[upper], 'candidate_strokes'):
                for point in _prepared_points(stroke):
                    proof = point.get('upgrade_evidence')
                    if not isinstance(proof, Mapping) or proof.get('role') != 'endpoint':
                        continue
                    validate_promotion(point, upper - 1)
                    record = promotion_point(point)
                    signature = _geometry_signature(record)
                    if signature is None:
                        raise ValueError('strict promotion must have finite serializable evidence')
                    parent_path = stroke['source_path']
                    # 路径ID由每一级真实来源串接，逐级回溯，绝不按股票日期
                    # 或字符串前缀猜测跨级归属。三级可同时接管一级与二级。
                    for lower in range(upper - 1, first - 1, -1):
                        parent_stroke = next((row for row in _prepared_strokes(prepared[lower], 'candidate_strokes')
                                     if row['id'] == parent_path), None)
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
