from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
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
