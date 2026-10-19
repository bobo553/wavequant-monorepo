"""Block new entries within one frozen N box of a known, pending five-top."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from math import isfinite

from ..models.model import Bar
from .event_history import EventHistoryStream


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


class FiveTopEntryHistory(EventHistoryStream):
    """Use the previous session's goal, including on its first reach day.

    A same-day advance to ten-full cannot erase the five-top approached by that
    day's entry. On later days, completed or suspended goals no longer block.
    All entry channels share this gate; optional reward/risk settings do not
    change the minimum remaining space of one original N box.
    """
    def _effective_index(self, event: Mapping[str, object]) -> int:
        return super()._effective_index(event) + 1

    def _reset_state(self) -> None:
        self.active: dict[tuple[int, int], _Goal] = {}
        self.invalidated: set[tuple[int, int]] = set()

    def _advance_day(self, index: int) -> None:
        bars, active, invalidated = self.bars, self.active, self.invalidated
        bar = bars[index]
        known = index - 1
        dated = sorted(self.dated.get(known, ()), key=lambda event: (
            event.get('event') == 'wave_projection_target_reached',
            event.get('event') == 'wave_projection_invalidated'))
        for event in dated:
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
        if not near:
            return
        (attack, origin), goal = min(near, key=lambda item: (item[1].target, -item[0][0]))
        self.history[index] = dict(
            reason=FIVE_TOP_ENTRY_TOO_CLOSE, attack=attack,
            wave_n_date=bars[attack].timestamp.date().isoformat(),
            wave_n_origin_date=bars[origin].timestamp.date().isoformat(),
            wave_five_top_target=float(goal.target), wave_box_height=float(goal.box),
            reference_price=bar.close, wave_remaining_reward=float(goal.target - close),
            wave_remaining_boxes=float((goal.target - close) / goal.box),
            wave_target_known_date=bars[goal.known_index].timestamp.date().isoformat(),
        )


def five_top_entry_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]],
) -> dict[int, dict[str, object]]:
    """Batch compatibility uses the same dated reducer as the daily stream."""
    return FiveTopEntryHistory(bars).advance(len(bars) - 1, events)
