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


def _active_bear_to_bull_highs(strokes, landmarks):
    """Drop a bull-flip high after its confirmed low is strictly broken.

    A later same-level low below the low that preceded ``翻空为多`` proves the
    attempted bullish transition failed.  The old high then belongs to the
    renewed bearish segment as its last-fall-high evidence, so publishing it as
    an active bull-flip landmark would describe both regimes at once.  Equal
    lows are retests and remain valid.  Because callers provide one causal
    prefix, replay still shows the landmark until the breaking low is confirmed.
    """
    points_by_path = {
        stroke.get("id"): [point for point in stroke.get("points", []) if isinstance(point, dict)]
        for stroke in strokes
        if isinstance(stroke, dict)
    }
    active = []
    for landmark in landmarks:
        confirmed_low = landmark.get("confirmed_low")
        if not _complete_reference(confirmed_low, kind="L"):
            continue
        try:
            path_points = points_by_path.get(landmark.get("source_path"), [])
            current_high = next(
                (point for point in path_points if _point_order(point) == _point_order(landmark)),
                None,
            )
            confirming_low = current_high.get("confirmed_by") if isinstance(current_high, dict) else None
            converted_to_last_fall_high = (
                isinstance(current_high, dict)
                and current_high.get("flip") == "翻多为空"
                and _complete_reference(confirming_low, kind="L")
                and confirming_low["value"] < confirmed_low["value"]
            )
            invalidated = converted_to_last_fall_high or any(
                point.get("kind") == "L"
                and _point_order(point) > _point_order(landmark)
                and point["value"] < confirmed_low["value"]
                for point in path_points
            )
        except (KeyError, TypeError):
            continue
        if not invalidated:
            active.append(landmark)
    return active


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


def _observed_bear_to_bull_highs(strokes, *, trend_level):
    """Read same-level key-break observations produced by ``_annotate``.

    Every hierarchy is annotated only after its formal points are known.  The
    explicit ``翻空为多`` observation on a high therefore owns the correct
    *same-level* frozen last-fall-high and bearish low.  A higher-level low's
    aggregation metadata instead describes which lower-level key confirmed
    that low; treating that lower-level proof as the higher-level flip can
    publish an earlier, incorrect high.
    """
    landmarks = []
    for stroke in strokes:
        points = stroke.get("points", [])
        # A reduced H -> L -> H already carries a confirmed bearish-wave
        # reversal on L. Waiting for two formal lows loses an entire first
        # higher-level alternation on short chart prefixes.
        inferred = {}
        if trend_level > 1:
            for key, low, high in zip(points, points[1:], points[2:]):
                if (low.get("kind") == "L" and low.get("flip") == "翻空为多"
                        and _strictly_breaks_last_fall_high(high, key)
                        and _point_order(key) < _point_order(low) < _point_order(high)):
                    inferred[_point_order(high)] = dict(
                        title="翻空为多", key=key, confirmed_low=low,
                        available_at=max(key["available_at"], low["available_at"], high["available_at"]))
        for high in stroke.get("points", []):
            if high.get("kind") != "H":
                continue
            observations = high.get("observations", [])
            if not any(event.get("title") == "翻空为多" for event in observations):
                observations = [*observations, *([inferred[_point_order(high)]] if _point_order(high) in inferred else [])]
            for event in observations:
                if event.get("title") != "翻空为多":
                    continue
                broken_key = event.get("key")
                low = event.get("confirmed_low")
                if not isinstance(low, dict) or low.get("kind") != "L":
                    continue
                if not _strictly_breaks_last_fall_high(high, broken_key):
                    continue
                known_at = max(
                    high["available_at"],
                    event.get("available_at", high["available_at"]),
                    low.get("available_at", high["available_at"]),
                    broken_key.get("available_at", high["available_at"]),
                )
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
    return landmarks


