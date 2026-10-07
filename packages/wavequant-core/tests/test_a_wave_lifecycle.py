from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.a_wave_rules import a_origin_broken, classify_a_attack
from wavequant.domain.market_structure.abc_candidate import AbcAnchor, abc_pullback_evidence
from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.wave_continuation import wave_gap_entry, wave_pullback_context


def sample(high: float) -> tuple[list[Bar], WaveProjectionSetup]:
    rows = [
        (8.5, 9, 8, 8.5), (9.5, 10, 9, 9.8), (10, 11, 9.5, 10.8),
        (11, high, 10.5, high - 0.2), (10, 11, 8.5, 9.6), (9.7, 10, 8.7, 9.8),
        (10.4, 10.9, 10.4, 10.8),
    ]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), "TEST", *row, 2000 if i == 6 else 1000)
            for i, row in enumerate(rows)]
    return bars, WaveProjectionSetup(0, 1, 2, 8, 10, 14, 9)


@pytest.mark.parametrize("case", json.loads(
    (Path(__file__).parent / "fixtures/a_wave_rule_boundaries.json").read_text(encoding="utf-8")))
def test_a_classification_contract(case):
    assert classify_a_attack(case["high"], case["one_p"], case["two_t"]) == case["class"]


@pytest.mark.parametrize("high,a_class", [(12.01, "ordinary"), (14, "strong")])
def test_b_can_break_squeeze_low_for_both_a_classes_and_retains_actual_duration(high, a_class):
    bars, setup = sample(high)
    context = wave_pullback_context(bars, setup, 6)
    assert context is not None
    assert context["wave_a_class"] == a_class
    assert context["wave_b_low"] == 8.5 < setup.defense
    assert context["wave_b_broke_squeeze_low"] == 1
    assert context["wave_b_squeeze_break_date"] == "2024-01-05"
    assert (context["wave_b_duration"], context["wave_b_consolidation_duration"],
            context["wave_b_elapsed_duration"]) == (1, 1, 2)
    pivot = ReversalPoint(LinePoint(5, 0, PointKind.HIGH, 10), 5, "fixture")
    proof = wave_gap_entry(bars, setup, 6, pivots=[pivot])
    assert proof is not None and proof["wave_b_broke_squeeze_low"] == 1
    assert proof == wave_gap_entry(bars[:7], setup, 6, pivots=[pivot])
    if a_class == "strong":
        assert not any(event.state == "invalidated" for event in wave_projection_history(bars, setup))


@pytest.mark.parametrize("high", [12.01, 14])
def test_origin_equality_holds_but_a_wick_break_cancels_old_c_even_after_recovery(high):
    bars, setup = sample(high)
    bars[4] = replace(bars[4], low=8)
    assert wave_pullback_context(bars, setup, 6) is not None
    assert not a_origin_broken(8, 8)
    bars[4] = replace(bars[4], low=7.99)
    assert bars[4].close > setup.origin
    assert wave_pullback_context(bars, setup, 6) is None
    assert wave_gap_entry(bars, setup, 6) is None
    for end in range(5, len(bars)):
        assert wave_pullback_context(bars[:end + 1], setup, end) is None


def test_new_a_has_its_own_lifetime_after_old_a_fails():
    bars, old = sample(14)
    bars[4] = replace(bars[4], low=7.99)
    rows = [(8, 8.5, 7.5, 8.2), (8.4, 9, 8.2, 8.9), (8.9, 10, 8.7, 9.8),
            (9.8, 11, 9.7, 10.8), (9, 9.5, 8.5, 9), (9.8, 10.6, 9.6, 10.4)]
    for row in rows:
        bars.append(Bar(bars[-1].timestamp + timedelta(days=1), "TEST", *row, 2000))
    new = WaveProjectionSetup(7, 8, 9, 7.5, 9, 12, 8.2)
    assert wave_pullback_context(bars, old, 12) is None
    proof = wave_pullback_context(bars, new, 12)
    assert proof is not None and proof["wave_a_class"] == "ordinary"
    assert proof["wave_a_origin_date"] == "2024-01-08"


def test_deep_b_touching_a_origin_is_valid_but_strict_loss_is_rejected():
    bars, _ = sample(14)
    anchor = AbcAnchor(0, 3, 3, 8, 14, "fixture")
    bars[4] = replace(bars[4], low=8)
    assert abc_pullback_evidence(bars, anchor, 5, allow_deep_pullback=True) is not None
    bars[4] = replace(bars[4], low=7.99)
    assert abc_pullback_evidence(bars, anchor, 5, allow_deep_pullback=True) is None


def test_origin_loss_before_attack_cannot_be_hidden_by_a_later_n_or_high():
    bars, setup = sample(14)
    bars[1] = replace(bars[1], low=7.99)
    assert not wave_projection_history(bars, setup)
    assert wave_pullback_context(bars, setup, 6) is None
    anchor = AbcAnchor(0, 3, 3, 8, 14, "fixture")
    assert abc_pullback_evidence(bars, anchor, 5, allow_deep_pullback=True) is None
