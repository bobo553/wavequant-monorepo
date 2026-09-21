"""Causal N/squeeze confirmation of a qualified higher B low.

The A endpoint may still be developing at its own level, but must already be
a confirmed source pivot following a formal bullish reversal. This does not
promote it into a formal trend vertex. Each attack receives its own prefix.
"""

from .abc_candidate import AbcAnchor, tertiary_abc_observations


def squeeze_anchors(level, dates, source_level):
    """Read only confirmed source endpoints; never use a live candle extreme."""
    anchors = {}
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
    latest = {}
    for item in anchors.values():
        origin = item["origin"]["index"]
        if origin not in latest or item["high"]["index"] > latest[origin]["high"]["index"]:
            latest[origin] = item
    return list(latest.values())


def squeeze_alternations(bars, audit, anchors_at_attack):
    """Preserve dated confirmation, breakout and failure instead of repainting."""
    positive = {e["bar_index"]: e for e in audit if e["event"] == "n_completed" and e.get("direction") == "up"}
    observations, occupied, used = [], {}, set()
    for trigger in sorted(audit, key=lambda e: e["bar_index"]):
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
            if events[0].get('deep_price_path') and bars[now].close <= max(b.high for b in bars[attack:now]):
                continue
            from .alternation_duration import short_shallow_pullback
            proof = events[0]
            if short_shallow_pullback(proof['a_origin_price'], proof['a_high_price'],
                                      proof['b_minimum_close'], proof['a_duration'], proof['b_duration']):
                breakout = next((e for e in events if e['event'] == 'tertiary_c_breakout'), None)
                if breakout is None:
                    continue
                confirmed = breakout['bar_index']
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
                        trend_level=item["trend_level"],
                        anchor_known_index=item["known_index"],
                        key_source_index=item["key"]["index"],
                        key_price=item["key"]["value"],
                    )
                )
                if kind == "squeeze_alternation_invalidated":
                    occupied[identity] = event["bar_index"]
    return sorted(observations, key=lambda e: e["bar_index"])


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
