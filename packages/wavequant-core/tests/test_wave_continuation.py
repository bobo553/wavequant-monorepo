from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.wave_continuation import (
    wave_confirmation_is_new,
    wave_confirmation_state,
    wave_gap_entry,
    wave_pullback_context,
)


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/huaci_wave_continuation.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    origin, attack, squeeze = [dates[d] for d in ("2026-07-27", "2026-08-03", "2026-08-05")]
    box = bars[attack].high
    setup = WaveProjectionSetup(
        origin, attack, squeeze, bars[origin].low, box, 3 * box - 2 * bars[origin].low, bars[attack].low
    )
    return bars, dates, setup


def guofang_strong_a_sample():
    rows = [
        ("2023-06-26", 4.27, 4.36, 4.27, 4.32, 10_046_900),
        ("2023-07-03", 4.46, 4.56, 4.43, 4.54, 14_883_001),
        ("2023-07-19", 4.80, 4.94, 4.74, 4.89, 19_022_900),
        ("2023-07-20", 4.90, 5.38, 4.90, 5.38, 49_777_665),
        ("2023-07-21", 5.37, 5.70, 5.18, 5.32, 73_203_163),
        ("2023-07-24", 5.13, 5.33, 4.98, 5.26, 47_724_598),
        ("2023-07-25", 5.26, 5.37, 5.17, 5.24, 33_760_865),
        ("2023-07-26", 5.19, 5.76, 5.10, 5.76, 44_259_187),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *values) for day, *values in rows]
    setup = WaveProjectionSetup(0, 1, 2, 4.27, 4.56, 5.14, 4.43)
    return bars, setup


def test_guofang_strong_a_resistance_rebreak_confirms_july_26():
    bars, setup = guofang_strong_a_sample()
    proof = wave_gap_entry(bars, setup, 7)
    assert wave_gap_entry(bars, setup, 6) is None
    assert proof == wave_gap_entry(bars[:8], setup, 7)
    assert proof["wave_entry_path"] == "two_t_strong_a_resistance_rebreak"
    assert proof["wave_a_origin_date"] == "2023-06-26"
    assert proof["wave_a_high_date"] == proof["wave_resistance_date"] == "2023-07-21"
    assert proof["wave_b_low_date"] == "2023-07-24"
    assert proof["wave_two_t_break_date"] == "2023-07-20"
    assert proof["wave_two_t_body_midpoint"] == pytest.approx(5.14)
    assert proof["wave_breakout_close"] == pytest.approx(5.76)
    assert proof["wave_breakout_high"] == pytest.approx(5.70)
    assert proof["wave_c_0618_target"] == pytest.approx(5.86374)
    assert proof["wave_equal_target"] == pytest.approx(6.41)
    assert proof["wave_five_top_target"] == pytest.approx(5.85)


def test_strong_a_signal_marker_carries_only_its_matching_buy_proof():
    from wavequant.application.analytics.trade_evidence import result_markers

    stamp = "2023-07-26T00:00:00"
    result = dict(
        orders=[],
        signals=[dict(timestamp=stamp, time="2023-07-26", side="LONG",
                      reference_price=5.76, reason="system_wave_push_gap", regime="轧空",
                      rvol=1.31, invalidation_price=4.43, target_price=5.86374)],
        audit=[
            dict(timestamp=stamp, event="long_signal", channel="shallow_base_breakout"),
            dict(timestamp=stamp, event="long_signal", channel="wave_push_gap",
                 wave_entry_path="two_t_strong_a_resistance_rebreak", wave_five_top_target=5.85),
        ],
    )
    marker = result_markers(result)[0]
    assert len(marker["decision_evidence"]) == 1
    assert marker["decision_evidence"][0]["wave_five_top_target"] == 5.85


