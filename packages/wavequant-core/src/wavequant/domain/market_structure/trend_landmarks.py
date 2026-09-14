"""Build auditable landmarks from confirmed hierarchical trend transitions.

The chart needs the high that *confirmed* each bear-to-bull transition, not the
highest bar in its current viewport. This module keeps that choice inside the
domain layer and preserves the date on which the complete same-level transition
became knowable. Consumers may filter landmarks for display, but must not
replace them with locally selected market highs.
"""

from zoneinfo import ZoneInfo


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


def _point_order(point):
    """Return a stable within-stroke order for same-day projected vertices."""
    if not isinstance(point, dict):
        return -1, -1
    return point.get("index", -1), point.get("ordinal", 0)


def _bar_date(bar):
    """Return one market bar's Shanghai exchange date."""
    timestamp = bar.timestamp
    if timestamp.tzinfo:
        timestamp = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
    return timestamp.date().isoformat()


def _complete_reference(point, *, kind):
    """Require every field needed to validate and publish one evidence point.

    Trend metadata may come from persisted calculations produced by an older
    Core version. Reject incomplete references here so one stale observation
    cannot fail the complete chart response while ``known_at`` is assembled.
    """
    return (
        isinstance(point, dict)
        and point.get("kind") == kind
        and all(field in point for field in ("index", "value", "available_at"))
    )


def _valid_alternation(low, event):
    """Validate the complete, already-confirmed bear-to-bull pullback chain.

    ``空多交替`` is stronger than merely finding a low to the right of a high.
    The event must retain the frozen bearish key, the low established before
    the flip, the actual breakout high and the impulse origin used by the
    retracement rule.  This prevents consumers from reconstructing a plausible
    but unconfirmed low from the current viewport.
    """
    if not _complete_reference(low, kind="L") or not isinstance(event, dict):
        return False
    if event.get("title") != "空多交替" or "ratio" not in event:
        return False
    high = event.get("flip_high")
    bear_low = event.get("confirmed_bear_low")
    key = event.get("broken_key")
    origin = event.get("origin")
    if not _strictly_breaks_last_fall_high(high, key):
        return False
    if not _complete_reference(high, kind="H"):
        return False
    if not _complete_reference(key, kind="H"):
        return False
    if not _complete_reference(bear_low, kind="L"):
        return False
    if not _complete_reference(origin, kind="L"):
        return False
    if not (_point_order(high) < _point_order(low)):
        return False
    try:
        ratio = event["ratio"]
        return (
            0 < ratio < 2 / 3
            and bear_low["value"] < low["value"] < high["value"]
            and origin["value"] < low["value"] < high["value"]
        )
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


def bear_bull_alternation_lows(strokes, *, trend_level):
    """Return same-level lows that formally completed bear/bull alternation.

    The low is emitted only from an explicit ``空多交替`` observation produced
    by the trend annotator.  Its full predecessor chain must still prove that
    the preceding high strictly broke the frozen last-fall-high and that this
    low is a partial, strictly-below-two-thirds higher pullback.  ``available_at``
    is the latest date in that chain, so historical replay never reveals the
    landmark before all of its evidence was confirmed.
    """
    landmarks = []
    for stroke in strokes:
        for low in stroke.get("points", []):
            if not isinstance(low, dict):
                continue
            for event in low.get("observations", []):
                if not _valid_alternation(low, event):
                    continue
                high = event["flip_high"]
                bear_low = event["confirmed_bear_low"]
                key = event["broken_key"]
                origin = event["origin"]
                known_at = max(
                    low["available_at"],
                    event.get("available_at", low["available_at"]),
                    high["available_at"],
                    bear_low["available_at"],
                    key["available_at"],
                    origin["available_at"],
                )
                reference = _reference(low)
                reference.update(
                    id=(
                        f'level{trend_level}-bear-bull-alternation-low-'
                        f'{high["index"]}-{high.get("ordinal", 0)}-'
                        f'{low["index"]}-{low.get("ordinal", 0)}-{known_at}'
                    ),
                    label=low.get("label", "L·交替"),
                    trend_level=trend_level,
                    source_level=trend_level,
                    source_path=stroke["id"],
                    source_available_at=low["available_at"],
                    available_at=known_at,
                    flip="空多交替",
                    confirmation_rule="confirmed_higher_pullback_strictly_below_two_thirds",
                    retracement_ratio=event["ratio"],
                    weak_countermove=bool(event.get("weak_countermove", False)),
                    confirmed_flip_high=_reference(high),
                    confirmed_bear_low=_reference(bear_low),
                    broken_key=_reference(key),
                    retracement_origin=_reference(origin),
                )
                landmarks.append(reference)
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
    )


