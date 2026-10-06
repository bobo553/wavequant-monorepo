"""Block new entries within one frozen N box of a known, pending five-top."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from ..market_state.candle_strength import strong_bullish_candle
from ..models.model import Bar


FIVE_TOP_ENTRY_TOO_CLOSE = "wave_five_top_entry_too_close"


def _price(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        return None
    return Decimal(str(value))


@dataclass(frozen=True)
class _Goal:
    known_index: int
    target: Decimal
    box: Decimal
    defense: Decimal


def five_top_entry_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]],
) -> dict[int, dict[str, object]]:
    """Use the previous session's goal, including on its first reach day.

    A same-day advance to ten-full cannot erase the five-top approached by that
    day's entry. On later days, completed or suspended goals no longer block.
    A first strong close strictly crossing above the target is a direct attack,
    exempt from the remaining-space gate. Reached-goal reattacks still pass the
    separate global next-response gate; optional reward/risk settings cannot
    remove either requirement.
    """
    dated: list[tuple[int, Mapping[str, object]]] = []
    for event in events:
        known = event.get("bar_index")
        if type(known) is int and 0 <= known < len(bars):
            dated.append((known, event))
    dated.sort(key=lambda row: (row[0], row[1].get("event") == "wave_projection_target_reached",
                               row[1].get("event") == "wave_projection_invalidated"))
    active: dict[tuple[int, int], _Goal] = {}
    invalidated: set[tuple[int, int]] = set()
    risks: dict[int, dict[str, object]] = {}
    cursor = 0
    for index, bar in enumerate(bars):
        while cursor < len(dated) and dated[cursor][0] < index:
            known, event = dated[cursor]
            cursor += 1
            attack, origin = event.get("attack"), event.get("origin_index")
            if type(attack) is not int or type(origin) is not int:
                continue
            if not 0 <= origin < attack <= known:
                continue
            key = (attack, origin)
            if event.get("state") == "invalidated":
                invalidated.add(key)
            if key in invalidated:
                active.pop(key, None)
                continue
            target, box, defense = (_price(event.get(field)) for field in ("target", "box_height", "defense"))
            if (event.get("target_stage") != "five_top" or event.get("state") not in ("stacking", "pushing")
                    or target is None or box is None or defense is None or defense >= target):
                active.pop(key, None)
                continue
            active[key] = _Goal(known, target, box, defense)
        close, low = Decimal(str(bar.close)), Decimal(str(bar.low))
        for key, goal in list(active.items()):
            if low < goal.defense:
                invalidated.add(key)
                active.pop(key)
        near = [(key, goal) for key, goal in active.items() if goal.target - close <= goal.box]
        if index and strong_bullish_candle(bar):
            near = [(key, goal) for key, goal in near
                    if not Decimal(str(bars[index - 1].close)) <= goal.target < close]
        if not near:
            continue
        (attack, origin), goal = min(near, key=lambda item: (item[1].target, -item[0][0]))
        risks[index] = dict(
            reason=FIVE_TOP_ENTRY_TOO_CLOSE, attack=attack,
            wave_n_date=bars[attack].timestamp.date().isoformat(),
            wave_n_origin_date=bars[origin].timestamp.date().isoformat(),
            wave_five_top_target=float(goal.target), wave_box_height=float(goal.box),
            reference_price=bar.close, wave_remaining_reward=float(goal.target - close),
            wave_remaining_boxes=float((goal.target - close) / goal.box),
            wave_target_known_date=bars[goal.known_index].timestamp.date().isoformat(),
        )
    return risks
