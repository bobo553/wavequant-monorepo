"""Confirm an already-qualified alternation by a strict traded-high breakout."""

from collections.abc import Sequence
from copy import deepcopy
from typing import Any

from ..models.model import Bar


def high_breakout_events(bars: Sequence[Bar], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace post-confirmation close waits; retain the original B qualification.

    A bar breaking both B low and A high is ambiguous with daily data. Invalidation
    wins, so the routine never invents the order of intraday extremes.
    """
    result = []
    for event in events:
        if event["event"] != "squeeze_alternation_confirmed":
            continue
        result.append(event)
        # Preserve an already-observed breakout while waiting for B's confirmation.
        earlier = next(
            (
                old
                for old in events
                if old["event"] == "squeeze_alternation_breakout"
                and old.get("attack") == event.get("attack")
                and old.get("a_high_index") == event.get("a_high_index")
                and old.get("b_low_index") == event.get("b_low_index")
                and old["bar_index"] == event["bar_index"]
            ),
            None,
        )
        if earlier is not None and earlier.get("breakout_index", event["bar_index"]) < event["bar_index"]:
            index = earlier["breakout_index"]
            result.append(
                dict(earlier, breakout_basis="high_after_confirmed_alternation", breakout_price=bars[index].high)
            )
            continue
        for i in range(event["bar_index"], len(bars)):
            if bars[i].low < event["b_low_price"]:
                result.append(dict(event, event="squeeze_alternation_invalidated", bar_index=i))
                break
            if bars[i].high > event["a_high_price"]:
                result.append(
                    dict(
                        event,
                        event="squeeze_alternation_breakout",
                        bar_index=i,
                        breakout_index=i,
                        breakout_basis="high_after_confirmed_alternation",
                        breakout_price=bars[i].high,
                    )
                )
                break
    return sorted(result, key=lambda event: event["bar_index"])


def promote_alternation_segments(
    geometry: dict[str, Any],
    bars: Sequence[Bar],
    events: Sequence[dict[str, Any]],
    level: int,
) -> dict[str, Any]:
    """Publish formal A-high/B-low segments from the same evidence used by strategy.

    The breakout K confirms B, but is not itself a confirmed swing high. Keep
    subsequent development separate rather than making that live high formal.
    """
    result = deepcopy(geometry)
    added = []
    merged = 0
    for event in events:
        if (
            event["event"] != "squeeze_alternation_breakout"
            or event.get("trend_level") != level
            or event.get("breakout_basis") != "high_after_confirmed_alternation"
        ):
            continue
        known = bars[event["bar_index"]].timestamp.date().isoformat()
        points = []
        for kind, index, value in [
            ("H", event["a_high_index"], event["a_high_price"]),
            ("L", event["b_low_index"], event["b_low_price"]),
        ]:
            points.append(
                dict(
                    index=index,
                    ordinal=0,
                    time=bars[index].timestamp.date().isoformat(),
                    value=value,
                    kind=kind,
                    label=kind,
                    state="confirmed",
                    display_only=False,
                    available_at=known,
                    trend_level=level,
                    flip="多头确认" if kind == "L" else "空多交替高点确认",
                    reversal="正反转" if kind == "L" else "负反转",
                    confirmation_rule="confirmed_alternation_then_high_breakout",
                    confirmed_by=dict(
                        index=event["bar_index"],
                        time=known,
                        kind="K",
                        value=bars[event["bar_index"]].high,
                        available_at=known,
                    ),
                    levels=[
                        dict(name="空翻多高点", price=event["a_high_price"]),
                        dict(name="空多交替低点", price=event["b_low_price"]),
                    ],
                    observations=[],
                    wave_direction_after="up" if kind == "L" else "down",
                )
            )
        name = "tertiary" if level == 3 else "secondary"
        # A separately rendered segment must not span an already confirmed
        # path. When it fits inside one formal leg, splice its actual extrema
        # into that leg so there is one ordered line for the next level.
        handled = False
        if level == 2:
            for stroke in result.get("strokes", []):
                original = stroke["points"]
                if (
                    not original
                    or points[-1]["index"] < original[0]["index"]
                    or points[0]["index"] > original[-1]["index"]
                ):
                    continue
                handled = True
                for position, (left, right) in enumerate(zip(original, original[1:])):
                    if not (left["index"] <= points[0]["index"] < points[1]["index"] <= right["index"]):
                        continue
                    joined_known = max(known, left["available_at"])
                    inserts = [
                        dict(p, available_at=joined_known)
                        for p in points
                        if p["index"] not in (left["index"], right["index"])
                    ]
                    sequence = [left, *inserts, right]
                    if (
                        inserts
                        and joined_known <= right["available_at"]
                        and all(
                            a["kind"] != b["kind"]
                            and (a["value"] < b["value"] if a["kind"] == "L" else a["value"] > b["value"])
                            for a, b in zip(sequence, sequence[1:])
                        )
                    ):
                        stroke["points"] = [*original[: position + 1], *inserts, *original[position + 1 :]]
                        merged += 1
                    break
                break
        if handled:
            continue
        added.append(
            dict(
                id=f"{name}-alternation-{event['a_high_index']}-{event['b_low_index']}",
                kind=name,
                trend_level=level,
                source_path=event["source_path"],
                points=points,
                available_at=known,
                confirmation_evidence=dict(event),
            )
        )
        # The now-formal edge must not remain under a duplicate developing edge.
        for stroke in result.get("developing_strokes", []):
            tail = stroke["points"]
            if tail and tail[0]["index"] <= event["a_high_index"] and tail[-1]["index"] >= event["b_low_index"]:
                stroke["points"] = [
                    dict(points[-1], display_only=True, development_role="formal_start"),
                    *[p for p in tail if p["index"] > event["b_low_index"]],
                ]
    result["strokes"] = [*result.get("strokes", []), *added]
    result["developing_strokes"] = [s for s in result.get("developing_strokes", []) if len(s["points"]) >= 2]
    result["confirmed_alternation_segment_count"] = len(added) + merged
    if added or merged:
        result["confirmed_wave_count"] = len(
            {(p["index"], p.get("ordinal", 0), p["kind"]) for s in result["strokes"] for p in s["points"]}
        )
        result["scope"] = "shared_strategy_and_chart_confirmed_alternation"
        result["note"] = (
            result.get("note", "") + " 已确认空多交替后最高价严格突破空翻多高点，"
            "将该高点至交替低点确认为同级正式趋势段；突破K不作为已确认波段高点。"
        )
    result["developing_wave_count"] = len(result["developing_strokes"])
    result["developing_point_count"] = sum(len(s["points"]) for s in result["developing_strokes"])
    return result
