"""Causal N/squeeze confirmation of a qualified higher B low.

The A endpoint may still be developing at its own level, but must already be
a confirmed source pivot following a formal bullish reversal. This does not
promote it into a formal trend vertex. Each attack receives its own prefix.
"""

from collections.abc import Sequence
from typing import TypedDict

from ..models.model import Bar
from ..strategies.attack_quality import squeeze_buy_rejection, squeeze_bullish_body
from .abc_candidate import AbcAnchor, tertiary_abc_observations


class _AnchorPoint(TypedDict):
    index: int
    value: float


class _SqueezeAnchor(TypedDict):
    origin: _AnchorPoint
    high: _AnchorPoint
    key: _AnchorPoint
    source_path: str
    known_index: int
    trend_level: int


def squeeze_anchors(level, dates, source_level):
    """Read only confirmed source endpoints; never use a live candle extreme."""
    anchors: dict[tuple[int, int], _SqueezeAnchor] = {}
    for high in level.get("bear_to_bull_highs", []):
        origin, key = high["confirmed_low"], high["broken_key"]
        anchors[(origin["index"], high["index"])] = dict(
            origin=origin,
            high=high,
            key=key,
            source_path=high["source_path"],
            known_index=dates[high["available_at"]],
            trend_level=level["trend_level"],
        )
    sources = {stroke["id"]: stroke for stroke in source_level.get("strokes", []) if not stroke.get("display_only")}
    for path in level.get("strokes", []):
        if not path.get("points") or path.get("display_only"):
            continue
        low = path["points"][-1]
        if low["kind"] != "L" or low.get("flip") != "翻空为多":
            continue
        key = low.get("preceding_turn")
        source = sources.get(path.get("source_path"), {})
        highs = [
            point
            for point in source.get("points", [])
            if point["kind"] == "H"
            and point["index"] > low["index"]
            and point.get("state") != "developing"
            and not point.get("display_only")
        ]
        if not highs or not key or key["kind"] != "H":
            continue
        high = max(highs, key=lambda point: (point["value"], -point["index"]))
        if not key["index"] < low["index"] < high["index"] or not high["value"] > key["value"] > low["value"]:
            continue
        known = max(dates[p["available_at"]] for p in (low, high, key))
        anchors.setdefault(
            (low["index"], high["index"]),
            dict(
                origin=low,
                high=high,
                key=key,
                source_path=path["id"],
                known_index=known,
                trend_level=level["trend_level"],
            ),
        )
    # A later source-confirmed high extends the same A; do not simultaneously
    # reuse its older interior high as another A for the very same B/N.
    latest: dict[int, _SqueezeAnchor] = {}
    for item in anchors.values():
        origin = item["origin"]["index"]
        if origin not in latest or item["high"]["index"] > latest[origin]["high"]["index"]:
            latest[origin] = item
    return list(latest.values())


def _volume_squeeze_close(bars: Sequence[Bar], attack: int, now: int) -> bool:
    """资格只使用当前收盘及原 N 自身的量价基准，不要求达到倍量。"""
    return (0 < attack < now < len(bars) and bars[now].close > bars[attack].close
            and squeeze_bullish_body(bars[now], bars[now - 1])
            and squeeze_buy_rejection(bars[now], bars[now - 1], bars[attack - 1]) is None)


