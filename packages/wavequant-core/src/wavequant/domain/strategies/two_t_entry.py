"""Require a clean close breakout of an already known two-T target."""

from collections.abc import Mapping, Sequence
from fractions import Fraction
from math import isfinite

from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance
from ..models.model import Bar


TWO_T_ENTRY_OBSERVATION = "wave_two_t_requires_strong_close"


def _strong_body(bar: Bar) -> bool:
    opened, high, low, closed = (Fraction(str(v)) for v in (bar.open, bar.high, bar.low, bar.close))
    # The existing strong A-wave confirmation uses these same body thresholds.
    return closed - opened >= opened * Fraction(3, 100) and closed - opened >= (high - low) * Fraction(3, 5)


def two_t_entry_history(
    bars: Sequence[Bar], events: Sequence[Mapping[str, object]],
) -> dict[int, dict[str, object]]:
    """Keep the original N's failed target breakout across newer entry Ns.

    Only targets known by the observed session participate. First touch needs
    a strong bullish close above the target without bearish resistance. The
    following session can revoke that entry permission, never the earlier
    decision. Later retests still need a clean strong close, but a new wave
    trading entirely below the old target is not a target breakout. The
    original defense failure or a confirmed breakout retires this target.
    """
    risks: dict[int, dict[str, object]] = {}
    barriers: dict[int, float] = {}
    seen: set[tuple[int, int, float]] = set()
    targets: list[tuple[int, Mapping[str, object]]] = []
    for event in events:
        index = event.get("bar_index")
        if event.get("event") == "wave_continuation_ready" and isinstance(index, int) and not isinstance(index, bool):
            targets.append((index, event))
    for known, event in sorted(targets, key=lambda item: item[0]):
        origin, attack = event.get("origin_index"), event.get("attack_index")
        squeeze, target, defense = event.get("squeeze_index"), event.get("two_t"), event.get("defense")
        if (type(origin) is not int or type(attack) is not int
                or type(squeeze) is not int or not 0 <= origin < attack <= squeeze <= known < len(bars)
                or type(target) not in (int, float) or not isinstance(target, (int, float))
                or type(defense) not in (int, float) or not isinstance(defense, (int, float))
                or not isfinite(target) or not isfinite(defense) or not 0 < defense < target):
            continue
        key = (origin, attack, float(target))
        if key in seen:
            continue
        seen.add(key)
        exact_target = Fraction(str(target))
        reached: int | None = None
        blocked = False
        for now in range(attack + 1, len(bars)):
            bar = bars[now]
            if bar.low < defense:
                break
            if now < known:
                continue
            if reached is None:
                # The same session that establishes the target cannot borrow
                # an earlier intraday high to claim a prior target touch.
                observed = bar.close if now == known else bar.high
                if Fraction(str(observed)) < exact_target:
                    continue
                reached = now
            if now > reached + 1 and Fraction(str(bar.high)) < exact_target:
                continue
            close_above = Fraction(str(bar.close)) > exact_target
            strong = _strong_body(bar)
            resistance = observe_resistance(bars[now - 1], bar, attack_direction=Direction.UP,
                                             shadow_policy=ShadowPolicy(.5))
            clean = not resistance.detected
            if close_above and clean and (strong or (not blocked and now == reached + 1)):
                if now > reached:
                    break
                continue
            blocked = True
            evidence: dict[str, object] = dict(
                reason=TWO_T_ENTRY_OBSERVATION, attack=attack,
                wave_n_date=str(bars[attack].timestamp.date()),
                wave_n_origin_date=str(bars[origin].timestamp.date()),
                wave_n_origin_price=bars[origin].low,
                wave_reached_stage="two_t", wave_reached_price=target,
                wave_reached_date=str(bars[reached].timestamp.date()),
                target_known_date=str(bars[known].timestamp.date()),
                target_entry_trigger=("first_two_t_touch" if now == reached else
                                      "next_two_t_session" if now == reached + 1 else
                                      "unconfirmed_two_t_retest"),
                target_resistance_patterns=list(resistance.reasons),
                two_t_close_above=close_above, two_t_strong_body=strong,
                two_t_without_bearish_resistance=clean,
                observed_open=bar.open, observed_high=bar.high, observed_low=bar.low,
                observed_close=bar.close, previous_close=bars[now - 1].close,
            )
            # Multiple valid original Ns remain independent; a newer, farther
            # entry target cannot suppress the earlier resistance evidence.
            if now not in barriers or target > barriers[now]:
                risks[now] = evidence
                barriers[now] = float(target)
    return risks
