from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.chart_entry_history import chart_entry_history
from wavequant.domain.strategies.combined_a_entry import (
    CombinedAContext,
    CombinedAHigh,
    GeometryLevel,
    GeometryPoint,
    combined_a_contexts_from_geometry,
    combined_a_entry_history,
)
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def setup():
    candles = [
        (4.5, 5, 4, 4.5), (6, 8, 5.8, 7.5), (7.2, 7.5, 6.8, 7),
        (6.5, 7, 6, 6.5), (8, 9, 7.8, 8.5), (6.5, 6.8, 6, 6.3),
        (6.7, 7.3, 6.6, 7.1), (7.1, 7.2, 6.8, 7), (7, 7.7, 6.9, 7.6),
    ]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), "TEST", *candle,
                200 if index == 8 else 100) for index, candle in enumerate(candles)]
    context = CombinedAContext("source", 0, 4, 1, 8, 3, 6, 4, 9, 5, 2, (3,),
                               (CombinedAHigh(6, 7.3, 7),))
    return bars, {index: (context,) for index in range(5, len(bars))}, context


def test_duration_counts_consolidation_and_uses_strict_or():
    bars, history, context = setup()
    _, proofs = combined_a_entry_history(bars, history)
    assert proofs[8]["combined_a_pullback_sessions"] == 4
    assert proofs[8]["pullback_low_index"] == 5
    # Equality to both alternatives fails, even with a qualified candle.
    equal = replace(context, a_index=0, b_index=4, child_pullback_durations=(4,))
    _, denied = combined_a_entry_history(bars, {index: (equal,) for index in history})
    assert denied == {}
    child = replace(equal, child_pullback_durations=(3,))
    _, accepted = combined_a_entry_history(bars, {index: (child,) for index in history})
    assert accepted[8]["duration_exceeded_internal_b"] is False
    assert accepted[8]["duration_exceeded_child_n"] is True


@pytest.mark.parametrize("change", ["volume_equal", "previous_volume_zero", "short_body", "thin_body", "future_high"])
def test_breakout_requires_known_high_and_every_candle_condition(change):
    bars, history, context = setup()
    if change == "volume_equal":
        bars[8] = replace(bars[8], volume=100)
    elif change == "previous_volume_zero":
        bars[7] = replace(bars[7], volume=0)
    elif change == "short_body":
        bars[8] = replace(bars[8], open=7.5)
    elif change == "thin_body":
        bars[8] = replace(bars[8], high=8.2)
    else:
        context = replace(context, rebound_highs=(CombinedAHigh(6, 7.3, 8),))
        history = {index: (context,) for index in history}
    _, proofs = combined_a_entry_history(bars, history)
    assert proofs == {}


@pytest.mark.parametrize("broken", ["floor", "origin"])
def test_lost_defense_does_not_revive_after_price_recovers(broken):
    bars, history, _ = setup()
    bars[5] = replace(bars[5], close=5.5, low=5.4) if broken == "floor" else replace(bars[5], low=3.9)
    events, proofs = combined_a_entry_history(bars, history)
    assert proofs == {}
    failures = [event for event in events if event["event"] == "combined_a_invalidated"]
    assert len(failures) == 1
    assert failures[0]["invalidated_index"] == 5


def test_gap_also_needs_medium_large_body_and_deduplicates():
    bars, history, context = setup()
    bars[8] = replace(bars[8], open=7.4, low=7.3, high=7.7, close=7.6)
    _, denied = combined_a_entry_history(bars, history)
    assert denied == {}
    bars[8] = replace(bars[8], open=7.3, low=7.3, high=7.7, close=7.7)
    bars.append(replace(bars[8], timestamp=bars[8].timestamp + timedelta(days=1),
                        open=7.8, low=7.8, high=8.3, close=8.3, volume=400))
    history[9] = (replace(context, source_path="joined-source"),)
    events, proofs = combined_a_entry_history(bars, history)
    assert list(proofs) == [8]
    assert proofs[8]["combined_a_breakout_type"] == "gap"
    assert len([event for event in events if event["event"] == "combined_a_pullback_breakout"]) == 1


