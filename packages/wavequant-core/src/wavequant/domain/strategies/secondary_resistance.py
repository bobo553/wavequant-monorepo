"""A lower-level squeeze cannot resolve a fresh resisted secondary breakout."""

from ..models.model import Bar
from ..market_structure.price_action import Direction, ShadowPolicy, observe_resistance


def secondary_resistance_history(
    bars: list[Bar], history: dict, *, key_events=(), include_resolved=False
) -> dict[int, dict]:
    blocked = {}
    current_key = None
    attack = None
    resisted = False
    record = 0.0
    defense = 0.0
    resolved_at = None
    resolved_high = 0.0
    tracked_level = None
    keys = {e["bar_index"]: e["key"] for e in key_events if e["event"] == "hierarchy_resistance_key"}
    chart_key = None
    chart_known = False
    for i in range(1, len(bars)):
        if i - 1 in keys:
            chart_key, chart_known = keys[i - 1], True
        highs = [p for p in history.get(i - 1, {}).get(2, ()) if p["kind"] == "H"]
        if chart_known:
            # Supplement the still-known formal pressure; a newer, lower source
            # high must not silently lower that unresolved structural hurdle.
            candidates = highs[-1:] + ([chart_key] if chart_key is not None else [])
            highs = [max(candidates, key=lambda p: p["value"])] if candidates else []
        if attack is not None and resisted and tracked_level is not None:
            # Promoting the resistance rally's own high must not erase the
            # unresolved breakout that produced it.
            highs = [tracked_level]
        if not highs:
            current_key, attack, resisted = None, None, False
            resolved_at = None
            continue
        level = highs[-1]
        key = (level["index"], level["value"])
        if key != current_key:
            current_key, attack, resisted = key, None, False
            resolved_at = None
            tracked_level = level
        bar, prev = bars[i], bars[i - 1]
        upper = bar.high - max(bar.open, bar.close)
        # A dominant upper wick can express supply despite a long lower wick
        # diluting its fraction of the entire range.
        resistance = observe_resistance(
            prev, bar, attack_direction=Direction.UP, shadow_policy=ShadowPolicy(0.5)
        ).detected is True or (upper > abs(bar.close - bar.open) and upper >= (bar.high - bar.low) / 3 and upper > 0)
        if attack is None and prev.high <= level["value"] < bar.high:
            attack, record, defense = i, bar.high, min(bar.low, prev.close)
            resisted = resistance
        if attack is None:
            continue
        if i == attack + 1:
            resisted |= resistance
        if resisted:
            confirmed = (
                i >= attack + 2
                and not resistance
                and bar.close > bar.open
                and bar.close > record
                and bar.low >= defense
                and bar.low >= min(prev.low, bars[i - 2].close)
            )
            if not confirmed:
                blocked[i] = dict(
                    secondary_high_date=bars[level["index"]].timestamp.date().isoformat(),
                    secondary_high=level["value"],
                    secondary_attack_date=bars[attack].timestamp.date().isoformat(),
                    secondary_resistance_high=record,
                    secondary_defense=defense,
                    secondary_current_resistance=resistance,
                )
            else:
                resisted = False
                resolved_at = i
                resolved_high = record
        if include_resolved and resolved_at is not None:
            blocked[i] = dict(
                secondary_resistance_resolved=True,
                secondary_high_date=bars[level["index"]].timestamp.date().isoformat(),
                secondary_high=level["value"],
                secondary_attack_date=bars[attack].timestamp.date().isoformat(),
                secondary_resolution_date=bars[resolved_at].timestamp.date().isoformat(),
                secondary_resistance_high=resolved_high,
                secondary_defense=defense,
                secondary_confirmation_close=bars[resolved_at].close,
            )
        record = max(record, bar.high)
        if bar.low < defense:
            attack, resisted = None, False
            resolved_at = None
    return blocked
