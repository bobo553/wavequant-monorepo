"""An unfinished C projection remains binding across newer entry structures."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def sample():
    prices = [(10, 11, 10, 10.5), (19, 20, 18, 19.5), (17, 17.5, 15, 16),
              (18, 19, 17, 18.8), (20.4, 21.4, 19.1, 20.8), (20.8, 21.2, 20.6, 21)]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), "TEST", *row, 100)
            for index, row in enumerate(prices)]
    event = dict(event="wave_gap_observed", bar_index=3, attack=1,
                 wave_a_origin=10, wave_a_origin_date="2025-01-01",
                 wave_a_high=20, wave_a_high_index=1, wave_a_high_date="2025-01-02",
                 wave_b_low=15, wave_b_low_index=2, wave_b_low_date="2025-01-03",
                 wave_c_0618_target=21.18, wave_equal_target=25)
    return bars, event


def observe(bars, events, **options):
    from wavequant.domain.strategies.unfinished_c_wave import unfinished_c_wave_history

    return unfinished_c_wave_history(bars, events, **options)


def test_reached_point_618_without_equal_wave_blocks_later_weak_rebounds():
    bars, event = sample()
    risks = observe(bars, [event])
    assert set(risks) == {4, 5}
    assert risks[5]["reason"] == "wave_c_0618_unfinished_pressure"
    assert risks[5]["previous_c_0618_target"] == 21.18
    assert risks[5]["previous_c_equal_target"] == 25
    assert risks[5]["previous_c_a_amplitude"] == 10
    assert risks[5]["previous_c_retracement_reference"] == pytest.approx(11.4)
    assert risks[5]["previous_c_reached_date"] == "2025-01-05"


def test_price_equal_and_weak_close_above_target_do_not_count_as_strong_break():
    bars, event = sample()
    for closed in (21.18, 21.2):
        changed = bars[:-1] + [replace(bars[-1], open=21, high=21.3, close=closed)]
        assert 5 in observe(changed, [event])


def test_strong_daily_break_without_equal_target_does_not_release_old_pressure():
    bars, event = sample()
    strong = replace(bars[-1], open=20.7, high=22.8, low=20.6, close=22.5)
    held = replace(strong, timestamp=strong.timestamp + timedelta(days=1), open=22.4, close=22.5)
    fallen = replace(held, timestamp=held.timestamp + timedelta(days=1), open=22, low=20.5, close=21)
    risks = observe(bars[:-1] + [strong, held, fallen], [event])
    assert all(index in risks for index in (5, 6, 7))
    assert risks[7]["previous_c_phase"] == "pullback_holds_c_origin"
    assert 5 in observe(bars[:-1] + [strong], [event], last_bar_complete=False)


def test_three_stage_c_keeps_frozen_targets_until_second_rise_completes_equal_wave():
    bars, event = sample()
    bars[5] = replace(bars[5], high=21, low=18.82, close=19.1)
    resumed = replace(bars[5], timestamp=bars[5].timestamp + timedelta(days=1),
                      open=19.1, high=22.5, low=19.1, close=22.3)
    completed = replace(resumed, timestamp=resumed.timestamp + timedelta(days=1),
                        open=22.3, high=25, low=22, close=24)
    events = []
    risks = observe(bars + [resumed, completed], [event], event_sink=events)
    assert risks[4]["previous_c_phase"] == "reached_0618"
    assert risks[5]["previous_c_phase"] == "pullback_holds_c_origin"
    assert risks[6]["previous_c_phase"] == "resuming_to_equal"
    assert risks[6]["previous_c_second_rise_reference"] == pytest.approx(25)
    assert risks[6]["previous_c_0618_target"] == 21.18
    assert risks[6]["previous_c_equal_target"] == 25
    assert 7 not in risks
    assert [row["previous_c_phase"] for row in events] == [
        "reached_0618", "pullback_holds_c_origin", "resuming_to_equal", "equal_completed"]
    assert min(bar.low for bar in bars[4:] + [resumed, completed]) > event["wave_b_low"]


@pytest.mark.parametrize("recovery", ["equal_wave", "broken_c_origin"])
def test_completed_wave_or_broken_c_origin_ends_the_risk_cycle(recovery):
    bars, event = sample()
    recovered = replace(bars[-1], high=25) if recovery == "equal_wave" else replace(
        bars[-1], low=14.99, close=16)
    repeated = dict(event, bar_index=5)
    assert 5 not in observe(bars[:-1] + [recovered], [event, repeated])


def test_new_n_and_larger_recomputed_c_targets_cannot_replace_old_unfinished_wave():
    bars, event = sample()
    newer = dict(event, bar_index=5, attack=4, wave_a_high_index=4, wave_a_high=21.4,
                 wave_b_low_index=5, wave_b_low=20.6, wave_c_0618_target=27.6452, wave_equal_target=32)
    risks = observe(bars, [newer, event])
    assert risks[5]["previous_c_known_date"] == "2025-01-04"
    assert risks[5]["previous_c_0618_target"] == 21.18


def test_confirmation_wick_and_future_proofs_never_backdate_target_reaches():
    bars, event = sample()
    wick = bars[:4]
    wick[-1] = replace(wick[-1], high=24)
    assert observe(wick, [event]) == {}
    assert observe(bars[:3], [event]) == {}
    full = observe(bars, [event])
    for length in range(1, len(bars) + 1):
        assert observe(bars[:length], [event]) == {i: risk for i, risk in full.items() if i < length}


def test_unreached_wave_broken_b_and_malformed_evidence_do_not_create_pressure():
    bars, event = sample()
    changed = bars.copy()
    changed[4] = replace(changed[4], low=14.9, high=21, close=20)
    assert observe(changed, [event]) == {}
    for invalid in [dict(event, wave_equal_target=float("nan")), dict(event, wave_a_origin=True),
                    dict(event, wave_b_low_index=4), dict(event, wave_c_0618_target=25)]:
        assert observe(bars, [invalid]) == {}


def test_broken_c_origin_retires_pressure_before_a_full_amplitude_decline():
    bars, event = sample()
    # No A-sized decline has happened. Breaking B, rather than waiting for the
    # much lower A origin or an amplitude forecast, ends this C cycle.
    event = dict(event, wave_a_high=60, wave_c_0618_target=45.9, wave_equal_target=65)
    bars[1] = replace(bars[1], high=60)
    bars[3] = replace(bars[3], open=46, high=47, low=45, close=46.2)
    bars[4] = replace(bars[4], open=46.2, high=47, low=45, close=46.3)
    bars[5] = replace(bars[5], open=46, high=47, low=14.99, close=42)
    assert 4 in observe(bars[:5], [event])
    assert 5 not in observe(bars, [event])


def test_first_c_confirmation_is_not_its_own_previous_c_pressure():
    bars, event = sample()
    bars[3] = replace(bars[3], open=21.2, high=21.4, low=21, close=21.3)
    assert 3 not in observe(bars, [event])
    assert 4 in observe(bars, [event])


def test_falling_below_0618_without_breaking_c_origin_keeps_pressure():
    bars, event = sample()
    bars[5] = replace(bars[5], high=21, low=16, close=17)
    assert 5 in observe(bars, [event])


def test_c_origin_invalidation_requires_a_strict_break():
    bars, event = sample()
    event = dict(event, wave_a_high=60, wave_c_0618_target=45.9, wave_equal_target=65)
    bars[1] = replace(bars[1], high=60)
    bars[3] = replace(bars[3], open=46, high=47, low=45, close=46.2)
    bars[4] = replace(bars[4], open=46.2, high=47, low=45, close=46.3)
    bars[5] = replace(bars[5], open=46, high=47, low=15, close=42)
    assert 5 in observe(bars, [event])


@pytest.mark.parametrize("completion", ["equal", "origin_break"])
def test_minute_confirmed_c_uses_later_minutes_only_after_daily_completion(completion):
    bars, event = sample()
    event = dict(event, wave_breakout_close=18.8, wave_post_confirmation_high=21.4,
                 wave_post_confirmation_low=17)
    if completion == "equal":
        event["wave_post_confirmation_high"] = 25
    else:
        event["wave_post_confirmation_low"] = 14.99
    events = []
    assert observe(bars, [event], event_sink=events) == {}
    assert events[0]["previous_c_phase"] == ("equal_completed" if completion == "equal" else "c_origin_broken")
    partial_events = []
    assert observe(bars[:4], [event], last_bar_complete=False, event_sink=partial_events) == {}
    assert partial_events == []


def test_first_minute_confirmation_is_retained_when_daily_proof_shares_its_date():
    bars, daily = sample()
    bars[3] = replace(bars[3], high=25)
    minute = dict(daily, wave_breakout_close=18.8, wave_post_confirmation_high=25,
                  wave_post_confirmation_low=17)
    assert observe(bars, [daily, minute]) == {}
    assert observe(bars, [minute, daily]) == {}


@pytest.fixture(scope="module")
def xianfeng():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2025-11-18"]
    strategy = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"]
    strategy.update(volume_filter=False, shallow_base_breakout_enabled=True)
    config = SystemStrategy(**strategy)
    return bars, config, generate_system_signals(bars, config)


def test_xianfeng_november_17_new_c_cannot_bypass_previous_unfinished_c(xianfeng):
    bars, _, result = xianfeng
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2025-11-17")
    assert not any(s.side == "LONG" and s.bar_index == now for s in result.signals)
    rejected = next(e for e in result.audit if e["event"] == "entry_rejected" and e["bar_index"] == now
                    and e["reason"] == "wave_c_0618_unfinished_pressure")
    assert rejected["previous_c_0618_target"] == pytest.approx(5.28648)
    assert rejected["previous_c_equal_target"] == pytest.approx(6.57)
    assert rejected["previous_c_a_amplitude"] == pytest.approx(3.36)
    assert rejected["observed_close"] == 5.19


def test_xianfeng_prefix_matches_full_and_partial_bar_keeps_old_pressure(xianfeng):
    bars, config, result = xianfeng
    prefix = generate_system_signals(bars[:-1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index < len(bars) - 1]
    partial = generate_system_signals(bars[:-1], config, current_bar_complete=False)
    assert not any(s.side == "LONG" and s.bar_index == len(bars) - 2 for s in partial.signals)