@pytest.mark.parametrize("case", ["midpoint_close", "volume", "a_high", "no_resistance", "defense", "new_b_low"])
def test_strong_a_rebreak_requires_held_close_volume_and_a_high_break(case):
    bars, setup = guofang_strong_a_sample()
    if case == "midpoint_close":
        bars[5] = replace(bars[5], close=5.13)
    elif case == "volume":
        bars[7] = replace(bars[7], volume=bars[6].volume)
    elif case == "a_high":
        bars[7] = replace(bars[7], close=5.69)
    elif case == "no_resistance":
        bars[4] = replace(bars[4], open=5.30)
    elif case == "defense":
        bars[5] = replace(bars[5], low=4.42)
    else:
        bars[7] = replace(bars[7], low=4.97)
    assert wave_gap_entry(bars, setup, 7) is None


def test_lexin_february_buy_keeps_the_september_a_origin():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    origin, attack, squeeze = [dates[day] for day in ("2024-09-18", "2024-09-25", "2024-12-16")]
    setup = WaveProjectionSetup(
        origin, attack, squeeze, bars[origin].low, bars[attack].high,
        3 * bars[attack].high - 2 * bars[origin].low, bars[attack].low,
    )

    proof = wave_pullback_context(bars, setup, dates["2025-02-10"])

    assert proof is not None
    assert proof["wave_a_origin_date"] == "2024-09-18"
    assert proof["wave_a_origin"] == pytest.approx(10.5900, abs=0.0001)
    assert proof["wave_a_high_date"] == "2024-12-18"
    assert proof["wave_b_low_date"] == "2025-01-13"


def test_real_gap_keeps_whole_b_low_and_equal_a_target():
    bars, dates, setup = sample()
    now = dates["2026-09-15"]
    proof = wave_gap_entry(bars, setup, now)
    assert proof["wave_a_origin_date"] == "2026-07-27"
    assert proof["wave_a_origin"] == setup.origin
    assert proof["wave_b_low_date"] == "2026-08-21"
    assert proof["wave_a_high_date"] == proof["wave_entry_two_t_date"] == "2026-08-11"
    assert proof["wave_equal_target"] == pytest.approx(19.69748771297527)
    assert proof["wave_c_1618_target"] == pytest.approx(22.054895534642093)
    assert proof["wave_c_2618_target"] == pytest.approx(25.869471297857007)
    assert proof["wave_c_1618_target"] > proof["wave_equal_target"]
    assert proof["wave_c_2618_target"] > proof["wave_c_1618_target"]
    assert proof["wave_defense"] == pytest.approx(15.11330455893629)
    assert bars[now - 1].close > bars[setup.attack_index].high
    assert proof == wave_gap_entry(bars[: now + 1], setup, now)
    assert wave_gap_entry(bars, setup, dates["2026-08-21"]) is None
    projection = wave_projection_history(bars[:now], setup, target_policy="nearest_box")
    assert projection[-1].b_low_index == dates["2026-08-21"]
    assert projection[-1].target == pytest.approx(proof["wave_equal_target"])


@pytest.mark.parametrize(
    "case",
    [
        "equal_volume",
        "no_gap",
        "broken_defense",
        "no_two_t",
        "attack_wick_only",
        "no_squeeze",
        "no_pullback",
        "target_exhausted",
    ],
)
def test_gap_entry_requires_every_condition(case):
    bars, dates, setup = sample()
    now = dates["2026-09-15"]
    if case == "equal_volume":
        bars[now] = replace(bars[now], volume=bars[now - 1].volume)
    elif case == "no_gap":
        bars[now] = replace(bars[now], low=bars[now - 1].high)
    elif case == "broken_defense":
        bars[dates["2026-08-21"]] = replace(bars[dates["2026-08-21"]], low=setup.defense - 0.01)
    elif case == "no_two_t":
        setup = replace(setup, two_t=30, box_anchor=(30 + 2 * setup.origin) / 3)
    elif case == "attack_wick_only":
        setup = replace(setup, two_t=23, box_anchor=(23 + 2 * setup.origin) / 3)
        bars[setup.attack_index] = replace(bars[setup.attack_index], high=30)
        bars[now] = replace(bars[now], close=30.5, high=31)
    elif case == "no_squeeze":
        setup = replace(setup, squeeze_index=now)
    elif case == "no_pullback":
        bars[now - 1] = replace(bars[now - 1], high=20)
        bars[now] = replace(bars[now], low=21, open=21, close=22, high=22)
    else:
        bars[now] = replace(bars[now], close=20, high=20)
    assert wave_gap_entry(bars, setup, now) is None


