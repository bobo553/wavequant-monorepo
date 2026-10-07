from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.a_wave import AWaveSeed, AWaveTurn, a_wave_history
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.interfaces.charts.visualization import ChartRepository


def sample():
    rows = [(8.5, 9, 8, 8.5), (9.5, 10, 9, 9.8), (10, 12, 9.5, 11),
            (11, 12.01, 10.5, 11.5), (12, 14, 11, 13), (13, 15, 12, 14),
            (10, 11, 8.5, 10), (10, 11, 8.7, 10.5), (15, 16, 9, 15.5)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), "TEST", *row, 1000)
            for i, row in enumerate(rows)]
    return bars, AWaveSeed("n:first", 0, 1, 1, 12, 14, 9)


def test_strict_one_p_confirms_without_squeeze_b_or_formal_second_level_and_two_t_upgrades():
    bars, seed = sample()
    history = a_wave_history(bars[:6], [seed])
    assert [(event.event, event.bar_index) for event in history] == [
        ("a_wave_confirmed", 3), ("a_wave_upgraded", 4), ("a_wave_extended", 5)]
    assert history[0].a_class == "ordinary"
    assert history[1].a_class == history[2].a_class == "strong"
    assert history[0].confirmed_index == history[2].confirmed_index == 3
    assert history[2].a_high_index == 5
    assert history[2].b_low_index is None


def test_origin_holds_after_n_defense_loss_and_known_first_level_top_supplies_b_and_c():
    bars, seed = sample()
    history = a_wave_history(bars, [seed], [AWaveTurn(5, 6), AWaveTurn(8, 8)])
    assert any(event.phase == "b" and event.b_low_index == 6 for event in history)
    assert history[-1].phase == "c"
    assert history[-1].event == "c_wave_started"
    assert history[-1].a_high_index == 5
    assert history[-1].b_low_index == 6


def test_attack_candle_wick_break_confirms_a_even_when_close_has_not_reached_one_p():
    bars, seed = sample()
    bars[1] = replace(bars[1], high=12.01)
    first = a_wave_history(bars[:2], [seed])
    assert first[0].confirmed_index == seed.attack_index
    assert first[0].a_class == "ordinary"
    bars[1] = replace(bars[1], high=14)
    assert a_wave_history(bars[:2], [seed])[0].a_class == "strong"


def test_completed_c_does_not_extend_into_the_next_leg():
    bars, seed = sample()
    for row in [(15, 15.5, 9, 14), (18, 20, 10, 19)]:
        bars.append(Bar(bars[-1].timestamp + timedelta(days=1), "TEST", *row, 1000))
    history = a_wave_history(bars, [seed], [AWaveTurn(5, 6), AWaveTurn(8, 9)])
    assert history[-1].event == "c_wave_completed"
    assert history[-1].c_high_index == 8
    assert history[-1].bar_index == 9


@pytest.mark.parametrize("index", [6, 8])
def test_origin_wick_loss_wins_over_upside_and_permanently_prevents_later_c(index):
    bars, seed = sample()
    bars[index] = replace(bars[index], low=7.99)
    history = a_wave_history(bars, [seed], [AWaveTurn(5, 6)])
    assert history[-1].event == "a_wave_invalidated"
    assert history[-1].bar_index == index
    assert not any(event.event.startswith("c_wave") for event in history)
    bars.append(Bar(bars[-1].timestamp + timedelta(days=1), "TEST", 18, 20, 10, 19, 1000))
    assert history == a_wave_history(bars, [seed], [AWaveTurn(5, 6)])


def test_origin_equality_and_same_day_independent_sources_remain_separate():
    bars, seed = sample()
    bars[6] = replace(bars[6], low=8)
    second = replace(seed, source_id="n:second", one_p=13)
    history = a_wave_history(bars, [seed, second], [AWaveTurn(5, 6)])
    assert {event.source_id for event in history if event.event == "c_wave_started"} == {"n:first", "n:second"}


def test_retirement_only_prevents_new_a_and_unknown_turns_cannot_borrow_future():
    bars, seed = sample()
    assert not a_wave_history(bars, [replace(seed, confirm_before=3)])
    history = a_wave_history(bars, [replace(seed, confirm_before=4)], [AWaveTurn(5, 6)])
    assert history[-1].event == "c_wave_started"
    for end in range(1, len(bars)):
        assert tuple(event for event in history if event.bar_index <= end) == a_wave_history(
            bars[:end + 1], [replace(seed, confirm_before=4)],
            [turn for turn in [AWaveTurn(5, 6)] if turn.known_at <= end])


def test_xiangyang_july_n_confirms_a_july31_and_extends_through_august18():
    data = json.loads((Path(__file__).parent / "fixtures/xiangyang_a_wave_2026.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), data["symbol"], *values) for day, *values in data["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    seed = AWaveSeed("july23", dates["2026-07-21"], dates["2026-07-23"], dates["2026-07-23"], 8.48, 9.06, 7.56)
    turn = AWaveTurn(dates["2026-08-18"], dates["2026-08-24"])
    history = a_wave_history(bars, [seed], [turn])
    assert next(event.bar_index for event in history if event.event == "a_wave_confirmed") == dates["2026-07-31"]
    assert next(event.bar_index for event in history if event.event == "a_wave_upgraded") == dates["2026-08-05"]
    august17 = a_wave_history(bars[:dates["2026-08-17"] + 1], [seed])[-1]
    assert august17.a_class == "strong" and august17.phase == "a"
    assert bars[august17.a_high_index].high == 9.83
    assert history[-1].a_high_index == dates["2026-08-18"]
    assert history[-1].b_low_index == dates["2026-08-25"]
    assert history[-1].phase == "b"


def test_full_strategy_publishes_a_before_b_and_keeps_prefix_and_partial_cache_causal():
    data = json.loads((Path(__file__).parent / "fixtures/xiangyang_a_wave_2026.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), data["symbol"], *values) for day, *values in data["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    config = SystemStrategy(**data["strategy"])
    full = generate_system_signals(bars, config)
    lifecycle = lambda result: [row for row in result.audit if row['event'].startswith(('a_wave_', 'b_wave_', 'c_wave_'))]
    for day in ('2026-07-31', '2026-08-17', '2026-08-25'):
        end = dates[day]
        prefix = generate_system_signals(bars[:end + 1], config)
        assert lifecycle(prefix) == [row for row in lifecycle(full) if row['bar_index'] <= end]
        assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= end]
    before = dates['2026-07-31']
    cache = {'source_bars': tuple(bars)}
    generate_system_signals(bars[:before], config, chart_history_cache=cache)
    partial = [*bars[:before], replace(bars[before], high=8.47)]
    cached = generate_system_signals(partial, config, chart_history_cache=cache)
    assert lifecycle(cached) == lifecycle(generate_system_signals(partial, config)) == []
    theory = ChartRepository.render_theory(None, bars, config, full, str(bars[-1].timestamp.date()),
                                          geometry={"secondary_trends": {"strokes": []}})
    assert theory['a_wave_policy'] == 'one_p_break_confirmed_a_origin_lifetime_v1'
    confirmed = next(row for row in theory['events'] if row['event'] == 'a_wave_confirmed')
    assert confirmed['available_at'] == confirmed['confirmed_date'] == '2026-07-31'
