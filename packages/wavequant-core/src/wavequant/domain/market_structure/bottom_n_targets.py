"""Choose the causal daily entry source; independent reformed Ns keep own boxes."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, replace

from ..models.model import Bar
from .observation_window import observation_window_end
from .trend_evidence_reuse import _geometry_signature


BOTTOM_N_TARGET_POLICY = "decline_floor_n_with_independent_defense_reformations_v2"


@dataclass(frozen=True)
class DeclineStart:
    index: int
    known_at: int


@dataclass(frozen=True)
class PositiveNCompletion:
    origin: int
    attack: int
    known_at: int
    defense: float | None = None
    reformed: bool = False


@dataclass(frozen=True)
class TargetQualification:
    eligible: bool
    source_attack: int | None
    decline_index: int | None
    bottom_index: int | None
    reason: str


@dataclass(frozen=True)
class TargetRetirement:
    attack: int
    bar_index: int
    reason: str


@dataclass(frozen=True)
class BottomNTargetHistory:
    qualifications: dict[int, TargetQualification]
    source_at: tuple[int | None, ...]
    retirements: tuple[TargetRetirement, ...]
    completions: dict[int, PositiveNCompletion]


class BottomNTargetState:
    """Causal target ownership updated once per close; arrivals are known today."""

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0) -> None:
        self.bars = bars
        self.minimum: deque[int] = deque()
        self.current: DeclineStart | None = None
        self.active: PositiveNCompletion | None = None
        self.used: set[tuple[int, int]] = set()
        self.decisions: dict[int, TargetQualification] = {}
        self.selected: dict[int, PositiveNCompletion] = {}
        self.sources: list[int | None] = [None] * start_index
        self.retirements: list[TargetRetirement] = []
        # 只建立几何底点索引；资格前不产生来源、触达或失效状态。
        for index in range(start_index):
            while self.minimum and bars[self.minimum[-1]].low > bars[index].low:
                self.minimum.pop()
            self.minimum.append(index)

    def advance(self, now: int, declines: Sequence[DeclineStart] = (),
                completions: Sequence[PositiveNCompletion] = ()) -> None:
        if type(now) is not int or now != len(self.sources) or now >= len(self.bars):
            raise ValueError('target days must be consecutive and available')
        if any(not 0 <= value.index <= value.known_at == now for value in declines):
            raise ValueError('decline arrivals must be known today')
        if any(not 0 <= value.origin < value.attack <= value.known_at == now for value in completions):
            raise ValueError('N arrivals must be known today')
        bars, bar = self.bars, self.bars[now]
        minimum, current, active = self.minimum, self.current, self.active
        used, decisions, selected = self.used, self.decisions, self.selected
        sources, retirements = self.sources, self.retirements
        while minimum and bars[minimum[-1]].low > bar.low:
            minimum.pop()
        minimum.append(now)
        for decline in sorted(declines, key=lambda item: item.index):
            if current is not None and decline.index <= current.index:
                continue
            # A late confirmation inside the original N cannot retrospectively
            # split its launch into a new decline or rewrite its frozen box.
            if active is not None and decline.index <= active.attack:
                continue
            current = decline
            if active is not None:
                retirements.append(TargetRetirement(active.attack, now, "new_confirmed_decline"))
                active = None
        if active is not None and now > active.attack:
            origin_broken = bar.low < bars[active.origin].low
            defense_broken = active.defense is not None and bar.low < active.defense
            if origin_broken or defense_broken:
                retirements.append(TargetRetirement(active.attack, now,
                    "launch_origin_broken" if origin_broken else "squeeze_defense_broken"))
                if current is not None:
                    used.discard((current.index, active.origin))
                active = None
        if current is not None:
            while minimum and minimum[0] <= current.index:
                minimum.popleft()
        bottom = minimum[0] if minimum and current is not None else None
        # A new inside child must neither hide a contemporaneous floor N nor
        # lose to a cross-cycle N. Resolve only candidates known at this close.
        for n in sorted(completions, key=lambda item: (item.attack, item.origin != bottom)):
            if n.attack in selected:
                continue
            selected[n.attack] = n
            identity = (current.index, n.origin) if current is not None else None
            if n.reformed:
                active = n
                reason = "defense_reformed_n"
            elif active is not None:
                reason = "existing_bottom_launch"
            elif current is None or bottom is None:
                reason = "confirmed_decline_unavailable"
            elif n.origin != bottom or n.origin <= current.index:
                reason = "origin_is_not_decline_floor"
            elif identity in used:
                reason = "bottom_already_launched"
            else:
                active = n
                assert identity is not None
                used.add(identity)
                reason = "first_n_at_decline_floor"
            decisions[n.attack] = TargetQualification(
                active is n, active.attack if active is not None else None,
                current.index if current is not None else None, bottom, reason,
            )
        sources.append(active.attack if active is not None else None)
        self.current, self.active = current, active

    def snapshot(self) -> BottomNTargetHistory:
        return BottomNTargetHistory(self.decisions.copy(), tuple(self.sources),
                                    tuple(self.retirements), self.selected.copy())


def _target_arrivals(
    length: int, declines: Sequence[DeclineStart], completions: Sequence[PositiveNCompletion],
) -> tuple[dict[int, list[DeclineStart]], dict[int, list[PositiveNCompletion]]]:
    starts: dict[int, list[DeclineStart]] = {}
    arrivals: dict[int, list[PositiveNCompletion]] = {}
    for decline in declines:
        if not 0 <= decline.index <= decline.known_at < length:
            raise ValueError("decline must be known inside the supplied history")
        starts.setdefault(decline.known_at, []).append(decline)
    for n in completions:
        if not 0 <= n.origin < n.attack <= n.known_at < length:
            raise ValueError("completed N must have causal, chronological indices")
        arrivals.setdefault(n.known_at, []).append(n)
    return starts, arrivals


def _target_certificate(
    declines: Sequence[DeclineStart], completions: Sequence[PositiveNCompletion], end: int,
) -> bytes | None:
    if any(type(value) is not DeclineStart for value in declines):
        return None
    if any(type(value) is not PositiveNCompletion for value in completions):
        return None
    return _geometry_signature((
        tuple((value.index, value.known_at) for value in declines if value.known_at <= end),
        tuple((value.origin, value.attack, value.known_at, value.defense, value.reformed)
              for value in completions if value.known_at <= end),
    ))


class BottomNTargetHistoryStream:
    """只重建本资格窗口的目标归属；历史锚点与输出仍使用全局行情索引。"""

    def __init__(self, bars: Sequence[Bar], *, start_index: int = 0, end_index: int | None = None) -> None:
        self.bars = tuple(bars)
        self.start_index = start_index
        self.end_index = observation_window_end(len(self.bars), start_index, end_index)
        self.state = BottomNTargetState(self.bars, start_index=start_index)
        self.index = start_index - 1
        self.certificate: bytes | None = _target_certificate((), (), self.index)
        self.processed_days = 0

    def advance(
        self, index: int, declines: Sequence[DeclineStart], completions: Sequence[PositiveNCompletion],
    ) -> BottomNTargetHistory:
        if type(index) is not int or not self.start_index <= index <= self.end_index:
            raise ValueError('target history requires a date inside its observation window')
        if self.start_index:
            _target_arrivals(index + 1, declines, completions)
            declines = tuple(replace(value, known_at=max(value.known_at, self.start_index)) for value in declines)
            completions = tuple(replace(value, known_at=max(value.known_at, self.start_index)) for value in completions)
        starts, arrivals = _target_arrivals(index + 1, declines, completions)
        consumed = _target_certificate(declines, completions, self.index)
        if index < self.index or self.certificate is None or consumed != self.certificate:
            self.state = BottomNTargetState(self.bars, start_index=self.start_index)
            self.index = self.start_index - 1
        for now in range(self.index + 1, index + 1):
            self.state.advance(now, starts.get(now, ()), arrivals.get(now, ()))
            self.processed_days += 1
        self.index = index
        self.certificate = _target_certificate(declines, completions, index)
        return self.state.snapshot()


def bottom_n_target_history(
    bars: Sequence[Bar], declines: Sequence[DeclineStart], completions: Sequence[PositiveNCompletion],
) -> BottomNTargetHistory:
    """Use only known level-one declines; no new box for an internal or folded N.

    A later confirmed decline or strict origin/defense break ends the old box.
    Equal lows hold the earliest bottom. A separately confirmed defense reformation
    can restart measurement from a surviving origin. The minimum queue is O(bars + inputs),
    apart from sorting dated inputs, and never validates or scans a future bar.
    """
    starts, arrivals = _target_arrivals(len(bars), declines, completions)

    state = BottomNTargetState(bars)
    for now in range(len(bars)):
        state.advance(now, starts.get(now, ()), arrivals.get(now, ()))
    return state.snapshot()
