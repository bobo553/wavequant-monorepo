"""Replay entry contexts through the same causal hierarchy used by charts."""

from dataclasses import asdict, replace
from collections.abc import Mapping, Sequence
from copy import deepcopy
from math import isfinite
from typing import cast

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.lecture_trend import reversal_trends
from ..market_structure.secondary_trend import secondary_trends, _bar_date
from ..market_structure.tertiary_trend import tertiary_trends
from ..models.model import Bar
from .hierarchical_entry import EntryContext


_CONFIRMATION_CACHE_VERSION = "daily_trend_confirmation_descent_pressure_v3"


def _confirmed_descent_pressures(
    level: Mapping[str, object], dates: Mapping[str, int], asof_index: int,
) -> list[dict[str, object]]:
    """Retain confirmed downward pressures without publishing an upward trend.

    A higher-level rise can fail its publication gate while its following
    downward reversal is still a confirmed structural fact. Only that level's
    dated descent highs are eligible; raw lower-level highs are not pressures.
    """
    paths = level.get("structure_strokes", level.get("strokes", ()))
    if not isinstance(paths, (list, tuple)):
        return []
    pressures: list[dict[str, object]] = []
    for raw_path in paths:
        if not isinstance(raw_path, Mapping):
            continue
        path = cast(Mapping[str, object], raw_path)
        if path.get("display_only"):
            continue
        vertices = path.get("points")
        if not isinstance(vertices, (list, tuple)):
            continue
        for raw_point in vertices:
            if not isinstance(raw_point, Mapping):
                continue
            point = cast(Mapping[str, object], raw_point)
            if (point.get("kind") != "H" or point.get("wave_direction_after") != "down"
                    or point.get("state") not in ("confirmed", "reversal", "teaching")
                    or point.get("display_only")):
                continue
            if point.get("trend_level", 2) != 2:
                continue
            index, value, known = point.get("index"), point.get("value"), point.get("available_at")
            if type(index) is not int or not 0 <= index <= asof_index:
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value) or value <= 0:
                continue
            known_index = dates.get(known) if isinstance(known, str) else known if type(known) is int else None
            if known_index is None or not index <= known_index <= asof_index:
                continue
            pressure: dict[str, object] = dict(index=index, value=value, source="confirmed_descent",
                                               available_at=known, known_index=known_index)
            for field in ("confirmation_rule", "broken_key", "confirmed_by"):
                if field in point:
                    pressure[field] = point[field]
            pressures.append(pressure)
    return pressures


def _has_confirmation_price_cross(
    raw: Sequence[object], levels: Sequence[tuple[Mapping[str, object], Mapping[str, object]]],
    previous: Bar, current: Bar, asof: str, asof_index: int,
) -> bool:
    """Refresh price certificates even when no confirmed source vertex changed.

    A live candle may complete a source close re-cross or a same-level extreme
    break. Candidate skeletons retain pressures absent from public strokes;
    raw points also retain pressures from the base level. Unknown evidence and
    developing endpoints never manufacture a refresh threshold.
    """
    points: list[object] = list(raw)
    for pair in levels:
        for level in pair:
            for field in ("strokes", "candidate_strokes"):
                paths = level.get(field)
                if not isinstance(paths, (list, tuple)):
                    continue
                for raw_path in paths:
                    if not isinstance(raw_path, Mapping):
                        continue
                    path = cast(Mapping[str, object], raw_path)
                    vertices = path.get("points")
                    if isinstance(vertices, (list, tuple)):
                        points.extend(vertices)
    for raw_point in points:
        if not isinstance(raw_point, Mapping):
            continue
        point = cast(Mapping[str, object], raw_point)
        if point.get("state") in ("seed", "developing") or point.get("display_only"):
            continue
        value, known = point.get("value"), point.get("available_at")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        if isinstance(known, str):
            if known > asof:
                continue
            newly_known = known == asof
            newly_eligible = known == _bar_date(previous)
        elif type(known) is int:
            if known > asof_index:
                continue
            newly_known = known == asof_index
            newly_eligible = known == asof_index - 1
        else:
            continue
        if newly_known or newly_eligible:
            return True
        if point.get("kind") == "H" and (previous.high <= value < current.high
                                         or previous.close <= value < current.close):
            return True
        if point.get("kind") == "L" and (previous.low >= value > current.low
                                         or previous.close >= value > current.close):
            return True
    return False