def test_exact_three_percent_body_and_two_thirds_close_are_inclusive():
    bars, history, context = setup()
    context = replace(context, origin_price=3, rebound_highs=(CombinedAHigh(6, 7.1, 7),))
    bars[5] = replace(bars[5], low=4.9, close=5)
    bars[8] = replace(bars[8], open=7, high=7.3, low=7, close=7.21)
    _, proofs = combined_a_entry_history(bars, {index: (context,) for index in history})
    assert proofs[8]["breakout_body_pct"] == pytest.approx(0.03)
    assert proofs[8]["combined_a_minimum_close"] == proofs[8]["combined_a_two_thirds_price"]


def test_bare_four_pivots_without_original_n_are_not_a_combined_a():
    bars, _, _ = setup()
    dates = {bar.timestamp.date().isoformat(): index for index, bar in enumerate(bars)}
    points: list[GeometryPoint] = [dict(index=index, value=bars[index].high if kind == "H" else bars[index].low,
                   kind=kind, available_at=bars[index].timestamp.date().isoformat(), state="confirmed",
                   flip="翻多为空" if kind == "H" else "翻空为多")
              for index, kind in ((0, "L"), (1, "H"), (3, "L"), (4, "H"))]
    first: GeometryLevel = dict(strokes=[dict(id="source", points=points)])
    second: GeometryLevel = dict(strokes=[dict(id="second", source_path="source", points=points)])
    assert combined_a_contexts_from_geometry(bars, dict(strokes=[]), first, second, dict(strokes=[]), dates, 8) == ()


@pytest.mark.parametrize("fixture", ["guofang_2026_consolidation.json", "guofang_2025_combined_a.json"])
def test_guofang_april_3_real_geometry_and_prefix_consistency(fixture):
    raw = json.loads((Path(__file__).parent / "fixtures" / fixture).read_text(encoding="utf-8"))
    all_bars = [Bar(datetime.fromisoformat(day), raw["symbol"],
                    *(values[0], values[1], values[2], values[3], values[4]),
                    adjustment_factor=values[5] if len(values) > 5 else 1.0)
                for day, *values in raw["bars"]]
    signal = next(index for index, bar in enumerate(all_bars) if bar.timestamp.date().isoformat() == "2025-04-03")
    bars = all_bars[: signal + 2]
    config = SystemStrategy(
        pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3", first_pullback_threshold=None,
        strict_n_attack_quality=False, preflight_reward_risk=False, volume_filter=False,
    )
    result = generate_system_signals(bars, config)
    sink: dict[int, tuple[CombinedAContext, ...]] = {}
    chart_entry_history(bars, audit=result.audit, combined_candidate_sink=sink)
    events, proofs = combined_a_entry_history(bars, sink)
    proof = proofs[signal]
    assert proof["combined_a_origin_date"] == "2024-02-08"
    assert proof["combined_a_top_date"] == "2025-01-03"
    assert proof["combined_a_known_date"] == "2025-01-22"
    assert proof["combined_a_internal_pullback_sessions"] == 70
    assert proof["combined_a_child_pullback_sessions"] == 9
    assert proof["combined_a_pullback_sessions"] == 58
    assert proof["combined_a_breakout_date"] == "2025-03-17"
    assert proof["combined_a_breakout_known_date"] == "2025-03-26"
    assert proof["combined_a_breakout_price"] == pytest.approx(7.049092238189244)
    assert proof["combined_a_two_thirds_price"] == pytest.approx(5.918296563582128)
    assert proof["confirmation_close"] == pytest.approx(7.158380489944116)
    assert proof["breakout_volume"] == 20_135_200
    short = bars[: signal + 1]
    short_result = generate_system_signals(short, config)
    short_sink: dict[int, tuple[CombinedAContext, ...]] = {}
    chart_entry_history(short, audit=short_result.audit, combined_candidate_sink=short_sink)
    short_events, short_proofs = combined_a_entry_history(short, short_sink)
    assert short_events == [event for event in events if int(str(event["bar_index"])) <= signal]
    assert short_proofs == {index: evidence for index, evidence in proofs.items() if index <= signal}
