"""The pending low and breakout must be dated independently of formal turns."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy
from wavequant.domain.strategies.shallow_base_breakout import (
    ShallowAlternationCandidate,
    shallow_base_history,
    shallow_candidate_from_geometry,
)
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


def geometry(counter: float, *, known: int = 111):
    origin = dict(index=0, kind="L", value=4.0, available_at="origin")
    key = dict(index=-1, kind="H", value=5.0, available_at="origin")
    high = dict(
        index=100, kind="H", value=10.0, available_at="high", confirmed_low=origin, broken_key=key, source_path="level2"
    )
    low = dict(index=110, kind="L", value=counter, available_at="pullback")
    level = dict(
        trend_level=2, bear_to_bull_highs=[high], strokes=[dict(id="level2", source_path="level1", points=[high])]
    )
    source = dict(strokes=[dict(id="level1", points=[low])])
    return ((level, source),), dict(origin=1, high=101, pullback=known)


@pytest.mark.parametrize("counter,expected", [(6.292, True), (6.294, False), (6.0, False)])
def test_pending_low_requires_at_least_0618_and_less_than_two_thirds(counter, expected):
    levels, dates = geometry(counter)
    candidate = shallow_candidate_from_geometry(levels, dates, 111)
    assert (candidate is not None) is expected
    if candidate is not None:
        assert candidate.trend_level == 2
        assert candidate.known_index == 111
        assert candidate.pullback_index == 110
    assert shallow_candidate_from_geometry(levels, dates, 110) is None


def sample():
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=i), "TEST", 6.7, 7.1, 6.5, 6.8, 100) for i in range(63)]
    bars[10] = replace(bars[10], low=6.2)
    bars[61] = replace(bars[61], open=7.0, high=7.9, low=6.95, close=7.8, volume=300)
    candidate = ShallowAlternationCandidate(2, 0, 4.0, 6, 10.0, 10, 6.2, 12, 0.6333333333333333)
    snapshots = {i: candidate for i in range(12, len(bars))}
    return bars, snapshots


def test_long_base_breakout_is_once_and_prefix_stable():
    bars, snapshots = sample()
    events, proofs = shallow_base_history(bars, snapshots, minimum_reward_risk=1.5)
    assert [(e["bar_index"], e["event"]) for e in events] == [(12, "shallow_alternation_candidate")]
    assert list(proofs) == [61]
    assert proofs[61]["base_sessions"] == 50
    assert proofs[61]["breakout_volume_multiple"] == 3
    assert proofs[61]["target"] == 10.0
    for stop in (40, 61, 62, 63):
        prefix_events, prefix_proofs = shallow_base_history(
            bars[:stop], {i: p for i, p in snapshots.items() if i < stop}, minimum_reward_risk=1.5
        )
        assert prefix_events == [e for e in events if e["bar_index"] < stop]
        assert prefix_proofs == {i: p for i, p in proofs.items() if i < stop}


@pytest.mark.parametrize(
    "failure", ["too_early", "low_broken", "wide_base", "no_breakout", "small_body", "low_volume", "poor_reward"]
)
def test_incomplete_or_untradeable_breakout_is_not_promoted(failure):
    bars, snapshots = sample()
    if failure == "too_early":
        bars[50] = replace(bars[50], open=7.0, high=7.9, low=6.95, close=7.8, volume=300)
        bars = bars[:51]
        snapshots = {i: p for i, p in snapshots.items() if i < 51}
    elif failure == "low_broken":
        bars[30] = replace(bars[30], low=6.19)
    elif failure == "wide_base":
        bars[30] = replace(bars[30], high=8.0)
    elif failure == "no_breakout":
        bars[61] = replace(bars[61], close=7.1)
    elif failure == "small_body":
        bars[61] = replace(bars[61], open=7.5)
    elif failure == "low_volume":
        bars[61] = replace(bars[61], volume=199)
    else:
        bars[61] = replace(bars[61], open=7.3, close=8.3, high=8.4)
    _, proofs = shallow_base_history(bars, snapshots, minimum_reward_risk=1.5)
    assert proofs == {}


def test_v3_switch_defaults_on_and_rejects_non_boolean():
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    assert profile["strategy"]["shallow_base_breakout_enabled"] is True
    assert SystemStrategy(**profile["strategy"]).shallow_base_breakout_enabled
    with pytest.raises(ValueError, match="shallow base breakout switch"):
        replace(SystemStrategy(**profile["strategy"]), shallow_base_breakout_enabled=1).validate()
