"""Build auditable landmarks from confirmed hierarchical trend transitions.

The chart needs the high that *confirmed* each bear-to-bull transition, not the
highest bar in its current viewport. This module keeps that choice inside the
domain layer and preserves the date on which the complete same-level transition
became knowable. Consumers may filter landmarks for display, but must not
replace them with locally selected market highs.
"""


def _reference(point):
    """Copy stable evidence fields without exposing a mutable trend point."""
    return {
        key: point[key]
        for key in ("index", "ordinal", "time", "kind", "value", "available_at", "label")
        if key in point
    }


def _confirmation_high(confirmation):
    """Resolve the source high that completed a confirmed down-to-up switch.

    Hierarchical reducers store one breaking source point. Level 1 stores the
    four-point HH/HL-or-LH/LL proof instead, so its confirming high is the last
    high in source order. Source coordinates also give a deterministic result
    when several points share one trading date.
    """
    if isinstance(confirmation, dict):
        return confirmation if confirmation.get("kind") == "H" else None
    if not isinstance(confirmation, list):
        return None
    highs = [point for point in confirmation if isinstance(point, dict) and point.get("kind") == "H"]
    return max(highs, key=lambda point: (point.get("index", -1), point.get("ordinal", 0)), default=None)


def _strictly_breaks_last_fall_high(high, key):
    """Accept only a real high strictly above a frozen high key.

    Equality is a touch, not a break. Missing or malformed evidence is rejected
    instead of being guessed from the visible chart window.
    """
    if not isinstance(high, dict) or not isinstance(key, dict):
        return False
    if high.get("kind") != "H" or key.get("kind") != "H":
        return False
    try:
        return high["value"] > key["value"]
    except (KeyError, TypeError):
        return False


def _landmark(stroke, *, high, low, broken_key, trend_level, known_at):
    """Build one stable, auditable landmark after qualification succeeds."""
    high_reference = _reference(high)
    high_reference.update(
        id=(
            f'level{trend_level}-bear-to-bull-high-'
            f'{high["index"]}-{high.get("ordinal", 0)}-'
            f'{low["index"]}-{low.get("ordinal", 0)}-{known_at}'
        ),
        label=high.get("label", "H·翻多"),
        trend_level=trend_level,
        source_level=max(0, trend_level - 1),
        source_path=stroke["id"],
        source_available_at=high["available_at"],
        available_at=known_at,
        flip="翻空为多",
        confirmation_rule="strict_last_fall_high_break",
        confirmed_low=_reference(low),
        broken_key=_reference(broken_key),
    )
    return high_reference


def _level_one_bear_to_bull_highs(strokes):
    """Read actual level-one key-break observations produced by ``_annotate``.

    Level-one wave points also carry local ``down -> up`` aggregation metadata,
    but that metadata alone does not prove a last-fall-high break. Only the
    explicit ``翻空为多`` observation owns the frozen key and confirmed bear low.
    """
    landmarks = []
    for stroke in strokes:
        for high in stroke.get("points", []):
            for event in high.get("observations", []):
                if event.get("title") != "翻空为多":
                    continue
                broken_key = event.get("key")
                low = event.get("confirmed_low")
                if not isinstance(low, dict) or low.get("kind") != "L":
                    continue
                if not _strictly_breaks_last_fall_high(high, broken_key):
                    continue
                known_at = max(high["available_at"], event.get("available_at", high["available_at"]))
                landmarks.append(
                    _landmark(
                        stroke,
                        high=high,
                        low=low,
                        broken_key=broken_key,
                        trend_level=1,
                        known_at=known_at,
                    )
                )
    return landmarks


def bear_to_bull_highs(strokes, *, trend_level):
    """Return causal high landmarks for formal bear-to-bull transitions.

    A formal low represents the extreme of the bearish wave. It qualifies only
    when its metadata proves a ``down -> up`` switch (or carries the equivalent
    Chinese flip label). The landmark is the source high that confirmed that
    low, while ``available_at`` remains the later date when the formal low and
    its transition proof were both knowable.
    """
    if trend_level == 1:
        landmarks = _level_one_bear_to_bull_highs(strokes)
        return sorted(
            landmarks,
            key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
        )

    landmarks = []
    for stroke in strokes:
        for low in stroke.get("points", []):
            if low.get("kind") != "L" or low.get("flip") != "翻空为多":
                continue
            high = _confirmation_high(low.get("confirmed_by"))
            broken_key = low.get("broken_key")
            if not _strictly_breaks_last_fall_high(high, broken_key):
                continue
            known_at = max(low["available_at"], high["available_at"])
            landmarks.append(
                _landmark(
                    stroke,
                    high=high,
                    low=low,
                    broken_key=broken_key,
                    trend_level=trend_level,
                    known_at=known_at,
                )
            )
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
    )
