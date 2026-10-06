"""Causal next-session confirmation for reattacks after a reached five-top."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from math import isfinite

from ..market_state.candle_strength import strong_bullish_candle
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


FIVE_TOP_REBREAK_PENDING = "wave_five_top_rebreak_response_pending"


@dataclass
class _Episode:
    attack: int
    origin: int
    reached: int
    target: Decimal
    origin_price: Decimal
    oscillation: int | None = None
    rebreak: int | None = None
    last_rebreak: int | None = None
    permitted: bool = False


@dataclass(frozen=True)
class FiveTopRebreakHistory:
    """Dated refusals and confirmations; no suffix or cached mutable episode state."""

    risks: dict[int, dict[str, object]] = field(default_factory=dict)
    confirmations: dict[int, dict[str, object]] = field(default_factory=dict)


def _price(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or value <= 0:
        return None
    return Decimal(str(value))


def five_top_rebreak_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]],
) -> FiveTopRebreakHistory:
    """Keep direct strong attacks; after weakness require the next actual session.

    A renewed attack closes above the previous high and the frozen five-top.
    Its next response must advance the close, hold its virtual low and either
    be a strong candle or defeat resistance on that very attack with a clean
    close above its high. An unrelated N's squeeze label cannot release it.
    A failed response cannot borrow a strong third candle retroactively.
    Below the frozen five-top, existing pullback gates retain their own scope.
    Confirmation remains valid through clean advancing closes, until weakness
    starts another episode. Only the original A's invalidation retires a goal.
    """
    dated = sorted(
        ((known, event) for event in events
         if type(known := event.get("bar_index")) is int and 0 <= known < len(bars)),
        key=lambda row: (row[0], row[1].get("event") == "wave_projection_invalidated"),
    )
    active: dict[tuple[int, int], _Episode] = {}
    invalidated: set[tuple[int, int]] = set()
    invalidations: dict[int, list[tuple[int, int]]] = {}
    for known, event in dated:
        attack, origin = event.get("attack"), event.get("origin_index")
        if (type(attack) is int and type(origin) is int and 0 <= origin < attack <= known
                and (event.get("event") == "wave_projection_invalidated" or event.get("state") == "invalidated")):
            invalidations.setdefault(known, []).append((attack, origin))
    result = FiveTopRebreakHistory()
    strong = [strong_bullish_candle(bar) for bar in bars]
    resistance = [()] + [observe_resistance(bars[index - 1], bars[index],
        attack_direction=Direction.UP, shadow_policy=ShadowPolicy(.5)).reasons for index in range(1, len(bars))]
    cursor = 0
    for index, bar in enumerate(bars):
        # Milestones on today's close cannot govern today's earlier candidates.
        while cursor < len(dated) and dated[cursor][0] < index:
            known, event = dated[cursor]
            cursor += 1
            attack, origin = event.get("attack"), event.get("origin_index")
            if type(attack) is not int or type(origin) is not int or not 0 <= origin < attack <= known:
                continue
            key = attack, origin
            if event.get("event") == "wave_projection_invalidated" or event.get("state") == "invalidated":
                active.pop(key, None)
                invalidated.add(key)
            if key in invalidated or key in active:
                continue
            if (event.get("event") != "wave_projection_target_reached"
                    or event.get("reached_stage") != "five_top"):
                continue
            target, origin_price = _price(event.get("reached_target")), _price(event.get("a_origin"))
            if target is None or origin_price is None or origin_price >= target:
                continue
            # A strong equality touch may continue directly above on the next
            # session; the touch itself still supplies no strict-above permission.
            direct = strong[known] and Decimal(str(bars[known].close)) >= target
            active[key] = _Episode(attack, origin, known, target, origin_price,
                                   oscillation=None if direct else known, permitted=direct)
        for key in invalidations.get(index, []):
            active.pop(key, None)
            invalidated.add(key)
        blocked: list[tuple[_Episode, dict[str, object]]] = []
        allowed: list[tuple[_Episode, dict[str, object]]] = []
        for key, episode in list(active.items()):
            if Decimal(str(bar.low)) < episode.origin_price:
                invalidated.add(key)
                active.pop(key)
                continue
            previous = bars[index - 1]
            above = Decimal(str(bar.close)) > episode.target
            status = "await_reattack"
            response: int | None = None
            path: str | None = None
            if episode.rebreak is not None and index == episode.rebreak + 1:
                response = index
                attack_bar = bars[episode.rebreak]
                support = min(attack_bar.low, bars[episode.rebreak - 1].close)
                advances = above and bar.close > attack_bar.close and bar.close > bar.open and bar.low >= support
                squeeze = bool(resistance[episode.rebreak]) and not resistance[index] and bar.close > attack_bar.high
                if advances and (strong[index] or squeeze):
                    episode.permitted = True
                    path = "next_strong_candle" if strong[index] else "next_resistance_failed_squeeze"
                    status = "confirmed"
                else:
                    episode.permitted = False
                    status = "response_failed"
                # Failed responses do not extend the original two-session window.
                episode.rebreak = None
            elif episode.permitted:
                if above and bar.close > previous.close and (strong[index] or not resistance[index]):
                    path = ("direct_strong_continuation" if episode.oscillation is None
                            else "confirmed_reattack_continuation")
                    status = "confirmed"
                else:
                    episode.permitted = False
                    episode.oscillation = index
            if not episode.permitted and episode.oscillation is None:
                episode.oscillation = index
            if not episode.permitted and above and bar.close > bar.open and bar.close > previous.high:
                episode.rebreak = index
                episode.last_rebreak = index
                response, status = None, "await_next_session"
            attack_index = episode.last_rebreak
            evidence: dict[str, object] = dict(
                attack=episode.attack, wave_n_date=bars[episode.attack].timestamp.date().isoformat(),
                wave_n_origin_date=bars[episode.origin].timestamp.date().isoformat(),
                wave_five_top_reached_date=bars[episode.reached].timestamp.date().isoformat(),
                wave_five_top_target=float(episode.target),
                wave_oscillation_date=(bars[episode.oscillation].timestamp.date().isoformat()
                                       if episode.oscillation is not None else None),
                wave_rebreak_date=(bars[attack_index].timestamp.date().isoformat()
                                   if attack_index is not None else None),
                wave_rebreak_high=bars[attack_index].high if attack_index is not None else None,
                wave_rebreak_close=bars[attack_index].close if attack_index is not None else None,
                wave_rebreak_virtual_low=(min(bars[attack_index].low, bars[attack_index - 1].close)
                                          if attack_index is not None else None),
                wave_response_date=bar.timestamp.date().isoformat() if response is not None else None,
                wave_response_status=status, wave_rebreak_path=path,
                observed_close=bar.close, confirmation_strong_bullish=strong[index],
                rebreak_resistance_patterns=list(resistance[attack_index]) if attack_index is not None else [],
                response_resistance_patterns=list(resistance[index]) if response is not None else [],
            )
            if episode.permitted:
                allowed.append((episode, evidence))
            elif above:
                blocked.append((episode, dict(evidence, reason=FIVE_TOP_REBREAK_PENDING)))
        # Every live reached goal must pass; a new small N cannot erase old supply.
        rank = lambda row: (row[0].reached, row[0].target, row[0].attack)
        if blocked:
            result.risks[index] = max(blocked, key=rank)[1]
        elif allowed:
            result.confirmations[index] = max(allowed, key=rank)[1]
    return result
