from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.abc_candidate import AbcAnchor, abc_pullback_evidence
from wavequant.domain.strategies.inverse_reentry import deep_pullback_recovery, inverse_reentry_rejection
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def recovery_case():
    bars = [Bar(datetime(2020, 1, 1) + timedelta(days=i), "TEST", 15, 18, 14, 16, 1000) for i in range(10)]
    bars[6] = replace(bars[6], low=13)
    proof = dict(
        definition="whole_flip_wave_v3",
        trend_level=3,
        origin_index=0,
        flip_high_index=4,
        origin_price=10,
        flip_high_price=20,
        alternation_low_index=6,
        alternation_index=9,
    )
    inverse = [dict(attack=6, known_at=6, b_index=5, b_high=19, kill_high=15)]
    return bars, dict(now=9, attack=7, inverse=inverse, alternation=proof, record_break=True)


def test_deep_recovery_reclaims_kill_high_without_requiring_old_b_high():
    bars, args = recovery_case()
    assert inverse_reentry_rejection(bars, **{k: args[k] for k in ("now", "attack", "inverse")})
    result = deep_pullback_recovery(bars, **args)
    assert result["recovery_whole_retracement"] == 0.7
    assert result["recovery_kill_high"] == 15


def test_new_deep_path_is_opt_in_and_does_not_replace_existing_price_or_time_paths():
    bars, _ = recovery_case()
    anchor = AbcAnchor(0, 4, 4, 10, 20, "test")
    assert abc_pullback_evidence(bars, anchor, 6) is None
    deep = abc_pullback_evidence(bars, anchor, 6, allow_deep_pullback=True)
    assert deep is not None and deep["deep_price_path"]
    equality = replace(anchor, high_price=19)
    equal = abc_pullback_evidence(bars, equality, 6, allow_deep_pullback=True)
    assert equal is not None and not equal.get("deep_price_path")
    bars[6] = replace(bars[6], close=14)
    timed = replace(anchor, high_index=2)
    evidence = abc_pullback_evidence(bars, timed, 6, allow_deep_pullback=True)
    assert evidence is not None and evidence["time_path"] and not evidence.get("deep_price_path")


@pytest.mark.parametrize(
    "invalid",
    [
        "no_record",
        "old_n",
        "future_context",
        "shallow",
        "broken_b",
        "kill_touch",
        "short_duration",
        "later_inverse",
        "wrong_level",
    ],
)
def test_deep_recovery_never_waives_required_evidence(invalid):
    bars, args = recovery_case()
    if invalid == "no_record":
        args["record_break"] = False
    elif invalid == "old_n":
        args["attack"] = 5
    elif invalid == "future_context":
        args["alternation"]["alternation_index"] = 10
    elif invalid == "shallow":
        bars[6] = replace(bars[6], low=14)
    elif invalid == "broken_b":
        bars[8] = replace(bars[8], low=12)
    elif invalid == "kill_touch":
        bars[9] = replace(bars[9], close=15)
    elif invalid == "short_duration":
        args["alternation"]["flip_high_index"] = 5
    elif invalid == "later_inverse":
        args["inverse"].append(dict(attack=8, known_at=8, b_index=7, b_high=19, kill_high=15))
    elif invalid == "wrong_level":
        args["alternation"]["trend_level"] = 1
    assert deep_pullback_recovery(bars, **args) is None


def attack_bar_recovery_case():
    bars, args = recovery_case()
    bars[7] = replace(bars[7], high=17, close=15.5)
    bars[8] = replace(bars[8], high=18.5)
    bars[9] = replace(bars[9], open=16, high=17.8, low=15.9, close=17.6)
    args.update(record_break=False, attack_bar_break=True)
    return bars, args


@pytest.mark.parametrize("level", [2, 3])
def test_attack_bar_deep_recovery_keeps_confirmed_deep_structure_below_later_record(level: int) -> None:
    bars, args = attack_bar_recovery_case()
    args["alternation"]["trend_level"] = level
    assert bars[9].high < bars[8].high
    assert bars[9].close < args["inverse"][0]["b_high"]
    assert inverse_reentry_rejection(bars, **{key: args[key] for key in ("now", "attack", "inverse")})
    result = deep_pullback_recovery(bars, **args)
    assert result is not None
    assert result["inverse_reentry_path"] == "deep_alternation_kill_high_attack_bar_squeeze"
    assert result["recovery_whole_retracement"] == 0.7
    assert result["recovery_kill_high"] == 15
    args["record_break"] = True
    assert deep_pullback_recovery(bars, **args)["inverse_reentry_path"] == "deep_alternation_kill_high_record_squeeze"


