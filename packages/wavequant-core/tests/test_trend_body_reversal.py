"""缩减价格序列覆盖翻空后的影线反弹，不代替国芳完整回测。"""

from copy import deepcopy

import pytest

from wavequant.domain.market_structure.trend_body_reversal import BODY_REVERSAL_RULE, rejects_wick_extension
from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import _completed_source_point, _stamp_wave_completion


def known_keys(points, level):
    return [dict(point, trend_level=level) for point in points[:2]]


def source_case(level=2, rebound_close=7.83, rebound_high=9.32, rebound_open=8.47, anchor_close=8.91):
    prices = [7.88, 4.62, 7.31, 6.02, 8.87, 7.20, 8.91, 6.49, rebound_high, 5.89]
    points = [dict(index=i, ordinal=0, kind='H' if i % 2 == 0 else 'L', value=value,
                   time=str(i), available_at=i, label=f'p{i}', state='confirmed', trend_level=level-1)
              for i, value in enumerate(prices)]
    for i, opening, closing in ((6, 8.18, anchor_close), (8, rebound_open, rebound_close)):
        points[i]['price_body'] = dict(index=i, time=str(i), open=opening, close=closing,
                                       high=points[i]['value'], low=min(opening, closing) - 0.2)
    anchor, counter, key = points[6], points[7], points[5]
    def reference(point):
        return {name: point[name] for name in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label')}
    anchor.update(source_level=level-2, available_at=7, wave_direction_before='up', wave_direction_after='down',
        confirmation_rule='strict_lower_bear_flip', upgrade_evidence=dict(direction='down', role='origin',
            available_at=7, origin=reference(anchor), confirmed_by=reference(counter), broken_key=reference(key)))
    _stamp_wave_completion(anchor, closed=True)
    return points


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('rebound_close', [7.83, 8.91])
def test_wick_rebound_does_not_replace_high_and_lower_low_confirms_new_down_leg(level, rebound_close):
    points = source_case(level, rebound_close=rebound_close)
    events = []
    before = deepcopy(points)
    result = promote_trend_key_breaks(points, known_keys(points, level), trend_level=level, event_sink=events)
    assert points == before
    assert [(p['index'], p['kind']) for p in result][-2:] == [(6, 'H'), (9, 'L')]
    assert not any(p['index'] == 8 for p in result)
    closure = next(p for p in events if p.get('confirmation_rule') == BODY_REVERSAL_RULE and p['kind'] == 'H')
    assert closure['wave_completed_at'] == 9
    projected = _completed_source_point(closure)
    assert projected['index'] == 6 and projected['available_at'] == 9
    assert closure['body_reversal_evidence']['rebound']['value'] == 9.32


@pytest.mark.parametrize('level', [2, 3])
def test_unbroken_counter_cannot_publish_a_completed_body_reversal(level):
    points = source_case(level)
    events = []
    result = promote_trend_key_breaks(points[:-1], known_keys(points, level), trend_level=level, event_sink=events)
    assert result[-1]['index'] == 6
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)
    points[-1]['value'] = points[7]['value']
    promote_trend_key_breaks(points, known_keys(points, level), trend_level=level, event_sink=events)
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)


@pytest.mark.parametrize('change', ['close_break', 'missing_body', 'missing_bear_flip'])
def test_only_a_proven_close_rejection_blocks_normal_high_extension(change):
    points = source_case(rebound_close=9.0 if change == 'close_break' else 7.83)
    if change == 'missing_body':
        points[8].pop('price_body')
    if change == 'missing_bear_flip':
        for field in ('wave_completion_policy', 'wave_identity_state', 'wave_completed_at', 'wave_completion_evidence'):
            points[6].pop(field)
    events = []
    result = promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    assert result[-1]['index'] == 8
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('rebound_open,anchor_close,rebound_close', [
    (9.10, 8.91, 8.80),
    (8.47, 8.20, 8.80),
    (9.10, 8.20, 8.91),
])
def test_exception_compares_close_to_anchor_high_not_either_candle_body(level, rebound_open, anchor_close, rebound_close):
    points = source_case(level, rebound_open=rebound_open, anchor_close=anchor_close, rebound_close=rebound_close)
    result = promote_trend_key_breaks(points, known_keys(points, level), trend_level=level)
    assert [(p['index'], p['kind']) for p in result][-2:] == [(6, 'H'), (9, 'L')]


def test_equal_high_does_not_enter_close_exception():
    points = source_case(rebound_high=8.91)
    assert not rejects_wick_extension(points[6], points[7], points[8])


def test_close_above_anchor_high_cancels_pending_exception():
    points = source_case()
    points[-1]['value'] = 6.7
    points.append(dict(index=10, ordinal=0, kind='H', value=9.6, time='10', available_at=10,
                       label='p10', state='confirmed', trend_level=1,
                       price_body=dict(index=10, time='10', open=8.5, close=9.1, high=9.6, low=8.4)))
    events = []
    result = promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    assert result[-1]['index'] == 10
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)


@pytest.mark.parametrize('corruption', ['future', 'wrong_level', 'fake_body', 'unbroken_low'])
def test_recursive_boundary_rejects_forged_body_reversal_evidence(corruption):
    points = source_case()
    events = []
    promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    closure = deepcopy(next(p for p in events if p.get('confirmation_rule') == BODY_REVERSAL_RULE and p['kind'] == 'H'))
    proof = closure['wave_completion_evidence']['body_reversal_evidence']
    if corruption == 'future':
        proof['rebound']['available_at'] = 10
    elif corruption == 'wrong_level':
        proof['counter']['trend_level'] = 2
    elif corruption == 'fake_body':
        proof['rebound']['price_body']['index'] = 7
    else:
        proof['endpoint']['value'] = proof['counter']['value']
    with pytest.raises(ValueError):
        _completed_source_point(closure)


def test_pending_body_rejection_survives_incremental_append():
    from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
    from wavequant.domain.market_structure.trend_levels import _source_turns

    points = source_case()
    turns = _source_turns(points, 1)
    regular = _source_turns(known_keys(points, 2), 2, retain_geometry=True)
    events = []
    state = _TrendUpgradeState(turns[:-1], regular, trend_level=2, event_sink=events)
    state.advance()
    assert state.body_rejection is not None
    copied = deepcopy(state)
    assert copied.append(turns[-1:], [])
    result = copied.advance()
    batch_events = []
    batch = promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=batch_events)
    assert result == batch and copied.event_sink == batch_events
    assert state.processed_count == len(turns) - 1


def test_inner_lower_highs_do_not_close_before_original_counter_break():
    points = source_case()
    points[-1]['value'] = 6.7
    for index, kind, value in ((10, 'H', 7.0), (11, 'L', 6.6), (12, 'H', 7.2), (13, 'L', 5.89)):
        points.append(dict(index=index, ordinal=0, kind=kind, value=value, time=str(index),
                           available_at=index, label=f'p{index}', state='confirmed', trend_level=1))
    events = []
    promote_trend_key_breaks(points[:-1], known_keys(points, 2), trend_level=2, event_sink=events)
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)
    promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    closure = next(p for p in events if p.get('confirmation_rule') == BODY_REVERSAL_RULE and p['kind'] == 'H')
    assert _completed_source_point(closure)['available_at'] == 13
