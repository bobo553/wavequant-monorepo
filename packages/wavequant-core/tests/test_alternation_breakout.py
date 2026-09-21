from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

from wavequant.domain.market_structure.alternation_breakout import high_breakout_events, promote_alternation_segments
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.chart_entry_history import merge_squeeze_history


def fixture():
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", 8, 9, 7, 8, 100) for i in range(6)]
    bars[4] = replace(bars[4], high=11, close=8)
    event = dict(
        event="squeeze_alternation_confirmed",
        bar_index=3,
        candidate_index=3,
        a_origin_index=0,
        a_origin_price=5,
        a_high_index=1,
        a_high_price=10,
        b_low_index=2,
        b_low_price=7,
        trend_level=3,
        source_path="tertiary-source",
        attack=2,
        anchor_known_index=1,
        key_source_index=0,
        key_price=9,
    )
    return bars, event


def test_high_break_confirms_without_close_and_prefix_never_backdates():
    bars, event = fixture()
    full = high_breakout_events(bars, [event])
    assert full[-1]["bar_index"] == 4
    assert full[-1]["breakout_basis"] == "high_after_confirmed_alternation"
    assert bars[4].close < event["a_high_price"]
    assert high_breakout_events(bars[:4], [event]) == [event]
    assert high_breakout_events(bars[:5], [event]) == full
    history = merge_squeeze_history(bars, {i: () for i in range(len(bars))}, full)
    assert history[3][0].maturity_index is None
    assert history[4][0].maturity_index == 4


def test_equal_high_or_b_low_breach_does_not_promote():
    bars, event = fixture()
    bars[4] = replace(bars[4], high=10)
    assert high_breakout_events(bars, [event]) == [event]
    bars[4] = replace(bars[4], high=11, low=6)
    assert high_breakout_events(bars, [event])[-1]["event"] == "squeeze_alternation_invalidated"
    # No qualification cannot be manufactured by a raw high alone.
    assert high_breakout_events(bars, []) == []


def test_formal_segment_owns_confirmation_date_and_clears_duplicate_development():
    bars, event = fixture()
    geometry = dict(strokes=[], developing_strokes=[dict(points=[dict(index=1, kind="H"), dict(index=2, kind="L")])])
    before = deepcopy(geometry)
    promoted = promote_alternation_segments(geometry, bars, high_breakout_events(bars, [event]), 3)
    assert geometry == before
    assert promoted["developing_strokes"] == []
    points = promoted["strokes"][0]["points"]
    assert [(p["index"], p["kind"]) for p in points] == [(1, "H"), (2, "L")]
    assert all(p["state"] == "confirmed" and p["available_at"] == "2026-01-05" for p in points)
    assert promote_alternation_segments(geometry, bars, [event], 3)["strokes"] == []


def test_xidian_august20_confirmed_high_break_and_formal_segment_match_prefix():
    raw = json.loads((Path(__file__).parent / "fixtures/xidian_2026_deep_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *v) for d, *v in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    matching = [
        e
        for e in full.audit
        if e["event"] == "squeeze_alternation_breakout"
        and e["trend_level"] == 3
        and bars[e["a_high_index"]].timestamp.date().isoformat() == "2025-08-11"
    ]
    assert len(matching) == 1
    event = matching[0]
    assert event["timestamp"].startswith("2026-08-20")
    assert str(bars[event["b_low_index"]].timestamp.date()) == "2026-07-21"
    for end in (event["bar_index"] - 1, event["bar_index"]):
        prefix = generate_system_signals(bars[: end + 1], config)
        assert [e for e in prefix.audit if e["event"].startswith("squeeze_alternation")] == [
            e for e in full.audit if e["event"].startswith("squeeze_alternation") and e["bar_index"] <= end
        ]
    geometry = promote_alternation_segments(dict(strokes=[]), bars, matching, 3)
    assert [(p["time"], p["available_at"]) for p in geometry["strokes"][0]["points"]] == [
        ("2025-08-11", "2026-08-20"),
        ("2026-07-21", "2026-08-20"),
    ]
