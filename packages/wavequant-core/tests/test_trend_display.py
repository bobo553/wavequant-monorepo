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


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('values', [
    [12, 9, 11, 8, 10, 7, 13, 11, 14],
    [12, 5, 6, 5, 7, 6, 13],
    [20, 5, 6, 5.5, 7, 6, 13, 11, 14],
])
@pytest.mark.parametrize('own_key', [False, True])
def test_frozen_key_doubt_cannot_change_the_core_structure(level, values, own_key):
    row = geometry(level, values)
    source = guarded_source(row)
    if own_key:
        row['strokes'][0]['points'] = deepcopy(source['strokes'][0]['points'])
    points = row['strokes'][0]['points']
    row['strokes'][0].update(
        structural_legs=[dict(points=[left, right], basis='shared-core') for left, right in zip(points, points[1:])],
        confirmed_legs=[dict(points=points[-2:], proof={'id': 'actual-permission'})])
    row.update(confirmed_wave_count=len(points), developing_wave_count=0, developing_point_count=0)
    before = deepcopy(row)
    chart = publish_trend_display(row, [], level=level, asof_index=len(values) - 1, source_row=source)
    assert all(chart[key] == value for key, value in before.items())
    assert chart['observation_strokes']
    assert all(item['display_only'] is True and item['confirmed'] is False for item in chart['observation_strokes'])
    assert row == before


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('reason', [None, 'origin_broken', 'source_evidence_revised', 'source_path_ended'])
def test_special_recovery_changes_metadata_only_and_never_rebuilds_edges(level, reason):
    row = geometry(level, [20, 5, 6, 5.5, 7, 6, 13, 11, 14])
    source = guarded_source(row)
    source['strokes'][0]['trend_level'] = level - 1
    special = flip(level - 1, source_path='lower-path', special_bull_recovery=True,
                   source_event='短暂跌破后同级再次空翻多')
    audit = [special]
    if reason:
        audit.append(dict(event='trend_lifecycle_invalidated', trend_level=level - 1,
                          source_path='lower-path', bar_index=7, reason=reason))
    before = deepcopy(row)
    chart = publish_trend_display(row, audit, level=level, asof_index=8, source_row=source)
    assert chart['strokes'] == before['strokes']
    assert chart['developing_strokes'] == before['developing_strokes']
    assert row == before


@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('values', [[12, 9, 11, 8], [12, 9, 11, 10, 13], [12, 9, 11]])
def test_developing_tail_keeps_its_shared_core_anchor_and_evidence(level, values):
    full = geometry(level, values)
    source = guarded_source(full)
    row = deepcopy(full)
    points = row['strokes'][0]['points']
    row['strokes'][0]['points'] = points[:-1]
    row['developing_strokes'] = [dict(id='tail', source_path='lower-path', display_only=True,
                                     confirmed_anchor=points[-2], points=points[-2:])]
    before = deepcopy(row)
    chart = publish_trend_display(row, [], level=level, asof_index=len(values) - 1, source_row=source)
    assert chart['strokes'] == before['strokes']
    assert chart['developing_strokes'] == before['developing_strokes']
    assert row == before


def test_guofang_price_relation_is_only_observed_by_display_not_repaired_there():
    key = dict(index=0, ordinal=0, time='2018-05-28', kind='H', value=11.50, available_at='2018-06-28')
    low = dict(index=1, ordinal=0, time='2018-10-19', kind='L', value=4.91, available_at='2018-10-24')
    high = dict(index=2, ordinal=0, time='2019-04-03', kind='H', value=6.96, available_at='2019-04-25')
    lower = dict(strokes=[dict(id='secondary-path', source_path='first-path',
        points=[key, low, dict(high, lifecycle_context=dict(direction='up', key=key))])])
    upper = dict(strokes=[dict(id='tertiary-path', source_path='secondary-path', points=[key, low, high])])
    chart = publish_trend_display(upper, [], level=3, asof_index=3, source_row=lower)
    assert chart['strokes'] == upper['strokes']
    assert chart['observation_strokes'][0]['source_key'] == key
    assert chart['observation_strokes'][0]['reason'] == 'source_level_bull_flip_unconfirmed'


def test_preserves_independent_source_paths_and_original_ids():
    row = geometry()
    row['strokes'].append(dict(id='next-path', source_path='next-lower-path',
                               points=[dict(index=20, ordinal=0, kind='L', value=3)]))
    chart = publish_trend_display(row, [], level=3, asof_index=20, source_row=guarded_source(row))
    assert chart['strokes'] == row['strokes']
