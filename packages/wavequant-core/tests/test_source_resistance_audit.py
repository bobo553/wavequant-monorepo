import json
from datetime import datetime
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.secondary_resistance import secondary_resistance_history
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_xianfeng_source_high_is_checked_and_june9_actually_resolves_resistance():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], *values) for d, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    full = generate_system_signals(bars, config)
    events = [e for e in full.audit if e["event"] == "hierarchy_resistance_key"]
    evidence = secondary_resistance_history(bars, {}, key_events=events, include_resolved=True)
    at = {str(b.timestamp.date()): i for i, b in enumerate(bars)}
    for day in ("2026-05-21", "2026-05-22", "2026-05-26", "2026-06-08"):
        assert evidence[at[day]]["secondary_high_date"] == "2026-02-02"
        assert evidence[at[day]]["secondary_high"] == 5.99
        assert not evidence[at[day]].get("secondary_resistance_resolved")
    resolved = evidence[at["2026-06-09"]]
    assert resolved["secondary_resistance_resolved"]
    assert resolved["secondary_attack_date"] == "2026-05-25"
    assert resolved["secondary_resistance_high"] == 6.66
    assert resolved["secondary_confirmation_close"] == 6.94
    # Clearing resistance cannot restore a target source retired by a confirmed decline.
    assert not any(s.side == "LONG" and s.bar_index == at["2026-06-09"] for s in full.signals)
    assert any(e["event"] == "entry_rejected" and e["bar_index"] == at["2026-06-09"]
               and e.get("attack") == at["2026-05-25"]
               and e["reason"] == "no_live_structural_risk_reward" for e in full.audit)
    assert any(e["event"] == "n_target_source_retired" and e["bar_index"] == at["2026-06-01"]
               and e["attack"] == at["2026-05-18"] and e["reason"] == "new_confirmed_decline"
               for e in full.audit)
    current_n = next(e for e in full.audit if e["event"] == "n_completed"
                     and e["bar_index"] == at["2026-06-09"] and e.get("target_primary"))
    assert current_n["target_eligible"] is False
    assert current_n["target_source_attack"] is None
    assert current_n["one_p"] is None and current_n["two_t"] is None
    prefix = generate_system_signals(bars[:-1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index < len(bars) - 1]
    assert not any(e.get("secondary_resistance_resolved") for e in prefix.audit if e["timestamp"].startswith("2026-06"))
