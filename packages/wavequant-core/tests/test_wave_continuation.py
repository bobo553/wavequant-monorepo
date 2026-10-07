from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals, pivot_history
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


def test_guofang_april_1_ordinary_a_projects_point_618_and_equal_wave():
    from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint

    rows = [
        ("2024-02-29", 4.02, 4.28, 4.02, 4.25, 16_685_400),
        ("2024-03-14", 4.50, 4.67, 4.42, 4.48, 16_213_600),
        ("2024-03-18", 5.28, 5.42, 5.18, 5.42, 75_191_900),
        ("2024-03-22", 5.07, 5.61, 5.01, 5.34, 70_992_647),
        ("2024-03-25", 5.25, 5.30, 4.95, 4.99, 41_133_558),
        ("2024-03-26", 5.00, 5.03, 4.75, 4.85, 26_411_476),
        ("2024-03-27", 4.80, 4.98, 4.72, 4.82, 28_858_988),
        ("2024-03-28", 4.76, 4.92, 4.73, 4.92, 23_457_120),
        ("2024-03-29", 4.88, 4.91, 4.77, 4.86, 17_200_056),
        ("2024-04-01", 4.91, 4.99, 4.88, 4.97, 21_046_000),
    ]
    bars = [Bar(datetime.fromisoformat(day), "sh.601086", *values) for day, *values in rows]
    setup = WaveProjectionSetup(0, 1, 2, 4.02, 4.67, 5.97, 4.29)
    pivot = ReversalPoint(LinePoint(7, 0, PointKind.HIGH, 4.92), 8, "fixture")

    proof = wave_gap_entry(bars, setup, 9, pivots=[pivot])
    assert proof is not None
    assert proof["wave_a_class"] == "ordinary"
    assert proof["wave_a_origin_date"] == "2024-02-29"
    assert proof["wave_a_high_date"] == "2024-03-22"
    assert proof["wave_b_low_date"] == "2024-03-27"
    assert proof["wave_c_0618_target"] == pytest.approx(4.72 + .618 * (5.61 - 4.02))
    assert proof["wave_equal_target"] == pytest.approx(6.31)
    assert "wave_c_1618_target" not in proof
    assert wave_pullback_context(bars[:-1], setup, 8)["wave_c_0618_target"] == pytest.approx(5.70262)


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


@pytest.mark.parametrize("case", ["midpoint_close", "volume", "a_high", "no_resistance", "origin", "new_b_low"])
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
    elif case == "origin":
        bars[5] = replace(bars[5], low=4.26)
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
        "broken_origin",
        "no_two_t",
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
    elif case == "broken_origin":
        bars[dates["2026-08-21"]] = replace(bars[dates["2026-08-21"]], low=setup.origin - 0.01)
    elif case == "no_two_t":
        setup = replace(setup, two_t=30, box_anchor=(30 + 2 * setup.origin) / 3)
    elif case == "no_squeeze":
        setup = replace(setup, squeeze_index=now)
    elif case == "no_pullback":
        bars[now - 1] = replace(bars[now - 1], high=20)
        bars[now] = replace(bars[now], low=21, open=21, close=22, high=22)
    else:
        bars[now] = replace(bars[now], close=20, high=20)
    assert wave_gap_entry(bars, setup, now) is None


def test_attack_wick_reaching_two_t_confirms_strong_a_before_later_c_entry():
    bars, dates, setup = sample()
    now = dates["2026-09-15"]
    setup = replace(setup, two_t=23, box_anchor=(23 + 2 * setup.origin) / 3)
    bars[setup.attack_index] = replace(bars[setup.attack_index], high=30)
    bars[now] = replace(bars[now], close=30.5, high=31)
    proof = wave_gap_entry(bars, setup, now)
    assert proof is not None
    assert proof["wave_a_class"] == "strong"
    assert proof["wave_a_confirmed_date"] == str(bars[setup.attack_index].timestamp.date())


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


