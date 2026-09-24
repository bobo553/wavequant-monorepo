from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.completed_wave_recovery import secondary_wave_recovery, inverse_wave_recovery


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/shilian_wave_continuation.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    return bars, dates


def config():
    return SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        first_pullback_threshold=None,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
        volume_filter=False,
    )


def test_completed_a_b_volume_gap_recovers_local_pressure_and_inverse():
    bars, dates = sample()
    full = generate_system_signals(bars, config())
    day = dates["2020-07-20"]
    buys = [s for s in full.signals if s.side == "LONG" and s.bar_index == day]
    assert len(buys) == 1
    assert buys[0].reason == "system_wave_push_gap"
    assert buys[0].target_price == pytest.approx(3.983199181073716)
    proof = next(e for e in full.audit if e["event"] == "long_signal" and e["bar_index"] == day)
    assert proof["wave_entry_n_date"] == "2020-06-01"
    assert proof["wave_entry_two_t_date"] == "2020-07-03"
    assert proof["wave_a_high_date"] == "2020-07-06"
    assert proof["wave_b_low_date"] == "2020-07-17"
    assert proof["wave_secondary_recovery"] == "completed_a_defended_b_gap_attack"
    transition = next(e for e in full.audit if e["event"] == "long_transition_evidence" and e["bar_index"] == day)
    assert transition["inverse_reentry_path"] == "completed_a_defended_b_gap_attack"
    assert transition["recovery_inverse_date"] == "2020-07-15"
    for end in (dates["2020-07-17"], day):
        prefix = generate_system_signals(bars[: end + 1], config())
        assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
        assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]


@pytest.fixture(scope="module")
def evidence():
    bars, dates = sample()
    now = dates["2020-07-20"]
    result = generate_system_signals(bars, config())
    wave = next(e for e in result.audit if e["event"] == "long_signal" and e["bar_index"] == now)
    pressure = dict(
        secondary_attack_date="2020-07-02",
        secondary_current_resistance=False,
        secondary_high=3.154911993547389,
        secondary_resistance_high=3.5564278402443894,
        secondary_defense=2.936593273801796,
    )
    inverse = dict(
        known_at=dates["2020-07-15"], attack=dates["2020-07-15"], b_index=dates["2020-07-14"], b_high=3.444654393836709
    )
    return bars, dates, now, wave, pressure, inverse


@pytest.mark.parametrize(
    "case",
    [
        "no_wave",
        "ordinary_gap",
        "new_attack",
        "old_attack",
        "higher_record",
        "higher_level",
        "broken_pressure_defense",
        "unrecovered_level",
        "current_resistance",
    ],
)
def test_completed_wave_does_not_waive_independent_pressure(evidence, case):
    bars, dates, now, wave, pressure, _ = evidence
    wave, pressure = dict(wave), dict(pressure)
    assert secondary_wave_recovery(bars, now, wave, pressure)
    if case == "no_wave":
        wave = None
    elif case == "ordinary_gap":
        wave["wave_entry_path"] = "defended_n_consolidation_gap"
    elif case == "new_attack":
        pressure["secondary_attack_date"] = "2020-07-20"
    elif case == "old_attack":
        pressure["secondary_attack_date"] = "2020-05-29"
    elif case == "higher_record":
        pressure["secondary_resistance_high"] = wave["wave_a_high"] + 0.01
    elif case == "higher_level":
        pressure["secondary_high"] = wave["wave_a_high"] + 0.01
    elif case == "broken_pressure_defense":
        pressure["secondary_defense"] = wave["wave_b_low"] + 0.01
    elif case == "unrecovered_level":
        pressure["secondary_high"] = bars[now].close
    else:
        pressure["secondary_current_resistance"] = True
    assert secondary_wave_recovery(bars, now, wave, pressure) is None


@pytest.mark.parametrize(
    "case", ["no_wave", "ordinary_gap", "before_a", "after_b", "same_day", "higher_b", "b_before_a", "no_inverse"]
)
def test_completed_wave_does_not_waive_unrelated_inverse(evidence, case):
    bars, dates, now, wave, _, inverse = evidence
    wave, inverse = dict(wave), dict(inverse)
    assert inverse_wave_recovery(bars, now, wave, [inverse])
    if case == "no_wave":
        wave = None
    elif case == "ordinary_gap":
        wave["wave_entry_path"] = "defended_n_consolidation_gap"
    elif case == "before_a":
        inverse.update(attack=dates["2020-07-03"], known_at=dates["2020-07-03"], b_index=dates["2020-07-02"])
    elif case == "after_b":
        inverse.update(attack=now, known_at=now)
    elif case == "same_day":
        inverse["known_at"] = now
    elif case == "higher_b":
        inverse["b_high"] = wave["wave_a_high"] + 0.01
    elif case == "b_before_a":
        inverse["b_index"] = dates["2020-07-03"]
    assert inverse_wave_recovery(bars, now, wave, [] if case == "no_inverse" else [inverse]) is None


def test_latest_inverse_wins_and_future_inverse_is_not_known(evidence):
    bars, _, now, wave, _, inverse = evidence
    future = dict(inverse, attack=now + 1, known_at=now + 1)
    assert inverse_wave_recovery(bars, now, wave, [inverse, future])
    latest = dict(inverse, attack=now, known_at=now)
    assert inverse_wave_recovery(bars, now, wave, [inverse, latest]) is None


def test_optional_filters_remain_effective_and_rule_is_symbol_independent():
    from dataclasses import replace

    bars, dates = sample()
    day = dates["2020-07-20"]
    bars = [replace(b, symbol="sh.600000") for b in bars[: day + 1]]
    assert any(s.side == "LONG" and s.bar_index == day for s in generate_system_signals(bars, config()).signals)
    assert any(
        s.side == "LONG" and s.bar_index == day
        for s in generate_system_signals(bars, replace(config(), volume_filter=True)).signals
    )
    for changes, reason in ((dict(preflight_reward_risk=True), "insufficient_close_gross_reward_risk"),):
        result = generate_system_signals(bars, replace(config(), **changes))
        assert not any(s.side == "LONG" and s.bar_index == day for s in result.signals)
        assert any(e.get("reason") == reason and e["bar_index"] == day for e in result.audit)


@pytest.mark.parametrize("increment, expected", [(0, False), (1, True)])
def test_v3_volume_is_strictly_above_yesterday_not_twenty_day_mean(increment, expected):
    from dataclasses import replace

    bars, dates = sample()
    now = dates["2020-07-20"]
    bars = bars[: now + 1]
    bars[-1] = replace(bars[-1], volume=bars[-2].volume + increment)
    result = generate_system_signals(bars, replace(config(), volume_filter=True, minimum_rvol=1.2))
    buys = [s for s in result.signals if s.bar_index == now and s.side == "LONG"]
    assert bool(buys) is expected
    if expected:
        assert 1 < buys[0].rvol < 1.2
