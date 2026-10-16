from copy import deepcopy

import pytest

from wavequant.domain.market_structure.trend_display import publish_trend_display


def geometry(level=3, values=None):
    values = values or [12, 5, 6, 5.5, 7, 6, 13, 11, 14]
    points = [dict(index=index, ordinal=0, kind='H' if index % 2 == 0 else 'L', value=value,
                   time=f'2020-01-{index + 1:02d}', available_at=f'2020-01-{index + 1:02d}')
              for index, value in enumerate(values)]
    return dict(scope='structural_trend_points_with_independent_strategy_permissions',
                strokes=[dict(id='path', source_path='lower-path', trend_level=level,
                              kind='secondary' if level == 2 else 'tertiary', points=points)],
                developing_strokes=[])


def flip(level=3, **extra):
    return dict(dict(event='trend_lifecycle_state', trend_level=level, source_path='path',
                     bar_index=6, available_index=6, origin_index=5, flip_index=6, flip_known_index=6,
                     source_event='翻空为多', direction='up', state='空翻多',
                     flip_confirmed=True, special_bull_recovery=False), **extra)


def guarded_source(row):
    source = deepcopy(row)
    stroke = source['strokes'][0]
    stroke.update(id='lower-path', source_path='first-path', trend_level=2)
    key = dict(stroke['points'][0])
    for point in stroke['points'][1:]:
        point['lifecycle_context'] = dict(direction='up', key=key)
    return source


def rising_legs(row):
    return [(left['index'], right['index']) for stroke in row['strokes']
            for left, right in zip(stroke['points'], stroke['points'][1:]) if left['kind'] == 'L']


def assert_continuous(stroke):
    for left, right in zip(stroke['points'], stroke['points'][1:]):
        assert (left['index'], left.get('ordinal', 0)) < (right['index'], right.get('ordinal', 0))
        assert left['kind'] != right['kind']
        assert (right['value'] > left['value']) == (left['kind'] == 'L')


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('state_change', [None, 'bear_flip', 'source_evidence_revised', 'confirmation_gap_filled'])
def test_lifecycle_or_eligibility_windows_do_not_cut_structural_paths(level, state_change):
    row = geometry(level)
    stroke = row['strokes'][0]
    points = stroke['points']
    stroke['structural_legs'] = [dict(points=[left, right], basis='original-structure')
                                 for left, right in zip(points, points[1:])]
    stroke['confirmed_legs'] = [dict(points=points[5:7], proof={'source': 'original-confirmation'})]
    row['developing_strokes'] = [dict(id='tail', source_path='lower-path', display_only=True,
                                     points=points[-1:] + [dict(index=9, kind='L', value=12)])]
    row.update(confirmed_wave_count=9, developing_wave_count=1, developing_point_count=2)
    audit = []
    if state_change:
        audit.append(flip(level))
        if state_change == 'bear_flip':
            audit.append(flip(level, direction='down', source_event='翻多为空', state='多翻空',
                              origin_index=6, bar_index=7, available_index=7))
        else:
            event = ('trend_lifecycle_invalidated' if state_change == 'source_evidence_revised'
                     else 'trend_lifecycle_confirmation_revoked')
            audit.append(dict(event=event, trend_level=level, source_path='path', bar_index=7, reason=state_change))
    before, audit_before = deepcopy(row), deepcopy(audit)
    chart = publish_trend_display(row, audit, level=level, asof_index=9)
    assert all(chart[key] == value for key, value in before.items())
    assert chart['strokes'][0]['id'] == 'path'
    assert not chart['strokes'][0].get('prevent_display_bridge')
    assert_continuous(chart['strokes'][0])
    assert row == before and audit == audit_before


def test_missing_lifecycle_audit_or_source_metadata_is_not_rejection_evidence():
    row = geometry()
    source = guarded_source(row)
    source['strokes'][0]['id'] = 'unrelated-path'
    for lower in (None, {}, source):
        chart = publish_trend_display(row, [], level=3, asof_index=8, source_row=lower)
        assert chart['strokes'] == row['strokes']
        assert all(observation['confirmed'] is False for observation in chart['observation_strokes'])


def test_future_frozen_key_cannot_rewrite_an_earlier_visible_path():
    row = geometry(values=[20, 5, 6])
    source = guarded_source(row)
    source['strokes'][0]['points'][2]['lifecycle_context']['key']['available_at'] = '2020-02-01'
    chart = publish_trend_display(row, [], level=3, asof_index=2, source_row=source)
    assert chart['strokes'] == row['strokes']


