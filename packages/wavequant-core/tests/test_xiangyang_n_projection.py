"""Real daily bars exercise N recognition, squeeze and target display together."""

from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.market_regime import MarketRegime
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.interfaces.charts.visualization import ChartRepository


@pytest.fixture(scope="module")
def xiangyang_history():
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2023_shared_edge_n.json").read_text(encoding="utf-8"))
    bars = tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"])
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    result = generate_system_signals(bars, config)
    return bars, dates, config, result


def test_real_shared_low_n_retains_its_squeeze_and_five_ten_projection(xiangyang_history):
    bars, dates, _, result = xiangyang_history
    attack = dates["2023-06-12"]
    anchors = [dates[day] for day in ("2023-06-07", "2023-06-08", "2023-06-09")]
    n_events = [e for e in result.audit if e["event"] == "n_completed"
                and [e[key] for key in ("origin", "neckline", "pullback")] == anchors]
    assert len(n_events) == 1
    event = n_events[0]
    assert event["bar_index"] == attack
    assert event["known_at"] == dates["2023-06-09"]
    assert event["direction"] == "up"
    assert [bars[index].low for index in (event["origin"], event["pullback"])] == [5.31, 5.32]
    assert (event["box_anchor"], event["defense"], event["one_p"], event["two_t"]) == (5.71, 5.32, 6.11, 6.51)

    squeeze = next(e for e in result.audit if e["event"] == "regime_confirmation"
                   and e.get("attack") == attack)
    assert squeeze["bar_index"] == dates["2023-06-15"]
    assert squeeze["regime"] == MarketRegime.STRONG_BULL.value
    projection = [e for e in result.audit if e["event"].startswith("wave_projection_")
                  and e.get("attack") == attack and e["bar_index"] <= dates["2023-07-04"]]
    assert [e["event"] for e in projection] == [
        "wave_projection_ready", "wave_projection_pullback", "wave_projection_push",
        "wave_projection_target_reached", "wave_projection_target_reached",
    ]
    assert [str(bars[e["bar_index"]].timestamp.date()) for e in projection] == [
        "2023-06-26", "2023-06-27", "2023-06-28", "2023-06-29", "2023-07-04",
    ]
    assert all(e["origin_index"] == anchors[0] and e["a_origin"] == 5.31 for e in projection)
    assert projection[0]["target"] is None
    assert (projection[2]["target_stage"], projection[2]["target"], projection[2]["b_low"]) == ("five_top", 6.97, 5.77)
    assert (projection[3]["reached_stage"], projection[3]["reached_target"],
            projection[3]["target_stage"], projection[3]["target"]) == ("five_top", 6.97, "ten_full", 9.37)
    assert (projection[4]["reached_stage"], projection[4]["reached_target"]) == ("ten_full", 9.37)


@pytest.mark.parametrize("day", [
    "2023-06-09", "2023-06-12", "2023-06-15", "2023-06-27",
    "2023-06-28", "2023-06-29", "2023-07-04",
])
def test_real_n_and_targets_do_not_change_when_future_sessions_are_appended(xiangyang_history, day):
    bars, dates, config, result = xiangyang_history
    end = dates[day]
    prefix = generate_system_signals(bars[:end+1], config)
    assert prefix.audit == [e for e in result.audit if e["bar_index"] <= end]
    assert prefix.signals == [signal for signal in result.signals if signal.bar_index <= end]
    anchors = [dates[date] for date in ("2023-06-07", "2023-06-08", "2023-06-09")]
    events = [e for e in prefix.audit if e["event"] == "n_completed"
              and [e[key] for key in ("origin", "neckline", "pullback")] == anchors]
    assert len(events) == (0 if day == "2023-06-09" else 1)