def _copy_replay_state(state):
    """Detach only containers the next callback extends or replaces.

    Dated entries, events, landmarks, and level reductions are read-only after
    publication. The drawing's live point list is the exception: its final
    developing endpoint may be replaced by the next candle.
    """
    (
        history, events, active, first_seen, retired, closed, previous_epoch,
        previous_raw, signature, landmarks, invalidated, levels,
        shallow_candidate, resistance_key, anchors_at_attack, prior_shallow, prior_combined, prior_pullback,
    ) = state
    return (
        history.copy(), events.copy(), active.copy(), first_seen.copy(),
        retired.copy(), closed.copy(), previous_epoch, deepcopy(previous_raw),
        signature, landmarks.copy(), invalidated.copy(), levels,
        shallow_candidate, resistance_key, anchors_at_attack.copy(),
        prior_shallow.copy(), prior_combined.copy(), prior_pullback.copy(),
    )


def chart_entry_history(
    bars: Sequence[Bar], *, audit=(), shallow_candidate_sink=None, combined_candidate_sink=None,
    secondary_pullback_sink=None, prefix_cache=None
) -> tuple[dict[int, tuple[EntryContext, ...]], list[dict[str, object]]]:
    """Share confirmed landmarks, including cross-path continuity, with trading.

    Only a prefix is reduced. A source-confirmed pullback is entry evidence,
    never an invented formal higher-level vertex. Its first observed date wins
    over an earlier price date, even when a later prefix joins drawing paths.
    """
    dates = {_bar_date(bar): i for i, bar in enumerate(bars)}
    history = {}
    events = []
    active = {}
    first_seen = {}
    retired = set()
    closed = []
    previous_epoch = None
    previous_raw = []
    signature = None
    landmarks = []
    invalidated = set()
    from ..market_structure.squeeze_alternation import squeeze_anchors, squeeze_alternations
    attacks = {e["bar_index"] for e in audit if e["event"] == "n_completed" and e.get("direction") == "up"}
    anchors_at_attack = {}
    levels: tuple[tuple[dict, dict], ...] = ()
    shallow_candidate = None
    resistance_key = None
    last = len(bars) - 1
    # Intraday replays change only the unfinished last candle. All earlier
    # drawing steps (and their expensive hierarchy reductions) are identical.
    # Include the earlier attack dates because they control stored anchors.
    prefix_key = (
        tuple(bars[:-1]),
        frozenset(i for i in attacks if i < last),
        shallow_candidate_sink is not None,
        combined_candidate_sink is not None,
        secondary_pullback_sink is not None,
        _CONFIRMATION_CACHE_VERSION,
    )
    cached_key = prefix_cache.get("key") if prefix_cache is not None else None
    if cached_key == prefix_key:
        resume_start = last
    elif (
        cached_key is not None
        and len(cached_key) == len(prefix_key)
        and cached_key[0] == tuple(bars[:-2])
        and cached_key[1] == frozenset(i for i in attacks if i < last - 1)
        and cached_key[2] == (shallow_candidate_sink is not None)
        and cached_key[3] == (combined_candidate_sink is not None)
        and cached_key[4] == (secondary_pullback_sink is not None)
        and cached_key[5] == _CONFIRMATION_CACHE_VERSION
    ):
        # Yesterday was unfinished when the prior checkpoint was saved. Replay
        # its final candle, then today's partial candle, from the older state.
        resume_start = last - 1
    else:
        resume_start = 0
    if resume_start:
        (
            history, events, active, first_seen, retired, closed, previous_epoch,
            previous_raw, signature, landmarks, invalidated, levels,
            shallow_candidate, resistance_key, anchors_at_attack, prior_shallow, prior_combined, prior_pullback,
        ) = _copy_replay_state(prefix_cache["checkpoint"])
        if shallow_candidate_sink is not None:
            shallow_candidate_sink.update(prior_shallow)
        if combined_candidate_sink is not None:
            combined_candidate_sink.update(prior_combined)
        if secondary_pullback_sink is not None:
            secondary_pullback_sink.update(prior_pullback)

    def accept(i, epoch, raw):
        nonlocal previous_epoch, previous_raw, signature, landmarks, active, invalidated, levels, resistance_key, shallow_candidate
        if i < resume_start:
            return
        if previous_epoch is not None and epoch != previous_epoch and len(previous_raw) > 1:
            closed.append(dict(id=f"lecture-{previous_epoch}", points=previous_raw))
        previous_epoch, previous_raw = epoch, raw
        # Source vertices determine the skeleton; market prices can separately
        # publish a previously unqualified trend on an otherwise unchanged day.
        current_signature = (
            epoch,
            tuple(
                (p["index"], p["ordinal"], p["kind"], p["value"], p["state"], p["available_at"])
                for p in raw
                if p["state"] != "developing"
            ),
        )
        market_refresh = current_signature == signature and i > 0 and _has_confirmation_price_cross(
            [*(point for path in closed for point in path["points"]), *raw],
            levels, bars[i - 1], bars[i], _bar_date(bars[i]), i)
        if current_signature != signature or market_refresh:
            signature = current_signature
            drawing = dict(strokes=[*closed, *([dict(id=f"lecture-{epoch}", points=raw)] if len(raw) > 1 else [])])
            prefix = bars[: i + 1]
            first = reversal_trends(drawing, prefix)
            second = secondary_trends(first, prefix)
            third = tertiary_trends(second, prefix)
            levels = ((second, first), (third, second))
            if shallow_candidate_sink is not None:
                from .shallow_base_breakout import shallow_candidate_from_geometry
                shallow_candidate = shallow_candidate_from_geometry(levels, dates, i)
            # The source-confirmed A high is already tradable evidence before
            # it is promoted to a formal vertex of its own level.
            candidates = [dict(index=p['index'], value=p['value'], source='formal')
                          for stroke in second['strokes'] for p in stroke['points']
                          if p['kind'] == 'H' and p.get('state') != 'developing']
            candidates += _confirmed_descent_pressures(second, dates, i)
            candidates += [dict(index=a['high']['index'], value=a['high']['value'], source='confirmed_source')
                           for a in squeeze_anchors(second, dates, first) if a['known_index'] <= i]
            key = max(candidates, key=lambda p: (p['index'], p['value']), default=None)
            resistance_identity = (key['index'], key['value']) if key else None
            if resistance_identity != resistance_key:
                events.append(dict(bar_index=i, event='hierarchy_resistance_key', trend_level=2, key=key))
                resistance_key = resistance_identity
            landmarks = [item for level in (first, second, third) for item in level["bear_bull_alternation_lows"]]
            invalidated = set()
            for level in (first, second, third):
                paths = {stroke["id"]: stroke["points"] for stroke in level["strokes"]}
                for low in level["bear_bull_alternation_lows"]:
                    if any(
                        (p["index"], p["ordinal"]) > (low["index"], low.get("ordinal", 0))
                        and any(event["title"] == "翻多为空" for event in p.get("observations", []))
                        for p in paths[low["source_path"]]
                    ):
                        invalidated.add(low["id"])
        if i in attacks:
            anchors_at_attack[i] = [anchor for level, source in levels for anchor in squeeze_anchors(level, dates, source)]
        if shallow_candidate_sink is not None:
            shallow_candidate_sink[i] = shallow_candidate
        if combined_candidate_sink is not None and levels:
            from .combined_a_entry import GeometryLevel, combined_a_contexts_from_geometry
            drawing = dict(strokes=[*closed, *([dict(id=f"lecture-{epoch}", points=raw)] if len(raw) > 1 else [])])
            second, first = levels[0]
            third, _ = levels[1]
            combined_candidate_sink[i] = combined_a_contexts_from_geometry(
                bars, cast(GeometryLevel, drawing), cast(GeometryLevel, first),
                cast(GeometryLevel, second), cast(GeometryLevel, third), dates, i, audit)
        if secondary_pullback_sink is not None and levels:
            from .secondary_pullback_entry import secondary_pullback_candidates
            drawing = dict(strokes=[*closed, *([dict(id=f"lecture-{epoch}", points=raw)] if len(raw) > 1 else [])])
            secondary_pullback_sink[i] = secondary_pullback_candidates(
                bars, drawing, levels[0][0], dates, i, first=levels[0][1])
        current = {}
        for low in landmarks:
            high = low["confirmed_flip_high"]
            origin = low["confirmed_bear_low"]
            key = low["broken_key"]
            identity = (
                low["trend_level"],
                origin["index"],
                high["index"],
                low["index"],
                key["index"],
                high["available_at"],
                low["available_at"],
            )
            if identity in retired or low["id"] in invalidated:
                if identity in active:
                    events.append(
                        dict(bar_index=i, event="hierarchy_context_invalidated", trend_level=low["trend_level"])
                    )
                retired.add(identity)
                continue
            known = first_seen.setdefault(identity, max(i, dates[low["available_at"]]))
            ctx = EntryContext(
                low["trend_level"],
                origin["index"],
                origin["index"],
                key["index"],
                key["value"],
                dates[high["available_at"]],
                high["index"],
                high["value"],
                origin["index"],
                origin["value"],
                known,
                low["index"],
                low["value"],
            )
            if min(bar.low for bar in bars[origin["index"] : i + 1]) < origin["value"]:
                retired.add(identity)
                if identity in active:
                    events.append(dict(bar_index=i, event="hierarchy_context_invalidated", trend_level=ctx.trend_level))
                continue
            if identity in active:
                ctx = replace(ctx, maturity_index=active[identity].maturity_index)
            else:
                events.append(dict(bar_index=i, event="hierarchy_alternation_ready", **asdict(ctx)))
            if ctx.maturity_index is None and i > known and bars[i].high > ctx.flip_high_price:
                ctx = replace(ctx, maturity_index=i)
                events.append(dict(bar_index=i, event="hierarchy_bull_matured", **asdict(ctx)))
            current[identity] = ctx
        # A disappeared or revised chain cannot later revive an old N attack.
        retired.update(set(active) - set(current))
        active = current
        history[i] = tuple(current.values())
        if prefix_cache is not None and resume_start != last and i == last - 1:
            prefix_cache["key"] = prefix_key
            prefix_cache["checkpoint"] = _copy_replay_state((
                history, events, active, first_seen, retired, closed, previous_epoch,
                previous_raw, signature, landmarks, invalidated, levels,
                shallow_candidate, resistance_key, anchors_at_attack,
                dict(shallow_candidate_sink or {}),
                dict(combined_candidate_sink or {}),
                dict(secondary_pullback_sink or {}),
            ))

    lecture_drawing(bars, on_step=accept)
    if audit:
        additional = squeeze_alternations(bars, audit, anchors_at_attack)
        history = merge_squeeze_history(bars, history, additional)
        events.extend(additional)
    return history, events