def test_unbroken_frozen_key_folds_local_rebounds_into_one_continuous_path():
    row = geometry(values=[12, 9, 11, 8, 10, 7, 13, 11, 14])
    source = guarded_source(row)
    before = deepcopy(row)
    chart = publish_trend_display(row, [], level=3, asof_index=8, source_row=source)
    assert len(chart['strokes']) == 1
    path = chart['strokes'][0]
    assert path['id'] == 'path'
    assert [point['index'] for point in path['points']] == [0, 5, 6, 7, 8]
    assert rising_legs(chart) == [(5, 6), (7, 8)]
    assert_continuous(path)
    assert row == before


def test_fold_keeps_earliest_equal_low_and_does_not_substitute_a_higher_low():
    row = geometry(values=[12, 5, 6, 5, 7, 6, 13])
    chart = publish_trend_display(row, [], level=3, asof_index=6, source_row=guarded_source(row))
    assert [point['index'] for point in chart['strokes'][0]['points']] == [0, 1, 6]
    assert_continuous(chart['strokes'][0])


def test_merged_display_edges_cannot_inherit_removed_leg_confirmation():
    row = geometry(values=[12, 9, 11, 8, 10, 7, 13])
    points = row['strokes'][0]['points']
    kept = dict(points=points[5:7], proof={'id': 'kept'})
    removed = dict(points=points[:2], proof={'id': 'removed'})
    row['strokes'][0].update(structural_legs=[kept, removed], confirmed_legs=[kept, removed])
    chart = publish_trend_display(row, [], level=3, asof_index=6, source_row=guarded_source(row))
    stroke = chart['strokes'][0]
    assert stroke['confirmed_legs'] == [kept]
    assert stroke['structural_legs'][-1] == kept
    assert stroke['structural_legs'][0]['display_only'] is True
    assert 'proof' not in stroke['structural_legs'][0]


@pytest.mark.parametrize('wrong', [dict(trend_level=1), dict(trend_level=3), dict(source_path='another'),
                                   dict(flip_confirmed=False), dict(source_event='空多交替'), dict(available_index=9)])
def test_wrong_or_future_events_cannot_override_an_explicit_frozen_key(wrong):
    row = geometry(values=[20, 5, 6, 5.5, 7, 6, 13, 11, 14])
    source = guarded_source(row)
    special = flip(2, source_path='lower-path', special_bull_recovery=True,
                   source_event='短暂跌破后同级再次空翻多')
    special.update(wrong)
    chart = publish_trend_display(row, [special], level=3, asof_index=8, source_row=source)
    assert rising_legs(chart) == []
    assert len(chart['strokes']) == 1
    assert [point['index'] for point in chart['strokes'][0]['points']] == [0, 1]


def test_existing_special_recovery_opens_a_continuous_wave_without_waiting_for_eligibility():
    row = geometry(values=[20, 5, 6, 5.5, 7, 6, 13, 11, 14])
    source = guarded_source(row)
    special = flip(2, source_path='lower-path', special_bull_recovery=True,
                   source_event='短暂跌破后同级再次空翻多')
    complete = dict(special, source_event='短暂跌破后空多交替结束', state='空多交替', bar_index=7, available_index=7)
    blocked = publish_trend_display(row, [complete], level=3, asof_index=8, source_row=source)
    assert rising_legs(blocked) == []
    for audit in ([special], [special, complete]):
        chart = publish_trend_display(row, audit, level=3, asof_index=8, source_row=source)
        assert rising_legs(chart) == [(1, 6), (7, 8)]
        assert_continuous(chart['strokes'][0])


@pytest.mark.parametrize('reason, expected', [
    ('origin_broken', [(1, 6)]), ('source_evidence_revised', []), ('source_path_ended', [])])
def test_later_price_failure_preserves_confirmed_history_but_source_revocation_does_not(reason, expected):
    row = geometry(values=[20, 5, 6, 5.5, 7, 6, 13, 11, 14])
    source = guarded_source(row)
    special = flip(2, source_path='lower-path', special_bull_recovery=True,
                   source_event='短暂跌破后同级再次空翻多')
    stop = dict(event='trend_lifecycle_invalidated', trend_level=2, source_path='lower-path',
                bar_index=7, reason=reason)
    chart = publish_trend_display(row, [special, stop], level=3, asof_index=8, source_row=source)
    assert rising_legs(chart) == expected
    assert_continuous(chart['strokes'][0])


