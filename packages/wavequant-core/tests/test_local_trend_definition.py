"""普通四点豁免仅限实际升级段；未升级和异路径保持原门槛。"""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import completed_source_points
from wavequant.domain.market_structure.trend_promotion_scope import PROMOTION_FIELD


def local_source(level: int, mirror: bool, dates: bool, upper: int | None = None, last_high: float = 22):
    start = date(2024, 1, 1)
    source = []
    for index, value in enumerate((60, 15, 50, 10, 20, 12, last_high)):
        kind = 'H' if index % 2 == 0 else 'L'
        source.append(dict(index=index, ordinal=0, time=(start + timedelta(days=index)).isoformat(),
            available_at=(start + timedelta(days=index + 1)).isoformat() if dates else index + 1,
            kind=('L' if kind == 'H' else 'H') if mirror else kind,
            value=100 - value if mirror else value, trend_level=level - 1, state='confirmed', source_path='sample'))
    key = dict(source[2], trend_level=level, available_at=source[3]['available_at'])
    if upper is not None:
        direction = 'up' if mirror else 'down'
        rule = (f'level{upper - 1}_confirmed_' + ('high' if mirror else 'low')
                + f'_breaks_known_level{upper}_' + ('last_fall_high' if mirror else 'last_rise_low'))
        ref = lambda index: {field: source[index][field] for field in
                            ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')}
        record = dict(ref(3), trend_level=upper, source_level=upper-1, wave_identity_state='owned',
            wave_completion_policy='whole_wave_v117', wave_direction_before=direction,
            wave_direction_after=direction, confirmation_rule=rule,
            upgrade_evidence=dict(role='endpoint', direction=direction, confirmation_rule=rule,
                available_at=source[3]['available_at'], origin=ref(2), broken_key=ref(1), confirmed_by=ref(3)),
            _promotion_target_level=level, _promotion_target_path='sample')
        source[3][PROMOTION_FIELD] = [record]
    return source, key


@pytest.mark.parametrize('level,upper', [(1, 2), (1, 3), (2, 3)])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('dates', [False, True])
def test_only_actual_promoted_wave_releases_own_four_point_completion(level, upper, mirror, dates):
    ordinary, key = local_source(level, mirror, dates)
    assert not any(point['index'] == 3 for point in
                   promote_trend_key_breaks(ordinary, [key], trend_level=level))
    source, key = local_source(level, mirror, dates, upper)
    before = deepcopy(source)
    events = []
    result = promote_trend_key_breaks(source, [key], trend_level=level, event_sink=events)
    closed = next(point for point in result if point['index'] == 3)
    assert closed['wave_direction_after'] == ('down' if mirror else 'up')
    assert closed['wave_completed_at'] == source[6]['available_at']
    proof = closed['lower_wave_identity_evidence']
    assert proof['local_definition_boundary']['index'] == 3
    assert proof['higher_trend_promotion']['trend_level'] == upper
    assert 'reversal_key' not in proof
    assert PROMOTION_FIELD not in closed
    assert not any('upgrade_evidence' in point for point in events)
    assert any(point['index'] == 3 for point in completed_source_points(result, source_level=level))
    assert source == before


@pytest.mark.parametrize('level', [1, 2, 3])
def test_ordinary_unpromoted_wave_keeps_its_frozen_key(level):
    source, key = local_source(level, False, False)
    result = promote_trend_key_breaks(source, [key], trend_level=level)
    assert not any(point['index'] == 3 for point in result)


@pytest.mark.parametrize('last_high', [19, 20])
def test_promoted_wave_still_requires_two_strict_pairs(last_high):
    source, key = local_source(1, False, False, 2, last_high)
    assert not any(point['index'] == 3 for point in
                   promote_trend_key_breaks(source, [key], trend_level=1))


@pytest.mark.parametrize('change', ['same_level', 'path', 'endpoint', 'display'])
def test_wrong_level_path_or_display_promotion_is_rejected(change):
    source, key = local_source(1, False, False, 2)
    record = source[3][PROMOTION_FIELD][0]
    if change == 'same_level':
        record['trend_level'] = 1
    elif change == 'path':
        record['_promotion_target_path'] = 'other'
    elif change == 'endpoint':
        source[3][PROMOTION_FIELD] = []
        source[5][PROMOTION_FIELD] = [record]
    else:
        record['display_only'] = True
    with pytest.raises(ValueError):
        promote_trend_key_breaks(source, [key], trend_level=1)


def test_future_promotion_does_not_release_current_four_points():
    source, key = local_source(1, False, False, 2)
    record = source[3][PROMOTION_FIELD][0]
    record['available_at'] = record['upgrade_evidence']['available_at'] = 9
    assert not any(point['index'] == 3 for point in
                   promote_trend_key_breaks(source, [key], trend_level=1))


def test_completed_local_evidence_cannot_remove_its_actual_promotion():
    source, key = local_source(2, False, False, 3)
    result = promote_trend_key_breaks(source, [key], trend_level=2)
    closed = next(point for point in result if point['index'] == 3)
    closed['wave_completion_evidence']['lower_wave_identity_evidence'].pop('higher_trend_promotion')
    with pytest.raises(ValueError, match='higher-level promotion'):
        completed_source_points(result, source_level=2)