def post_alternation_bull_highs(strokes, *, trend_level):
    """Return the confirmed high ending the first bull leg after alternation.

    One confirmed ``空多交替`` low changes the same-level background to bull.
    The immediately following same-level vertex is therefore the endpoint of
    that first bullish ``L -> H`` leg.  It qualifies only when the next vertex
    is a complete confirmed high whose price is strictly above the low.  The
    function never skips an invalid next vertex to select a later convenient
    high and never searches raw bars or a consumer's current chart window.
    """
    alternation_lows = bear_bull_alternation_lows(strokes, trend_level=trend_level)
    lows_by_path = {}
    for low in alternation_lows:
        lows_by_path.setdefault(low["source_path"], []).append(low)

    landmarks = []
    for stroke in strokes:
        source_path = stroke.get("id")
        candidates = lows_by_path.get(source_path, [])
        if not candidates:
            continue
        points = sorted(
            (point for point in stroke.get("points", []) if isinstance(point, dict)),
            key=_point_order,
        )
        positions = {_point_order(point): position for position, point in enumerate(points)}
        for low in candidates:
            position = positions.get(_point_order(low))
            if position is None or position + 1 >= len(points):
                continue
            high = points[position + 1]
            if not _complete_reference(high, kind="H"):
                continue
            try:
                if high["value"] <= low["value"]:
                    continue
            except (KeyError, TypeError):
                continue

            known_at = max(low["available_at"], high["available_at"])
            reference = _reference(high)
            reference.update(
                id=(
                    f'level{trend_level}-post-alternation-bull-high-'
                    f'{low["index"]}-{low.get("ordinal", 0)}-'
                    f'{high["index"]}-{high.get("ordinal", 0)}-{known_at}'
                ),
                label=high.get("label", "H·多头段"),
                trend_level=trend_level,
                source_level=trend_level,
                source_path=source_path,
                source_available_at=high["available_at"],
                available_at=known_at,
                flip="空多交替后首段多头高点",
                confirmation_rule="first_confirmed_same_level_high_after_bear_bull_alternation",
                confirmed_alternation_low=_reference(low),
                confirmed_flip_high=low["confirmed_flip_high"],
                confirmed_bear_low=low["confirmed_bear_low"],
                broken_key=low["broken_key"],
                retracement_origin=low["retracement_origin"],
                retracement_ratio=low["retracement_ratio"],
            )
            landmarks.append(reference)
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
    )


def bullish_turn_signals(strokes, bars, *, trend_level):
    """Return the first strict close breakout after each confirmed alternation.

    The corresponding bear-to-bull high is frozen by Core evidence.  A signal
    exists only when a later session moves from a previous close at or below
    that price to a close strictly above it.  Intraday highs, equality and
    crosses occurring before the alternation became knowable do not qualify.
    """
    alternation_lows = bear_bull_alternation_lows(strokes, trend_level=trend_level)
    market = [(_bar_date(bar), index, bar) for index, bar in enumerate(bars)]
    landmarks = []
    for low in alternation_lows:
        flip_high = low.get("confirmed_flip_high")
        if not _complete_reference(flip_high, kind="H"):
            continue
        try:
            breakout_level = flip_high["value"]
            candidate = next(
                (date, index, previous, current)
                for (date, index, current), (_, _, previous) in zip(market[1:], market)
                if date > low["available_at"]
                and previous.close <= breakout_level < current.close
            )
        except (KeyError, TypeError, StopIteration):
            continue

        date, index, previous, current = candidate
        landmarks.append(
            dict(
                id=(
                    f'level{trend_level}-bullish-turn-signal-'
                    f'{low["index"]}-{low.get("ordinal", 0)}-{index}-{date}'
                ),
                index=index,
                ordinal=0,
                time=date,
                kind="K",
                value=current.close,
                available_at=date,
                label=f"K{index + 1}·转多",
                trend_level=trend_level,
                source_level=trend_level,
                source_path=low["source_path"],
                source_available_at=date,
                flip="转多信号",
                confirmation_rule="first_strict_close_cross_above_flip_high_after_confirmed_alternation",
                breakout_level=breakout_level,
                previous_close=previous.close,
                open=current.open,
                high=current.high,
                low=current.low,
                close=current.close,
                confirmed_alternation_low=_reference(low),
                confirmed_flip_high=_reference(flip_high),
                confirmed_bear_low=low["confirmed_bear_low"],
                broken_key=low["broken_key"],
                retracement_origin=low["retracement_origin"],
                retracement_ratio=low["retracement_ratio"],
            )
        )
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item["available_at"], item["source_path"], item["id"]),
    )