def test_a_future_lower_breakout_cannot_authorize_an_earlier_local_high():
    row = geometry(values=[20, 5, 6, 5.5, 7, 6, 13, 11, 21])
    source = guarded_source(row)
    later = flip(2, source_path='lower-path', origin_index=5, flip_index=8,
                  flip_known_index=8, bar_index=8, available_index=8)
    chart = publish_trend_display(row, [later], level=3, asof_index=8, source_row=source)
    assert rising_legs(chart) == [(1, 8)]
    assert [point['index'] for point in chart['strokes'][0]['points']] == [0, 1, 8]


def test_guofang_unbroken_last_fall_high_remains_an_observation_without_an_orphan_down_leg():
    key = dict(index=0, ordinal=0, time='2018-05-28', kind='H', value=11.50, available_at='2018-06-28')
    low = dict(index=1, ordinal=0, time='2018-10-19', kind='L', value=4.91, available_at='2018-10-24')
    high = dict(index=2, ordinal=0, time='2019-04-03', kind='H', value=6.96, available_at='2019-04-25')
    later_low = dict(index=3, ordinal=0, time='2019-05-01', kind='L', value=4.5, available_at='2019-05-10')
    lower = dict(strokes=[dict(id='secondary-path', source_path='first-path',
        points=[key, low, dict(high, lifecycle_context=dict(direction='up', key=key)), later_low])])
    upper = dict(strokes=[dict(id='tertiary-path', source_path='secondary-path', points=[key, low, high, later_low])])
    false_flip = flip(2, source_path='secondary-path', origin_index=1, flip_index=2,
                      flip_known_index=3, bar_index=3, available_index=3)
    chart = publish_trend_display(upper, [false_flip], level=3, asof_index=3, source_row=lower)
    assert [point['index'] for point in chart['strokes'][0]['points']] == [0, 3]
    assert chart['observation_strokes'][0]['source_key'] == key
    assert chart['observation_strokes'][0]['reason'] == 'source_level_bull_flip_unconfirmed'
    assert rising_legs(chart) == []
    assert_continuous(chart['strokes'][0])
    second = publish_trend_display(lower, [false_flip], level=2, asof_index=3)
    assert second['strokes'] == lower['strokes']


def test_own_explicit_unbroken_key_uses_the_same_continuity_reducer():
    row = geometry(values=[20, 5, 6, 5.5, 7, 6, 13, 11, 14])
    own = guarded_source(row)
    own['strokes'][0].update(id='path', source_path='lower-path', trend_level=3)
    chart = publish_trend_display(own, [], level=3, asof_index=8)
    assert rising_legs(chart) == []
    special = flip(3, special_bull_recovery=True, source_event='短暂跌破后同级再次空翻多')
    chart = publish_trend_display(own, [special], level=3, asof_index=8)
    assert rising_legs(chart) == [(1, 6), (7, 8)]


@pytest.mark.parametrize('values, expected_formal, expected_tail', [
    ([12, 9, 11, 8], [0], [0, 3]),
    ([12, 9, 11, 10, 13], [0, 1], [1, 4]),
    ([12, 9, 11], [0, 1], []),
])
def test_developing_tail_shares_the_retained_path_anchor(values, expected_formal, expected_tail):
    full = geometry(values=values)
    source = guarded_source(full)
    row = deepcopy(full)
    points = row['strokes'][0]['points']
    row['strokes'][0]['points'] = points[:-1]
    row['developing_strokes'] = [dict(id='tail', source_path='lower-path', display_only=True,
                                     confirmed_anchor=points[-2], points=points[-2:])]
    before = deepcopy(row)
    chart = publish_trend_display(row, [], level=3, asof_index=len(values) - 1, source_row=source)
    assert [point['index'] for point in chart['strokes'][0]['points']] == expected_formal
    if expected_tail:
        tail = chart['developing_strokes'][0]
        assert [point['index'] for point in tail['points']] == expected_tail
        assert tail['points'][0] == chart['strokes'][0]['points'][-1]
        assert tail['confirmed_anchor'] == tail['points'][0]
        assert tail['id'] == 'tail'
        assert_continuous(tail)
    else:
        assert chart['developing_strokes'] == []
    assert row == before


def test_preserves_independent_source_paths_and_original_ids():
    row = geometry()
    row['strokes'].append(dict(id='next-path', source_path='next-lower-path',
                               points=[dict(index=20, ordinal=0, kind='L', value=3)]))
    chart = publish_trend_display(row, [], level=3, asof_index=20, source_row=guarded_source(row))
    assert [stroke['id'] for stroke in chart['strokes']] == ['path', 'next-path']
    assert chart['strokes'][1] == row['strokes'][1]
