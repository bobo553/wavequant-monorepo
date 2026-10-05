"""Post ten-full entry pause, including the August 2023 Guofang target."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.ten_full_entry import TEN_FULL_PULLBACK_PENDING, ten_full_entry_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def sample():
    start = datetime(2023, 8, 1)
    values = [
        (4.5, 5.0, 4.0, 4.8),
        (5.0, 6.0, 4.9, 5.8),
        (6.0, 7.0, 5.0, 6.8),
        (9.0, 10.0, 8.0, 9.5),
        (8.0, 9.9, 7.01, 8.0),
        (8.0, 10.0, 7.01, 9.0),
        (9.0, 10.01, 7.01, 9.5),
        (9.0, 10.1, 7.01, 9.5),
    ]
    bars = [Bar(start + timedelta(days=index), "sh.601086", *ohlc, 100)
            for index, ohlc in enumerate(values)]
    events = [
        dict(event="wave_projection_push", bar_index=2, attack=1, origin_index=0, b_low=5.0),
        dict(event="wave_projection_target_reached", bar_index=3, attack=1, origin_index=0,
             reached_stage="ten_full", a_origin=4.0),
    ]
    return bars, events


def risks(bars, events, *, window=3, ratio=.5, anchor="origin", timed_half=False):
    return ten_full_entry_history(bars, events, breakout_window=window,
                                  retracement_ratio=ratio, anchor=anchor,
                                  timed_half=timed_half)


def test_equal_high_is_not_a_breakout_but_strict_new_high_within_window_releases():
    bars, events = sample()
    result = risks(bars, events)
    assert set(result) == {3, 4, 5, 6}
    assert result[3]["reason"] == TEN_FULL_PULLBACK_PENDING
    assert result[3]["wave_ten_full_high"] == 10.0
    assert result[4]["wave_retracement_threshold"] == 7.0
    assert result[5]["wave_breakout_sessions_elapsed"] == 2
    assert risks(bars[:6], events) == {i: value for i, value in result.items() if i < 6}


def test_late_new_high_stays_blocked_until_retracement_and_then_stays_released():
    bars, events = sample()
    bars[6] = replace(bars[6], high=10.0)
    result = risks(bars, events)
    assert set(result) == {3, 4, 5, 6, 7}
    bars.append(Bar(datetime(2023, 8, 9), "sh.601086", 7.1, 9.0, 7.0, 8.0, 100))
    bars.append(Bar(datetime(2023, 8, 10), "sh.601086", 8.0, 9.0, 7.1, 8.5, 100))
    assert set(risks(bars, events)) == {3, 4, 5, 6, 7, 8}


def test_twenty_third_trading_day_is_inside_breakout_window_but_next_day_is_not():
    original, events = sample()

    def extended(break_index):
        bars = original[:4]
        for index in range(4, 29):
            bars.append(Bar(datetime(2023, 8, 1) + timedelta(days=index), "sh.601086",
                            9.0, 10.01 if index == break_index else 10.0, 7.6, 9.0, 100))
        return bars

    timely = risks(extended(26), events, window=23, anchor="b_low")
    assert 26 in timely
    assert 27 not in timely
    late = risks(extended(27), events, window=23, anchor="b_low")
    assert 28 in late


def test_two_thirds_and_recent_b_low_use_the_selected_frozen_amplitude():
    bars, events = sample()
    bars[6] = replace(bars[6], high=10.0)
    bars[7] = replace(bars[7], high=10.0, low=6.5)
    assert risks(bars, events, ratio=2 / 3)[7]["wave_retracement_threshold"] == pytest.approx(6.0)
    bars.append(Bar(datetime(2023, 8, 9), "sh.601086", 8.0, 9.0, 7.0, 8.0, 100))
    assert 8 not in risks(bars, events, ratio=2 / 3, anchor="b_low")
    assert risks(bars[:7], events, ratio=2 / 3, anchor="b_low")[6]["wave_retracement_threshold"] == pytest.approx(6.6666667)


def test_half_depth_needs_b_longer_than_a_but_two_thirds_needs_no_time():
    bars, events = sample()
    bars[6] = replace(bars[6], open=7.0, high=9.0, low=6.9, close=6.9)
    bars[7] = replace(bars[7], open=7.0, high=9.0, low=6.8, close=6.8)
    bars.append(Bar(datetime(2023, 8, 9), "sh.601086", 7.0, 9.0, 6.8, 7.0, 100))
    waiting = risks(bars, events, ratio=2 / 3, timed_half=True)
    assert 7 in waiting  # B duration equals A duration; strict > is required.
    assert 8 not in waiting  # B reaches half depth after exceeding A duration.
    assert waiting[7]["wave_half_retracement_threshold"] == 7.0
    assert waiting[7]["wave_retracement_threshold"] == pytest.approx(6.0)

    bars[4] = replace(bars[4], open=7.0, high=9.0, low=6.0, close=6.0)
    deep = risks(bars, events, ratio=2 / 3, timed_half=True)
    assert 4 in deep
    assert 5 not in deep  # Equality at 2/3 releases without waiting for A duration.


def test_timed_half_requires_the_a_origin_two_thirds_configuration():
    bars, events = sample()
    with pytest.raises(ValueError, match="requires A origin"):
        risks(bars, events, ratio=.5, timed_half=True)
    with pytest.raises(ValueError, match="requires A origin"):
        risks(bars, events, ratio=2 / 3, anchor="b_low", timed_half=True)


def test_missing_pre_target_b_uses_the_original_n_origin_and_still_blocks():
    bars, events = sample()
    only_target = events[1:]
    result = risks(bars, only_target, anchor="b_low")
    assert result[3]["wave_retracement_anchor_source"] == "origin_no_b"
    assert result[3]["wave_retracement_anchor"] == 4.0
    assert result[4]["wave_retracement_threshold"] == 7.0


def test_future_target_never_backdates_the_pause():
    bars, events = sample()
    for end in range(1, len(bars) + 1):
        prefix = risks(bars[:end], events)
        full = risks(bars, events)
        assert prefix == {i: value for i, value in full.items() if i < end}


def test_guofang_august_first_reaches_ten_full_and_post_target_gate_is_causal():
    rows = [
        ("2023-06-26", 4.27, 4.36, 4.27, 4.32),
        ("2023-07-03", 4.46, 4.56, 4.43, 4.54),
        ("2023-07-19", 4.80, 4.94, 4.74, 4.89),
        ("2023-07-20", 4.90, 5.38, 4.90, 5.38),
        ("2023-07-21", 5.37, 5.70, 5.18, 5.32),
        ("2023-07-24", 5.13, 5.33, 4.98, 5.26),
        ("2023-07-25", 5.26, 5.37, 5.17, 5.24),
        ("2023-07-26", 5.19, 5.76, 5.10, 5.76),
        ("2023-07-27", 5.99, 6.34, 5.57, 6.34),
        ("2023-07-28", 6.03, 6.97, 5.92, 6.97),
        ("2023-07-31", 7.51, 7.67, 6.88, 7.67),
        ("2023-08-01", 8.10, 8.44, 7.69, 7.73),
        ("2023-08-02", 7.27, 8.08, 7.27, 7.39),
        ("2023-08-03", 7.11, 7.47, 6.66, 7.15),
        ("2023-08-04", 7.20, 7.36, 6.85, 6.89),
        ("2023-08-07", 6.85, 6.91, 6.50, 6.69),
        ("2023-08-08", 6.67, 6.70, 6.45, 6.49),
        ("2023-08-09", 6.56, 6.57, 6.35, 6.45),
        ("2023-08-10", 6.45, 6.51, 6.27, 6.43),
        ("2023-08-11", 6.34, 6.52, 6.31, 6.48),
        ("2023-08-14", 6.35, 6.82, 6.31, 6.73),
        ("2023-08-15", 6.54, 7.06, 6.54, 6.98),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *ohlc, 100)
            for day, *ohlc in rows]
    setup = WaveProjectionSetup(0, 1, 2, 4.27, 4.56, 5.14, 4.43)
    events = [dict(asdict(event), origin_index=setup.origin_index)
              for event in wave_projection_history(bars, setup)]
    assert next(event for event in events if event["reached_stage"] == "ten_full")["bar_index"] == 11
    result = risks(bars, events, anchor="b_low")
    assert result[11]["wave_ten_full_reached_date"] == "2023-08-01"
    assert result[12]["wave_ten_full_high"] == 8.44
    assert result[12]["wave_retracement_anchor"] == 4.98
    assert result[12]["wave_retracement_anchor_source"] == "b_low"
    assert result[12]["wave_retracement_threshold"] == pytest.approx(6.71)
    assert len(bars) - 1 not in result  # The old default half-depth released on August 4.
    deep = risks(bars, events, window=23, ratio=2 / 3, anchor="b_low")
    assert deep[len(bars) - 1]["wave_ten_full_reached_date"] == "2023-08-01"
    assert deep[len(bars) - 1]["wave_retracement_threshold"] == pytest.approx(6.1333333333)
    assert min(bar.low for bar in bars[12:]) == 6.27


def test_guofang_september_first_half_depth_is_too_fast_to_release():
    start, end = datetime(2023, 6, 26), datetime(2023, 9, 1)
    dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)
             if (start + timedelta(days=offset)).weekday() < 5]
    correction = [
        (7.27, 7.39), (6.66, 7.15), (6.85, 6.89), (6.50, 6.69),
        (6.45, 6.49), (6.35, 6.45), (6.27, 6.43), (6.31, 6.48),
        (6.31, 6.73), (6.54, 6.98), (6.28, 6.30), (6.06, 6.17),
        (6.02, 6.03), (5.92, 6.10), (6.07, 6.19), (6.03, 6.05),
        (5.92, 6.03), (5.83, 5.88), (5.87, 5.91), (5.82, 6.08),
        (5.93, 6.00), (5.88, 6.01), (6.05, 6.61),
    ]
    peak = dates.index(datetime(2023, 8, 1))
    assert peak == 26 and len(dates) - 1 - peak == 23
    bars = []
    for index, date in enumerate(dates):
        low, close = ((7.69, 7.73) if index == peak else
                      correction[index - peak - 1] if index > peak else (4.27, 4.32))
        high = 8.44 if index == peak else max(low + .1, close)
        bars.append(Bar(date, "sh.601086", low, high, low, close, 100))
    events = [dict(event="wave_projection_target_reached", bar_index=peak,
                   attack=5, origin_index=0, reached_stage="ten_full", a_origin=4.27)]
    gate = risks(bars, events, window=23, ratio=2 / 3, anchor="origin", timed_half=True)
    september = gate[len(bars) - 1]
    assert september["wave_ten_full_reached_date"] == "2023-08-01"
    assert september["wave_retracement_anchor"] == 4.27
    assert september["wave_retracement_threshold"] == pytest.approx(5.66)
    assert september["wave_half_retracement_threshold"] == pytest.approx(6.355)
    assert september["wave_a_duration"] == 26
    assert september["wave_b_duration"] == 20
    assert september["wave_b_low_date"] == "2023-08-29"
    assert september["wave_b_low"] == 5.82
    assert september["wave_b_minimum_close"] == 5.88


@pytest.mark.parametrize("variant", [
    "lecture_v3", "lecture_v3_c50", "lecture_v3_d50_c50",
    "lecture_v3_d67_c33", "lecture_v3_d67_c50", "lecture_v3_close_d50_c50",
])
def test_v3_profiles_use_a_origin_two_thirds_and_timed_half(variant):
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}}, variant)
    config = SystemStrategy(**profile["strategy"])
    config.validate()
    assert profile["profile_version"].startswith("shared_edge_n_v94_")
    assert config.ten_full_breakout_window == 23
    assert config.ten_full_retracement_ratio == 2 / 3
    assert config.ten_full_retracement_anchor == "origin"
    assert config.ten_full_timed_half_retracement is True


def test_global_gate_blocks_normal_and_shallow_base_entry_channels(monkeypatch):
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_strong_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2020-06-05"]
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    assert any(signal.side == "LONG" and signal.bar_index == len(bars) - 1
               for signal in generate_system_signals(bars, config).signals)
    proof = dict(stop=4.5, target=6.0, counter_ratio=.62, breakout_volume_multiple=2.5)
    monkeypatch.setattr("wavequant.domain.strategies.shallow_base_breakout.shallow_base_history",
                        lambda *args, **kwargs: ([], {len(bars) - 1: proof}))
    risk = dict(reason=TEN_FULL_PULLBACK_PENDING, attack=1, wave_ten_full_high=5.0)
    monkeypatch.setattr("wavequant.domain.strategies.integrated_strategy.ten_full_entry_history",
                        lambda *args, **kwargs: {len(bars) - 1: risk})
    result = generate_system_signals(bars, config)
    assert not any(signal.side == "LONG" and signal.bar_index == len(bars) - 1
                   for signal in result.signals)
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == len(bars) - 1
               and event["reason"] == TEN_FULL_PULLBACK_PENDING
               and event["candidate_channel"] == "global_ten_full_pullback_guard"
               for event in result.audit)
