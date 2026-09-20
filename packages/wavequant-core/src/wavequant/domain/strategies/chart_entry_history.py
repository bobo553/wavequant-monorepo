"""Replay entry contexts through the same causal hierarchy used by charts."""

from dataclasses import asdict, replace
from collections.abc import Sequence

from ..market_structure.lecture_drawing import lecture_drawing
from ..market_structure.lecture_trend import reversal_trends
from ..market_structure.secondary_trend import secondary_trends, _bar_date
from ..market_structure.tertiary_trend import tertiary_trends
from ..models.model import Bar
from .hierarchical_entry import EntryContext


def chart_entry_history(bars: Sequence[Bar], *, audit=()) -> tuple[dict[int, tuple[EntryContext, ...]], list[dict[str, object]]]:
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
    levels = ()

    def accept(i, epoch, raw):
        nonlocal previous_epoch, previous_raw, signature, landmarks, active, invalidated, levels
        if previous_epoch is not None and epoch != previous_epoch and len(previous_raw) > 1:
            closed.append(dict(id=f"lecture-{previous_epoch}", points=previous_raw))
        previous_epoch, previous_raw = epoch, raw
        # Extending the final developing vertex cannot change a confirmed turn.
        current_signature = (
            epoch,
            tuple(
                (p["index"], p["ordinal"], p["kind"], p["value"], p["state"], p["available_at"])
                for p in raw
                if p["state"] != "developing"
            ),
        )
        if current_signature != signature:
            signature = current_signature
            drawing = dict(strokes=[*closed, *([dict(id=f"lecture-{epoch}", points=raw)] if len(raw) > 1 else [])])
            prefix = bars[: i + 1]
            first = reversal_trends(drawing, prefix)
            second = secondary_trends(first, prefix)
            third = tertiary_trends(second, prefix)
            levels = ((second, first), (third, second))
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
            if ctx.maturity_index is None and i > known and bars[i].close > ctx.flip_high_price:
                ctx = replace(ctx, maturity_index=i)
                events.append(dict(bar_index=i, event="hierarchy_bull_matured", **asdict(ctx)))
            current[identity] = ctx
        # A disappeared or revised chain cannot later revive an old N attack.
        retired.update(set(active) - set(current))
        active = current
        history[i] = tuple(current.values())

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
    active, claimed, merged = {}, set(), {}
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
