"""Defense failure ends old measurements; same-day independent Ns restart them."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.bottom_n_targets import DeclineStart, PositiveNCompletion, bottom_n_target_history
from wavequant.domain.market_structure.positive_n_reformation import PositiveNSeed, ReformedPositiveN, positive_n_reformations
from wavequant.domain.market_structure.n_shape import NCompletion, NTargets
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events


def completion_of(item: ReformedPositiveN) -> NCompletion:
    completion = item.observation.completion
    assert completion is not None, "重成形结果必须包含已完成N"
    return completion


def targets_of(item: ReformedPositiveN) -> NTargets:
    targets = item.observation.targets
    assert targets is not None, "完成结构必须有测幅几何，发布资格另行筛选"
    return targets


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
    assert [completion_of(n).bar_index for n in reforms] == [dates["2024-08-20"]] * 2
    assert [(targets_of(n).one_p, targets_of(n).two_t) for n in reforms] == [(4.83, 5.52), (4.63, 5.12)]
    assert [completion_of(n).defense for n in reforms] == [3.66, 3.66]
    assert [n.setup.neckline.index for n in reforms] == [dates["2024-08-01"], dates["2024-08-16"]]
    assert reforms[1].setup.pullback.index == dates["2024-08-20"]


def test_every_visible_prefix_and_later_extension_preserves_known_reformations(xiangyang):
    bars, dates, seeds = xiangyang
    full = positive_n_reformations(bars, seeds)
    for end in range(dates["2024-07-31"], len(bars)):
        known_seeds = [seed for seed in seeds if seed.known_at <= end]
        assert positive_n_reformations(bars[:end + 1], known_seeds) == tuple(
            n for n in full if completion_of(n).bar_index <= end)
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


def test_reformation_stream_advances_each_family_only_once_and_preserves_full_prefix(xiangyang):
    from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory

    bars, _, seeds = xiangyang
    stream = PositiveNReformationHistory(bars)
    for end in range(len(bars)):
        known = [seed for seed in seeds if seed.known_at <= end]
        actual = stream.advance(end, known)
        assert actual == positive_n_reformations(bars[:end + 1], known)
        assert stream.advance(end, known) == actual
    assert len(actual) == 2
    assert [targets_of(value).one_p for value in actual] == [4.83, 4.63]
    assert stream.processed_days == sum(len(bars) - 1 - seed.attack for seed in seeds)


def test_reformation_stream_rebuilds_changed_seed_and_resolves_reordered_duplicate_identity(xiangyang):
    from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory

    bars, _, seeds = xiangyang
    stream = PositiveNReformationHistory(bars)
    end = len(bars) - 1
    stream.advance(end, seeds)
    revised = [replace(seeds[0], defense=3.70, level=2), seeds[1]]
    assert stream.advance(end, revised) == positive_n_reformations(bars, revised)
    duplicate = [replace(seeds[1], level=2), replace(seeds[1], level=3)]
    assert stream.advance(end, duplicate)[0].level == 2
    duplicate.reverse()
    assert stream.advance(end, duplicate)[0].level == 3
    assert stream.advance(end, []) == ()
    assert not stream.families
    assert stream.advance(end, seeds) == positive_n_reformations(bars, seeds)


def test_reformation_stream_accepts_late_seed_rebuilds_on_rewind_and_owns_market(xiangyang):
    from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory

    source, _, seeds = xiangyang
    bars = list(source)
    stream = PositiveNReformationHistory(bars)
    end = len(bars) - 1
    stream.advance(end, seeds[:1])
    bars[-1] = replace(bars[-1], low=1)
    assert stream.advance(end, seeds) == positive_n_reformations(source, seeds)
    rewind = seeds[0].known_at
    assert stream.advance(rewind, seeds[:1]) == positive_n_reformations(source[:rewind + 1], seeds[:1])
    assert stream.advance(end, seeds) == positive_n_reformations(source, seeds)


def test_reformation_stream_unknown_seed_type_uses_original_rules_without_serialization_hooks(xiangyang):
    from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory

    class DerivedSeed(PositiveNSeed):
        def __reduce_ex__(self, protocol):
            raise AssertionError('unknown source must not execute a serialization hook')

    bars, _, seeds = xiangyang
    seed = seeds[1]
    derived = DerivedSeed(seed.origin, seed.attack, seed.known_at, seed.defense, seed.level)
    stream = PositiveNReformationHistory(bars)
    assert stream.advance(len(bars) - 1, [derived]) == positive_n_reformations(bars, [derived])
    assert not stream.families


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


def test_daily_pipeline_does_not_restore_old_ns_outside_the_actual_qualified_window(pipeline):
    bars, config, result = pipeline
    # 旧用例把无资格期间的独立形态误当成交易输出；保留真实1593日完整输入核对边界。
    closed = [row for row in result.audit if row["event"] == "trading_interval_closed"]
    assert closed and closed[-1]["timestamp"][:10] == "2024-01-24"
    assert not any(row["event"] == "trading_interval_opened" and row["timestamp"][:10] >= "2024-07-25"
                   for row in result.audit)
    assert not any(row["event"] == "n_completed" and row["timestamp"][:10] >= "2024-07-25"
                   for row in result.audit)
    theory = ChartRepository.render_theory(object.__new__(ChartRepository), bars, config, result, "2024-08-20",
                                          geometry={"tertiary_trends": {}})
    assert not any(event["event"] == "n_completed" and event["time"] >= "2024-07-25"
                   for event in theory["events"])


def measurement_contract(bars, seeds, decline):
    """独立形态→底部资格→图表的契约夹具，不冒充真实账户已开放交易资格。"""
    reforms = positive_n_reformations(bars, seeds)
    completions = [PositiveNCompletion(seed.origin, seed.attack, seed.known_at, seed.defense) for seed in seeds]
    completions.extend(PositiveNCompletion(item.setup.origin.index, completion_of(item).bar_index,
        item.setup.pullback.confirmed_index, completion_of(item).defense, True) for item in reforms)
    history = bottom_n_target_history(bars, [decline], completions)
    rows = []
    for item in reforms:
        setup, n = item.setup, item.observation
        assert n.completion is not None and n.targets is not None
        attack = n.completion.bar_index
        chosen = history.completions[attack]
        eligible = history.qualifications[attack].eligible and chosen.origin == setup.origin.index
        rows.append(dict(event="n_completed", direction="up", origin=setup.origin.index,
            neckline=setup.neckline.index, pullback=setup.pullback.index, bar_index=attack,
            timestamp=bars[attack].timestamp.isoformat(), known_at=setup.pullback.confirmed_index,
            n_id=f"up:{setup.origin.index}:{setup.neckline.index}:{setup.pullback.index}:{attack}",
            defense=n.completion.defense, n_level=item.level, box_anchor=n.targets.box_anchor,
            one_p=n.targets.one_p if eligible else None, two_t=n.targets.two_t if eligible else None,
            target_eligible=eligible, target_primary=eligible,
            target_bottom_index=history.qualifications[attack].bottom_index,
            reformed_from=item.previous_attack, reformed_root=item.root_attack))
    return SystemResult([], rows, {})


def test_real_reformation_geometry_keeps_two_shapes_but_exactly_one_floor_measurement(xiangyang):
    bars, dates, seeds = xiangyang
    result = measurement_contract(bars, seeds, DeclineStart(dates["2024-07-12"], dates["2024-07-25"]))
    config = SystemStrategy(pivot_mode="lecture_causal", buy_point_definition="whole_flip_wave_v3")
    theory = ChartRepository.render_theory(object.__new__(ChartRepository), bars, config, result, "2024-08-20",
                                          geometry={"tertiary_trends": {}})
    formed = [event for event in theory["events"] if event["event"] == "n_completed"]
    assert len(formed) == len({event["id"] for event in formed}) == 2
    assert [event["target_eligible"] for event in formed] == [True, False]
    assert [event["reformed_from_date"] for event in formed] == ["2024-07-31", "2024-08-15"]
    assert [[level["price"] for level in event["levels"] if level.get("stage") in ("one_p", "two_t")]
            for event in formed] == [[4.83, 5.52], []]
    assert formed[0]["origin"] == formed[0]["target_bottom_index"] == dates["2024-07-25"]
    assert formed[1]["one_p"] is None and formed[1]["two_t"] is None
    assert len([shape for shape in theory["shapes"] if shape["attack_time"] == "2024-08-20"]) == 2
    historical = ChartRepository.render_theory(object.__new__(ChartRepository), bars[:-1], config, result, "2024-08-19",
                                               geometry={"tertiary_trends": {}})
    assert not any(event["event"] == "n_completed" for event in historical["events"])


def test_measurement_prefix_and_partial_bar_do_not_borrow_future_reformations(xiangyang):
    bars, dates, seeds = xiangyang
    decline = DeclineStart(dates["2024-07-12"], dates["2024-07-25"])
    full = measurement_contract(bars, seeds, decline)
    assert len(full.audit) == 2 and sum(row["target_eligible"] for row in full.audit) == 1
    prefix = measurement_contract(bars[:-1], seeds, decline)
    assert prefix.audit == [row for row in full.audit if row["bar_index"] < len(bars) - 1] == []
    partial = [*bars[:-1], replace(bars[-1], high=bars[-1].open, low=bars[-1].open,
                                 close=bars[-1].open, volume=bars[-1].volume / 10)]
    assert measurement_contract(partial, seeds, decline).audit == []
