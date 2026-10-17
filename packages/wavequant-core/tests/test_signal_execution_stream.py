"""Execution facts share N lifetimes while preserving audit and owner semantics."""

from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from wavequant.domain.models.model import Bar
from wavequant.interfaces.research_tools.signal_execution_stream import SignalExecutionFactsStream
from wavequant.interfaces.research_tools.stock_backtest import _post_b_wave_exit_events, signal_execution_facts


def sample():
    prices = [(8, 10, 7, 9), (9, 12, 8, 10), (10, 14, 9, 11),
              (11, 18, 10, 12), (13, 16, 10, 15), (15, 20, 12, 18), (16, 21, 8, 17)]
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', *row, 100)
            for index, row in enumerate(prices)]
    source = dict(event='n_completed', direction='up', origin=1, bar_index=2, known_at=3,
                  one_p=15, two_t=19, defense=9, n_id='n:one', target_primary=True)
    first = dict(event='long_signal', channel='wave_push_gap', attack=2,
                 bar_index=3, wave_b_low_index=0, wave_a_class='strong')
    second = dict(first, bar_index=5, wave_b_low_index=1)
    return bars, source, first, second


def test_each_n_observation_is_shared_by_multiple_long_owners_and_matches_cold_prefixes():
    bars, source, first, second = sample()
    stream = SignalExecutionFactsStream(bars)
    for index in range(len(bars)):
        audit = ([source, first] if index >= 3 else []) + ([second] if index >= 5 else [])
        facts = stream.advance(index, audit)
        assert facts == signal_execution_facts(bars[:index + 1], SimpleNamespace(audit=audit))
    assert stream.processed_observations == 4
    n_bars, events = facts
    assert n_bars == {3: 2}
    assert [(event['event'], event['bar_index'], event['owner_signal_index']) for event in events] == [
        ('wave_segment_start', 3, 3), ('wave_n_target_reached', 4, 3),
        ('wave_n_target_reached', 5, 3), ('wave_projection_invalidated', 6, 3),
        ('wave_segment_start', 5, 5), ('wave_n_target_reached', 4, 5),
        ('wave_n_target_reached', 5, 5), ('wave_projection_invalidated', 6, 5),
    ]
    assert all(event['bar_index'] != 3 for event in events if event['event'] == 'wave_n_target_reached')


def test_execution_targets_start_at_new_eligibility_without_reviving_old_owner():
    bars, source, owner, _ = sample()
    stream = SignalExecutionFactsStream(bars, start_index=5)
    assert stream.advance(5, [source, owner]) == ({}, [])
    assert stream.processed_observations == 0
    current = dict(source, known_at=5, two_t=19)
    current_owner = dict(owner, bar_index=5)
    _, events = stream.advance(5, [source, owner, current, current_owner])
    assert stream.processed_observations == 1
    assert all(row['bar_index'] >= 5 for row in events)
    assert not any(row.get('reached_stage') == 'two_t' for row in events)


def test_old_window_retirement_does_not_end_reopened_same_attack_target():
    bars, source, owner, _ = sample()
    old = dict(source, n_id='n:one:window:3', window_start=3)
    reopened = dict(source, n_id='n:one:window:5', known_at=5, window_start=5)
    old_owner = dict(owner, window_start=3)
    new_owner = dict(owner, bar_index=5, window_start=5)
    retired = dict(event='n_target_source_retired', attack=2, bar_index=4,
                   n_id=old['n_id'], window_start=3)
    stream = SignalExecutionFactsStream(bars)
    _, events = stream.advance(5, [old, old_owner, retired, reopened, new_owner])
    fresh = [row for row in events if row.get('owner_signal_index') == 5]
    assert not any(row['event'] == 'wave_projection_invalidated' for row in fresh)
    assert any(row['event'] == 'wave_n_target_reached' and row.get('n_id') == reopened['n_id'] for row in fresh)
    assert not any(row.get('n_id') == reopened['n_id'] and row.get('owner_signal_index') == 3 for row in events)


