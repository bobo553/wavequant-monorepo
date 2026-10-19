"""Daily risk reducers preserve causal output and repair changed source evidence."""

from copy import deepcopy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure.a_wave import AWaveHistoryStream, AWaveSeed, AWaveTurn, a_wave_history
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.daily_risk_histories import DailyRiskHistories
from wavequant.domain.strategies.event_history import EventHistoryStream
from wavequant.domain.strategies.five_top_entry import FiveTopEntryHistory, five_top_entry_history
from wavequant.domain.strategies.ten_full_entry import TenFullEntryHistory, ten_full_entry_history
from wavequant.domain.strategies.two_t_resistance import TwoTResistanceHistory, two_t_resistance_history


def market() -> list[Bar]:
    rows = [
        (4.5, 5.0, 4.0, 4.8, 100), (5.0, 6.0, 4.9, 5.8, 100),
        (6.0, 7.0, 5.0, 6.8, 100), (9.0, 12.0, 8.0, 9.5, 100),
        (9.6, 10.0, 7.5, 8.0, 300), (8.0, 10.0, 7.5, 9.0, 100),
        (9.0, 12.01, 7.5, 9.5, 100), (9.0, 12.1, 7.5, 9.5, 100),
    ]
    return [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', *row)
            for index, row in enumerate(rows)]


def projections() -> list[dict[str, object]]:
    return [
        dict(event='wave_projection_push', bar_index=2, attack=1, origin_index=0,
             state='pushing', target_stage='five_top', target=10.0, box_height=2.0,
             defense=4.0, b_low=5.0),
        dict(event='wave_projection_ready', bar_index=3, attack=1, origin_index=0,
             two_t=10.0),
        dict(event='wave_projection_target_reached', bar_index=3, attack=1, origin_index=0,
             reached_stage='ten_full', a_origin=4.0, target_stage='ten_full'),
    ]


def visible(events: list[dict[str, object]], index: int) -> list[dict[str, object]]:
    return [event for event in events if type(event['bar_index']) is int and cast(int, event['bar_index']) <= index]


def test_all_daily_maps_match_cold_prefixes_without_reprocessing_old_dates():
    bars, events = market(), projections()
    shared = DailyRiskHistories(bars, breakout_window=3, retracement_ratio=.5, anchor='origin')
    for index in range(len(bars)):
        known = visible(events, index)
        actual = shared.advance(index, known)
        assert actual.target_resistance == two_t_resistance_history(bars[:index + 1], known)
        assert actual.five_top_entry == five_top_entry_history(bars[:index + 1], known)
        assert actual.ten_full_entry == ten_full_entry_history(bars[:index + 1], known,
            breakout_window=3, retracement_ratio=.5, anchor='origin')
        assert shared.two_t(index, known) is actual.target_resistance
    assert shared.five_top.processed_days == len(bars)
    assert shared.ten_full.processed_days == len(bars)
    assert shared.two_t_streams['strategy', .8].processed_days == len(bars)


def test_two_t_target_warning_and_clear_keep_full_exit_precedence():
    bars = market()
    event = dict(event='wave_projection_ready', bar_index=3, attack=1, origin_index=0, two_t=10.0)
    risks = TwoTResistanceHistory(bars).advance(4, [event])
    assert risks[3]['reason'] == 'wave_two_t_resistance_reduce'
    assert risks[3]['exit_fraction'] == .8
    assert risks[4]['exit_fraction'] == 1.0
    assert risks[4]['wave_reached_date'] == '2024-01-04'


def test_target_risks_ignore_closed_window_facts_and_do_not_replay_gap_days():
    bars = market()
    shared = DailyRiskHistories(bars, breakout_window=3, retracement_ratio=.5,
        anchor='origin', start_index=6)
    result = shared.advance(6, projections())
    assert result.target_resistance == result.five_top_entry == result.ten_full_entry == {}
    assert shared.five_top.processed_days == shared.ten_full.processed_days == 1
    stream = shared.two_t_streams['strategy', .8]
    assert stream.processed_days == 1
    event = dict(event='wave_projection_ready', bar_index=6, attack=1, origin_index=0, two_t=10.0)
    assert bars[6].high > 10 > bars[6].close
    assert shared.two_t(6, [event]) == {}, 'qualification close cannot borrow its earlier intraday high'
    assert set(shared.two_t(7, [event])).issubset({6, 7})


