from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.abc_candidate import (
    AbcAnchor, abc_pullback_evidence, tertiary_abc_observations,
)
from wavequant.domain.models.model import Bar


def sample():
    prices = [(4, 5), (9, 10), (14, 15), (11, 12), (10, 11), (7, 9),
              (10, 11), (9, 10), (11, 12), (12, 13), (15, 17)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "sz.300154", close,
                max(close + 1, 16 if i == 2 else close), low, close, 100)
            for i, (low, close) in enumerate(prices)]
    anchor = AbcAnchor(0, 2, 3, 4, 16, "tertiary")
    audit = [dict(event="n_completed", direction="up", bar_index=8, known_at=8,
                  origin=5, neckline=6, pullback=7),
             dict(event="regime_confirmation", regime="轧空", bar_index=9, attack=8)]
    return bars, anchor, audit


def test_time_path_accepts_only_strictly_longer_b_and_strictly_below_half_close():
    bars, anchor, _ = sample()
    proof = abc_pullback_evidence(bars, anchor, 7)
    assert proof is not None
    assert proof["price_path"] is False
    assert proof["time_path"] is True
    assert (proof["a_duration"], proof["b_duration"]) == (2, 3)
    assert (proof["b_minimum_close"], proof["half_price"]) == (9, 10)
    bars[5] = replace(bars[5], close=10)
    assert abc_pullback_evidence(bars, anchor, 7) is None  # Equality is excluded.
    bars[5] = replace(bars[5], close=11)
    assert abc_pullback_evidence(bars, anchor, 7) is None
    bars[4] = replace(bars[4], low=6, close=9)
    assert abc_pullback_evidence(bars, anchor, 9) is None  # B=2, even after waiting.


def test_price_path_includes_exact_two_thirds_without_requiring_long_b():
    bars, anchor, _ = sample()
    bars[4] = replace(bars[4], low=8, close=10)
    bars[5] = replace(bars[5], low=9, close=10)
    proof = abc_pullback_evidence(bars, anchor, 7)
    assert proof is not None
    assert proof["price_path"] is True
    assert proof["time_path"] is False
    assert proof["two_thirds_price"] == proof["b_low_price"] == 8
    bars[4] = replace(bars[4], low=7.99)
    assert abc_pullback_evidence(bars, anchor, 7) is None


def test_minimum_close_covers_whole_b_and_duration_does_not_include_n_wait():
    bars, anchor, _ = sample()
    bars[4] = replace(bars[4], low=8, close=9)
    bars[5] = replace(bars[5], low=7, close=11)
    proof = abc_pullback_evidence(bars, anchor, 9)
    assert proof is not None
    assert proof["b_minimum_close_index"] == 4
    assert proof["b_low_index"] == 5
    assert proof["b_duration"] == 3


@pytest.mark.parametrize("regime", ["轧空", "强轧空"])
def test_candidate_is_separate_from_later_close_breakout_and_prefix_stable(regime):
    bars, anchor, audit = sample()
    audit[1]["regime"] = regime
    full = tertiary_abc_observations(bars, [anchor], audit)
    assert [(e["event"], e["bar_index"]) for e in full] == [
        ("tertiary_c_candidate", 9), ("tertiary_c_breakout", 10)]
    for end in range(1, len(bars) + 1):
        assert tertiary_abc_observations(bars[:end], [anchor], audit) == [
            e for e in full if e["bar_index"] < end]


def test_missing_n_or_squeeze_and_future_anchor_cannot_create_candidates():
    bars, anchor, audit = sample()
    assert tertiary_abc_observations(bars, [anchor], audit[:1]) == []
    assert tertiary_abc_observations(bars, [anchor], audit[1:]) == []
    assert tertiary_abc_observations(bars, [replace(anchor, known_index=9)], audit) == []
    audit[1]["regime"] = "盘整"
    assert tertiary_abc_observations(bars, [anchor], audit) == []
    audit[1]["regime"] = "轧空"
    audit[0]["direction"] = "down"
    assert tertiary_abc_observations(bars, [anchor], audit) == []


def test_b_break_invalidates_before_same_bar_breakout_and_does_not_rewrite_candidate():
    bars, anchor, audit = sample()
    bars[10] = replace(bars[10], low=6, close=17)
    events = tertiary_abc_observations(bars, [anchor], audit)
    assert [e["event"] for e in events] == ["tertiary_c_candidate", "tertiary_c_invalidated"]
    assert "invalidated_at" not in events[0]
    bars[9] = replace(bars[9], low=6)
    assert tertiary_abc_observations(bars, [anchor], audit) == []


def test_wick_and_equal_close_do_not_confirm_c_breakout():
    bars, anchor, audit = sample()
    bars[10] = replace(bars[10], high=18, close=16)
    events = tertiary_abc_observations(bars, [anchor], audit)
    assert [e["event"] for e in events] == ["tertiary_c_candidate"]


def test_original_low_breach_and_invalid_a_are_rejected():
    bars, anchor, _ = sample()
    bars[5] = replace(bars[5], low=3, close=9)
    assert abc_pullback_evidence(bars, anchor, 7) is None
    assert abc_pullback_evidence(bars, replace(anchor, high_price=4), 7) is None
    assert abc_pullback_evidence([], anchor, 7) is None


def test_a_high_candle_close_is_not_a_b_correction_close():
    bars, anchor, _ = sample()
    bars[2] = replace(bars[2], low=8, close=9)
    bars[5] = replace(bars[5], close=11)
    assert abc_pullback_evidence(bars, anchor, 7) is None


def test_chart_adapter_publishes_abc_evidence_without_altering_trade_audit():
    from types import SimpleNamespace

    from wavequant.domain.strategies.integrated_strategy import SystemStrategy
    from wavequant.interfaces.charts.visualization import ChartRepository

    bars, anchor, audit = sample()
    records = [dict(event, timestamp=bars[event["bar_index"]].timestamp.isoformat(), defense=9)
               for event in audit]
    high = dict(index=anchor.high_index, value=16, source_path="tertiary",
                available_at="2026-01-04", confirmed_low=dict(index=0, value=4))
    result = SimpleNamespace(audit=records, counts={})
    response = ChartRepository.render_theory(
        None, bars, SystemStrategy(), result, "2026-01-11",
        geometry=dict(tertiary_trends=dict(bear_to_bull_highs=[high])),
    )
    events = [event for event in response["events"] if event["event"].startswith("tertiary_c_")]
    assert [event["available_at"] for event in events] == ["2026-01-10", "2026-01-11"]
    assert events[0]["levels"][0] == dict(name="a 起点", price=4)
    assert events[0]["b_minimum_close"] < events[0]["half_price"]
    assert len(result.audit) == 2