@pytest.mark.parametrize("invalid", [
    "missing_confirmation", "high_touch", "high_below", "close_touch", "close_below",
    "doji", "bearish", "small_body", "small_body_fraction", "long_upper_shadow",
    "previous_close_touch", "next_session", "old_n", "future_context",
    "shallow", "broken_b", "kill_touch", "short_duration", "later_inverse", "wrong_level",
])
def test_attack_bar_deep_recovery_rechecks_breaks_and_keeps_all_structural_gates(invalid: str) -> None:
    bars, args = attack_bar_recovery_case()
    if invalid == "missing_confirmation":
        args["attack_bar_break"] = False
    elif invalid == "high_touch":
        bars[9] = replace(bars[9], high=17, close=17)
    elif invalid == "high_below":
        bars[9] = replace(bars[9], high=16.99, close=16.98)
    elif invalid in ("close_touch", "close_below"):
        bars[7] = replace(bars[7], high=17.4, close=17.3)
        bars[9] = replace(bars[9], high=17.6, close=17.3 if invalid == "close_touch" else 17.29)
    elif invalid == "doji":
        bars[9] = replace(bars[9], open=17.6)
    elif invalid == "bearish":
        bars[9] = replace(bars[9], open=17.7)
    elif invalid == "small_body":
        bars[9] = replace(bars[9], open=17.2, high=17.65, low=17.2)
    elif invalid == "small_body_fraction":
        bars[9] = replace(bars[9], open=16.8, high=17.7, low=16)
    elif invalid == "long_upper_shadow":
        bars[9] = replace(bars[9], high=18.3, low=16)
    elif invalid == "previous_close_touch":
        bars[8] = replace(bars[8], close=17.6)
    elif invalid == "next_session":
        args["now"] = 8
        args["alternation"]["alternation_index"] = 8
        bars[8] = replace(bars[8], open=16, low=15.9, close=18.2)
    elif invalid == "old_n":
        args["attack"] = 5
    elif invalid == "future_context":
        args["alternation"]["alternation_index"] = 10
    elif invalid == "shallow":
        bars[6] = replace(bars[6], low=14)
    elif invalid == "broken_b":
        bars[8] = replace(bars[8], low=12)
    elif invalid == "kill_touch":
        args["inverse"][0]["kill_high"] = 17.6
    elif invalid == "short_duration":
        args["alternation"]["flip_high_index"] = 5
    elif invalid == "later_inverse":
        args["inverse"].append(dict(attack=8, known_at=8, b_index=7, b_high=19, kill_high=15))
    elif invalid == "wrong_level":
        args["alternation"]["trend_level"] = 1
    assert deep_pullback_recovery(bars, **args) is None


def test_attack_bar_deep_recovery_keeps_prefix_causality_and_inclusive_depth() -> None:
    bars, args = attack_bar_recovery_case()
    args["alternation"]["origin_price"] = 11
    bars[6] = replace(bars[6], low=14)
    result = deep_pullback_recovery(bars, **args)
    assert result is not None and result["recovery_whole_retracement"] == pytest.approx(2/3)
    bars.append(replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1), low=12))
    args["inverse"].append(dict(attack=8, known_at=10, b_index=7, b_high=19, kill_high=15))
    assert deep_pullback_recovery(bars, **args) == result


def test_record_deep_recovery_keeps_its_existing_path_without_new_shape_gate() -> None:
    bars, args = attack_bar_recovery_case()
    args.update(record_break=True, attack_bar_break=False)
    bars[8] = replace(bars[8], high=16.5)
    bars[9] = replace(bars[9], open=17.5, high=18.2, low=17.5)
    result = deep_pullback_recovery(bars, **args)
    assert result is not None
    assert result["inverse_reentry_path"] == "deep_alternation_kill_high_record_squeeze"


def test_xidian_deep_b_record_squeeze_is_august7_and_prefix_stable():
    raw = json.loads((Path(__file__).parent / "fixtures/xidian_2026_deep_squeeze.json").read_text(encoding="utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"] if day <= "2026-08-10"
    ]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    signal = next(s for s in full.signals if s.side == "LONG" and str(s.timestamp.date()) == "2026-08-07")
    assert str(signal.trigger_timestamp.date()) == "2026-08-03"
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-08-06" for s in full.signals)
    proof = next(
        e for e in full.audit if e["event"] == "long_transition_evidence" and e["bar_index"] == signal.bar_index
    )
    assert proof["confirmation_source"] == "resistance_record_break"
    assert proof["recovery_whole_retracement"] == pytest.approx(0.6790134955)
    assert proof["recovery_kill_high"] == pytest.approx(24.8662777445)
    assert proof["recovery_resistance_high"] == pytest.approx(25.8282427322)
    assert proof["alternation_index"] == signal.bar_index
    assert str(bars[proof["origin_index"]].timestamp.date()) == "2024-02-08"
    assert str(bars[proof["flip_high_index"]].timestamp.date()) == "2025-08-11"
    assert str(bars[proof["alternation_low_index"]].timestamp.date()) == "2026-07-21"
    prefix = generate_system_signals(bars[: signal.bar_index + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= signal.bar_index]
