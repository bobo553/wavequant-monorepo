"""方向反转不自动授予单下级波本级身份；共同状态保留组合波与严格升级。"""

from collections.abc import Mapping
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import judge_trend, mutable_points
from wavequant.domain.market_structure.trend_level_upgrades import promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import completed_source_points, trend_reversals


# 从 000678 真实已结束一级来源独立抄录，保留价格日和本波结束可知日。
REAL_CASES = (
    ((157, '1997-09-04', 'H', 12.3, '1997-09-11'),
     (167, '1997-09-18', 'L', 10.71, '1997-09-22'),
     (169, '1997-09-22', 'H', 11.1, '1997-09-25'),
     (171, '1997-09-24', 'L', 9.0, '1997-10-10'),
     (191, '1997-10-27', 'H', 12.18, '1997-11-03'),
     (195, '1997-10-31', 'L', 10.6, '1997-11-10'),
     (206, '1997-11-17', 'H', 11.45, '1997-11-20'),
     (212, '1997-11-25', 'L', 10.0, '1997-12-08')),
    ((224, '1997-12-11', 'H', 11.51, '1997-12-19'),
     (229, '1997-12-18', 'L', 10.6, '1997-12-30'),
     (236, '1997-12-29', 'H', 11.98, '1998-01-07'),
     (240, '1998-01-06', 'L', 11.38, '1998-01-12'),
     (241, '1998-01-07', 'H', 12.08, '1998-01-14'),
     (245, '1998-01-13', 'L', 10.41, '1998-01-23'),
     (254, '1998-02-09', 'H', 12.0, '1998-02-16'),
     (258, '1998-02-13', 'L', 11.32, '1998-02-18'),
     (260, '1998-02-17', 'H', 12.63, '1998-02-26'),
     (270, '1998-03-03', 'L', 10.1, '1998-03-10')),
)


def _source(case: int, *, level: int, mirror: bool, real_dates: bool) -> list[dict[str, object]]:
    return [dict(index=index if real_dates else position, ordinal=0, time=time if real_dates else str(position),
                 kind=('L' if kind == 'H' else 'H') if mirror else kind,
                 value=100 - value if mirror else value, available_at=known if real_dates else position + 1,
                 trend_level=level - 1, state='confirmed')
            for position, (index, time, kind, value, known) in enumerate(REAL_CASES[case])]


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('real_dates', (False, True))
@pytest.mark.parametrize('case', (0, 1))
def test_a_single_lower_counter_wave_keeps_direction_facts_without_upper_identity(
    level: int, mirror: bool, real_dates: bool, case: int,
) -> None:
    source = _source(case, level=level, mirror=mirror, real_dates=real_dates)
    original = deepcopy(source)
    result = judge_trend(source, trend_level=level)
    assert result.points == ()
    assert len(result.candidates) == 2
    assert all(point['wave_identity_state'] == 'direction_only' for point in result.candidates)
    assert all('wave_completed_at' not in point for point in result.candidates)
    assert all(len(cast(tuple[object, ...], point['definition_evidence'])) == 4 for point in result.candidates)
    assert completed_source_points(result.candidates, source_level=level) == []
    assert source == original
    # 每个可知前缀冷算等于完整输入按当时知识过滤，不靠未来归并再隐藏。
    for size in range(1, len(source) + 1):
        cutoff = cast(int | str, source[size - 1]['available_at'])
        assert judge_trend(source, trend_level=level, asof=cutoff) == judge_trend(source[:size], trend_level=level)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_a_later_composite_wave_acquires_identity_without_backdating_the_pending_fact(
    level: int, mirror: bool,
) -> None:
    source = _source(0, level=level, mirror=mirror, real_dates=False)
    for index, value in ((8, 13.0), (9, 11.0)):
        source.append(dict(index=index, ordinal=0, time=str(index), kind=('L' if index % 2 == 0 else 'H') if mirror
                           else ('H' if index % 2 == 0 else 'L'), value=100 - value if mirror else value,
                           available_at=index + 1, trend_level=level - 1, state='confirmed'))
    assert judge_trend(source, trend_level=level, asof=9).points == ()
    result = judge_trend(source, trend_level=level)
    assert [(point['index'], point['wave_completed_at']) for point in result.points] == [(3, 10)]
    point = result.points[0]
    identity = cast(Mapping[str, object], point['lower_wave_identity_evidence'])
    # 最近左邻H2/L2必须保留；不能取整段全局最高H0／最低L0。
    assert cast(Mapping[str, object], point['broken_key'])['index'] == 2
    assert identity['available_at'] == 10
    assert cast(Mapping[str, object], identity['origin'])['index'] == 3
    assert cast(Mapping[str, object], identity['interior'])['index'] == 4
    assert cast(Mapping[str, object], identity['endpoint'])['index'] == 8
    early = next(event for event in result.candidates if event['index'] == 3 and event['available_at'] == 6)
    assert early['wave_identity_state'] == 'direction_only'
    assert 'wave_completed_at' not in early
    assert completed_source_points(result.points, source_level=level)[0]['available_at'] == 10


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_one_completed_lower_wave_can_upgrade_a_real_old_upper_key_and_keep_its_origin(
    level: int, mirror: bool,
) -> None:
    prices = (10, 5, 11, 6, 12)
    source = [dict(index=index, ordinal=0, time=str(index), kind=('L' if index % 2 == 0 else 'H') if mirror
                   else ('H' if index % 2 == 0 else 'L'), value=20 - value if mirror else value,
                   available_at=0 if index == 0 else index + 1, state='confirmed', trend_level=level - 1)
              for index, value in enumerate(prices)]
    # 裸 confirmed 旧本级点声明契约保持；不假造 generated 完成证据。
    key = dict(source[0], trend_level=level)
    events: list[dict[str, object]] = []
    points = promote_trend_key_breaks(source, [key], trend_level=level, event_sink=events)
    assert [point['index'] for point in points] == [0, 1, 2, 4]
    assert all(point['index'] != 3 for point in points)
    assert [cast(Mapping[str, object], event['upgrade_evidence'])['role'] for event in events
            if event['index'] == 4] == ['endpoint']
    latest = next(point for point in points if point['index'] == 4)
    proof = cast(Mapping[str, object], latest['upgrade_evidence'])
    assert cast(Mapping[str, object], proof['origin'])['index'] == 1
    assert cast(Mapping[str, object], proof['broken_key'])['index'] == 0
    assert 'wave_completed_at' not in latest


