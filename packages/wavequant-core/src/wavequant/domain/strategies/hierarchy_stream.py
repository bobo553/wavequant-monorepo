"""Request-owned, append-only history for independent strategy confirmations."""

from collections.abc import Mapping, Sequence
from contextlib import nullcontext
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ..market_structure.bottom_n_targets import DeclineStart
from ..market_structure.trend_body_reversal import with_price_bodies
from ..market_structure.trend_judgement import (
    TrendJudgement, _FrozenPoint, _FrozenPointCache, _OwnedPointAdmission, _frozen_point_scope, mutable_points,
)
from ..market_structure.trend_readonly_cache import _ReadonlySnapshotCache
from ..market_structure.trend_evidence_reuse import _geometry_signature
from ..market_structure.trend_lines import (
    _TrendCandidateCache, _TrendJudgementCache, _legacy_pending_bull_n, polyline_turns, qualify_trend_structure,
)
from ..market_structure.n_trend_confirmation import _NPermissionProofs, _reuse_n_permissions
from ..market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex, market_prefix
from ..models.model import Bar
from .hierarchical_entry import _HistoryPoint, _identity, _decline_starts

if TYPE_CHECKING:
    from .market_calculations import MarketDay


@dataclass(slots=True)
class _PreparedStructure:
    source_version: object | None
    source: Sequence[Mapping[str, object]]
    judgement: TrendJudgement
    candidates: list[dict[str, object]]
    geometry: list[_HistoryPoint]
    reusable: bool


def _available_structure(judgement: TrendJudgement, index: int) -> bool:
    """仅完整冻结且已到知识日的结构能跨日借用；未知证据保持原物化行为。"""
    if any(type(point) is not _FrozenPoint or point.json_template is None
           for points in (judgement.points, judgement.candidates) for point in points):
        return False
    return all(type(point.get('index')) is int and 0 <= cast(int, point['index']) <= index
               and type(point.get('available_at')) is int and 0 <= cast(int, point['available_at']) <= index
               and ('wave_completed_at' not in point or type(point['wave_completed_at']) is int
                    and 0 <= point['wave_completed_at'] <= index)
               for point in judgement.points)