def test_overlapping_scopes_keep_reformed_targets_and_projection_owners_separate():
    bars, source, owner, _ = sample()
    scope_a = dict(window_start=3, source_path='A', qualification_window_start=3)
    scope_b = dict(window_start=3, source_path='B', qualification_window_start=4)
    left = dict(source, n_id='n:A', target_primary=False, **scope_a)
    right = dict(source, n_id='n:B', known_at=4, target_primary=False, **scope_b)
    first, second = dict(owner, **scope_a), dict(owner, bar_index=4, **scope_b)
    closed = dict(event='n_target_source_retired', attack=2, bar_index=5,
                  n_id='n:A', reason='trading_eligibility_closed', **scope_a)
    projection = dict(event='wave_projection_target_reached', attack=2, origin_index=1,
                      bar_index=4, n_id='n:A', reached_stage='five_top', reached_target=15, **scope_a)
    stream = SignalExecutionFactsStream(bars)
    _, events = stream.advance(5, [left, right, first, second, closed, projection])
    left_events = [row for row in events if row.get('owner_signal_index') == 3]
    right_events = [row for row in events if row.get('owner_signal_index') == 4]
    assert any(row['event'] == 'wave_projection_invalidated' and row['bar_index'] == 5 for row in left_events)
    assert not any(row.get('n_id') == 'n:B' for row in left_events)
    assert not any(row.get('n_id') == 'n:A' for row in right_events)
    assert any(row.get('reached_stage') == 'two_t' for row in right_events)
    assert stream.active_positive_n_bars == {4: 2}


def test_active_n_view_excludes_closed_and_invalidated_structure_without_changing_batch_history():
    bars, source, _, _ = sample()
    structure = dict(source, n_id='n:structure', target_eligible=False,
                     window_start=3, source_path='A', qualification_window_start=3)
    replacement = dict(structure, n_id='n:replacement', source_path='B', qualification_window_start=4,
                       known_at=4, target_primary=False)
    closed = dict(event='n_target_source_retired', attack=2, bar_index=4, n_id='n:structure',
                  reason='trading_eligibility_closed', window_start=3, source_path='A', qualification_window_start=3)
    invalidated = dict(event='n_invalidated', attack=2, bar_index=5, n_id='n:replacement')
    stream = SignalExecutionFactsStream(bars)
    assert stream.advance(3, [structure])[0] == {3: 2}
    assert stream.active_positive_n_bars == {3: 2}
    assert stream.advance(4, [structure, replacement, closed])[0] == {3: 2, 4: 2}
    assert stream.active_positive_n_bars == {4: 2}
    stream.advance(5, [structure, replacement, closed, invalidated])
    assert stream.active_positive_n_bars == {}
    stream.advance(6, [structure, replacement, closed, invalidated])
    assert stream.active_positive_n_bars == {}


def test_ordinary_source_retirement_does_not_close_reformed_scope():
    bars, source, owner, _ = sample()
    source = dict(source, target_primary=False, window_start=3, source_path='A', qualification_window_start=3)
    owner = dict(owner, window_start=3, source_path='A', qualification_window_start=3)
    ordinary = dict(event='n_target_source_retired', attack=2, bar_index=4,
                    n_id=source['n_id'], reason='new_confirmed_decline')
    stream = SignalExecutionFactsStream(bars)
    _, events = stream.advance(5, [source, owner, ordinary])
    assert stream.active_positive_n_bars == {3: 2}
    assert any(row.get('reached_stage') == 'two_t' for row in events)
    assert not any(row['event'] == 'wave_projection_invalidated' for row in events)


def test_late_source_and_late_owner_only_catch_up_the_new_lifetime():
    bars, source, first, second = sample()
    stream = SignalExecutionFactsStream(bars)
    stream.advance(4, [first])
    _, events = stream.advance(5, [source, first])
    assert [row['bar_index'] for row in events if row['event'] == 'wave_n_target_reached'] == [4, 5]
    observed = stream.processed_observations
    stream.advance(5, [source, first, second])
    assert stream.processed_observations == observed
    assert stream.advance(6, [source, first, second]) == signal_execution_facts(
        bars, SimpleNamespace(audit=[source, first, second]))
    assert stream.processed_observations == 4


