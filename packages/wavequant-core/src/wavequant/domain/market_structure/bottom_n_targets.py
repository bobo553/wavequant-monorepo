"""Choose the causal daily entry source; independent reformed Ns keep own boxes."""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from ..models.model import Bar


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


def bottom_n_target_history(
    bars: Sequence[Bar], declines: Sequence[DeclineStart], completions: Sequence[PositiveNCompletion],
) -> BottomNTargetHistory:
    """Use only known level-one declines; no new box for an internal or folded N.

    A later confirmed decline or strict origin/defense break ends the old box.
    Equal lows hold the earliest bottom. A separately confirmed defense reformation
    can restart measurement from a surviving origin. The minimum queue is O(bars + inputs),
    apart from sorting dated inputs, and never validates or scans a future bar.
    """
    starts: dict[int, list[DeclineStart]] = {}
    arrivals: dict[int, list[PositiveNCompletion]] = {}
    for decline in declines:
        if not 0 <= decline.index <= decline.known_at < len(bars):
            raise ValueError("decline must be known inside the supplied history")
        starts.setdefault(decline.known_at, []).append(decline)
    for n in completions:
        if not 0 <= n.origin < n.attack <= n.known_at < len(bars):
            raise ValueError("completed N must have causal, chronological indices")
        arrivals.setdefault(n.known_at, []).append(n)

    minimum: deque[int] = deque()
    current: DeclineStart | None = None
    active: PositiveNCompletion | None = None
    used: set[tuple[int, int]] = set()
    decisions: dict[int, TargetQualification] = {}
    selected: dict[int, PositiveNCompletion] = {}
    sources: list[int | None] = []
    retirements: list[TargetRetirement] = []
    for now, bar in enumerate(bars):
        while minimum and bars[minimum[-1]].low > bar.low:
            minimum.pop()
        minimum.append(now)
        for decline in sorted(starts.get(now, ()), key=lambda item: item.index):
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
        for n in sorted(arrivals.get(now, ()), key=lambda item: (item.attack, item.origin != bottom)):
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
    return BottomNTargetHistory(decisions, tuple(sources), tuple(retirements), selected)
