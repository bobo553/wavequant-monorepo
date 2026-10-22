"""Archived chart targets keep their own qualification window and N identity."""

from datetime import datetime, timedelta

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.interfaces.charts.visualization import ChartRepository


def market():
    rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (10.8, 12.6, 10.4, 12.2), (12.2, 14, 12, 13.8), (14, 17.3, 13.5, 17.1),
            (17.2, 21.9, 17, 21.8), (15, 16, 14, 15.5), (15.5, 18, 15, 17)]
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), 'TEST', o, h, l, c, 1000)
            for index, (o, h, l, c) in enumerate(rows)]


def positive_n(bars, *, window_start=3, primary=True):
    return dict(event='n_completed', timestamp=bars[3].timestamp.isoformat(), bar_index=3,
                known_at=window_start, origin=0, neckline=1, pullback=2, attack=3,
                direction='up', defense=10.4, target_eligible=True, target_primary=primary,
                window_start=window_start, qualification_window_start=window_start,
                source_path='same-geometry', n_id=f'positive:0:1:2:3:window:{window_start}',
                **({} if primary else dict(reformed_from=3, reformed_root=3)))


def retirement(bars, row, *, index=5, reason='trading_eligibility_closed'):
    return dict(event='n_target_source_retired', timestamp=bars[index].timestamp.isoformat(),
                bar_index=index, attack=row['attack'], n_id=row['n_id'], reason=reason,
                window_start=row['window_start'], source_path=row['source_path'])


def render(bars, audit):
    theory = ChartRepository.render_theory(
        None, bars, SystemStrategy(), SystemResult([], audit, {}),
        bars[-1].timestamp.date().isoformat(), geometry={'tertiary_trends': {}},
    )
    return {row['n_id']: row for row in theory['events'] if row['event'] == 'n_completed'}


def target_levels(row):
    return {level['stage']: level for level in row['levels']
            if level.get('stage') in ('one_p', 'two_t', 'five_top', 'ten_full')}


def test_matching_structural_fact_keeps_one_chart_n_and_trading_source_identity():
    bars = market()
    trade = positive_n(bars)
    structure = dict(trade, event='n_structure_completed', n_id='polyline:separate',
        structural_target=True, target_eligible=False, scope_low_index=0, scope_low_ordinal=0,
        real_break=True, virtual_break=True, box_anchor=12.6, equal_wave=14, one_p=17.2, two_t=21.8)
    invalid = dict(event='n_structure_invalidated', bar_index=7, known_at=7,
        timestamp=bars[7].timestamp.isoformat(), n_id=structure['n_id'], reason='defense_breached')
    rows = render(bars, [trade, structure, invalid])
    assert list(rows) == [trade['n_id']]
    row = rows[trade['n_id']]
    assert row['target_eligible'] is True
    assert row['structural_target'] is True
    assert row['n_invalidated_at'] == '2026-01-08'
    assert target_levels(row)['two_t']['price'] == 21.8
    assert target_levels(row)['two_t']['valid_until'] == '2026-01-07'


def test_reformed_n_targets_stop_at_qualification_close_not_primary_source_retirement():
    bars = market()
    row = positive_n(bars, primary=False)
    natural = retirement(bars, row, index=4, reason='lower_floor')
    closed = retirement(bars, row)
    before = target_levels(render(bars[:5], [row, natural, closed])[row['n_id']])
    assert set(before) == {'one_p', 'two_t'}
    assert all('valid_until' not in level for level in before.values())

    after = target_levels(render(bars, [row, natural, closed])[row['n_id']])
    assert set(after) == {'one_p', 'two_t'}
    assert all(level['valid_until'] == '2026-01-05' for level in after.values())
    assert all(level['available_at'] == '2026-01-04' for level in after.values())


@pytest.mark.parametrize('primary', (True, False))
def test_same_attack_reopened_window_does_not_inherit_old_target_retirement(primary):
    bars = market()
    old = positive_n(bars, primary=primary)
    reopened = positive_n(bars, window_start=7, primary=primary)
    audit = [old, retirement(bars, old), reopened]
    early = render(bars[:7], audit)
    assert set(early) == {old['n_id']}

    events = render(bars, audit)
    assert events[old['n_id']]['id'] != events[reopened['n_id']]['id']
    old_levels = target_levels(events[old['n_id']])
    new_levels = target_levels(events[reopened['n_id']])
    assert all(level['valid_until'] == '2026-01-05' for level in old_levels.values())
    assert set(new_levels) == {'one_p', 'two_t'}
    assert all('valid_until' not in level for level in new_levels.values())
    assert all(level['available_at'] == '2026-01-08' for level in new_levels.values())


def test_strong_a_five_top_joins_its_target_source_id_and_keeps_window_cutoff():
    bars = market()
    old = positive_n(bars)
    reopened = positive_n(bars, window_start=7)
    strong_a = dict(event='long_signal', timestamp=bars[4].timestamp.isoformat(), bar_index=4,
                    attack=3, target_source_attack=3, target_source_id=old['n_id'],
                    window_start=3, source_path=old['source_path'],
                    wave_entry_path='two_t_strong_a_resistance_rebreak', wave_five_top_target=25,
                    wave_entry_two_t=21.8, wave_a_origin=8, wave_a_high=14, wave_b_low=12)
    events = render(bars, [old, strong_a, retirement(bars, old), reopened])
    old_levels = target_levels(events[old['n_id']])
    assert old_levels['five_top']['price'] == 25
    assert old_levels['five_top']['available_at'] == '2026-01-05'
    assert old_levels['five_top']['valid_until'] == '2026-01-05'
    assert 'five_top' not in target_levels(events[reopened['n_id']])
