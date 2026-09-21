from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def test_guilin_inverse_n_ends_old_squeeze_without_future_selloff():
    raw = json.loads((Path(__file__).parent / "fixtures/guilin_2025_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
    )
    full = generate_system_signals(bars, config)
    n = next(
        e
        for e in full.audit
        if e["event"] == "n_completed" and e["direction"] == "up" and e["timestamp"].startswith("2025-02-10")
    )
    inverse = next(
        e
        for e in full.audit
        if e["event"] == "n_completed" and e["direction"] == "down" and e["timestamp"].startswith("2025-02-14")
    )
    invalidation = next(
        e for e in full.audit if e["event"] == "n_squeeze_invalidated" and e["attack"] == n["bar_index"]
    )
    assert invalidation["bar_index"] == inverse["bar_index"]
    assert not any(
        e["event"] in ("regime_confirmation", "long_signal", "squeeze_resumption_observed")
        and e.get("attack") == n["bar_index"]
        and e["bar_index"] >= inverse["bar_index"]
        for e in full.audit
    )
    assert not any(s.side == "LONG" and s.timestamp.date().isoformat() == "2025-02-17" for s in full.signals)
    for day in ("2025-02-13", "2025-02-14", "2025-02-17"):
        cutoff = next(i for i, b in enumerate(bars) if b.timestamp.date().isoformat() == day)
        prefix = generate_system_signals(bars[: cutoff + 1], config)
        assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
        assert [e for e in prefix.audit if e["event"] == "n_squeeze_invalidated"] == [
            e for e in full.audit if e["event"] == "n_squeeze_invalidated" and e["bar_index"] <= cutoff
        ]