def squeeze_alternations(bars, audit, anchors_at_attack):
    """Preserve dated confirmation, breakout and failure instead of repainting."""
    positive = {e["bar_index"]: e for e in audit if e["event"] == "n_completed" and e.get("direction") == "up"}
    volume_dates: dict[int, list[int]] = {}
    for event in audit:
        attack, now = event.get('attack'), event['bar_index']
        if (event['event'] == 'regime_confirmation' and event.get('regime') in ('轧空', '强轧空')
                and type(attack) is int and attack in positive and type(now) is int
                and _volume_squeeze_close(bars, attack, now)):
            volume_dates.setdefault(attack, []).append(now)
    observations, used = [], set()
    occupied: dict[tuple[int, int, int], int] = {}
    for trigger in sorted(audit, key=lambda e: e["bar_index"]):
        gap_confirmation = trigger["event"] == "n_consolidation_gap_confirmed"
        if gap_confirmation:
            # The independently verified defended-N gap can establish today's
            # qualified B. It must not depend on an earlier local bounce label.
            trigger = dict(trigger, event="regime_confirmation", regime="轧空")
        if trigger["event"] not in ("regime_confirmation", "squeeze_resumption_observed"):
            continue
        attack, now = trigger.get("attack"), trigger["bar_index"]
        if attack not in positive or now >= len(bars):
            continue
        for item in anchors_at_attack.get(attack, []):
            low, high = item["origin"], item["high"]
            identity = (item["trend_level"], low["index"], high["index"])
            if now <= occupied.get(identity, -1):
                continue
            anchor = AbcAnchor(
                low["index"], high["index"], item["known_index"], low["value"], high["value"], item["source_path"]
            )
            events = tertiary_abc_observations(bars, [anchor], [positive[attack], trigger], allow_deep_pullback=True)
            if not events or (*identity, events[0]["b_low_index"]) in used:
                continue
            if gap_confirmation:
                events = [dict(e, confirmation_source="defended_n_consolidation_gap",
                               gap_confirmation_index=now,
                               gap_previous_high=trigger.get('gap_previous_high'),
                               gap_low=trigger.get('gap_low')) for e in events]
            if events[0].get('deep_price_path') and bars[now].close <= max(b.high for b in bars[attack:now]):
                continue
            from .alternation_duration import short_shallow_pullback
            proof = events[0]
            if short_shallow_pullback(proof['a_origin_price'], proof['a_high_price'],
                                      proof['b_minimum_close'], proof['a_duration'], proof['b_duration']):
                # 资格在正 N 后真实轧空的放量中大阳收盘确认，不回填至 N 完成日。
                # 回补、缺口替代及弱 K 不能借此免去原短时浅回撤的重破条件。
                volume_squeeze = (not gap_confirmation and trigger['event'] == 'regime_confirmation'
                                  and now in volume_dates.get(attack, ()))
                if volume_squeeze:
                    events = [dict(event, duration_confirmation='volume_bullish_n_squeeze') for event in events]
                else:
                    breakout = next((e for e in events if e['event'] == 'tertiary_c_breakout'), None)
                    if breakout is None:
                        continue
                    confirmed = breakout['bar_index']
                    # 全历史输入也按最早实际确认日归属，未来 A 高突破不得抢占更早的合格轧空。
                    if any(now < day < confirmed for day in volume_dates.get(attack, ())):
                        continue
                    events = [dict(proof, bar_index=confirmed, candidate_index=confirmed,
                                   duration_confirmation='close_above_a_high_after_short_pullback'),
                              dict(breakout, candidate_index=confirmed,
                                   duration_confirmation='close_above_a_high_after_short_pullback')]
            used.add((*identity, events[0]["b_low_index"]))
            occupied[identity] = len(bars)
            for event in events:
                kind = {
                    "tertiary_c_candidate": "squeeze_alternation_confirmed",
                    "tertiary_c_breakout": "squeeze_alternation_breakout",
                    "tertiary_c_invalidated": "squeeze_alternation_invalidated",
                }[event["event"]]
                observations.append(
                    dict(
                        event,
                        event=kind,
                        n_neckline=positive[attack].get('neckline'),
                        trend_level=item["trend_level"],
                        anchor_known_index=item["known_index"],
                        key_source_index=item["key"]["index"],
                        key_price=item["key"]["value"],
                    )
                )
                if kind == "squeeze_alternation_invalidated":
                    occupied[identity] = event["bar_index"]
    from .alternation_breakout import high_breakout_events
    return high_breakout_events(bars, observations)


def squeeze_landmarks(bars, events, level):
    """Adapt shared strategy events into dated chart landmarks without new math."""
    result = {}

    def reference(index, value, kind, known):
        return dict(
            index=index,
            ordinal=0,
            time=bars[index].timestamp.date().isoformat(),
            value=value,
            kind=kind,
            label=kind,
            available_at=known,
        )

    for event in sorted(events, key=lambda e: (e["bar_index"], e["event"] != "squeeze_alternation_confirmed")):
        if not event["event"].startswith("squeeze_alternation_") or event["trend_level"] != level:
            continue
        identity = (event["a_origin_index"], event["a_high_index"], event["b_low_index"])
        date = bars[event["bar_index"]].timestamp.date().isoformat()
        if event["event"] == "squeeze_alternation_confirmed":
            known = bars[event["anchor_known_index"]].timestamp.date().isoformat()
            origin = reference(event["a_origin_index"], event["a_origin_price"], "L", known)
            result[identity] = dict(
                reference(event["b_low_index"], event["b_low_price"], "L", date),
                id=f"squeeze-alternation-{level}-{'-'.join(map(str, identity))}-{event['attack']}",
                label="b",
                trend_level=level,
                source_level=level,
                source_path=event["source_path"],
                source_available_at=date,
                flip="空多交替",
                confirmation_rule="positive_n_and_squeeze_after_qualified_b",
                confirmed_flip_high=reference(event["a_high_index"], event["a_high_price"], "H", known),
                confirmed_bear_low=origin,
                retracement_origin=origin,
                broken_key=reference(event["key_source_index"], event["key_price"], "H", known),
                retracement_ratio=event["retracement_ratio"],
                weak_countermove=False,
                confirmation_evidence=event,
            )
        elif identity in result:
            field = "invalidated_at" if event["event"] == "squeeze_alternation_invalidated" else "breakout_at"
            result[identity][field] = date
    return list(result.values())