@pytest.mark.parametrize("day, expected", [
    ("2023-06-12", {"one_p": (6.11, "2023-06-12"), "two_t": (6.51, "2023-06-12")}),
    ("2023-06-27", {"one_p": (6.11, "2023-06-12"), "two_t": (6.51, "2023-06-12")}),
    ("2023-06-28", {"one_p": (6.11, "2023-06-12"), "two_t": (6.51, "2023-06-12"),
                    "five_top": (6.97, "2023-06-28")}),
    ("2023-06-29", {"one_p": (6.11, "2023-06-12"), "two_t": (6.51, "2023-06-12"),
                    "five_top": (6.97, "2023-06-28"), "ten_full": (9.37, "2023-06-29")}),
    ("2023-07-04", {"one_p": (6.11, "2023-06-12"), "two_t": (6.51, "2023-06-12"),
                    "five_top": (6.97, "2023-06-28"), "ten_full": (9.37, "2023-06-29")}),
])
def test_real_targets_are_displayed_on_the_original_n_when_they_become_known(xiangyang_history, day, expected):
    bars, dates, config, result = xiangyang_history
    attack = dates["2023-06-12"]
    end = dates[day]
    # The renderer must also filter the full audit by the visible prefix.
    theory = ChartRepository.render_theory(
        None, bars[:end+1], config, result, day, geometry={"tertiary_trends": {}},
    )
    n = next(e for e in theory["events"] if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert n["available_at"] == "2023-06-12"
    assert [p["time"] for p in n["shape"]] == ["2023-06-07", "2023-06-08", "2023-06-09", "2023-06-12"]
    levels = {level["stage"]: level for level in n["levels"] if "stage" in level}
    assert {stage: (level["price"], level["available_at"]) for stage, level in levels.items()} == expected
    assert all(level["available_at"] <= day for level in levels.values())
    if day >= "2023-06-29":
        assert levels["five_top"]["status"] == "已满足"
    if day == "2023-07-04":
        assert levels["ten_full"]["status"] == "已满足"


@pytest.fixture(scope="module")
def xiangyang_inside_history():
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2026_inside_child_n.json").read_text(encoding="utf8"))
    bars = tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"])
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, dates, config, generate_system_signals(bars, config)


def test_real_july23_inside_child_n_is_the_first_bottom_launch(xiangyang_inside_history):
    bars, dates, config, result = xiangyang_inside_history
    attack = dates["2026-07-23"]
    event = next(e for e in result.audit if e["event"] == "n_completed"
                 and e["bar_index"] == attack and e["direction"] == "up")
    assert [event[key] for key in ("origin", "neckline", "pullback")] == [
        dates["2026-07-21"], dates["2026-07-22"], dates["2026-07-22"]]
    assert event["inside_pullback_confirmed"] is True
    assert event["target_eligible"] is True
    assert event["target_source_attack"] == attack
    assert event["target_bottom_index"] == dates["2026-07-21"]
    assert (event["one_p"], event["two_t"]) == pytest.approx((8.48, 9.06))
    theory = ChartRepository.render_theory(None, bars[:attack + 1], config, result, "2026-07-23",
                                          geometry={"tertiary_trends": {}})
    n = next(e for e in theory["events"] if e["event"] == "n_completed" and e["bar_index"] == attack)
    assert n["target_bottom_date"] == "2026-07-21"
    assert n["target_source_date"] == "2026-07-23"
    levels = {level["stage"]: level["price"] for level in n["levels"] if "stage" in level}
    assert levels == pytest.approx({"one_p": 8.48, "two_t": 9.06})
    assert not any(e["event"].startswith("wave_projection_") and e.get("attack") == attack
                   for e in theory["events"])
    local = next(e for e in result.audit if e["event"] == "n_completed"
                 and e["bar_index"] == dates["2026-07-27"] and e["direction"] == "up")
    assert local["target_eligible"] is False and local["target_source_attack"] == attack
    assert local["one_p"] is None and local["two_t"] is None


@pytest.mark.parametrize("day", ["2026-07-21", "2026-07-22", "2026-07-23", "2026-07-27", "2026-08-10"])
def test_inside_bottom_n_full_history_and_partial_last_bar_do_not_rewrite_the_prefix(xiangyang_inside_history, day):
    from dataclasses import replace

    bars, dates, config, full = xiangyang_inside_history
    end = dates[day]
    cache = {"source_bars": bars}
    bar = bars[end]
    early = generate_system_signals([*bars[:end], replace(bar, high=bar.open, low=bar.open, close=bar.open)],
                                    config, chart_history_cache=cache)
    if day == "2026-07-23":
        assert not any(e["event"] == "n_completed" and e["bar_index"] == end for e in early.audit)
    prefix = generate_system_signals(bars[:end + 1], config, chart_history_cache=cache)
    assert prefix.audit == [e for e in full.audit if e["bar_index"] <= end]
    assert prefix.signals == [s for s in full.signals if s.bar_index <= end]
