"""Causal five-top entry limits, including the 2020 Xianfeng reattack."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.five_top_entry import FIVE_TOP_ENTRY_TOO_CLOSE, five_top_entry_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def sample():
    bars = [Bar(datetime(2020, 6, 18) + timedelta(days=index), "sz.300163", 4.9, 5.3, 4.8, 5.2, 100)
            for index in range(6)]
    event = dict(event="wave_projection_push", bar_index=2, attack=1, origin_index=0,
                 state="pushing", target_stage="five_top", target=5.42, box_height=.29, defense=3.65)
    return bars, event


@pytest.mark.parametrize("close, blocked", [(5.1299, False), (5.13, True), (5.1301, True), (5.42, True)])
def test_one_box_boundary_is_exact_and_inclusive(close, blocked):
    bars, event = sample()
    bars[3] = replace(bars[3], close=close)
    risk = five_top_entry_history(bars, [event]).get(3)
    assert (risk is not None) is blocked
    if risk is not None:
        assert risk["reason"] == FIVE_TOP_ENTRY_TOO_CLOSE
        assert risk["wave_remaining_reward"] == pytest.approx(5.42 - close)


def test_target_advance_does_not_hide_first_reach_and_expires_on_next_day():
    bars, event = sample()
    reached = dict(event, event="wave_projection_target_reached", bar_index=3,
                   target_stage="ten_full", target=6.8)
    risk = five_top_entry_history(bars, [reached, event])
    assert 2 not in risk
    assert risk[3]["wave_five_top_target"] == 5.42
    assert 4 not in risk
    assert five_top_entry_history(bars[:4], [event]) == risk


@pytest.mark.parametrize("change", [dict(state="invalidated", target=None),
                                     dict(state="pullback", target=None),
                                     dict(target_stage="ten_full"),
                                     dict(target=float("nan")), dict(box_height=0),
                                     dict(defense=True)])
def test_inactive_or_invalid_latest_goal_cannot_reuse_prior_target(change):
    bars, event = sample()
    later = dict(event, bar_index=3, **change)
    risk = five_top_entry_history(bars, [event, later])
    assert 3 in risk
    assert 4 not in risk


def test_defense_break_invalidates_goal_permanently_and_other_ns_remain_separate():
    bars, event = sample()
    bars[3] = replace(bars[3], low=3.64)
    repeated = dict(event, bar_index=4)
    assert not five_top_entry_history(bars, [event, repeated])
    other = dict(event, attack=2, origin_index=1, bar_index=3, defense=3.5)
    assert five_top_entry_history(bars, [event, repeated, other])[4]["attack"] == 2


def test_future_events_and_same_day_new_goal_never_backdate_entry_risk():
    bars, event = sample()
    future = dict(event, bar_index=4)
    full = five_top_entry_history(bars, [future])
    assert set(full) == {5}
    for end in range(1, len(bars) + 1):
        assert five_top_entry_history(bars[:end], [future]) == {i: risk for i, risk in full.items() if i < end}


@pytest.fixture(scope="module")
def xianfeng_june():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2020-06-24"]
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, config, generate_system_signals(bars, config)


def test_june_24_real_c_wave_candidate_is_rejected_even_without_optional_rr(xianfeng_june):
    bars, config, result = xianfeng_june
    assert not config.preflight_reward_risk
    assert (bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close, bars[-1].volume) == (
        4.90, 5.27, 4.86, 5.20, 32_364_083)
    assert not any(signal.side == "LONG" and signal.bar_index == len(bars) - 1 for signal in result.signals)
    rejection = next(event for event in result.audit if event["event"] == "entry_rejected"
                     and event["bar_index"] == len(bars) - 1 and event["reason"] == FIVE_TOP_ENTRY_TOO_CLOSE)
    assert (rejection["wave_n_date"], rejection["wave_target_known_date"]) == ("2020-06-02", "2020-06-23")
    assert (rejection["wave_five_top_target"], rejection["wave_box_height"],
            rejection["wave_remaining_reward"]) == (5.42, .29, .22)
    assert any(event["event"] == "wave_gap_observed" and event["bar_index"] == len(bars) - 1
               for event in result.audit)


def test_june_5_squeeze_is_preserved_without_using_later_five_top(xianfeng_june):
    bars, config, full = xianfeng_june
    end = next(index for index, bar in enumerate(bars) if bar.timestamp.date().isoformat() == "2020-06-05") + 1
    prefix = generate_system_signals(bars[:end], config)
    assert any(signal.side == "LONG" and signal.bar_index == end - 1 for signal in prefix.signals)
    assert not any(event.get("reason") == FIVE_TOP_ENTRY_TOO_CLOSE and event["bar_index"] == end - 1
                   for event in prefix.audit)
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index < end]


def test_shallow_base_fallback_cannot_bypass_five_top_gate(xianfeng_june, monkeypatch):
    bars, config, _ = xianfeng_june
    # A separately qualified fallback must also fail on the real near-goal day.
    proof = dict(stop=4.5, target=6.0, counter_ratio=.62, breakout_volume_multiple=2.5)
    monkeypatch.setattr("wavequant.domain.strategies.shallow_base_breakout.shallow_base_history",
                        lambda *args, **kwargs: ([], {len(bars) - 1: proof}))
    result = generate_system_signals(bars, config)
    assert not any(signal.side == "LONG" and signal.bar_index == len(bars) - 1 for signal in result.signals)
    assert any(event.get("candidate_channel") == "shallow_base_breakout"
               and event.get("reason") == FIVE_TOP_ENTRY_TOO_CLOSE for event in result.audit)