def test_equal_defense_is_held_and_symbol_does_not_change_rule():
    bars, dates, setup = sample()
    bars = [replace(b, symbol="sh.600000") for b in bars]
    bars[dates["2026-08-21"]] = replace(bars[dates["2026-08-21"]], low=setup.defense)
    proof = wave_gap_entry(bars, setup, dates["2026-09-15"])
    assert proof["wave_b_low"] == setup.defense


def test_volume_gap_does_not_require_final_bullish_body():
    bars, dates, setup = sample()
    now = dates["2026-09-15"]
    bars[now] = replace(bars[now], open=bars[now].close)
    assert wave_gap_entry(bars, setup, now)["wave_gap_trigger"] == "volume"


def test_full_global_pipeline_reenters_after_inverse_n_and_preserves_prefix():
    bars, dates, setup = sample()
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        first_pullback_threshold=None,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
        volume_filter=True,
    )
    full = generate_system_signals(bars, config)
    day = dates["2026-09-15"]
    buys = [s for s in full.signals if s.side == "LONG" and s.bar_index == day]
    assert len(buys) == 1
    assert buys[0].reason == "system_wave_push_gap"
    assert buys[0].target_price == pytest.approx(19.69748771297527)
    proof = next(e for e in full.audit if e["event"] == "long_signal" and e["bar_index"] == day)
    assert proof["wave_a_origin_date"] == "2026-07-27"
    assert proof["wave_b_low_date"] == "2026-08-21"
    assert proof["rvol"] > 1
    assert proof["rvol"] == pytest.approx(bars[day].volume / bars[day - 1].volume)
    for end in (dates["2026-08-21"], day - 1, day):
        prefix = generate_system_signals(bars[: end + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]


def test_strong_a_body_confirmation_can_advance_to_later_higher_gap():
    bars, dates, _ = sample()
    body, gap = dates["2026-08-26"], dates["2026-09-15"]
    # A completed early observation can confirm the body route even if the
    # final August candle no longer has a qualifying body.
    bars[body] = replace(bars[body], close=16.70, volume=2_100_000)
    strategy = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        first_pullback_threshold=None,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
        volume_filter=True,
    )
    full = generate_system_signals(bars[: gap + 1], strategy)
    confirmations = [
        e
        for e in full.audit
        if e["event"] == "long_signal" and e.get("wave_entry_path") and e["bar_index"] in (body, gap)
    ]
    assert [e["bar_index"] for e in confirmations] == [body, gap]
    assert [(e["bar_index"], e["wave_confirmation_phase"]) for e in confirmations] == [(body, "body"), (gap, "gap")]
    assert confirmations[0]["wave_a_high_index"] == confirmations[1]["wave_a_high_index"]
    assert confirmations[0]["wave_b_low_index"] == confirmations[1]["wave_b_low_index"]
    assert confirmations[1]["wave_gap_high"] > confirmations[0]["wave_gap_high"]
    for end in (body, gap):
        prefix = generate_system_signals(bars[: end + 1], strategy)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]


def test_strong_a_confirmation_requires_strictly_higher_gap_and_one_fill_per_phase():
    body = {"wave_confirmation_phase": "body", "wave_gap_high": 18.0}
    prior_body = wave_confirmation_state(body)
    assert not wave_confirmation_is_new({"wave_confirmation_phase": "body", "wave_gap_high": 19.0}, prior_body)
    assert not wave_confirmation_is_new({"wave_confirmation_phase": "gap", "wave_gap_high": 18.0}, prior_body)
    higher_gap = {"wave_confirmation_phase": "gap", "wave_gap_high": 18.01}
    assert wave_confirmation_is_new(higher_gap, prior_body)
    assert not wave_confirmation_is_new(higher_gap, wave_confirmation_state(higher_gap))
    assert not wave_confirmation_is_new(body, wave_confirmation_state(higher_gap))
