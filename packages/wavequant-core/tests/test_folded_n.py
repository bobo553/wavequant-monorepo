from datetime import datetime
import json
from pathlib import Path

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.strategies.folded_n import folded_positive_n_candidates
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_folded_n_requires_unbroken_origin_and_historical_neckline():
    def point(index, kind, price):
        return ReversalPoint(LinePoint(index, 0, kind, price), index + 1, "test")

    low, high = PointKind.LOW, PointKind.HIGH
    points = [
        point(0, low, 5),
        point(1, high, 8),
        point(2, low, 6),
        point(3, high, 10),
        point(4, low, 7),
        point(5, high, 9),
        point(6, low, 5.5),
    ]
    rows = folded_positive_n_candidates(points, earliest=0)
    assert any(tuple(p.point.index for p in refs) == (0, 3, 6) for refs, _ in rows)
    assert not folded_positive_n_candidates(points, earliest=2)
    assert all(
        refs[1].point.index != 3
        for refs, _ in folded_positive_n_candidates([*points[:-1], point(6, low, 5)], earliest=0)
    )
    crossed = [*points, point(7, high, 11), point(8, low, 7)]
    assert all(
        refs[1].point.index != 3 or refs[2].point.index < 7
        for refs, _ in folded_positive_n_candidates(crossed, earliest=0)
    )


def test_guofang_july29_n_survives_internal_smaller_swings():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2026_consolidation.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | dict(volume_filter=False))
    )
    full = generate_system_signals(bars, config)
    event = next(
        e
        for e in full.audit
        if e["event"] == "n_completed" and e["direction"] == "up" and e["timestamp"].startswith("2026-07-29")
    )
    assert bars[event["neckline"]].timestamp.date().isoformat() == "2026-07-17"
    assert bars[event["pullback"]].timestamp.date().isoformat() == "2026-07-27"
    cutoff = event["bar_index"]
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert event in prefix.audit
    assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
    gap = next(e for e in full.audit if e["event"] == "long_signal" and e["timestamp"].startswith("2026-08-28"))
    assert gap["squeeze_confirmation"] == "defended_n_consolidation_gap"
    assert gap["attack"] == event["bar_index"]
    evidence = next(
        e for e in full.audit if e["event"] == "long_transition_evidence" and e["timestamp"].startswith("2026-08-28")
    )
    assert evidence["alternation_index"] == gap["bar_index"]
    assert evidence["joint_alternation_confirmation"]
    assert evidence["confirmation_attack"] == event["bar_index"]
    assert bars[evidence["alternation_low_index"]].timestamp.date().isoformat() == "2026-06-29"
    # July 16 is itself resistance and cannot establish a new June 29 b context.
    assert not any(
        e["event"] == "squeeze_alternation_confirmed" and e["timestamp"].startswith("2026-07-16") for e in full.audit
    )
    cutoff = gap["bar_index"]
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= cutoff]
    assert gap in prefix.audit
