from dataclasses import replace

import pytest

from tests.test_abc_candidate import sample
from wavequant.domain.market_structure.squeeze_alternation import (
    squeeze_alternations,
    squeeze_anchors,
    squeeze_landmarks,
)
from wavequant.domain.strategies.chart_entry_history import merge_squeeze_history
from wavequant.domain.strategies.whole_wave_entry import select_wave_entry


def fixture():
    bars, a, audit = sample()
    item = dict(
        origin=dict(index=0, value=4),
        high=dict(index=2, value=16),
        key=dict(index=0, value=8),
        source_path="tertiary",
        known_index=3,
        trend_level=3,
    )
    return bars, audit, {8: [item]}


@pytest.mark.parametrize("regime", ["轧空", "强轧空"])
def test_confirming_n_only_confirms_alternation_and_requires_a_later_attack(regime):
    bars, audit, anchors = fixture()
    audit[1]["regime"] = regime
    events = squeeze_alternations(bars, audit, anchors)
    history = merge_squeeze_history(bars, {i: () for i in range(len(bars))}, events)
    assert history[8] == ()
    assert history[9][0].alternation_index == 9
    for now in (8, 9):
        proof, reason = select_wave_entry(
            history[now], history[8], bars=bars, attack=8, low_index=7, asof=now, deep_ratio=0.5
        )
        assert proof is None
        assert reason == "wave_no_alternation_at_attack"
    later, reason = select_wave_entry(
        history[10], history[10], bars=bars, attack=10, low_index=9, asof=10, deep_ratio=0.5
    )
    assert later is not None and reason == ""
    assert later["eligibility_frozen_at"] == 10
    proof, _ = select_wave_entry(history[9], (), bars=bars, attack=7, low_index=6, asof=9)
    assert proof is None  # Not permission for unrelated older Ns.


def test_failure_revokes_context_and_preserves_confirmation_and_prefixes():
    bars, audit, anchors = fixture()
    bars[10] = replace(bars[10], low=6, close=12)
    events = squeeze_alternations(bars, audit, anchors)
    assert [e["event"] for e in events] == ["squeeze_alternation_confirmed", "squeeze_alternation_invalidated"]
    history = merge_squeeze_history(bars, {i: () for i in range(len(bars))}, events)
    assert history[9] and not history[10]
    landmarks = squeeze_landmarks(bars, events, 3)
    assert landmarks[0]["available_at"] == "2026-01-10"
    assert landmarks[0]["time"] == "2026-01-06"
    assert landmarks[0]["invalidated_at"] == "2026-01-11"
    assert "invalidated_at" not in events[0]
    for stop in range(1, len(bars) + 1):
        short = squeeze_alternations(bars[:stop], audit, anchors)
        assert short == [e for e in events if e["bar_index"] < stop]


def test_equal_b_is_not_failure_and_breakout_disables_pre_breakout_failure():
    bars, audit, anchors = fixture()
    bars[10] = replace(bars[10], low=7, close=17)
    bars.append(replace(bars[10], timestamp=bars[10].timestamp.replace(day=12), low=6, close=12))
    events = squeeze_alternations(bars, audit, anchors)
    assert [e["event"] for e in events] == ["squeeze_alternation_confirmed", "squeeze_alternation_breakout"]
    history = merge_squeeze_history(bars, {i: () for i in range(len(bars))}, events)
    assert history[11][0].maturity_index == 10


def test_time_and_close_equality_do_not_confirm_and_missing_squeeze_rejected():
    bars, audit, anchors = fixture()
    assert squeeze_alternations(bars, audit[:1], anchors) == []
    bars[5] = replace(bars[5], close=10)
    assert squeeze_alternations(bars, audit, anchors) == []
    bars[4] = replace(bars[4], low=6, close=9)
    assert squeeze_alternations(bars, audit, anchors) == []


def test_developing_a_uses_formal_low_and_confirmed_source_high_not_future_formal_high():
    key = dict(index=0, kind="H", value=10, available_at="d0")
    low = dict(index=1, kind="L", value=4, available_at="d1", preceding_turn=key, flip="翻空为多")
    high = dict(index=3, kind="H", value=16, available_at="d4")
    level = dict(
        trend_level=3,
        strokes=[dict(id="formal", source_path="source", points=[key, low])],
        developing_strokes=[
            dict(source_path="source", wave_direction="up", points=[dict(low, development_role="formal_start"), high])
        ],
    )
    source = dict(strokes=[dict(id="source", points=[high])])
    anchors = squeeze_anchors(level, dict(d0=0, d1=1, d4=4), source)
    assert len(anchors) == 1 and anchors[0]["known_index"] == 4
    high["value"] = 10
    assert squeeze_anchors(level, dict(d0=0, d1=1, d4=4), source) == []


def test_breakout_between_attack_and_squeeze_is_remembered_and_same_day_adapter_order_safe():
    bars, audit, anchors = fixture()
    bars[8] = replace(bars[8], close=17, high=18)
    bars[10] = replace(bars[10], low=6, close=12)
    events = squeeze_alternations(bars, audit, anchors)
    assert [(e["event"], e["bar_index"]) for e in events] == [
        ("squeeze_alternation_confirmed", 9),
        ("squeeze_alternation_breakout", 9),
    ]
    assert events[1]["breakout_index"] == 8
    landmarks = squeeze_landmarks(bars, list(reversed(events)), 3)
    assert landmarks[0]["breakout_at"] == "2026-01-10"
    assert "invalidated_at" not in landmarks[0]


def test_display_only_and_live_source_highs_cannot_supply_a():
    key = dict(index=0, kind="H", value=10, available_at="d0")
    low = dict(index=1, kind="L", value=4, available_at="d1", preceding_turn=key, flip="翻空为多")
    high = dict(index=3, kind="H", value=16, available_at="d4", state="developing")
    level = dict(trend_level=3, strokes=[dict(id="formal", source_path="source", points=[key, low])])
    source = dict(strokes=[dict(id="source", points=[high])])
    dates = dict(d0=0, d1=1, d4=4)
    assert squeeze_anchors(level, dates, source) == []
    high["state"] = "confirmed"
    high["display_only"] = True
    assert squeeze_anchors(level, dates, source) == []
    del high["display_only"]
    assert len(squeeze_anchors(level, dates, source)) == 1
    low["flip"] = ""
    assert squeeze_anchors(level, dates, source) == []