def merge_squeeze_history(bars, history, events):
    """Expose confirmed alternations to future N attacks, never to their own N."""
    from collections import defaultdict
    dated = defaultdict(list)
    for event in events:
        dated[event["bar_index"]].append(event)
    active: dict[tuple[int, int, int, int], EntryContext] = {}
    claimed, merged = set(), {}
    for i, bar in enumerate(bars):
        for event in dated[i]:
            identity = (event["trend_level"], event["a_origin_index"], event["a_high_index"], event["b_low_index"])
            if event["event"] == "squeeze_alternation_confirmed":
                claimed.add(identity)
                active = {key: ctx for key, ctx in active.items()
                          if key[:2] != identity[:2] or key[2] > identity[2]}
                active[identity] = EntryContext(
                    trend_level=event["trend_level"], epoch=event["a_origin_index"],
                    context_index=event["a_origin_index"], key_source_index=event["key_source_index"],
                    key_price=event["key_price"], flip_index=event["anchor_known_index"],
                    flip_high_index=event["a_high_index"], flip_high_price=event["a_high_price"],
                    origin_index=event["a_origin_index"], origin_price=event["a_origin_price"],
                    alternation_index=i, alternation_low_index=event["b_low_index"],
                    alternation_low_price=event["b_low_price"], confirmation_attack=event["attack"],
                )
            elif event["event"] == "squeeze_alternation_invalidated":
                active.pop(identity, None)
            elif event["event"] == "squeeze_alternation_breakout" and identity in active:
                active[identity] = replace(active[identity], maturity_index=i)
        active = {key: ctx for key, ctx in active.items() if bar.low >= ctx.origin_price}
        ordinary = [ctx for ctx in history[i] if
                    (ctx.trend_level, ctx.origin_index, ctx.flip_high_index, ctx.alternation_low_index) not in claimed]
        merged[i] = tuple([*ordinary, *active.values()])
    return merged
