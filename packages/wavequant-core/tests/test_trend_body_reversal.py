"""缩减价格序列覆盖翻空后的影线反弹，不代替国芳完整回测。"""

from copy import deepcopy
import json
from pathlib import Path
from typing import cast

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


@pytest.mark.parametrize('change', ['close_break', 'missing_body', 'missing_anchor_body', 'invalid_anchor_body',
                                    'missing_bear_flip'])
def test_only_a_proven_close_rejection_blocks_normal_high_extension(change):
    points = source_case(rebound_close=9.0 if change == 'close_break' else 7.83)
    if change == 'missing_body':
        points[8].pop('price_body')
    if change == 'missing_anchor_body':
        points[6].pop('price_body')
    if change == 'invalid_anchor_body':
        points[6]['price_body']['index'] = 5
    if change == 'missing_bear_flip':
        for field in ('wave_completion_policy', 'wave_identity_state', 'wave_completed_at', 'wave_completion_evidence'):
            points[6].pop(field)
    events = []
    result = promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    assert result[-1]['index'] == 8
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)


@pytest.mark.parametrize('level', [2, 3])
@pytest.mark.parametrize('rebound_open,anchor_close,rebound_close,keeps_exception', [
    (9.10, 8.91, 8.80, True),
    (8.47, 8.20, 8.80, False),
    (9.10, 8.20, 8.91, False),
    (9.10, 8.20, 8.20, True),
    (8.47, 8.20, 8.19, True),
])
def test_exception_compares_both_closes_independently_of_open(level, rebound_open, anchor_close,
                                                             rebound_close, keeps_exception):
    points = source_case(level, rebound_open=rebound_open, anchor_close=anchor_close, rebound_close=rebound_close)
    result = promote_trend_key_breaks(points, known_keys(points, level), trend_level=level)
    assert rejects_wick_extension(points[6], points[7], points[8]) == keeps_exception
    if keeps_exception:
        assert [(p['index'], p['kind']) for p in result][-2:] == [(6, 'H'), (9, 'L')]
    else:
        assert result[-1]['index'] == 8


def test_equal_high_does_not_enter_close_exception():
    points = source_case(rebound_high=8.91)
    assert not rejects_wick_extension(points[6], points[7], points[8])


@pytest.mark.parametrize('rebound_close', [8.30, 9.10])
def test_close_above_anchor_close_cancels_pending_exception(rebound_close):
    points = source_case(anchor_close=8.20)
    points[-1]['value'] = 6.7
    points.append(dict(index=10, ordinal=0, kind='H', value=9.6, time='10', available_at=10,
                       label='p10', state='confirmed', trend_level=1,
                       price_body=dict(index=10, time='10', open=8.5, close=rebound_close, high=9.6, low=8.2)))
    events = []
    result = promote_trend_key_breaks(points, known_keys(points, 2), trend_level=2, event_sink=events)
    assert result[-1]['index'] == 10
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)


@pytest.mark.parametrize('corruption', ['future', 'wrong_level', 'fake_body', 'close_break', 'unbroken_low'])
def test_recursive_boundary_rejects_forged_body_reversal_evidence(corruption):
    points = source_case(anchor_close=8.20)
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
    elif corruption == 'close_break':
        proof['rebound']['price_body']['close'] = 8.80
    else:
        proof['endpoint']['value'] = proof['counter']['value']
    with pytest.raises(ValueError):
        _completed_source_point(closure)