def test_full_global_pipeline_keeps_qualified_a_sources_and_preserves_prefix():
    bars, dates, _ = sample()
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
    assert not buys
    # The supplied Aug 3 setup used by the wave observer is not a completed N
    # under the current lecture reducer. The global pipeline must own its actual
    # Aug 10 N, rather than borrow the unqualified setup's larger C target.
    attack = dates["2026-08-10"]
    completed = next(e for e in full.audit if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert completed["origin"] == dates["2026-07-27"]
    assert completed["neckline"] == dates["2026-08-06"]
    assert completed["pullback"] == dates["2026-08-07"]
    # Formal squeeze evidence alone cannot restore a retired target source or
    # invent an A that never exceeded that N's one-P.
    assert any(e["event"] == "n_consolidation_gap_confirmed" and e["attack"] == attack
               and e["bar_index"] == day for e in full.audit)
    assert any(e["event"] == "n_target_source_retired" and e["attack"] == attack
               and e["bar_index"] < day for e in full.audit)
    assert any(e["event"] == "entry_rejected" and e["bar_index"] == day
               and e["reason"] == "no_live_structural_risk_reward" for e in full.audit)
    assert not any(e["event"] == "a_wave_confirmed" and e["source_id"] == completed["n_id"] for e in full.audit)
    new_n = next(e for e in full.audit if e["event"] == "n_completed" and e["bar_index"] == day
                 and e.get("target_primary") is False and e["target_eligible"])
    assert new_n["one_p"] > bars[day].high
    assert any(e["event"] == "a_wave_confirmed" and e["source_id"] == new_n["n_id"]
               and e["bar_index"] == dates["2026-09-17"] for e in full.audit)
    for end in (dates["2026-08-21"], day - 1, day):
        prefix = generate_system_signals(bars[: end + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]


def test_strong_a_body_confirmation_can_advance_to_later_higher_gap():
    bars, dates, setup = sample()
    body, gap = dates["2026-08-26"], dates["2026-09-15"]
    # A completed early observation can confirm the body route even if the
    # final August candle no longer has a qualifying body.
    bars[body] = replace(bars[body], close=16.70, volume=2_100_000)
    config = SystemStrategy(
        pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3", first_pullback_threshold=None,
        strict_n_attack_quality=False, preflight_reward_risk=False, volume_filter=True,
    )
    snapshots = pivot_history(bars, config)[0]
    # Observe the explicitly supplied N independently of formal N eligibility;
    # the global pipeline's real completion is covered in the preceding test.
    confirmations = [wave_gap_entry(bars, setup, index, pivots=snapshots[index - 1]) for index in (body, gap)]
    assert all(proof is not None for proof in confirmations)
    assert [proof["wave_confirmation_phase"] for proof in confirmations] == ["body", "gap"]
    assert confirmations[0]["wave_a_high_index"] == confirmations[1]["wave_a_high_index"]
    assert confirmations[0]["wave_b_low_index"] == confirmations[1]["wave_b_low_index"]
    assert confirmations[1]["wave_gap_high"] > confirmations[0]["wave_gap_high"]
    assert wave_confirmation_is_new(confirmations[0], None)
    assert wave_confirmation_is_new(confirmations[1], wave_confirmation_state(confirmations[0]))
    assert not wave_confirmation_is_new(confirmations[1], wave_confirmation_state(confirmations[1]))
    for index, confirmation in zip((body, gap), confirmations, strict=True):
        prefix = bars[: index + 1]
        known_pivots = pivot_history(prefix, config)[0][index - 1]
        assert wave_gap_entry(prefix, setup, index, pivots=known_pivots) == confirmation


def test_strong_a_confirmation_requires_strictly_higher_gap_and_one_fill_per_phase():
    body = {"wave_confirmation_phase": "body", "wave_gap_high": 18.0}
    prior_body = wave_confirmation_state(body)
    assert not wave_confirmation_is_new({"wave_confirmation_phase": "body", "wave_gap_high": 19.0}, prior_body)
    assert not wave_confirmation_is_new({"wave_confirmation_phase": "gap", "wave_gap_high": 18.0}, prior_body)
    higher_gap = {"wave_confirmation_phase": "gap", "wave_gap_high": 18.01}
    assert wave_confirmation_is_new(higher_gap, prior_body)
    assert not wave_confirmation_is_new(higher_gap, wave_confirmation_state(higher_gap))
    assert not wave_confirmation_is_new(body, wave_confirmation_state(higher_gap))
