"""共用趋势追加只处理新证据，历史修订仍以完整批量结果为基准。"""

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from random import Random
from typing import TypeVar

import pytest

from wavequant.domain.market_structure import trend_judgement as judgement_module
from wavequant.domain.market_structure.trend_judgement import _TrendJudgementState, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState, promote_trend_key_breaks
from wavequant.domain.market_structure.trend_levels import _SourceTurn, _source_turns, trend_reversals


def _source(level: int, clock: str, count: int = 96) -> list[dict[str, object]]:
    random = Random(31)
    points: list[dict[str, object]] = []
    for index in range(count):
        timestamp = str(index) if clock != 'date' else (date(2020, 1, 1) + timedelta(days=index)).isoformat()
        known: int | str = index + 1
        if clock == 'numeric':
            known = str(index + 1)
        elif clock == 'date':
            known = (date(2020, 1, 1) + timedelta(days=index + 1)).isoformat()
        points.append(dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
            value=random.randint(30, 60) if index % 2 else random.randint(5, 25),
            time=timestamp, available_at=known, trend_level=level - 1, state='confirmed',
            audit={'source_index': index}))
    return points


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
def test_ordinary_evidence_work_is_linear_and_every_snapshot_retains_cold_proofs(
    monkeypatch: pytest.MonkeyPatch, level: int, clock: str,
) -> None:
    source = _source(level, clock)
    state = _TrendJudgementState(level)
    original_parse = judgement_module._source_turns
    parsed_regular = 0
    largest_regular_batch = 0

    def parse(points: Sequence[Mapping[str, object]], source_level: int, *, retain_geometry: bool = False,
              accept_direction_facts: bool = False, _position_offset: int = 0) -> list[_SourceTurn]:
        nonlocal parsed_regular, largest_regular_batch
        if accept_direction_facts:
            parsed_regular += len(points)
            largest_regular_batch = max(largest_regular_batch, len(points))
        return original_parse(points, source_level, retain_geometry=retain_geometry,
            accept_direction_facts=accept_direction_facts, _position_offset=_position_offset)

    monkeypatch.setattr(judgement_module, '_source_turns', parse)
    for end in range(1, len(source) + 1):
        observed = judge_trend(source[:end], trend_level=level, _append_state=state)
        expected = judge_trend(source[:end], trend_level=level)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        assert state.memory_used >= state._owned_bytes()
    assert state._ordinary is not None
    assert state.cold_runs == 1
    assert len(state._ordinary.selected) > 5
    assert parsed_regular <= len(source) + len(state._ordinary.selected)
    assert largest_regular_batch <= 2


Item = TypeVar('Item')


class _HistoryWithoutIteration(list[Item]):
    def __iter__(self) -> Iterator[Item]:
        raise AssertionError('an append revisited the consumed history')

    def sort(self, *args: object, **kwargs: object) -> None:
        raise AssertionError('an append sorted the consumed history')


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('clock', ('integer', 'numeric', 'date'))
def test_upgrade_append_uses_retained_key_and_predecessor_indexes(level: int, clock: str) -> None:
    source = _source(level, clock, 80)
    prior = source[:48]
    prior_ordinary = trend_reversals(prior, trend_level=level)
    prior_regular = _source_turns(prior_ordinary, level, retain_geometry=True, accept_direction_facts=True)
    state = _TrendUpgradeState(_source_turns(prior, level - 1), prior_regular, trend_level=level)
    state.advance()

    ordinary = trend_reversals(source, trend_level=level)
    regular = _source_turns(ordinary, level, retain_geometry=True, accept_direction_facts=True)
    turns = _source_turns(source, level - 1)
    expected = promote_trend_key_breaks(source, ordinary, trend_level=level)
    state.turns = _HistoryWithoutIteration(state.turns)
    state.regular = _HistoryWithoutIteration(state.regular)
    assert state.append(turns[len(prior):], list(reversed(regular[len(prior_regular):])))
    assert state.advance() == expected


@pytest.mark.parametrize('level', (1, 2, 3))
def test_revised_source_before_the_active_wave_rebuilds_complete_evidence(level: int) -> None:
    source = _source(level, 'integer', 64)
    state = _TrendJudgementState(level, _tail_reuse=True)
    judge_trend(source[:32], trend_level=level, _append_state=state)
    state.remember_source_flip()
    judge_trend(source, trend_level=level, _append_state=state)
    cold_runs = state.cold_runs
    revised = deepcopy(source)
    revised[2]['audit'] = {'corrected_source': True}

    observed = judge_trend(revised, trend_level=level, _append_state=state)
    expected = judge_trend(revised, trend_level=level)

    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert state.cold_runs == cold_runs + 1
