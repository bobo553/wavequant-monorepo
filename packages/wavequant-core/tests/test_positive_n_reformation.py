"""Defense failure ends old measurements; same-day independent Ns restart them."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.bottom_n_targets import DeclineStart, PositiveNCompletion, bottom_n_target_history
from wavequant.domain.market_structure.positive_n_reformation import PositiveNSeed, positive_n_reformations
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events


@pytest.fixture(scope="module")
def xiangyang():
    root = Path(__file__).parent / "fixtures"
    raw = json.loads((root / "xiangyang_2025_trend_break.json").read_text(encoding="utf8"))
    bars = [Bar(datetime.fromisoformat(d), raw["symbol"], o, h, l, c, v)
            for d, o, h, l, c, v in raw["rows"] if d <= "2024-08-20"]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    seeds = [PositiveNSeed(dates[a], dates[t], dates[t], defense)
             for a, t, defense in (("2024-07-25", "2024-07-31", 3.76), ("2024-08-13", "2024-08-15", 3.70))]
    return bars, dates, seeds


def test_two_independent_origins_complete_on_august20_and_freeze_separate_targets(xiangyang):
    bars, dates, seeds = xiangyang
    reforms = positive_n_reformations(bars, seeds)
    assert len(reforms) == 2
    assert [n.previous_attack for n in reforms] == [dates["2024-07-31"], dates["2024-08-15"]]
    assert [n.observation.completion.bar_index for n in reforms] == [dates["2024-08-20"]] * 2
    assert [(n.observation.targets.one_p, n.observation.targets.two_t) for n in reforms] == [(4.83, 5.52), (4.63, 5.12)]
    assert [n.observation.completion.defense for n in reforms] == [3.66, 3.66]
    assert [n.setup.neckline.index for n in reforms] == [dates["2024-08-01"], dates["2024-08-16"]]
    assert reforms[1].setup.pullback.index == dates["2024-08-20"]


def test_every_visible_prefix_and_later_extension_preserves_known_reformations(xiangyang):
    bars, dates, seeds = xiangyang
    full = positive_n_reformations(bars, seeds)
    for end in range(dates["2024-07-31"], len(bars)):
        known_seeds = [seed for seed in seeds if seed.known_at <= end]
        assert positive_n_reformations(bars[:end + 1], known_seeds) == tuple(
            n for n in full if n.observation.completion.bar_index <= end)
    next_bar = replace(bars[-1], timestamp=datetime(2024, 8, 21), high=4.40, low=3.66, close=4.35)
    assert positive_n_reformations([*bars, next_bar], seeds) == full


def test_equal_defense_does_not_fail_and_origin_break_cancels_that_family(xiangyang):
    bars, dates, seeds = xiangyang
    equal = [*bars[:-1], replace(bars[-1], low=3.70)]
    assert len(positive_n_reformations(equal, seeds)) == 1
    origin_break = [*bars[:-1], replace(bars[-1], low=3.64)]
    assert len(positive_n_reformations(origin_break, seeds)) == 1
    assert positive_n_reformations([*bars[:-1], replace(bars[-1], low=3.44)], seeds) == ()
    not_outside = [*bars[:-1], replace(bars[-1], low=3.66, close=3.77)]
    assert not any(n.setup.origin.index == seeds[1].origin for n in positive_n_reformations(not_outside, seeds))


def test_defense_failure_releases_the_floor_without_waiting_for_origin_break(xiangyang):
    bars, dates, seeds = xiangyang
    source = seeds[0]
    history = bottom_n_target_history(bars, [DeclineStart(dates["2024-07-12"], dates["2024-07-25"])], [
        PositiveNCompletion(source.origin, source.attack, source.known_at, source.defense),
        PositiveNCompletion(source.origin, dates["2024-08-20"], dates["2024-08-20"], 3.66, True),
    ])
    assert history.source_at[dates["2024-08-02"]] == source.attack
    assert history.source_at[dates["2024-08-05"]] is None
    assert history.source_at[-1] == dates["2024-08-20"]
    assert history.retirements[0].reason == "squeeze_defense_broken"


def test_invalid_or_unknown_seed_cannot_create_a_reformation(xiangyang):
    bars, _, seeds = xiangyang
    for seed in (replace(seeds[0], known_at=len(bars)), replace(seeds[0], defense=float("nan"))):
        with pytest.raises(ValueError):
            positive_n_reformations(bars, [seed])


def test_independent_same_day_exit_targets_do_not_inherit_another_sources_retirement():
    bars = [Bar(datetime(2024, 1, index + 1), "TEST", low + 1, low + 2, low, low + 1.5, 100)
            for index, low in enumerate((1, 2, 2.5, 3, 3.2, 5))]
    common = dict(event="n_completed", direction="up", bar_index=3, known_at=3,
                  target_eligible=True, defense=2.5, one_p=6, two_t=7)
    rows = [dict(common, origin=0, n_id="long", target_primary=True),
            dict(common, origin=1, n_id="short", target_primary=False),
            dict(event="n_target_source_retired", attack=3, bar_index=4)]
    signal = dict(wave_b_low_index=0, bar_index=3)
    measured = _post_b_wave_exit_events(bars, rows, signal)
    assert [event["n_id"] for event in measured if event["event"] == "wave_n_target_reached"] == ["short", "short"]
    rows.append(dict(event="n_invalidated", n_id="short", bar_index=5))
    assert not any(event["event"] == "wave_n_target_reached" for event in _post_b_wave_exit_events(bars, rows, signal))


@pytest.fixture(scope="module")
def pipeline(xiangyang):
    bars, _, _ = xiangyang
    raw = json.loads((Path(__file__).parent / "fixtures/xinhua_2024_mother_pullback.json").read_text(encoding="utf8"))
    config = SystemStrategy(**raw["strategy"])
    result = generate_system_signals(bars, config)
    return bars, config, result


def test_pipeline_and_chart_keep_both_targets_and_hide_invalid_shapes(pipeline):
    bars, config, result = pipeline
    theory = ChartRepository.render_theory(None, bars, config, result, "2024-08-20", geometry={"tertiary_trends": {}})
    positive = [e for e in theory["events"] if e["event"] == "n_completed" and e["direction"] == "up"]
    old = {e["time"]: e for e in positive if e["time"] in ("2024-07-31", "2024-08-15")}
    assert old["2024-07-31"]["n_invalidated_at"] == "2024-08-05"
    assert old["2024-08-15"]["n_invalidated_at"] == "2024-08-20"
    formed = [e for e in positive if e["time"] == "2024-08-20"]
    assert len(formed) == len({e["id"] for e in formed}) == 2
    assert all(e["target_eligible"] for e in formed)
    assert [[level["price"] for level in e["levels"] if level.get("stage") in ("one_p", "two_t")]
            for e in formed] == [[4.83, 5.52], [4.63, 5.12]]
    assert [e["reformed_from_date"] for e in formed] == ["2024-07-31", "2024-08-15"]
    assert not any(shape["attack_time"] in old for shape in theory["shapes"])
    assert len([shape for shape in theory["shapes"] if shape["attack_time"] == "2024-08-20"]) == 2
    historical = ChartRepository.render_theory(None, bars[:-1], config, result, "2024-08-19", geometry={"tertiary_trends": {}})
    before = next(e for e in historical["events"] if e["event"] == "n_completed" and e["time"] == "2024-08-15")
    assert "n_invalidated_at" not in before
    assert not any(e.get("reformed_from_date") for e in historical["events"] if e["time"] == "2024-08-20")


def test_pipeline_prefix_and_incomplete_replay_do_not_borrow_future_reformations(pipeline):
    bars, config, full = pipeline
    end = len(bars) - 2
    prefix = generate_system_signals(bars[:end + 1], config)
    assert prefix.audit == [row for row in full.audit if row["bar_index"] <= end]
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= end]
    partial = [*bars[:-1], replace(bars[-1], high=bars[-1].open, low=bars[-1].open,
                                 close=bars[-1].open, volume=bars[-1].volume / 10)]
    replay = generate_system_signals(partial, config, chart_history_cache={"source_bars": tuple(bars)})
    assert not any(row["event"] == "n_completed" and row["bar_index"] == len(bars)-1
                   and row.get("reformed_from") is not None for row in replay.audit)