@pytest.mark.parametrize('level', [2, 3])
def test_guofang_september_higher_close_and_high_do_not_create_special_down_leg(level):
    """用户提供三日OHLC，周边转折为缩减构造，不能替代完整实股验收。"""
    prices = [5.10, 4.60, 5.00, 4.80, 5.40, 5.20, 5.46, 5.00, 5.69, 4.91]
    dates = ['2022-08-30', '2022-08-31', '2022-09-01', '2022-09-02', '2022-09-05',
             '2022-09-07', '2022-09-14', '2022-09-16', '2022-09-21', '2022-09-26']
    points = [dict(index=i, ordinal=0, kind='H' if i % 2 == 0 else 'L', value=value,
                   time=dates[i], available_at=i, label=f'p{i}', state='confirmed', trend_level=level - 1)
              for i, value in enumerate(prices)]
    for i, opening, high, low, closing in ((6, 5.19, 5.46, 5.14, 5.39),
                                          (8, 5.27, 5.69, 5.25, 5.45),
                                          (9, 5.23, 5.26, 4.91, 5.03)):
        points[i]['price_body'] = dict(index=i, time=dates[i], open=opening, close=closing, high=high, low=low)
    anchor, counter, key = points[6], points[7], points[5]
    fields = ('index', 'ordinal', 'kind', 'value', 'time', 'available_at', 'label')
    anchor.update(source_level=level - 2, available_at=7,
        wave_direction_before='up', wave_direction_after='down', confirmation_rule='strict_lower_bear_flip',
        upgrade_evidence=dict(direction='down', role='origin', available_at=7,
            origin={field: anchor[field] for field in fields},
            confirmed_by={field: counter[field] for field in fields},
            broken_key={field: key[field] for field in fields}))
    _stamp_wave_completion(anchor, closed=True)

    events = []
    result = promote_trend_key_breaks(points, known_keys(points, level), trend_level=level, event_sink=events)

    assert not rejects_wick_extension(anchor, counter, points[8])
    assert result[-1]['time'] == '2022-09-21'
    assert not any(p.get('confirmation_rule') == BODY_REVERSAL_RULE for p in events)
    assert not any(left['time'] == '2022-09-14' and right['time'] == '2022-09-26'
                   for left, right in zip(result, result[1:]))


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


def _stored_guofang_sources() -> list[dict[str, object]]:
    fixture = Path(__file__).parent / 'fixtures' / 'guofang_bear_flip_split_evidence.json'
    return cast(list[dict[str, object]], json.loads(fixture.read_text(encoding='utf-8'))['points'])


def _split_bear_flip_points() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    points = {point['time']: point for point in _stored_guofang_sources()}
    return points['2024-12-12'], points['2024-12-25'], points['2025-01-03']


def test_stored_september_sources_do_not_draw_the_rejected_secondary_segment() -> None:
    from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
    from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache

    points = [point for point in _stored_guofang_sources() if str(point['time']) <= '2022-09-26']
    dated = {point['time']: point for point in points}
    anchor, counter, rebound = (dated[day] for day in ('2022-09-14', '2022-09-19', '2022-09-21'))
    assert not rejects_wick_extension(anchor, counter, rebound)
    equal_close = deepcopy(rebound)
    cast(dict[str, object], equal_close['price_body'])['close'] = cast(dict[str, object], anchor['price_body'])['close']
    assert rejects_wick_extension(anchor, counter, equal_close)
    before = deepcopy(points)
    cache = _TrendJudgementCache(tail_reuse=True)
    for asof in ('2022-09-22', '2022-09-27', '2022-10-10', '2022-10-11'):
        actual = cache.judge(points, path='guofang-september-fixture', trend_level=2, asof=asof)
        expected = judge_trend(points, trend_level=2, asof=asof)
        geometry = mutable_points(actual.points)
        assert geometry == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
        assert not any(left['time'] == '2022-09-14' and right['time'] == '2022-09-26'
                       for left, right in zip(geometry, geometry[1:]))
        assert not any(point.get('confirmation_rule') == BODY_REVERSAL_RULE
                       and point['time'] in ('2022-09-14', '2022-09-26')
                       for point in mutable_points(actual.candidates))
    assert points == before


