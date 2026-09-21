from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.market_state.market_regime import MarketRegime
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_small_low_volume_n_can_wait_for_squeeze_without_strict_quality_filter():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2025_november.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
        strict_n_attack_quality=False,
    )
    full = generate_system_signals(bars, config)
    n = next(
        e
        for e in full.audit
        if e["event"] == "n_completed" and e["direction"] == "up" and e["timestamp"].startswith("2025-11-03")
    )
    confirmation = [e for e in full.audit if e["event"] == "regime_confirmation" and e["attack"] == n["bar_index"]]
    assert confirmation[0]["timestamp"].startswith("2025-11-05")
    assert confirmation[0]["regime"] == MarketRegime.STRONG_BULL.value
    assert not any(s.side == "LONG" and s.timestamp.date().isoformat() == "2025-11-03" for s in full.signals)
    strict = generate_system_signals(bars, replace(config, strict_n_attack_quality=True))
    assert any(e["event"] == "n_attack_rejected" and e["timestamp"].startswith("2025-11-03") for e in strict.audit)
    for date in ("2025-11-03", "2025-11-05"):
        cutoff = next(i for i, b in enumerate(bars) if b.timestamp.date().isoformat() == date)
        prefix = generate_system_signals(bars[: cutoff + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
        assert [e for e in prefix.audit if e["event"] == "regime_confirmation"] == [
            e for e in full.audit if e["event"] == "regime_confirmation" and e["bar_index"] <= cutoff
        ]


def test_v3_profile_separates_structural_n_from_optional_quality():
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    assert profile["strategy"]["strict_n_attack_quality"] is False
    assert profile["strategy"]["regime_filter"] is True
    assert profile["definition"]["minimum_attack_body_open_fraction"] is None


def test_weak_n_local_rebound_alone_does_not_gain_entry_permission():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2022_hierarchy.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    result = generate_system_signals(
        bars,
        SystemStrategy(
            pivot_mode="lecture_causal",
            entry_policy="hierarchical_two_buy_points",
            buy_point_definition="whole_flip_wave_v3",
            volume_filter=False,
            strict_n_attack_quality=False,
        ),
    )
    index = next(i for i, b in enumerate(bars) if b.timestamp.date().isoformat() == "2022-08-23")
    assert bars[index].open < bars[index - 1].close
    # Still a lower-open resistance: reject before it reaches entry quality.
    assert not any(e["event"] == "regime_confirmation" and e["bar_index"] == index for e in result.audit)
    assert not any(s.side == "LONG" and s.timestamp.date().isoformat() == "2022-08-23" for s in result.signals)
