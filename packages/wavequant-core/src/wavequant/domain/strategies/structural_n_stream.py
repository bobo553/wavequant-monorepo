"""已确认折线的正 N 事实；结构测幅不授予交易资格。"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NObservation, NSetup, NStatus, PivotRef, observe_n,
)
from ..market_structure.polyline import PointKind
from ..market_structure.price_action import Direction
from ..market_structure.bottom_n_targets import DeclineStart
from ..market_structure.structural_n_ownership import StructuralNOwnership
from ..models.model import Bar
from ..models.validated_bars import ValidatedBars
from .eligible_polyline_n import EligibleNSource
from .polyline_target_stream import advance_completed_n


@dataclass
class _Structure:
    setup: NSetup
    local: NSetup
    offset: int
    expires: int
    identity: str
    source: EligibleNSource
    observation: NObservation | None = None


def _completion_order(pending: _Structure) -> tuple[int, int, int, int, int, int, int, str]:
    assert pending.observation is not None and pending.observation.completion is not None
    return (pending.observation.completion.bar_index + pending.offset,
            pending.setup.origin.index, pending.setup.origin.ordinal or 0,
            pending.setup.neckline.index, pending.setup.neckline.ordinal or 0,
            pending.setup.pullback.index, pending.setup.pullback.ordinal or 0, pending.identity)


class StructuralNStream:
    """复用红色资格背景的固定起点；原形成日不等于区间首次可知日。"""

    def __init__(self, bars: Sequence[Bar], *, pattern_ttl: int, exclusive_targets: bool = True) -> None:
        self.bars = bars if isinstance(bars, ValidatedBars) else ValidatedBars(bars)
        self.ttl = pattern_ttl
        self.events: list[dict[str, object]] = []
        self._seen: set[EligibleNSource] = set()
        self._pending: list[_Structure] = []
        self._last = -1
        self._owners = StructuralNOwnership(bars)
        self._exclusive_targets = exclusive_targets

    def _event(self, index: int, event: str, pending: _Structure, **fields: object) -> None:
        row: dict[str, object] = dict(event=event, bar_index=index, known_at=index,
            timestamp=self.bars[index].timestamp.isoformat(), symbol=self.bars[index].symbol,
            n_id=pending.identity, source_path=pending.source.source_path,
            qualification_window_start=pending.source.window_start,
            scope_low_index=pending.source.low_index, scope_low_ordinal=pending.source.low_ordinal)
        row.update(fields)
        self.events.append(row)

    def advance(self, index: int, sources: Sequence[EligibleNSource], *,
                active_scopes: set[tuple[str, int]], interrupted: bool = False,
                declines: Sequence[DeclineStart] = (), epoch: int = 0) -> None:
        if index != self._last + 1 or index >= len(self.bars):
            raise ValueError('structural N requires consecutive market dates')
        if any(source.known_at != index or source.scope not in active_scopes
               or any(p.confirmed_index > index for p in source.points) for source in sources):
            raise ValueError('structural N cannot consume unknown pivots')
        self._last = index
        self._owners.observe(index, epoch, declines)
        if interrupted:
            for pending in self._pending:
                if pending.observation is not None and pending.observation.completion is not None:
                    self._event(index, 'n_structure_invalidated', pending, reason='polyline_interrupted')
            self._pending.clear()
            self._seen.clear()
            return
        if sources:
            for source in sources:
                refs = source.points
                if source in self._seen:
                    continue
                self._seen.add(source)
                if tuple(p.point.kind for p in refs) != (PointKind.LOW, PointKind.HIGH, PointKind.LOW):
                    continue
                a, b, c = refs
                if not a.point.price <= c.point.price < b.point.price:
                    continue
                setup = NSetup(self.bars[0].symbol, '1d', Direction.UP,
                    PivotRef(a.point.index, a.confirmed_index, a.point.ordinal),
                    PivotRef(b.point.index, b.confirmed_index, b.point.ordinal),
                    PivotRef(c.point.index, c.confirmed_index, c.point.ordinal),
                    source='lecture_causal', box_anchor_mode=BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
                    allow_confirmation_bar=True, staged_defense=True, polyline_structure=True)
                offset = max(0, a.point.index - 1)
                local = replace(setup, **{name: replace(getattr(setup, name),
                    index=getattr(setup, name).index-offset,
                    confirmed_index=getattr(setup, name).confirmed_index-offset)
                    for name in ('origin', 'neckline', 'pullback')})
                identity = (f'polyline:{source.source_path}:{source.window_start}:'
                    + ':'.join(f'{p.point.index}.{p.point.ordinal}.{p.confirmed_index}' for p in refs))
                self._pending.append(_Structure(setup, local, offset, index+self.ttl, identity, source))
        self._seen = {source for source in self._seen if source.scope in active_scopes}
        retained: list[_Structure] = []
        completed: list[_Structure] = []
        for pending in self._pending:
            prior = pending.observation
            if pending.source.scope not in active_scopes:
                if prior is not None and prior.completion is not None:
                    self._event(index, 'n_structure_invalidated', pending, reason='trading_eligibility_closed')
                continue
            try:
                observation = (advance_completed_n(prior, self.bars[index], index-pending.offset, Direction.UP)
                    if prior is not None and prior.completion is not None else
                    observe_n(self.bars[pending.offset:index+1], pending.local, timeframe='1d',
                        milestone_basis=MilestoneBasis.EXTREME))
            except ValueError:
                # 折叠候选仍须通过共用核心的完整 A/B/C 边界校验。
                continue
            pending.observation = observation
            completion = observation.completion
            if completion is not None and (prior is None or prior.completion is None):
                completed.append(pending)
            breach = observation.first_defense_breach_index
            origin_breach = observation.first_origin_breach_index
            if completion is not None and (breach is not None or origin_breach is not None):
                broken = min(value for value in (breach, origin_breach) if value is not None)
                self._event(broken+pending.offset, 'n_structure_invalidated', pending, known_at=index,
                    reason='origin_breached' if origin_breach is not None else 'defense_breached')
                continue
            if observation.status in (NStatus.INVALIDATED, NStatus.PULLBACK_EXTENDED):
                continue
            if completion is not None or index < pending.expires:
                retained.append(pending)
        self._pending = retained
        # 同日到达的重叠窗口和折叠变体按形成时间及真实顶点统一仲裁，
        # 不依赖来源枚举顺序，也不让较高的局部底点占用一级趋势名额。
        completed.sort(key=_completion_order)
        for pending in completed:
            observation = pending.observation
            assert observation is not None and observation.completion is not None and observation.targets is not None
            completion, targets = observation.completion, observation.targets
            attack = completion.bar_index + pending.offset
            selection_fields: dict[str, object] = {}
            if self._exclusive_targets:
                selection = self._owners.select(epoch=epoch, origin=pending.setup.origin.index,
                                                attack=attack, identity=pending.identity)
                selection_fields = dict(structural_target_selected=selection.selected,
                    structural_target_owner=selection.owner, structural_trend_id=selection.trend_id,
                    structural_decline_index=selection.decline_index, structural_bottom_index=selection.bottom_index,
                    structural_target_reason=selection.reason)
            self._event(attack, 'n_structure_completed', pending,
                direction='up', known_at=index,
                origin=pending.setup.origin.index, neckline=pending.setup.neckline.index,
                pullback=pending.setup.pullback.index, attack=attack,
                origin_ordinal=pending.setup.origin.ordinal, neckline_ordinal=pending.setup.neckline.ordinal,
                pullback_ordinal=pending.setup.pullback.ordinal, defense=completion.defense,
                real_break=completion.real_break, virtual_break=completion.virtual_break,
                box_anchor=targets.box_anchor, equal_wave=targets.equal_wave,
                one_p=targets.one_p, two_t=targets.two_t,
                structural_target=True, target_eligible=False, **selection_fields)