def bear_to_bull_highs(strokes, *, trend_level):
    """Return causal high landmarks for formal bear-to-bull transitions.

    A formal low represents the extreme of the bearish wave. It qualifies only
    when its metadata proves a ``down -> up`` switch (or carries the equivalent
    Chinese flip label). The landmark is the source high that confirmed that
    low, while ``available_at`` remains the later date when the formal low and
    its transition proof were both knowable.
    """
    observed = _observed_bear_to_bull_highs(strokes, trend_level=trend_level)
    if trend_level == 1:
        landmarks = _active_bear_to_bull_highs(strokes, observed)
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
    # Before a path has enough formal same-level context, the hierarchical
    # reducer can still expose a useful provisional flip from the low's
    # lower-level confirmation chain.  Preserve that backwards-compatible
    # evidence only when no explicit same-level observation owns the same
    # bearish low.  Once such an observation exists it is authoritative: for
    # example, a level-3 low may be confirmed by a level-2 high months before a
    # later level-3 high actually breaks the frozen level-3 last-fall-high.
    observed_lows = {
        (item["source_path"], _point_order(item.get("confirmed_low")))
        for item in observed
    }
    landmarks = [
        item
        for item in landmarks
        if (item["source_path"], _point_order(item.get("confirmed_low"))) not in observed_lows
    ]
    landmarks.extend(observed)
    return sorted(
        _active_bear_to_bull_highs(strokes, landmarks),
        key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
    )