@pytest.mark.parametrize('kind', ['two_t', 'five_top', 'ten_full'])
def test_same_length_mutation_removal_and_late_fact_cannot_reuse_a_stale_certificate(kind):
    bars, events = market(), projections()
    stream: EventHistoryStream
    cold: Callable[[Sequence[Mapping[str, object]]], dict[int, dict[str, object]]]
    if kind == 'two_t':
        stream = TwoTResistanceHistory(bars)
        cold = lambda values: two_t_resistance_history(bars, values)
    elif kind == 'five_top':
        stream = FiveTopEntryHistory(bars)
        cold = lambda values: five_top_entry_history(bars, values)
    else:
        stream = TenFullEntryHistory(bars, breakout_window=3, retracement_ratio=.5, anchor='origin')
        cold = lambda values: ten_full_entry_history(bars, values,
            breakout_window=3, retracement_ratio=.5, anchor='origin')
    index = len(bars) - 1
    stream.advance(index, events)
    events[0]['target'] = 30.0
    events[1]['two_t'] = 30.0
    events[2]['a_origin'] = 7.0
    assert stream.advance(index, events) == cold(events)
    removed = events.pop(1)
    assert stream.advance(index, events) == cold(events)
    events.insert(0, removed)
    assert stream.advance(index, events) == cold(events)


def test_five_top_fact_known_at_close_only_changes_next_session():
    bars = market()
    event = projections()[0]
    stream = FiveTopEntryHistory(bars)
    assert stream.advance(2, [event]) == {}
    changed = dict(event, target=8.0)
    actual = stream.advance(3, [changed])
    assert 2 not in actual
    assert actual[3]['wave_five_top_target'] == 8.0
    assert stream.processed_days == 4


def test_sources_are_owned_and_different_reduction_rules_do_not_share_results():
    bars, events = market(), projections()
    original = tuple(bars)
    shared = DailyRiskHistories(bars, breakout_window=3, retracement_ratio=.5, anchor='origin')
    bars[3] = replace(bars[3], high=100.0)
    assert shared.two_t(4, events) == two_t_resistance_history(original[:5], events)
    assert shared.two_t(4, events, reduction_fraction=.5)[3]['exit_fraction'] == .5
    assert shared.two_t(4, events)[3]['exit_fraction'] == .8


def test_account_and_strategy_fact_channels_never_reset_each_others_history():
    bars, events = market(), projections()
    shared = DailyRiskHistories(bars, breakout_window=3, retracement_ratio=.5, anchor='origin')
    strategy_events = [dict(events[1], owner_signal_index=2)]
    account_events = [dict(events[1], owner_signal_index=3)]
    for index in range(3, len(bars)):
        shared.two_t(index, strategy_events)
        shared.two_t(index, account_events, channel='account')
    assert shared.two_t_streams['strategy', .8].processed_days == len(bars)
    assert shared.two_t_streams['account', .8].processed_days == len(bars)


def a_market() -> tuple[list[Bar], AWaveSeed]:
    rows = [(8.5, 9, 8, 8.5), (9.5, 10, 9, 9.8), (10, 12, 9.5, 11),
            (11, 12.01, 10.5, 11.5), (12, 14, 11, 13), (13, 15, 12, 14),
            (10, 11, 8.5, 10), (10, 11, 8.7, 10.5), (15, 16, 9, 15.5)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', *row, 1000)
            for index, row in enumerate(rows)]
    return bars, AWaveSeed('n:first', 0, 1, 1, 12, 14, 9)


def test_a_wave_lifetime_consumes_each_date_once_despite_provisional_horizon_growth():
    bars, seed = a_market()
    turns = [AWaveTurn(5, 6)]
    stream = AWaveHistoryStream(bars)
    for index in range(1, len(bars)):
        current = replace(seed, confirm_before=index + 1)
        known_turns = [turn for turn in turns if turn.known_at <= index]
        actual = stream.advance(index, [current], known_turns)
        assert actual == a_wave_history(bars[:index + 1], [current], known_turns)
    assert [(event.event, event.bar_index) for event in actual] == [
        ('a_wave_confirmed', 3), ('a_wave_upgraded', 4), ('a_wave_extended', 5),
        ('a_wave_top_confirmed', 6), ('c_wave_started', 8)]
    assert stream.processed_observations == len(bars) - seed.known_at


def test_a_wave_backdated_turn_revised_seed_and_removal_replace_full_history():
    bars, seed = a_market()
    stream = AWaveHistoryStream(bars)
    stream.advance(8, [seed])
    assert stream.advance(8, [seed], [AWaveTurn(5, 6)]) == a_wave_history(bars, [seed], [AWaveTurn(5, 6)])
    revised = replace(seed, one_p=13.5, confirm_before=4)
    assert stream.advance(8, [revised], [AWaveTurn(5, 6)]) == ()
    assert stream.advance(8, []) == ()


def test_event_certificate_repairs_reordered_ready_events():
    bars = market()
    first = dict(event='wave_projection_ready', bar_index=3, attack=1, origin_index=0, two_t=10.0)
    second = dict(first, attack=2, two_t=11.0, evidence={'known': [3]})
    events = [first, second]
    stream = TwoTResistanceHistory(bars)
    initial = deepcopy(stream.advance(4, events))
    assert initial[3]['attack'] == 2
    events.reverse()
    assert stream.advance(4, events)[3]['attack'] == 1
    assert stream.advance(4, events) == two_t_resistance_history(bars[:5], events)