class HierarchyHistoryStream:
    """Keep knowledge clocks and judgement state across daily strategy calls.

    The public batch adapter still returns independent mutable snapshots. This
    private stream lends its accumulated history only to the current day's
    synchronous strategy/account consumer; no previous day is recalculated.
    """

    def __init__(self, bars: Sequence[Bar], *, n_enabled: bool = False, shared: bool = False) -> None:
        self.bars = tuple(bars)
        self.market_index = None if shared else _MarketRangeIndex.build(self.bars)
        self.n_enabled = n_enabled
        self._shared = shared
        self._dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(self.bars)}
        self.index = -1
        self.epoch: int | None = None
        self.history: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        self.epochs: dict[int, int] = {}
        self.declines: dict[int, tuple[DeclineStart, ...]] = {}
        self.previous: dict[int, dict[tuple[int, int, str, float], int]] = {}
        self.points: dict[int, dict[tuple[int, int, str, float], _HistoryPoint]] = {}
        self.caches: dict[int, _TrendJudgementCache] = {}
        self._candidate_caches: dict[int, _TrendCandidateCache] = {}
        self._n_proofs = {level: _NPermissionProofs() for level in (1, 2, 3)}
        self._frozen_points = _FrozenPointCache()
        self._point_admission = _OwnedPointAdmission()
        self._decoded = _ReadonlySnapshotCache[dict[str, object]](
            memory_bytes=8 * 1024 * 1024, max_entries=2048, evict=False)
        # 每级仅持有当前结构。版本来自本次已冻结判定，不能用外部 raw 的身份
        # 跳过修订校验；下级许可仍每天重新生成，不进入这个几何复用边界。
        self._prepared: dict[int, _PreparedStructure] = {}
        self._polyline_signature: bytes | None = None
        self._polyline_version: object | None = None
        self._closed = False

    def close(self) -> None:
        """释放本次回测的有界点缓存；不改变已交付的历史证据。"""
        self._frozen_points.close()
        self._point_admission.close()
        self._decoded.clear()
        self._prepared.clear()
        self._candidate_caches.clear()
        self._n_proofs.clear()
        self._polyline_signature = None
        self._polyline_version = None
        self._closed = True

    def _readonly_points(self, points: Sequence[Mapping[str, object]], *,
                         level: int, kind: str) -> list[dict[str, object]]:
        # 仅内部不可变 JSON 模板允许复用。未知审计值仍按公共契约逐次复制，
        # 模板内容改变即失效；资格与可知时间仍每日独立计算。
        templates: list[str] = []
        for point in points:
            if type(point) is not _FrozenPoint or point.json_template is None:
                return mutable_points(points)
            templates.append(point.json_template)
        decoded: list[dict[str, object]] = []
        missing: list[Mapping[str, object]] = []
        slots: list[tuple[int, tuple[int, str, str]]] = []
        for index, (point, template) in enumerate(zip(points, templates)):
            key = level, kind, template
            cached = self._decoded.get(key, None)
            if cached is not None:
                decoded.append(cached[0])
            else:
                decoded.append({})
                missing.append(point)
                slots.append((index, key))
        if missing:
            # 尾点更新不能迫使未变的历史前缀重新 JSON 解码；新点仍批量解码。
            for (index, key), point in zip(slots, mutable_points(missing), strict=True):
                decoded[index] = point
                self._decoded.put(key, None, point)
        return decoded

    def _freeze(self, points: Sequence[Mapping[str, object]], level: int, index: int) -> list[_HistoryPoint]:
        previous = self.previous.get(level, {})
        prior_points = self.points.get(level, {})
        frozen: list[_HistoryPoint] = []
        known_points: dict[tuple[int, int, str, float], int] = {}
        reusable: dict[tuple[int, int, str, float], _HistoryPoint] = {}
        for point in points:
            key = _identity(point)
            known = max(previous.get(key, index), cast(int, point.get('available_index', 0)))
            if frozen:
                known = max(known, frozen[-1]['available_at'])
            current_data = dict(point, available_at=known, label=point.get('label', point['kind']))
            if 'wave_completion_policy' in point:
                current_data.setdefault('source_geometry_available_at', point['available_at'])
            current = cast(_HistoryPoint, current_data)
            prior = prior_points.get(key)
            if prior is not None and prior == current:
                current = prior
            known_points[key] = known
            reusable[key] = current
            frozen.append(current)
        self.previous[level] = known_points
        self.points[level] = reusable
        return frozen

    def _known_index(self, value: object) -> int | None:
        if type(value) is int:
            return value if value >= 0 else None
        if isinstance(value, str):
            return int(value) if value.isdecimal() else self._dates.get(value)
        return None

    @staticmethod
    def _active_points(
        prepared: Mapping[str, object], view: Mapping[str, object], field: str, index: int,
    ) -> list[Mapping[str, object]]:
        cutoffs = cast(Mapping[str, int | None], prepared['_path_market_end_indices'])
        result: list[Mapping[str, object]] = []
        for stroke in cast(Sequence[Mapping[str, object]], view[field]):
            path = cast(str, stroke['source_path'])
            cutoff = cutoffs[path]
            if cutoff is None or index < cutoff:
                result.extend(cast(Sequence[Mapping[str, object]], stroke['points']))
        return result

    def advance_market_day(
        self, day: 'MarketDay', permissions: Sequence[Mapping[str, object]],
    ) -> None:
        """把共享当日结果投影为整数知识时钟，不再次判断任何级别的几何。

        几何、资格和价格日期仍分别保存。点第一次真正出现在本级许可视图
        之前不能被策略读取；已发布旧日只追加，后日修订不改写历史。
        """
        if self._closed:
            raise RuntimeError('hierarchy stream has been closed')
        index, epoch = day.frame.index, day.frame.epoch
        if not self._shared or index != self.index + 1 or index >= len(self.bars):
            raise ValueError('shared hierarchy requires consecutive market days')
        if len(day.levels) != 3 or len(permissions) != 3:
            raise ValueError('shared hierarchy requires all three immediate levels')
        if epoch != self.epoch:
            self.previous.clear()
            self.points.clear()
        levels: dict[int, tuple[dict[str, object], ...]] = {}
        for number, (prepared, view) in enumerate(zip(day.levels, permissions, strict=True), 1):
            if prepared.get('trend_level') != number or prepared.get('source_level') != number - 1:
                raise ValueError('shared hierarchy requires immediate lower-level geometry')
            points: list[Mapping[str, object]] = []
            for point in self._active_points(prepared, view, 'qualified_strokes', index):
                known = self._known_index(point.get('available_at'))
                origin = point.get('index')
                if type(origin) is not int or known is None or not 0 <= origin <= known <= index:
                    continue
                points.append(dict(index=origin, ordinal=point.get('ordinal', 0), kind=point['kind'],
                    value=point['value'], available_at=known, available_index=known))
            frozen = self._freeze(points, number, index)
            previous = self.history.get(self.index, {}).get(number)
            projected: list[dict[str, object]] = []
            unchanged = previous is not None and len(previous) == len(frozen)
            for position, point in enumerate(frozen):
                prior = previous[position] if previous is not None and position < len(previous) else None
                # 日期历史内部只读；尾点变化不应重新分配已发布的轻量前缀。
                if prior is not None and all(prior[field] == point[field]
                        for field in ('index', 'ordinal', 'kind', 'value', 'available_at')):
                    projected.append(prior)
                else:
                    unchanged = False
                    projected.append(dict(index=point['index'], ordinal=point['ordinal'], kind=point['kind'],
                        value=point['value'], available_at=point['available_at']))
            levels[number] = previous if unchanged and previous is not None else tuple(projected)
        candidates: list[Mapping[str, object]] = []
        for point in self._active_points(day.levels[0], day.levels[0], 'candidate_strokes', index):
            known = self._known_index(point.get('available_at'))
            if known is None or known > index:
                continue
            candidate = dict(point, available_at=known)
            upgrade = point.get('upgrade_evidence')
            if isinstance(upgrade, Mapping) and isinstance(upgrade.get('origin'), Mapping):
                origin = cast(Mapping[str, object], upgrade['origin'])
                origin_known = self._known_index(origin.get('available_at'))
                if origin_known is not None:
                    candidate['upgrade_evidence'] = dict(upgrade, origin=dict(origin, available_at=origin_known))
            candidates.append(candidate)
        self.declines[index] = _decline_starts(candidates)
        self.history[index], self.epochs[index] = levels, epoch
        self.index, self.epoch = index, epoch

    def advance(self, index: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
        if self._closed:
            raise RuntimeError('hierarchy stream has been closed')
        if self._shared:
            raise ValueError('shared hierarchy consumes MarketDay, not a second trend calculation')
        if index != self.index + 1 or index >= len(self.bars):
            raise ValueError('hierarchy stream requires consecutive available dates')
        if epoch != self.epoch:
            self._decoded.clear()
            self._prepared.clear()
            self.previous.clear()
            self.points.clear()
            self._polyline_signature = None
            self._polyline_version = None
            # 与日线流使用相同状态额度，避免长历史在分钟冷恢复时反复丢弃尾部状态。
            self.caches = {level: _TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024)
                           for level in (1, 2, 3)}
            self._candidate_caches = {level: _TrendCandidateCache() for level in (1, 2, 3)}
        market = market_prefix(self.bars, index + 1)
        prefix = _MarketPrefixIndex(self.market_index, market) if self.market_index is not None else None
        source: Sequence[Mapping[str, object]] = self._freeze(
            polyline_turns(raw, source_path=f'lecture-{epoch}'), 0, index)
        qualified_source: Sequence[Mapping[str, object]] = source
        signature = _geometry_signature(source) if self.market_index is not None else None
        if signature is None or signature != self._polyline_signature:
            self._polyline_version = object() if signature is not None else None
        self._polyline_signature = signature
        source_version = self._polyline_version
        levels: dict[int, tuple[dict[str, object], ...]] = {}
        with _frozen_point_scope(cache=self._frozen_points, admission=self._point_admission):
            for level in (1, 2, 3):
                previous = self._prepared.get(level)
                if (previous is not None and previous.reusable and source_version is not None
                        and previous.source_version is source_version):
                    source, judgement = previous.source, previous.judgement
                else:
                    source = with_price_bodies(source, market)
                    judgement = self.caches[level].judge(source, path=f'lecture-{epoch}', trend_level=level,
                        asof=index, _owned_source=True)
                if previous is not None and previous.reusable and judgement is previous.judgement:
                    candidates, geometry, reusable = previous.candidates, previous.geometry, True
                else:
                    candidates = self._readonly_points(judgement.candidates, level=level, kind='candidates')
                    geometry = self._freeze([dict(point, available_index=point['available_at'])
                        for point in self._readonly_points(judgement.points, level=level, kind='geometry')], level, index)
                    # 市场索引只接纳 exact frozen Bar 与标准价格；自定义行情对象
                    # 可能在两天之间改变实体，必须继续走原逐日附加与判断。
                    reusable = self.market_index is not None and _available_structure(judgement, index)
                self._prepared[level] = _PreparedStructure(
                    source_version, source, judgement, candidates, geometry, reusable)
                n_scope = (_reuse_n_permissions(self._n_proofs[level], market,
                    allow_new=_legacy_pending_bull_n(geometry, market, trend_level=level))
                    if self.n_enabled else nullcontext())
                with n_scope:
                    published = qualify_trend_structure(candidates, source, market, trend_level=level,
                        qualified_source=qualified_source, _owned_candidates=True,
                        n_target_trend_confirmation_enabled=self.n_enabled, _market_index=prefix,
                        _candidate_cache=self._candidate_caches[level])
                levels[level] = tuple(dict(index=point['index'], ordinal=point['ordinal'], kind=point['kind'],
                    value=point['value'], available_at=point['available_at']) for point in published)
                if level == 1:
                    self.declines[index] = _decline_starts(candidates)
                source, qualified_source = geometry, published
                source_version = geometry if reusable else None
        self.history[index] = levels
        self.epochs[index] = epoch
        self.index, self.epoch = index, epoch