def test_real_flip_can_be_proved_by_counter_when_anchor_keeps_ordinary_completion() -> None:
    from wavequant.domain.market_structure.trend_body_reversal import body_reference
    from wavequant.domain.market_structure.trend_stage_work import _hierarchy_source

    anchor, counter, rebound = _split_bear_flip_points()
    completion = cast(dict[str, object], anchor['wave_completion_evidence'])
    assert 'definition_evidence' in completion
    assert 'upgrade_evidence' not in completion
    assert rejects_wick_extension(anchor, counter, rebound)
    assert rejects_wick_extension(*(body_reference(point) for point in (anchor, counter, rebound)))
    projected = _hierarchy_source((anchor, counter, rebound))
    assert rejects_wick_extension(*projected)


@pytest.mark.parametrize('corruption', ['absent', 'origin', 'attack', 'equal_key', 'future', 'level', 'path'])
def test_split_flip_does_not_accept_unrelated_unbroken_or_future_proof(corruption: str) -> None:
    anchor, counter, rebound = _split_bear_flip_points()
    proof = cast(dict[str, object], counter['upgrade_evidence'])
    if corruption == 'absent':
        counter.pop('upgrade_evidence')
    elif corruption == 'origin':
        cast(dict[str, object], proof['origin'])['index'] = 0
    elif corruption == 'attack':
        cast(dict[str, object], proof['confirmed_by'])['value'] = 99.
    elif corruption == 'equal_key':
        cast(dict[str, object], proof['broken_key'])['value'] = counter['value']
    elif corruption == 'future':
        proof['available_at'] = '2025-01-06'
    elif corruption == 'level':
        counter['trend_level'] = 2
    else:
        anchor['source_path'], counter['source_path'] = 'path-a', 'path-b'
    assert not rejects_wick_extension(anchor, counter, rebound)


def test_real_lower_sources_keep_both_secondary_legs_and_their_knowledge_boundary() -> None:
    from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
    from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache

    points = _stored_guofang_sources()
    before = deepcopy(points)
    cache = _TrendJudgementCache(tail_reuse=True)
    for asof in ('2024-12-26', '2025-01-08', '2025-01-21', '2025-01-22'):
        actual = cache.judge(points, path='guofang-fixture', trend_level=2, asof=asof)
        expected = judge_trend(points, trend_level=2, asof=asof)
        assert mutable_points(actual.points) == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
        geometry = mutable_points(actual.points)
        assert not any(point['time'] == '2025-01-03' for point in geometry)
        if asof == '2025-01-22':
            assert [point['time'] for point in geometry[-3:]] == ['2024-07-25', '2024-12-12', '2025-01-13']
            high = next(point for point in geometry if point['time'] == '2024-12-12')
            assert high['wave_completed_at'] == '2025-01-22'
            assert _completed_source_point(high)['available_at'] == '2025-01-22'
        else:
            assert not any(point.get('confirmation_rule') == BODY_REVERSAL_RULE
                           for point in mutable_points(actual.candidates))
    assert points == before


@pytest.mark.parametrize('level', [2, 3])
def test_counter_endpoint_evidence_survives_recursive_completion_validation(level: int) -> None:
    from wavequant.domain.market_structure.trend_stage_work import _hierarchy_source

    points = source_case(level)
    proof = deepcopy(points[6]['wave_completion_evidence']['upgrade_evidence'])
    points[7]['upgrade_evidence'] = dict(proof, role='endpoint')
    # Both storage routes must survive the private projection and body proof export.
    points = _hierarchy_source(points)
    events: list[dict[str, object]] = []
    promote_trend_key_breaks(points, known_keys(points, level), trend_level=level, event_sink=events)
    closure = next(point for point in events if point.get('confirmation_rule') == BODY_REVERSAL_RULE
                   and point['kind'] == 'H')
    exported = cast(dict[str, object], closure['wave_completion_evidence'])
    body = cast(dict[str, object], exported['body_reversal_evidence'])
    assert 'upgrade_evidence' in cast(dict[str, object], body['counter'])
    assert _completed_source_point(closure)['available_at'] == 9
