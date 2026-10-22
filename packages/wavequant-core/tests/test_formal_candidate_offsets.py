"""正式旁路的候选过滤、修订和回退使用真实解析边界，不重验未变证明。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_judgement
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _OwnedTrendJudgement, TrendJudgement, mutable_points,
)
from wavequant.domain.market_structure.trend_levels import _SourceTurn


def _source() -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50)
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=price,
        time=str(index), available_at=index + 1, state='confirmed', trend_level=1)
        for index, price in enumerate(prices)]
    endpoint = source[-1]
    rule = 'level0_confirmed_high_breaks_known_level1_last_fall_high'
    references = [{name: point[name] for name in ('index', 'ordinal', 'kind', 'value', 'time', 'available_at')}
                  for point in source[-3:]]
    endpoint.update(source_level=0, wave_completion_policy='whole_wave_v117', wave_identity_state='owned',
        wave_direction_before='up', wave_direction_after='up', confirmation_rule=rule,
        upgrade_evidence=dict(role='endpoint', direction='up', confirmation_rule=rule,
            available_at=endpoint['available_at'], broken_key=references[0], origin=references[1],
            confirmed_by=references[2]))
    return source


def _base(candidates: Sequence[Mapping[str, object]]) -> TrendJudgement:
    return _OwnedTrendJudgement(2, 1, (), tuple(candidates))


def _assert_cold_result(source: Sequence[Mapping[str, object]], candidates: Sequence[Mapping[str, object]],
                        actual: TrendJudgement | None) -> None:
    expected = _FormalKeyBreakState(2).calculate(source, _base(candidates))
    assert actual is not None and expected is not None
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)


def test_tail_revision_filter_change_and_truncation_only_parse_changed_candidate_suffix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    candidates = [dict(point, trend_level=2, audit={'revision': 0}) for point in source[:4]]
    candidates[0]['state'] = 'seed'
    candidates[2]['state'] = 'developing'
    state = _FormalKeyBreakState(2)
    _assert_cold_result(source, candidates, state.calculate(source, _base(candidates)))
    original = trend_judgement._source_turns
    parsed: list[int] = []

    def observe(points: Sequence[Mapping[str, object]], source_level: int, *, retain_geometry: bool = False,
                accept_direction_facts: bool = False, formal_key_attacks: bool = False,
                _position_offset: int = 0) -> list[_SourceTurn]:
        if source_level == 2 and retain_geometry:
            parsed.extend(cast(int, point['index']) for point in points)
        return original(points, source_level, retain_geometry=retain_geometry,
            accept_direction_facts=accept_direction_facts, formal_key_attacks=formal_key_attacks,
            _position_offset=_position_offset)

    monkeypatch.setattr(trend_judgement, '_source_turns', observe)
    revised = deepcopy(candidates)
    revised[-1]['audit'] = {'revision': 1}
    actual = state.calculate(source, _base(revised))
    assert parsed == [3]
    _assert_cold_result(source, revised, actual)

    # 被过滤候选转为有效时，映射边界之前的有效候选仍保持原投影。
    revised[2] = dict(revised[2], state='confirmed')
    parsed.clear()
    actual = state.calculate(source, _base(revised))
    assert parsed == [2, 3]
    _assert_cold_result(source, revised, actual)

    shortened = revised[:3]
    parsed.clear()
    actual = state.calculate(source, _base(shortened))
    assert not parsed
    _assert_cold_result(source, shortened, actual)


def test_candidate_projection_offsets_are_restored_with_their_checkpoint() -> None:
    source = _source()
    candidates = [dict(point, trend_level=2) for point in source[:4]]
    candidates[0]['state'] = 'seed'
    candidates[2]['state'] = 'developing'
    state = _FormalKeyBreakState(2)
    state.calculate(source, _base(candidates))
    appended = [*candidates, dict(source[4], trend_level=2)]
    state.calculate(source, _base(appended))
    saved = state._checkpoint
    assert saved is not None
    saved_offsets = list(saved._regular_offsets)
    saved_regular = list(saved._regular)
    revised = [*candidates, dict(appended[-1], audit={'revised': True})]
    actual = state.calculate(source, _base(revised))
    _assert_cold_result(source, revised, actual)
    assert list(saved._regular_offsets) == saved_offsets
    assert list(saved._regular) == saved_regular
    assert state._regular_offsets is not saved._regular_offsets
    assert state._regular_offsets[-1] == len(state._regular)
    assert any(container is state._regular_offsets for container in state._containers())


def test_revised_candidate_still_runs_full_validation_and_failed_state_is_cleared() -> None:
    source = _source()
    candidates = [dict(point, trend_level=2) for point in source[:4]]
    state = _FormalKeyBreakState(2)
    state.calculate(source, _base(candidates))
    invalid = [*candidates[:-1], dict(candidates[-1], available_at=1)]
    with pytest.raises(ValueError, match='cannot precede'):
        state.calculate(source, _base(invalid))
    assert list(state._regular_offsets) == [0]
    assert not state._regular and not state._candidates
