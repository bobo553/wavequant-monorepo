"""Observe eligible polyline Ns in the market stream, retaining completed boxes."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ..market_structure.n_shape import (
    BoxAnchorMode, Milestone, MilestoneBasis, NObservation, NSetup, NStatus, PivotRef, observe_n,
)
from ..market_structure.polyline import PointKind
from ..market_structure.price_action import Direction, teaching_inside
from ..models.model import Bar
from ..models.validated_bars import ValidatedBars
from .eligible_polyline_n import EligibleNSource

if TYPE_CHECKING:
    from .integrated_strategy import SystemStrategy


def advance_completed_n(observation: NObservation, bar: Bar, index: int,
                        direction: Direction) -> NObservation:
    """Advance frozen targets without searching for the completed N again."""
    if (observation.completion is None or observation.anchors is None or observation.targets is None
            or index != observation.asof_index + 1):
        raise ValueError('a completed N and its next close are required')
    up = direction == Direction.UP
    sign = 1 if up else -1
    adverse = bar.low if up else bar.high
    defense = observation.first_defense_breach_index
    origin = observation.first_origin_breach_index
    if defense is None and sign * (adverse - observation.completion.defense) < 0:
        defense = index
    if origin is None and sign * (adverse - observation.anchors.origin) < 0:
        origin = index
    hits = list(observation.milestones)
    names = ('equal_wave', 'one_p', 'two_t')
    if origin is None and not (up and defense is not None):
        price = bar.high if up else bar.low
        while len(hits) < len(names):
            name = names[len(hits)]
            target = getattr(observation.targets, name)
            if target is None or sign * (price - target) < 0:
                break
            hits.append(Milestone(name, target, index, bar.timestamp, MilestoneBasis.EXTREME.value,
                                  bool(hits and hits[-1].bar_index == index)))
    return replace(observation, asof_index=index, observed_at=bar.timestamp, milestones=tuple(hits),
        next_target=names[len(hits)] if len(hits) < len(names) else None,
        first_defense_breach_index=defense, first_origin_breach_index=origin,
        measured_wave_complete=len(hits) == len(names), stacking_precondition_observed=len(hits) == len(names))


@dataclass
class _PendingN:
    source: EligibleNSource
    setup: NSetup
    local: NSetup
    offset: int
    expires: int
    observation: NObservation | None = None


class PolylineTargetStream:
    """Only forming Ns use bounded prefix observation; completed Ns advance once.

    The original real/virtual breakout rule remains in observe_n. Geometry
    comes exclusively from the eligible polyline suffix. Five/ten still need
    their separate confirmed wave prerequisites; they are not invented here.
    """

    def __init__(self, bars: Sequence[Bar], config: 'SystemStrategy', *,
                 publish: Callable[[dict[str, object]], None] | None = None) -> None:
        self.bars = bars if isinstance(bars, ValidatedBars) else ValidatedBars(bars)
        self.config = config
        self.publish = publish
        self.observations: dict[tuple[NSetup, int, int], NObservation] = {}
        self.scoped_observations: dict[tuple[EligibleNSource, int], NObservation] = {}
        self._pending: list[_PendingN] = []
        self._seen: set[EligibleNSource] = set()
        self._last = -1
        self.processed_observations = 0

    def _register(self, source: EligibleNSource) -> None:
        a, b, c = source.points
        if source in self._seen:
            return
        self._seen.add(source)
        if source.known_at - a.point.index > self.config.structure_window:
            return
        mother_impulse = (a.point.kind == PointKind.LOW
            and a.point.index == b.point.index < c.point.index and a.point.ordinal < b.point.ordinal)
        same_bar = (a.point.kind == PointKind.LOW and b.point.kind == PointKind.HIGH
            and c.point.kind == PointKind.LOW and a.point.index < b.point.index == c.point.index
            and b.point.ordinal < c.point.ordinal)
        if not (a.point.index < b.point.index < c.point.index or mother_impulse or same_bar):
            return
        inside = same_bar and b.point.index > 0 and teaching_inside(self.bars[b.point.index - 1], self.bars[b.point.index])
        direction = Direction.UP if a.point.kind == PointKind.LOW else Direction.DOWN
        setup = NSetup(self.bars[0].symbol, '1d', direction,
            PivotRef(a.point.index, a.confirmed_index), PivotRef(b.point.index, b.confirmed_index),
            PivotRef(c.point.index, c.confirmed_index), self.config.pivot_mode, BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
            allow_confirmation_bar=True, allow_outside_close=True, allow_mother_impulse=mother_impulse,
            staged_defense=direction == Direction.UP, allow_mother_pullback=same_bar and not inside,
            allow_inside_pullback=inside)
        offset = max(0, a.point.index - self.config.volume_lookback)
        local = replace(setup, **{name: PivotRef(getattr(setup, name).index - offset,
            getattr(setup, name).confirmed_index - offset) for name in ('origin', 'neckline', 'pullback')})
        self._pending.append(_PendingN(source, setup, local, offset,
            min(len(self.bars) - 1, source.known_at + self.config.pattern_ttl)))

    def advance(self, index: int, sources: Sequence[EligibleNSource], *, interrupted: bool = False,
                buy_eligible: bool = True, active_scopes: set[tuple[str, int]] | None = None) -> None:
        if type(index) is not int or index != self._last + 1 or index >= len(self.bars):
            raise ValueError('N targets require consecutive market dates')
        if any(source.known_at != index for source in sources):
            raise ValueError('N sources must be delivered on their first knowledge day')
        if sources and not buy_eligible:
            raise ValueError('trading N sources require an open eligibility window')
        if active_scopes is not None and any(source.scope not in active_scopes for source in sources):
            raise ValueError('trading N sources must belong to a currently open window')
        retained = []
        for pending in self._pending:
            if not buy_eligible or (active_scopes is not None and pending.source.scope not in active_scopes):
                self._save(pending, index - 1)
            else:
                retained.append(pending)
        self._pending = retained
        if interrupted:
            for pending in self._pending:
                self._save(pending, index - 1)
            self._pending.clear()
        else:
            for source in sources:
                self._register(source)
        retained = []
        for pending in self._pending:
            prior = pending.observation
            try:
                if prior is not None and prior.completion is not None:
                    observation = advance_completed_n(prior, self.bars[index], index - pending.offset,
                                                      pending.setup.direction)
                elif prior is not None and prior.status in (NStatus.INVALIDATED, NStatus.PULLBACK_EXTENDED):
                    observation = replace(prior, asof_index=index - pending.offset,
                                          observed_at=self.bars[index].timestamp)
                else:
                    observation = observe_n(self.bars[pending.offset:index + 1], pending.local,
                        timeframe='1d', milestone_basis=MilestoneBasis.EXTREME,
                        measurement_start=pending.source.known_at - pending.offset)
            except ValueError as error:
                if self.publish is not None:
                    self.publish(dict(event='n_geometry_rejected', bar_index=index, known_at=pending.source.known_at,
                                      setup=pending.setup, reason=str(error)))
                continue
            self.processed_observations += 1
            pending.observation = observation
            if self.publish is not None and (prior is None or observation.completion != prior.completion
                    or observation.milestones != prior.milestones
                    or observation.first_defense_breach_index != prior.first_defense_breach_index
                    or observation.first_origin_breach_index != prior.first_origin_breach_index
                    or observation.status != prior.status):
                self.publish(dict(event='polyline_n_measurement', bar_index=index,
                    known_at=pending.source.known_at, setup=pending.setup, observation_offset=pending.offset,
                    observation=observation, window_start=pending.source.window_start,
                    alternation_low_index=pending.source.low_index,
                    alternation_low_ordinal=pending.source.low_ordinal, source_path=pending.source.source_path))
            if index == pending.expires:
                self._save(pending, index)
            else:
                retained.append(pending)
        self._pending = retained
        self._last = index

    def _save(self, pending: _PendingN, end: int) -> None:
        if pending.observation is not None:
            self.observations[pending.local, pending.offset, end] = pending.observation
            self.scoped_observations[pending.source, end] = pending.observation

    def finish(self) -> None:
        if self._last != len(self.bars) - 1 or self._pending:
            raise RuntimeError('N targets have not consumed the complete market stream')

    def current_observations(self) -> dict[tuple[NSetup, int, int], NObservation]:
        """借用当日已冻结的观察值，不等待形态TTL结束再交给策略。"""
        result = dict(self.observations)
        for pending in self._pending:
            if pending.observation is not None:
                result[pending.local, pending.offset, self._last] = pending.observation
        return result

    def current_scoped_observations(self) -> dict[tuple[EligibleNSource, int], NObservation]:
        """窗口关闭后只保留冻结结果；重开来源不会覆盖旧窗口产物。"""
        result = dict(self.scoped_observations)
        for pending in self._pending:
            if pending.observation is not None:
                result[pending.source, self._last] = pending.observation
        return result
