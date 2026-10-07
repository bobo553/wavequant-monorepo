"""Only the first positive N at a confirmed decline's floor owns named targets."""

from datetime import datetime
from dataclasses import replace
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.interfaces.charts.visualization import ChartRepository


@pytest.fixture(scope="module")
def xinhua():
    raw = json.loads((Path(__file__).parent / "fixtures/xinhua_2024_mother_pullback.json").read_text(encoding="utf8"))
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], o, h, l, c, v,
                adjustment_factor=f, **raw["permissions"][d]) for d, o, h, l, c, v, f, _ in raw["bars"]]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(**raw["strategy"])
    result = generate_system_signals(bars, config)
    return bars, dates, config, result


def test_may16_cross_cycle_n_does_not_publish_its_9_97_and_13_14_targets(xinhua):
    bars, dates, config, result = xinhua
    theory = ChartRepository.render_theory(None, bars, config, result, str(bars[-1].timestamp.date()),
                                          geometry={"tertiary_trends": {}})
    event = next(e for e in theory["events"] if e["event"] == "n_completed"
                 and e["bar_index"] == dates["2023-05-16"] and e["direction"] == "up")
    assert event["shape"][0]["time"] == "2022-11-10"
    assert not any(level.get("stage") in ("one_p", "two_t", "five_top", "ten_full")
                   or level["name"] in ("1P 投影", "2T 投影") for level in event["levels"])
    assert event["target_eligible"] is False
    assert event["target_source_attack"] == dates["2023-05-15"]


def test_first_n_at_the_may10_decline_floor_is_the_frozen_source(xinhua):
    bars, dates, config, result = xinhua
    event = next(e for e in result.audit if e["event"] == "n_completed"
                 and e["bar_index"] == dates["2023-05-15"] and e["direction"] == "up")
    assert event.get("target_eligible") is True
    assert event["target_decline_index"] == dates["2023-05-05"]
    assert event["target_bottom_index"] == dates["2023-05-10"]
    assert (event["one_p"], event["two_t"]) == pytest.approx((7.075628335394026, 7.953773364180446))
    for day in ("2023-05-15", "2023-05-16"):
        end = dates[day]
        prefix = generate_system_signals(bars[:end + 1], config)
        known = [e for e in result.audit if e["event"] == "n_completed" and e["bar_index"] <= end]
        assert [e for e in prefix.audit if e["event"] == "n_completed"] == known


def test_non_launch_ns_cannot_spawn_five_ten_or_post_b_exit_milestones(xinhua):
    _, _, _, result = xinhua
    sources = {e["bar_index"] for e in result.audit if e["event"] == "n_completed"
               and e["direction"] == "up" and e.get("target_eligible") is True}
    projections = [e for e in result.audit if e["event"].startswith("wave_projection_")]
    assert projections
    assert all(e["attack"] in sources for e in projections)


def test_partial_may16_and_cached_completed_replay_keep_the_same_bottom_source(xinhua):
    bars, dates, config, full = xinhua
    end = dates["2023-05-16"]
    bar = bars[end]
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:end], replace(bar, high=bar.open, low=bar.open, close=bar.open, volume=bar.volume / 10)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(e["event"] == "n_completed" and e["bar_index"] == end
                   and e.get("target_eligible") for e in early.audit)
    replay = generate_system_signals(bars[:end + 1], config, chart_history_cache=cache)
    assert replay.audit == [e for e in full.audit if e["bar_index"] <= end]
    assert replay.signals == [s for s in full.signals if s.bar_index <= end]
