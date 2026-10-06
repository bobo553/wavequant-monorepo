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
