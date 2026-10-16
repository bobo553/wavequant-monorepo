"""独立日期快照的计算端口；交易状态和阶段顺序仍由历史调用者拥有。"""

from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import ExitStack
from typing import Literal, Protocol, TypedDict, cast

from ..models.model import Bar
from .trend_evidence_reuse import _completion_projection_scope
from .trend_judgement import _FrozenPointCache, _frozen_point_scope, mutable_points
from .trend_lines import (
    _TrendJudgementCache, _TrendPermissionCache, attach_trend_permissions,
    high_low_points_from_strokes, prepare_trend_lines, qualify_trend_structure,
)
from .trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from .trend_body_reversal import with_price_bodies


StageKind = Literal['hierarchy', 'geometry', 'permissions', 'sidecars']


class HierarchyStageResult(TypedDict):
    geometry: list[dict[str, object]]
    candidates: list[dict[str, object]]
    published: list[dict[str, object]]


class StageTicket(Protocol):
    def result(self) -> object | None: ...


class TrendStageRunner(Protocol):
    def matches(self, bars: Sequence[Bar]) -> bool: ...

    def map(self, kind: StageKind, rows: Iterable[tuple[int, object]], *, level: int = 1,
            n_enabled: bool = False, options: tuple[bool, ...] = ()) -> Iterator[tuple[int, object | None]]:
        """按输入顺序返回；None要求在原调用者执行其完整串行路径。"""
        ...

    def close(self) -> None: ...

    def submit(self, kind: StageKind, index: int, value: object, *, level: int = 1,
               n_enabled: bool = False, options: tuple[bool, ...] = ()) -> StageTicket: ...


def _hierarchy_source(points: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """仅在私有历史通道投影直接上级的输入，完整结束证明仍由共同门禁校验。

    此通道公开的 history 和 structure_sink 都只有五字段。上级不消费下级
    许可证、候选账本或祖先的展示附件；保留真实坐标、知识日、路径边界和
    整波结束证明，避免把这些附件每天重新编码和递归复制。
    """
    fields = (
        'index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label', 'state', 'display_only',
        'trend_level', 'source_level', 'source_path', 'source_path_end_index', 'confirmed_by',
        'wave_completion_policy', 'wave_identity_state', 'wave_completed_at',
        'wave_completion_evidence', 'source_geometry_available_at', 'price_body',
    )
    return [{field: point[field] for field in fields if field in point} for point in points]


class TrendStageComputer:
    """一个股票计算单元；只处理当日来源和真实行情前缀，不推进交易状态。"""

    def __init__(self, bars: tuple[Bar, ...]) -> None:
        from .secondary_trend import _bar_date

        self.bars = bars
        self.dates = {_bar_date(bar): position for position, bar in enumerate(bars)}
        self.market = _MarketRangeIndex.build(bars)
        self.permissions = _TrendPermissionCache(bars, memory_bytes=2 * 1024 * 1024)
        self.scope = ExitStack()
        self.label: tuple[StageKind, int] | None = None
        self.frozen: _FrozenPointCache | None = None
        self.judgements = _TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024)

    def close(self) -> None:
        self.scope.close()

    def compute(self, kind: StageKind, index: int, value: object, *, level: int,
                n_enabled: bool, options: tuple[bool, ...]) -> object:
        label = kind, level
        if kind != 'sidecars' and label != self.label:
            self.scope.close()
            self.scope = ExitStack()
            # 单股票最近状态/证据各有32MiB上限，避免长区间超过小缓存后反复冷算。
            self.frozen = self.scope.enter_context(_frozen_point_scope(memory_bytes=32 * 1024 * 1024))
            self.scope.enter_context(_completion_projection_scope(memory_bytes=2 * 1024 * 1024))
            self.judgements = _TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024)
            self.label = label
        if kind == 'geometry':
            source, symbol = cast(tuple[Sequence[Mapping[str, object]], str], value)
            return prepare_trend_lines(source, trend_level=level, symbol=symbol, bars=self.bars,
                                       _judgement_cache=self.judgements, _owned_source=True)
        if kind == 'hierarchy':
            source, qualified = cast(tuple[Sequence[Mapping[str, object]], Sequence[Mapping[str, object]]], value)
            source = with_price_bodies(source, self.bars)
            judgement = self.judgements.judge(source, path='date-stage', trend_level=level,
                                               asof=index, _owned_source=True)
            geometry = mutable_points(judgement.points)
            candidates = mutable_points(judgement.candidates)
            market = self.bars[:index+1]
            prefix = _MarketPrefixIndex(self.market, market) if self.market is not None else None
            published = qualify_trend_structure(candidates, source, market, trend_level=level,
                qualified_source=qualified, _market_index=prefix, _owned_candidates=True,
                n_target_trend_confirmation_enabled=n_enabled)
            if not n_enabled:
                # 历史公开许可仅有这五字段；完整几何继续递归。N关闭时下级
                # 许可全文没有消费者，不应作为下一阶段来源账本重复传输。
                published = [{name: point[name] for name in
                              ('index', 'ordinal', 'kind', 'value', 'available_at')} for point in published]
                geometry = _hierarchy_source(geometry)
            candidates = ([{name: point[name] for name in
                            ('index', 'kind', 'wave_direction_after', 'available_at', 'upgrade_evidence')
                            if name in point} for point in candidates]
                          if level == 1 and options and options[0] else [])
            return HierarchyStageResult(geometry=geometry, candidates=candidates, published=published)
        if kind == 'permissions':
            prepared = cast(Sequence[Mapping[str, object]], value)
            market = self.bars[:index+1]
            first = attach_trend_permissions(prepared[0], market, _permission_cache=self.permissions,
                _owned_candidates=True, n_target_trend_confirmation_enabled=n_enabled)
            second = attach_trend_permissions(prepared[1], market, _permission_cache=self.permissions,
                qualified_source_points=high_low_points_from_strokes(first['qualified_strokes']) if n_enabled else None,
                _owned_candidates=True, n_target_trend_confirmation_enabled=n_enabled)
            third = attach_trend_permissions(prepared[2], market, _permission_cache=self.permissions,
                qualified_source_points=high_low_points_from_strokes(second['qualified_strokes']) if n_enabled else None,
                _owned_candidates=True, n_target_trend_confirmation_enabled=n_enabled)
            # 主消费器立即用qualified_strokes覆盖strokes；在线路传输前完成
            # 同一视图转换，避免传递随后被覆盖的整份几何。几何仍在阶段流中。
            return tuple(dict(result, strokes=result['qualified_strokes']) for result in (first, second, third))
        if kind == 'sidecars':
            from ..strategies.combined_a_entry import GeometryLevel, combined_a_contexts_from_geometry
            from ..strategies.secondary_pullback_entry import secondary_pullback_candidates

            drawing, first_view, second_view, third_view, audit = cast(tuple[
                Mapping[str, object], Mapping[str, object], Mapping[str, object], Mapping[str, object],
                Sequence[Mapping[str, object]],
            ], value)
            combined = (combined_a_contexts_from_geometry(self.bars,
                cast(GeometryLevel, drawing), cast(GeometryLevel, first_view), cast(GeometryLevel, second_view),
                cast(GeometryLevel, third_view), self.dates, index, audit,
                _market_index=self.market) if options[0] else ())
            pullback = (secondary_pullback_candidates(self.bars, drawing, second_view, self.dates, index,
                        first=first_view, _market_index=self.market)
                        if options[1] else ())
            return combined, pullback
        raise ValueError('unsupported independent trend stage')
