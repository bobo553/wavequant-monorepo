"""局部 HH/HL 反弹不能替代真实翻向并递归升级；三级使用同一门槛。"""

from collections.abc import Mapping
from copy import deepcopy

import pytest

from wavequant.domain.market_structure.trend_judgement import _TrendJudgementState, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_levels import completed_source_points
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from wavequant.domain.market_structure.trend_options import ordinary_trend_completion, ordinary_completion_enabled


def _source(level: int, mirror: bool, attack: float | None = None) -> list[dict[str, object]]:
    # L4 是整段最低点，末跌高是 H3=10；H5=7、H7=8 虽抬高但没有翻多。
    prices = [8.0, 12.0, 6.0, 10.0, 5.0, 7.0, 5.5, 8.0, 6.0]
    if attack is not None:
        prices.append(attack)
    return [dict(index=index, ordinal=0, time=str(index), available_at=index + 1,
                 kind=('H' if index % 2 == 0 else 'L') if mirror else ('L' if index % 2 == 0 else 'H'),
                 value=30 - price if mirror else price, trend_level=level - 1, state='confirmed')
            for index, price in enumerate(prices)]


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('attack', (None, 9.0, 10.0))
def test_local_direction_without_key_break_cannot_become_an_upper_source(
    level: int, mirror: bool, attack: float | None,
) -> None:
    source = _source(level, mirror, attack)
    original = deepcopy(source)
    result = judge_trend(source, trend_level=level)
    assert result.points == ()
    assert result.candidates
    assert all(point['wave_identity_state'] == 'direction_only' for point in result.candidates)
    assert completed_source_points(result.candidates, source_level=level) == []
    assert source == original


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_real_later_break_confirms_at_its_knowledge_date_without_backdating(level: int, mirror: bool) -> None:
    source = _source(level, mirror, 11.0)
    state = _TrendJudgementState(level)
    for size in range(1, len(source) + 1):
        current = judge_trend(source[:size], trend_level=level, _append_state=state)
        assert current == judge_trend(source[:size], trend_level=level)
        assert current == judge_trend(source, trend_level=level, asof=size)
        if size < len(source):
            assert current.points == ()
    result = judge_trend(source, trend_level=level)
    assert [(point['index'], point['available_at'], point['wave_completed_at'])
            for point in result.points] == [(4, 10, 10)]
    key = result.points[0]['broken_key']
    assert isinstance(key, Mapping)
    assert (key['index'], key['value']) == (3, 20.0 if mirror else 10.0)
    assert completed_source_points(result.points, source_level=level)[0]['available_at'] == 10


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_a_key_known_on_the_attack_day_is_not_a_preexisting_reversal_key(level: int, mirror: bool) -> None:
    source = _source(level, mirror, 11.0)
    source[3]['available_at'] = 9
    assert judge_trend(source, trend_level=level).points == ()


@pytest.mark.parametrize('mutation', ('equal_price', 'late_key'))
def test_recursive_source_rechecks_the_recorded_reversal_key(mutation: str) -> None:
    result = judge_trend(_source(1, False, 11.0))
    point = mutable_points(result.points)[0]
    evidence = point['wave_completion_evidence']
    assert isinstance(evidence, dict)
    identity = evidence['lower_wave_identity_evidence']
    assert isinstance(identity, dict)
    key = identity['reversal_key']
    assert isinstance(key, dict)
    if mutation == 'equal_price':
        key['value'] = 11.0
    else:
        key['available_at'] = 9
    with pytest.raises(ValueError, match='ordinary reversal'):
        completed_source_points([point], source_level=1)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_ordinary_completion_is_explicit_and_does_not_backdate(level: int, mirror: bool) -> None:
    source = _source(level, mirror)
    assert judge_trend(source, trend_level=level).points == ()
    enabled = judge_trend(source, trend_level=level, ordinary_trend_completion_enabled=True)
    assert [(point['index'], point['wave_completed_at']) for point in enabled.points] == [(4, 8)]
    assert completed_source_points(enabled.points, source_level=level)
    assert judge_trend(source, trend_level=level, asof=7, ordinary_trend_completion_enabled=True).points == ()
    assert judge_trend(source, trend_level=level).points == ()


def test_mode_switch_invalidates_append_state_and_result_cache() -> None:
    source = _source(1, False)
    state = _TrendJudgementState(1)
    cache = _TrendJudgementCache(tail_reuse=True)
    for selected in (False, True, False, True):
        with ordinary_trend_completion(selected):
            expected = judge_trend(source)
            assert judge_trend(source, _append_state=state) == expected
            assert cache.judge(source, path='mode-switch', trend_level=1) == expected
            assert bool(expected.points) is selected
    assert ordinary_completion_enabled() is False


def test_nested_option_restores_outer_mode_after_failure() -> None:
    with ordinary_trend_completion(True):
        with pytest.raises(RuntimeError):
            with ordinary_trend_completion(False):
                assert ordinary_completion_enabled() is False
                raise RuntimeError('interrupted request')
        assert ordinary_completion_enabled() is True
    assert ordinary_completion_enabled() is False
