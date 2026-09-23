from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.wave_continuation import wave_gap_entry


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


def test_real_gap_keeps_whole_b_low_and_equal_a_target():
    bars, dates, setup = sample()
    now = dates["2026-09-15"]
    proof = wave_gap_entry(bars, setup, now)
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
        "doji",
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
    elif case == "doji":
        bars[now] = replace(bars[now], open=bars[now].close)
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
    assert proof["wave_b_low_date"] == "2026-08-21"
    assert proof["rvol"] > config.minimum_rvol
    for end in (dates["2026-08-21"], day - 1, day):
        prefix = generate_system_signals(bars[: end + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]