def bear_bull_alternation_lows(strokes, *, trend_level, source_strokes=()):
    """Return same-level lows that formally completed bear/bull alternation.

    Read explicit ``空多交替`` observations or the confirmed source pullback
    attached to a formal higher-level flip high. A deeper *higher* pullback
    also qualifies when its own confirmed next high strictly re-breaks that
    flip high. Both routes require the original frozen last-fall-high break;
    ``available_at`` is the latest confirmation date in the selected chain.
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
    # The source low that confirmed a formal flip high is itself confirmed.
    # It can complete the higher-level pullback without waiting for a later
    # key break to promote that low to a formal higher-level polyline vertex.
    # Keep that source level explicit; never append it to the formal strokes.
    if trend_level > 1:
        by_path = {stroke["id"]: stroke for stroke in strokes}
        existing = {(item["source_path"], _point_order(item["confirmed_flip_high"])) for item in landmarks}
        for high in _observed_bear_to_bull_highs(strokes, trend_level=trend_level):
            stroke = by_path[high["source_path"]]
            formal_high = next(p for p in stroke["points"] if _point_order(p) == _point_order(high))
            low = formal_high.get("confirmed_by")
            origin = high["confirmed_low"]
            if ((stroke["id"], _point_order(high)) in existing
                    or not _complete_reference(low, kind="L")
                    or _point_order(low) <= _point_order(high)):
                continue
            amplitude = high["value"] - origin["value"]
            if amplitude <= 0:
                continue
            event = dict(title="空多交替", ratio=(high["value"] - low["value"]) / amplitude,
                         flip_high=high, confirmed_bear_low=origin, broken_key=high["broken_key"], origin=origin)
            if not _valid_alternation(low, event):
                continue
            known_at = max(high["available_at"], low["available_at"])
            landmarks.append(dict(
                _reference(low),
                id=f'level{trend_level}-bear-bull-alternation-low-{high["index"]}-{high.get("ordinal", 0)}-'
                   f'{low["index"]}-{low.get("ordinal", 0)}-{known_at}',
                trend_level=trend_level, source_level=trend_level-1, source_path=stroke["id"],
                source_available_at=low["available_at"], available_at=known_at, flip="空多交替",
                confirmation_rule="confirmed_source_pullback_after_same_level_flip",
                retracement_ratio=event["ratio"], weak_countermove=event["ratio"] < 1/3,
                confirmed_flip_high=_reference(high), confirmed_bear_low=_reference(origin),
                broken_key=_reference(high["broken_key"]), retracement_origin=_reference(origin)))
    # A confirmed pullback may exceed 2/3 yet still establish a higher low
    # before its next confirmed high re-breaks the original flip high. For
    # levels 2/3 the pullback is often still a source-level vertex, so follow
    # the reducer's source_path instead of promoting it into a formal stroke.
    source_by_path = {stroke.get("id"): stroke for stroke in source_strokes}
    existing = {(item["source_path"], _point_order(item["confirmed_flip_high"])) for item in landmarks}
    for high in bear_to_bull_highs(strokes, trend_level=trend_level):
        path = high["source_path"]
        if (path, _point_order(high)) in existing:
            continue
        stroke = next((item for item in strokes if item.get("id") == path), None)
        if stroke is None:
            continue
        points = stroke.get("points", [])
        formal_high = next((point for point in points if _point_order(point) == _point_order(high)), None)
        if formal_high is None:
            continue
        if trend_level == 1:
            # The immediate next same-level L owns its HH/HL confirmation.
            position = points.index(formal_high)
            low = points[position + 1] if position + 1 < len(points) else None
            if not _complete_reference(low, kind="L"):
                continue
            source_level = 1
        else:
            # The formal H carries only a reference to the confirming source L.
            # Resolve that exact source vertex to inspect its own confirmation
            # high; matching coordinates prevents cross-path or stale reuse.
            low_ref = formal_high.get("confirmed_by")
            source = source_by_path.get(stroke.get("source_path"), {})
            low = next(
                (
                    point for point in source.get("points", [])
                    if _point_order(point) == _point_order(low_ref)
                    and point.get("kind") == "L"
                    and point.get("value") == low_ref.get("value")
                    and point.get("time") == low_ref.get("time")
                ),
                None,
            ) if _complete_reference(low_ref, kind="L") else None
            source_level = trend_level - 1
        rebreak = _confirmation_high(low.get("confirmed_by")) if isinstance(low, dict) else None
        origin = high.get("confirmed_low")
        if (
            not _complete_reference(low, kind="L")
            or not _complete_reference(origin, kind="L")
            or not _complete_reference(rebreak, kind="H")
            or not _strictly_breaks_last_fall_high(high, high.get("broken_key"))
            or not _strictly_breaks_last_fall_high(rebreak, high)
            or not _point_order(high) < _point_order(low) < _point_order(rebreak)
        ):
            continue
        amplitude = high["value"] - origin["value"]
        if amplitude <= 0 or not origin["value"] < low["value"] < high["value"]:
            continue
        ratio = (high["value"] - low["value"]) / amplitude
        if ratio <= 0 or ratio >= 1:
            continue
        known_at = max(high["available_at"], low["available_at"], rebreak["available_at"])
        landmarks.append(dict(
            _reference(low),
            id=f'level{trend_level}-bear-bull-alternation-low-{high["index"]}-{high.get("ordinal", 0)}-'
               f'{low["index"]}-{low.get("ordinal", 0)}-{known_at}',
            trend_level=trend_level, source_level=source_level, source_path=path,
            source_available_at=low["available_at"], available_at=known_at, flip="空多交替",
            confirmation_rule="confirmed_higher_pullback_then_confirmed_flip_high_rebreak",
            retracement_ratio=ratio, weak_countermove=ratio < 1/3,
            confirmed_flip_high=_reference(high), confirmed_bear_low=_reference(origin),
            broken_key=_reference(high["broken_key"]), retracement_origin=_reference(origin),
            confirmed_rebreak_high=_reference(rebreak)))
        existing.add((path, _point_order(high)))
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item.get("ordinal", 0), item["available_at"], item["source_path"]),
    )


def post_alternation_bull_highs(strokes, *, trend_level, source_strokes=()):
    """Return the confirmed high ending the first bull leg after alternation.

    One confirmed ``空多交替`` low changes the same-level background to bull.
    The immediately following same-level vertex is therefore the endpoint of
    that first bullish ``L -> H`` leg.  It qualifies only when the next vertex
    is a complete confirmed high whose price is strictly above the low.  The
    function never skips an invalid next vertex to select a later convenient
    high and never searches raw bars or a consumer's current chart window.
    """
    alternation_lows = bear_bull_alternation_lows(strokes, trend_level=trend_level, source_strokes=source_strokes)
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


def bullish_turn_signals(strokes, bars, *, trend_level, source_strokes=()):
    """Return the first causal strict-close re-break of each bull-flip high.

    A confirmed same-level alternation remains the preferred starting point.
    Some formal higher-level highs, however, become knowable from a confirmed
    lower-level pullback that has not completed either alternation proof route.
    The already-confirmed ``翻空为多`` high is still real evidence, so its first
    later close cross is published without inventing an alternation low.
    Intraday highs, equality and crosses before the selected evidence became
    knowable never qualify.
    """
    alternation_lows = bear_bull_alternation_lows(strokes, trend_level=trend_level, source_strokes=source_strokes)
    flip_highs = bear_to_bull_highs(strokes, trend_level=trend_level)
    alternated_highs = {
        (low["source_path"], _point_order(low.get("confirmed_flip_high")))
        for low in alternation_lows
    }
    contexts = [
        {
            "anchor": low,
            "flip_high": low.get("confirmed_flip_high"),
            "known_at": low["available_at"],
            "alternation_low": low,
        }
        for low in alternation_lows
    ]
    contexts.extend(
        {
            "anchor": high,
            "flip_high": high,
            "known_at": high["available_at"],
            "alternation_low": None,
        }
        for high in flip_highs
        if (high["source_path"], _point_order(high)) not in alternated_highs
    )
    market = [(_bar_date(bar), index, bar) for index, bar in enumerate(bars)]
    landmarks = []
    for context in contexts:
        anchor = context["anchor"]
        flip_high = context["flip_high"]
        alternation_low = context["alternation_low"]
        if not _complete_reference(flip_high, kind="H"):
            continue
        try:
            breakout_level = flip_high["value"]
            candidate = next(
                (date, index, previous, current)
                for (date, index, current), (_, _, previous) in zip(market[1:], market)
                if date > context["known_at"]
                and previous.close <= breakout_level < current.close
            )
        except (KeyError, TypeError, StopIteration):
            continue

        date, index, previous, current = candidate
        landmarks.append(
            dict(
                id=(
                    f'level{trend_level}-bullish-turn-signal-'
                    f'{anchor["index"]}-{anchor.get("ordinal", 0)}-{index}-{date}'
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
                source_path=anchor["source_path"],
                source_available_at=date,
                flip="转多信号",
                confirmation_rule=(
                    "first_strict_close_cross_above_flip_high_after_confirmed_alternation"
                    if alternation_low is not None
                    else "first_strict_close_cross_above_confirmed_flip_high"
                ),
                breakout_level=breakout_level,
                previous_close=previous.close,
                open=current.open,
                high=current.high,
                low=current.low,
                close=current.close,
                confirmed_alternation_low=_reference(alternation_low) if alternation_low is not None else None,
                confirmed_flip_high=_reference(flip_high),
                confirmed_bear_low=(
                    alternation_low["confirmed_bear_low"]
                    if alternation_low is not None
                    else flip_high["confirmed_low"]
                ),
                broken_key=(
                    alternation_low["broken_key"]
                    if alternation_low is not None
                    else flip_high["broken_key"]
                ),
                retracement_origin=(
                    alternation_low["retracement_origin"] if alternation_low is not None else None
                ),
                retracement_ratio=(
                    alternation_low["retracement_ratio"] if alternation_low is not None else None
                ),
            )
        )
    return sorted(
        landmarks,
        key=lambda item: (item["index"], item["available_at"], item["source_path"], item["id"]),
    )
