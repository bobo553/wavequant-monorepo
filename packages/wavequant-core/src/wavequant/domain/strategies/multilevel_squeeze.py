"""Join one N to a previously known higher-level high without future pivots."""


def multilevel_squeeze(bars, candidate, frame, key_events):
    attack, now = candidate["attack"], candidate["start"] + frame.bar_index
    setup = candidate["setup"]
    if now < attack + 2 or frame.first_defense_breach_index is not None:
        return None
    keys = [
        e
        for e in key_events
        if e["event"] == "hierarchy_resistance_key"
        and e["bar_index"] < attack
        and e.get("trend_level") in (2, 3)
        and e.get("key")
        and setup.origin.index < e["key"]["index"] < attack
    ]
    if not keys:
        return None
    known = max(keys, key=lambda e: (e["key"]["value"], e["bar_index"]))
    key = known["key"]
    if key["index"] != setup.neckline.index:
        return None
    if not bars[attack - 1].high <= key["value"] < bars[attack].close:
        return None
    bar, prev = bars[now], bars[now - 1]
    record = max(b.high for b in bars[attack:now])
    if not (
        bar.open >= prev.close
        and bar.close > bar.open
        and bar.close > record
        and bar.low >= min(prev.low, bars[now - 2].close)
        and bar.volume > prev.volume
    ):
        return None
    # Supply on the current candle is not declared absent: the close has
    # defeated the *previous* resistance record at both scales despite it.
    return dict(
        buy_point_type="multilevel_breakout_squeeze",
        definition="multilevel_breakout_squeeze_v1",
        priority=1,
        trend_level=known["trend_level"],
        key_source_index=key["index"],
        key_price=key["value"],
        key_known_index=known["bar_index"],
        origin_index=setup.origin.index,
        pullback_index=setup.pullback.index,
        counter_ratio=candidate["force"].ratio,
        counter_filter_applied=False,
        higher_breakout_index=attack,
        higher_confirmation_index=now,
        higher_resistance_high=record,
        higher_defense=candidate["n"].completion.defense,
        confirmation_close=bar.close,
        confirmation_low=bar.low,
        confirmation_volume=bar.volume,
        previous_volume=prev.volume,
    )
