from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from dataclasses import replace
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.secondary_resistance import secondary_resistance_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def test_resisted_secondary_break_needs_later_confirmation_not_same_or_next_bar():
    rows = [(9, 10, 8, 9), (9, 9.5, 8.8, 9.4), (9.6, 11, 9.5, 10.1), (10.2, 11.1, 10, 10.3), (10.4, 11.5, 10.3, 11.4)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    history = {i: {2: [dict(index=0, kind="H", value=10)]} for i in range(len(bars))}
    result = secondary_resistance_history(bars, history)
    assert list(result) == [2, 3]
    assert secondary_resistance_history(bars[:4], history) == result
    assert 4 in secondary_resistance_history(bars[:4] + [replace(bars[4], close=11.1)], history)
    assert secondary_resistance_history(bars, {}) == {}


def test_lexin_july2_does_not_borrow_unconfirmed_secondary_pressure():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    config = SystemStrategy(
        **(whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"] | {"volume_filter": False})
    )
    result = generate_system_signals(bars, config)
    assert not any(s.side == "LONG" and str(s.timestamp.date()) == "2026-07-02" for s in result.signals)
    cutoff = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2026-07-02")
    key = next(e["key"] for e in reversed(result.audit) if e["event"] == "hierarchy_resistance_key"
               and e["bar_index"] < cutoff)
    # The latest confirmed secondary pressure is higher than this local rally;
    # April 30 cannot replace it merely because it is a newer source high.
    assert str(bars[key["index"]].timestamp.date()) == "2026-01-14"
    assert key["value"] > bars[cutoff].high
    rejections = [e for e in result.audit if e["event"] == "entry_rejected" and e["bar_index"] == cutoff]
    assert any(e["reason"] == "wave_second_peak_pullback_sequence" for e in rejections)
    assert not any(e["reason"] == "secondary_breakout_resistance_unresolved" for e in rejections)
    prefix = generate_system_signals(bars[: cutoff + 1], config)
    assert prefix.signals == [s for s in result.signals if s.bar_index <= cutoff]
    assert all(rejection in prefix.audit for rejection in rejections)
    august = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2026-08-04")
    july_attack = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2026-07-24")
    # A deep observed B cannot create a level-two background before either
    # permitted trend certificate exists. Independent N/A measurements survive.
    assert not any(s.side == "LONG" and s.bar_index == august for s in result.signals)
    assert any(e["event"] == "entry_rejected" and e["bar_index"] == august
               and e.get("attack") == july_attack and e["reason"] == "wave_second_peak_pullback_sequence"
               for e in result.audit)
    source_n = next(e for e in result.audit if e["event"] == "n_completed" and e["bar_index"] == july_attack
                    and e.get("direction") == "up")
    assert source_n["target_eligible"] is True
    assert source_n["target_qualification_reason"] == "first_n_at_decline_floor"
    assert str(bars[source_n["origin"]].timestamp.date()) == "2026-07-21"
    assert str(bars[source_n["target_decline_index"]].timestamp.date()) == "2026-07-03"
    assert source_n["one_p"] == pytest.approx(15.74203411137645)
    assert source_n["two_t"] == pytest.approx(17.402495243672323)
    measured_a = next(e for e in result.audit if e["event"] == "a_wave_confirmed" and e["bar_index"] == august
                      and e["source_id"] == source_n["n_id"])
    assert measured_a["a_class"] == "ordinary"
    assert measured_a["origin_index"] == source_n["origin"]
    assert measured_a["attack_index"] == july_attack
    assert measured_a["confirmed_index"] == august
    assert (measured_a["one_p"], measured_a["two_t"]) == (source_n["one_p"], source_n["two_t"])


def _level_points(level: Mapping[str, object], field: str) -> list[Mapping[str, object]]:
    paths = cast(Sequence[Mapping[str, object]], level[field])
    return [point for path in paths for point in cast(Sequence[Mapping[str, object]], path["points"])]


def test_lexin_august4_preserves_the_qualified_primary_a_without_inventing_a_secondary_rise() -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2026-08-04"]
    first = reversal_trends(lecture_drawing(bars), bars)
    second = secondary_trends(first, bars)
    primary = _level_points(first, "strokes")
    primary_structure = _level_points(first, "structure_strokes")
    secondary = _level_points(second, "strokes")
    secondary_structure = _level_points(second, "structure_strokes")

    origin = next(point for point in primary if point["time"] == "2026-06-12" and point["kind"] == "L")
    proof = cast(Mapping[str, object], origin["trend_confirmation"])
    key, trigger = cast(Mapping[str, object], proof["broken_key"]), cast(Mapping[str, object], proof["confirmed_by"])
    assert (origin["available_at"], proof["available_at"]) == ("2026-07-02", "2026-07-02")
    assert (proof["direction"], proof["confirmation_rule"]) == ("up", "strict_same_level_market_key_break")
    assert (key["time"], key["available_at"]) == ("2026-04-30", "2026-05-13")
    assert trigger["time"] == "2026-07-02"
    assert cast(float, trigger["value"]) > cast(float, key["value"])

    candidate = next(point for point in secondary_structure if point["index"] == origin["index"])
    assert candidate["available_at"] == "2026-07-23"
    assert candidate.get("trend_confirmation") is None
    assert not any(point["index"] == origin["index"] for point in secondary)
    source_a = next(point for point in primary if point["time"] == "2026-07-03" and point["kind"] == "H")
    assert source_a["available_at"] == "2026-07-23"
    candidate_key = cast(Mapping[str, object], candidate["broken_key"])
    assert (candidate_key["time"], candidate_key["value"]) == (key["time"], key["value"])
    pressure = next(point for point in secondary_structure if point["time"] == "2026-01-14")
    assert pressure["available_at"] == "2026-04-17"
    assert cast(float, source_a["value"]) < cast(float, pressure["value"])
    observed_b = next(point for point in primary_structure if point["time"] == "2026-07-21")
    counter = ((cast(float, source_a["value"]) - cast(float, observed_b["value"]))
               / (cast(float, source_a["value"]) - cast(float, origin["value"])))
    assert counter == pytest.approx(0.9540229885057475)
    assert 2 / 3 < counter < 1
    assert bars[-1].close < cast(float, source_a["value"])
    assert any(point["time"] == "2026-04-30" for point in primary_structure)
    assert not any(point["time"] == "2026-04-30" for point in [*secondary, *secondary_structure])


def test_source_key_is_causal_and_new_resistance_high_does_not_cancel_pending_gate():
    rows = [
        (9, 10, 8, 9),
        (9, 9.5, 8.8, 9.4),
        (9.6, 11, 9.5, 10.1),
        (10.2, 11.1, 10, 10.3),
        (10.4, 11.2, 10.3, 10.5),
        (10.6, 11.6, 10.5, 11.5),
    ]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    history = {i: {2: [dict(index=0, kind="H", value=8)]} for i in range(len(bars))}
    events = [
        dict(bar_index=1, event="hierarchy_resistance_key", key=dict(index=0, value=10)),
        dict(bar_index=3, event="hierarchy_resistance_key", key=dict(index=2, value=11)),
    ]
    result = secondary_resistance_history(bars, history, key_events=events, include_resolved=True)
    assert 1 not in result
    assert not result[4].get("secondary_resistance_resolved")
    assert result[4]["secondary_high"] == 10
    assert result[5]["secondary_resistance_resolved"]
    assert result[5]["secondary_resistance_high"] == 11.2
    assert result[5]["secondary_confirmation_close"] == 11.5
    assert secondary_resistance_history(bars[:5], history, key_events=events, include_resolved=True) == {
        i: e for i, e in result.items() if i < 5
    }
    # A future key event cannot retroactively make the earlier breakout known.
    assert secondary_resistance_history(bars[:3], history, key_events=[dict(events[0], bar_index=3)]) == {}
    stronger = {i: {2: [dict(index=0, kind="H", value=12)]} for i in range(len(bars))}
    assert secondary_resistance_history(bars, stronger, key_events=events) == {}
