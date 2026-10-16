"""Request-owned, append-only history for independent strategy confirmations."""

from collections.abc import Mapping, Sequence
from typing import cast

from ..market_structure.bottom_n_targets import DeclineStart
from ..market_structure.trend_body_reversal import with_price_bodies
from ..market_structure.trend_judgement import _FrozenPoint, _FrozenPointCache, _frozen_point_scope, mutable_points
from ..market_structure.trend_readonly_cache import _ReadonlySnapshotCache
from ..market_structure.trend_lines import _TrendJudgementCache, polyline_turns, qualify_trend_structure
from ..market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from ..models.model import Bar
from .hierarchical_entry import _HistoryPoint, _identity, _decline_starts


class HierarchyHistoryStream:
    """Keep knowledge clocks and judgement state across daily strategy calls.

    The public batch adapter still returns independent mutable snapshots. This
    private stream lends its accumulated history only to the current day's
    synchronous strategy/account consumer; no previous day is recalculated.
    """

    def __init__(self, bars: Sequence[Bar], *, n_enabled: bool = False) -> None:
        self.bars = tuple(bars)
        self.market_index = _MarketRangeIndex.build(self.bars)
        self.n_enabled = n_enabled
        self.index = -1
        self.epoch: int | None = None
        self.history: dict[int, dict[int, tuple[dict[str, object], ...]]] = {}
        self.epochs: dict[int, int] = {}
        self.declines: dict[int, tuple[DeclineStart, ...]] = {}
        self.previous: dict[int, dict[tuple[int, int, str, float], int]] = {}
        self.points: dict[int, dict[tuple[int, int, str, float], _HistoryPoint]] = {}
        self.caches: dict[int, _TrendJudgementCache] = {}
        self._frozen_points = _FrozenPointCache()
        self._decoded = _ReadonlySnapshotCache[dict[str, object]](
            memory_bytes=8 * 1024 * 1024, max_entries=2048, evict=False)
        self._closed = False

    def close(self) -> None:
        """释放本次回测的有界点缓存；不改变已交付的历史证据。"""
        self._frozen_points.close()
        self._decoded.clear()
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

    def advance(self, index: int, epoch: int, raw: Sequence[Mapping[str, object]]) -> None:
        if self._closed:
            raise RuntimeError('hierarchy stream has been closed')
        if index != self.index + 1 or index >= len(self.bars):
            raise ValueError('hierarchy stream requires consecutive available dates')
        if epoch != self.epoch:
            self._decoded.clear()
            self.previous.clear()
            self.points.clear()
            self.caches = {level: _TrendJudgementCache(tail_reuse=True) for level in (1, 2, 3)}
        market = self.bars[:index + 1]
        prefix = _MarketPrefixIndex(self.market_index, market) if self.market_index is not None else None
        source: Sequence[Mapping[str, object]] = self._freeze(
            polyline_turns(raw, source_path=f'lecture-{epoch}'), 0, index)
        qualified_source: Sequence[Mapping[str, object]] = source
        levels: dict[int, tuple[dict[str, object], ...]] = {}
        with _frozen_point_scope(cache=self._frozen_points):
            for level in (1, 2, 3):
                source = with_price_bodies(source, market)
                judgement = self.caches[level].judge(source, path=f'lecture-{epoch}', trend_level=level,
                    asof=index, _owned_source=True)
                candidates = self._readonly_points(judgement.candidates, level=level, kind='candidates')
                geometry = self._freeze([dict(point, available_index=point['available_at'])
                    for point in self._readonly_points(judgement.points, level=level, kind='geometry')], level, index)
                published = qualify_trend_structure(candidates, source, market, trend_level=level,
                    qualified_source=qualified_source, _owned_candidates=True,
                    n_target_trend_confirmation_enabled=self.n_enabled, _market_index=prefix)
                levels[level] = tuple(dict(index=point['index'], ordinal=point['ordinal'], kind=point['kind'],
                    value=point['value'], available_at=point['available_at']) for point in published)
                if level == 1:
                    self.declines[index] = _decline_starts(candidates)
                source, qualified_source = geometry, published
        self.history[index] = levels
        self.epochs[index] = epoch
        self.index, self.epoch = index, epoch