@pytest.mark.parametrize('mutation', ('missing_identity', 'missing_marker_and_identity', 'missing_marker', 'future_interior', 'direct_child', 'invalid_price'))
def test_owned_completion_requires_its_real_causal_composite_identity(mutation: str) -> None:
    source = _source(0, level=1, mirror=False, real_dates=False)
    source += [dict(index=index, ordinal=0, time=str(index), kind='H' if index % 2 == 0 else 'L',
                    value=value, available_at=index + 1, trend_level=0, state='confirmed')
               for index, value in ((8, 13.0), (9, 11.0))]
    point = mutable_points(judge_trend(source).points)[0]
    completion = cast(dict[str, object], point['wave_completion_evidence'])
    identity = cast(dict[str, object], completion['lower_wave_identity_evidence'])
    if mutation in ('missing_identity', 'missing_marker_and_identity'):
        completion.pop('lower_wave_identity_evidence')
        if mutation == 'missing_marker_and_identity':
            point.pop('wave_identity_state')
    elif mutation == 'missing_marker':
        point.pop('wave_identity_state')
    elif mutation == 'future_interior':
        cast(dict[str, object], identity['interior'])['available_at'] = 11
    elif mutation == 'invalid_price':
        cast(dict[str, object], identity['endpoint'])['value'] = 'invalid'
    else:
        identity['endpoint'] = deepcopy(identity['interior'])
    with pytest.raises(ValueError):
        completed_source_points([point], source_level=1)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_a_direction_marker_cannot_drop_its_policy_to_mint_legacy_keys(level: int, mirror: bool) -> None:
    source = _source(0, level=level, mirror=mirror, real_dates=False)
    facts = trend_reversals(source, trend_level=level)
    for fact in facts:
        fact.pop('wave_completion_policy')
    with pytest.raises(ValueError):
        promote_trend_key_breaks(source, facts, trend_level=level)
