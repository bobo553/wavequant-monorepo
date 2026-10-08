"""Price, volume and publication boundaries of secondary resistance recovery."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.secondary_reclaim_entry import SecondaryHigh, secondary_reclaim_history


def example() -> tuple[list[Bar], dict[int, list[SecondaryHigh]]]:
    rows = [
        (8, 10, 7, 9, 100), (8, 9, 6, 8, 100), (8, 9.8, 7.5, 9.7, 100),
        (9.7, 10.8, 9.5, 10.8, 100), (10.7, 11, 9.8, 10, 160),
        (9.4, 10.9, 9.3, 10.9, 140), (10.9, 11.8, 10.8, 11.3, 200),
        (11.2, 12, 11, 11.8, 250),
    ]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), "TEST", *row)
            for index, row in enumerate(rows)]
    return bars, {index: [SecondaryHigh(0, 10, 1)] for index in range(len(bars))}


def test_recovery_uses_frozen_two_bar_supply_and_actual_pullback_low() -> None:
    bars, highs = example()
    events, proofs = secondary_reclaim_history(bars, highs)
    assert list(proofs) == [6, 7]
    proof = proofs[6]
    assert proof["secondary_high"] == 10
    assert proof["secondary_resistance_high"] == 11
    assert proof["secondary_resistance_date"] == "2025-01-05"
    assert proof["secondary_origin_low"] == 6
    assert proof["stop"] == 9.3
    assert proof["secondary_reclaim_type"] == "body"
    assert any(event["event"] == "secondary_reclaim_resistance_observed" for event in events)
    assert secondary_reclaim_history(bars[:6], highs)[1] == {}


@pytest.mark.parametrize("changes", [
    {"close": 11}, {"close": 10.95}, {"high": 12, "close": 10.95},
    {"open": 11, "close": 11}, {"open": 11.2, "close": 11.1},
    {"open": 11, "close": 11.22}, {"volume": 140}, {"volume": 139},
])
def test_equal_wick_only_two_percent_doji_bearish_and_nonvolume_are_not_buys(changes: dict[str, float]) -> None:
    bars, highs = example()
    bars[6] = replace(bars[6], open=changes.get("open", bars[6].open), high=changes.get("high", bars[6].high),
                      low=changes.get("low", bars[6].low), close=changes.get("close", bars[6].close),
                      volume=changes.get("volume", bars[6].volume))
    assert secondary_reclaim_history(bars[:7], highs)[1] == {}


def test_zero_previous_volume_is_unknown_not_an_infinite_volume_multiple() -> None:
    bars, highs = example()
    bars[5] = replace(bars[5], volume=0)
    assert secondary_reclaim_history(bars[:7], highs)[1] == {}


def test_strict_origin_break_retires_episode_even_after_later_recovery() -> None:
    bars, highs = example()
    bars[5] = replace(bars[5], low=5.99)
    events, proofs = secondary_reclaim_history(bars, highs)
    assert proofs == {}
    assert any(event.get("reason") == "secondary_origin_low_broken" for event in events)
    bars[5] = replace(bars[5], low=6)
    assert 6 in secondary_reclaim_history(bars, highs)[1]


def test_no_resistance_no_old_unknown_or_unpublished_secondary_high() -> None:
    bars, highs = example()
    assert secondary_reclaim_history(bars, {}) == ([], {})
    unknown = {index: [SecondaryHigh(0, 10, len(bars))] for index in highs}
    assert secondary_reclaim_history(bars, unknown) == ([], {})
    # Publishing the old point on the attack session cannot backdate the attack.
    late = {index: [SecondaryHigh(0, 10, 3)] for index in highs}
    assert secondary_reclaim_history(bars, late) == ([], {})
    bars[4] = replace(bars[4], open=10.8, high=11, low=10.7, close=11)
    assert secondary_reclaim_history(bars, highs)[1] == {}


def test_future_formal_promotion_does_not_erase_pending_resistance() -> None:
    bars, highs = example()
    for index in range(5, len(bars)):
        highs[index] = [SecondaryHigh(4, 11, 5)]
    assert secondary_reclaim_history(bars, highs)[1][6]["secondary_high"] == 10


def test_unfilled_gap_mid_large_bull_body_is_explained_as_gap() -> None:
    bars, highs = example()
    bars[6] = replace(bars[6], open=11.1, low=11.05, high=11.6, close=11.5)
    assert secondary_reclaim_history(bars[:7], highs)[1][6]["secondary_reclaim_type"] == "gap"


def test_every_prefix_matches_future_suffix_without_early_attack_or_next_bar_entry() -> None:
    bars, highs = example()
    events, proofs = secondary_reclaim_history(bars, highs)
    for count in range(1, len(bars) + 1):
        observed_events, observed_proofs = secondary_reclaim_history(bars[:count], highs)
        assert observed_events == [event for event in events if cast(int, event["bar_index"]) < count]
        assert observed_proofs == {index: proof for index, proof in proofs.items() if index < count}


def test_xiangyang_march5_reclaims_march3_supply_from_known_december11_secondary_high() -> None:
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["rows"] if day <= "2025-03-05"]
    history, _ = hierarchical_history(bars)
    highs = {index: [SecondaryHigh(point["index"], point["value"], point["available_at"])
                     for point in levels[2] if point["kind"] == "H"]
             for index, levels in history.items()}
    _, proofs = secondary_reclaim_history(bars, highs)
    current = len(bars) - 1
    assert str(bars[current].timestamp.date()) == "2025-03-05"
    proof = proofs[current]
    assert proof["secondary_high_date"] == "2024-12-11"
    assert proof["secondary_high_known_date"] == "2025-01-21"
    assert proof["secondary_high"] == 8.17
    assert proof["secondary_attack_date"] == "2025-02-28"
    assert proof["secondary_resistance_date"] == "2025-03-03"
    assert proof["secondary_resistance_high"] == 8.77
    assert proof["secondary_pullback_low_date"] == "2025-03-04"
    assert proof["stop"] == 7.43
    assert proof["secondary_reclaim_close"] == 8.89
    assert proof["secondary_reclaim_body_fraction"] == pytest.approx(.19 / 8.7)
    assert proof["breakout_volume_multiple"] == pytest.approx(126499296 / 80806757)
    assert secondary_reclaim_history(bars[:-1], highs)[1] == {
        index: value for index, value in proofs.items() if index < current}
