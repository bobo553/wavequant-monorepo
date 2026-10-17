"""Overlapping qualification paths cannot share decision ownership or retire each other."""

from datetime import datetime, timedelta

from wavequant.domain.market_structure.positive_n_reformation import PositiveNReformationHistory, PositiveNSeed
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import (
    DailySignalState, _handoff_n_owners, _n_preparation_key, _qualified_n_identity,
)
from wavequant.domain.strategies.wave_projection_stream import WaveProjectionHistory


def test_same_geometry_and_continuous_window_have_distinct_qualification_owners():
    left = dict(window_start=3, source_path='A', qualification_window_start=3)
    right = dict(window_start=3, source_path='B', qualification_window_start=3)
    assert _qualified_n_identity(0, 1, 2, 3, left) != _qualified_n_identity(0, 1, 2, 3, right)
    event = dict(direction='up', bar_index=3, origin=0, neckline=1, pullback=2, known_at=4)
    canonical = _n_preparation_key(dict(event, **left))
    audit = [dict(event, **left), dict(event, **right)]
    assert [row for row in audit if _n_preparation_key(row) == canonical] == [audit[0]]


def test_owner_handoff_resets_only_departed_decisions_and_uses_new_target_observations():
    state = DailySignalState()
    state.canonical_n_owners = {3: 'A', 7: 'C'}
    state.emitted_attacks = {3, 7}
    state.bottom_hits = {3: {'equal_wave', 'one_p'}, 7: {'equal_wave'}}
    state.emitted_waves = {(3, 4, 5): ('strong', 20), (7, 8, 9): ('strong', 30)}
    state.emitted_nested = {(4, 5), (8, 9)}
    state.emitted_nested_owners = {(4, 5): {'A'}, (8, 9): {'A', 'C'}}
    state.c_equal_events = [dict(n_id='A', attack=3), dict(n_id='C', attack=7)]
    state.emitted_secondary_reclaims = {11}
    _handoff_n_owners(state, {3: 'B', 7: 'C'}, {3: {'equal_wave'}, 7: {'equal_wave'}})
    assert state.emitted_attacks == {7}
    assert state.bottom_hits == {3: {'equal_wave'}, 7: {'equal_wave'}}
    assert state.emitted_waves == {(7, 8, 9): ('strong', 30)}
    assert state.emitted_nested == {(8, 9)}
    assert state.emitted_nested_owners == {(8, 9): {'C'}}
    assert state.c_equal_events == [dict(n_id='C', attack=7)]
    assert state.emitted_secondary_reclaims == {11}
    state.emitted_attacks.add(3)
    _handoff_n_owners(state, {3: 'B', 7: 'C'}, {})
    assert state.emitted_attacks == {3, 7}, 'unchanged owner keeps already consumed decisions'


def market():
    rows = [(8.5, 9, 8, 8.5), (9, 10, 9, 9.5), (10, 30, 9, 18),
            (13, 16, 7, 13), (14, 15, 13, 14)]
    return [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', *row, 100)
            for index, row in enumerate(rows)]


def test_projection_handoff_starts_at_new_owner_without_inheriting_old_origin_failure():
    bars = market()
    setup = WaveProjectionSetup(0, 1, 2, 8, 10, 14, 9)
    stream = WaveProjectionHistory(bars, start_index=1)
    try:
        old = stream.advance(setup, 3, source_identity='A', measurement_start=1)
        assert old[-1].event == 'wave_projection_invalidated'
        assert stream.advance(setup, 3, source_identity='B', measurement_start=3) == ()
        fresh = stream.advance(setup, 4, source_identity='B', measurement_start=3)
        assert fresh[0].event == 'wave_projection_ready' and fresh[0].bar_index == 4
        assert stream.advance(setup, 4, source_identity='A', measurement_start=1) == old
    finally:
        stream.close()


def test_late_known_reformation_does_not_consume_earlier_defense_or_origin_failure():
    bars = market()
    seed = PositiveNSeed(0, 1, 4, 9, measurement_start=4)
    stream = PositiveNReformationHistory(bars, start_index=1)
    assert stream.advance(4, [seed]) == ()
    assert stream.processed_days == 1
    family = next(iter(stream.families.values()))
    assert not family.finished and family.breach is None