def test_retirement_same_day_wins_over_target_and_historical_revision_replaces_old_events():
    bars, source, first, _ = sample()
    stream = SignalExecutionFactsStream(bars)
    audit = [source, first]
    stream.advance(5, audit)
    retirement = dict(event='n_target_source_retired', attack=2, bar_index=5)
    audit.append(retirement)
    _, events = stream.advance(5, audit)
    assert [(row['event'], row['bar_index']) for row in events[1:]] == [
        ('wave_n_target_reached', 4), ('wave_projection_invalidated', 5)]
    retirement['bar_index'] = 4
    _, revised = stream.advance(5, audit)
    assert [(row['event'], row['bar_index']) for row in revised[1:]] == [('wave_projection_invalidated', 4)]
    audit.pop()
    assert stream.advance(6, audit) == signal_execution_facts(bars, SimpleNamespace(audit=audit))


def test_changed_source_price_eligibility_and_removal_cannot_leave_old_targets():
    bars, source, first, _ = sample()
    stream = SignalExecutionFactsStream(bars)
    audit = [source, first]
    before = deepcopy(stream.advance(5, audit))
    source['one_p'], source['two_t'] = 25, 30
    _, changed = stream.advance(5, audit)
    assert any(row['event'] == 'wave_n_target_reached' for row in before[1])
    assert not any(row['event'] == 'wave_n_target_reached' for row in changed)
    source['target_eligible'] = False
    assert stream.advance(6, audit) == ({3: 2}, [dict(event='wave_segment_start', attack=3,
        bar_index=3, origin_index=0, owner_signal_index=3)])
    assert stream.advance(6, [first])[0] == {}


def test_independent_same_attack_sources_keep_retirement_ownership_and_duplicate_order():
    bars, primary, owner, _ = sample()
    secondary = dict(primary, origin=0, n_id='n:secondary', target_primary=False)
    retirement = dict(event='n_target_source_retired', attack=2, bar_index=4)
    audit = [secondary, primary, secondary, retirement, owner]
    stream = SignalExecutionFactsStream(bars)
    _, events = stream.advance(5, audit)
    assert [row['n_id'] for row in events if row['event'] == 'wave_n_target_reached'] == [
        'n:secondary', 'n:secondary', 'n:secondary', 'n:secondary']
    assert stream.segment_events(owner) == _post_b_wave_exit_events(bars[:6], audit, owner)
    assert stream.processed_observations == 5


def test_plain_n_availability_does_not_require_unused_target_fields():
    bars, _, _, _ = sample()
    audit = [dict(event='n_completed', direction='up', bar_index=2)]
    assert SignalExecutionFactsStream(bars).advance(3, audit) == ({2: 2}, [])


def test_strong_continuation_keeps_parent_projection_and_ordinary_entry_fields():
    bars, source, owner, _ = sample()
    owner = dict(owner, wave_b_low_index=1, attack=0, wave_entry_path='two_t_strong_a_resistance_rebreak')
    parent = dict(event='wave_projection_target_reached', attack=0, origin_index=0, bar_index=4,
                  reached_stage='five_top', reached_target=18)
    ordinary = dict(event='long_signal', attack=2, bar_index=5, wave_a_class='ordinary',
                    wave_equal_target=17, wave_c_0618_target=15, wave_entry_one_p=15,
                    wave_entry_two_t=19, wave_a_origin=8, wave_a_high=14,
                    wave_a_high_index=2, wave_b_low=10, wave_b_low_index=3)
    _, events = SignalExecutionFactsStream(bars).advance(5, [parent, source, owner, ordinary])
    assert events[0] == parent
    assert dict(parent, owner_signal_index=3) in events
    assert events[-1] == dict(event='wave_ordinary_entry', attack=2, bar_index=5, owner_signal_index=5,
        target=17, target_0618=15, one_p=15, two_t=19, a_origin=8, a_high=14,
        a_high_index=2, b_low=10, b_low_index=3)
