"""各级仅维护当前正式末跌高／末升低位置，完整来源与旧关键位仍保留。"""

from collections.abc import Sequence
from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.trend_judgement import (
    _TrendJudgementState, _copy_append_states, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_level_upgrades import _PointRange, _TrendUpgradeState
from wavequant.domain.market_structure.trend_levels import (
    WaveDirection, _SourceTurn, _TrendReversalState, _source_turns,
)


def _source(level: int, mirror: bool) -> list[dict[str, object]]:
    prices = (10.0, 11.50, 4.91, 6.13, 5.06, 5.50)
    return [dict(index=position * 3, ordinal=0,
        kind=('H' if mirror else 'L') if position % 2 == 0 else ('L' if mirror else 'H'),
        value=100 - price if mirror else price, time=str(position * 3),
        available_at=position * 3 + 1, trend_level=level - 1, state='confirmed')
        for position, price in enumerate(prices)]


def _state(level: int, mirror: bool, closed_positions: Sequence[int]) -> _TrendUpgradeState:
    source = _source(level, mirror)
    turns = _source_turns(source, level - 1)
    state = _TrendUpgradeState(turns, [], trend_level=level)
    state.processed_count = len(turns) - 1
    state.prefix_known = turns[-2].available_at
    for point in _source_turns([dict(source[position], trend_level=level) for position in closed_positions], level):
        state.keys_by_kind[point.kind].update(state.positions[point.index, point.ordinal], point)
    return state


def _set_marker(state: _TrendUpgradeState, mirror: bool, position: int) -> None:
    if mirror:
        state.last_rise_low_position = position
    else:
        state.last_fall_high_position = position


def _marker(state: _TrendUpgradeState, mirror: bool) -> int | None:
    return state.last_rise_low_position if mirror else state.last_fall_high_position


def _observe_key_ranges(monkeypatch: pytest.MonkeyPatch, state: _TrendUpgradeState, mirror: bool) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    tree = state.keys_by_kind['L' if mirror else 'H']
    original = _PointRange.query

    def query(owner: _PointRange, start: int, end: int) -> _SourceTurn | None:
        if owner is tree:
            ranges.append((start, end))
        return original(owner, start, end)

    monkeypatch.setattr(_PointRange, 'query', query)
    return ranges


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_frozen_whole_wave_key_overrides_a_later_local_marker(
    monkeypatch: pytest.MonkeyPatch, level: int, mirror: bool,
) -> None:
    state = _state(level, mirror, (1, 3))
    _set_marker(state, mirror, 3)
    state.owned_direction = 'up' if mirror else 'down'
    state.whole_origin, state.whole_extreme = state.turns[1], state.turns[2]
    ranges = _observe_key_ranges(monkeypatch, state, mirror)

    state.advance()

    assert _marker(state, mirror) == 1
    assert ranges == [(1, 2)]
    assert len(state.turns) == 6
    assert (state.last_fall_high_position if mirror else state.last_rise_low_position) is None


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_key_query_starts_at_the_previous_marker_and_selects_new_confirmed_key(
    monkeypatch: pytest.MonkeyPatch, level: int, mirror: bool,
) -> None:
    state = _state(level, mirror, (1, 3))
    _set_marker(state, mirror, 1)
    ranges = _observe_key_ranges(monkeypatch, state, mirror)

    state.advance()

    assert _marker(state, mirror) == 3
    assert ranges == [(1, 5)]
    assert state.turns[3].index == 9
    assert len(state.turns) == len(state.source_by_order) == 6


@pytest.mark.parametrize('mirror', (False, True))
def test_a_marker_on_the_attack_or_to_its_right_cannot_hide_an_older_valid_key(
    monkeypatch: pytest.MonkeyPatch, mirror: bool,
) -> None:
    state = _state(2, mirror, (1, 3))
    _set_marker(state, mirror, 5)
    ranges = _observe_key_ranges(monkeypatch, state, mirror)

    state.advance()

    assert ranges == [(0, 5)]
    assert _marker(state, mirror) == 3


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_latest_source_high_or_low_without_closed_key_proof_does_not_advance_marker(level: int, mirror: bool) -> None:
    state = _state(level, mirror, (1,))

    state.advance()

    assert _marker(state, mirror) == 1
    assert state.turns[3].kind == state.turns[5].kind


@pytest.mark.parametrize('mirror', (False, True))
def test_key_known_after_attack_price_does_not_replace_the_usable_marker(mirror: bool) -> None:
    state = _state(2, mirror, (1,))
    _set_marker(state, mirror, 1)
    source = _source(2, mirror)
    late = _source_turns([dict(source[3], trend_level=2, available_at=16)], 2)[0]
    state.keys_by_kind[late.kind].update(state.positions[late.index, late.ordinal], late)

    state.advance()

    assert _marker(state, mirror) == 1


@pytest.mark.parametrize('level', (1, 2, 3))
def test_tail_checkpoint_owns_marker_state_without_discarding_history(level: int) -> None:
    state = _state(level, False, (1, 3))
    state.advance()
    ordinary = _TrendReversalState(level)
    events: list[dict[str, object]] = []

    _, restored, _ = _copy_append_states(ordinary, state, events)
    restored.last_fall_high_position = 1

    assert state.last_fall_high_position == 3
    assert restored.last_fall_high_position == 1
    assert restored.turns == state.turns and restored.turns is not state.turns
    assert restored.keys_by_kind['H'].points == state.keys_by_kind['H'].points
    assert restored.keys_by_kind['H'].points is not state.keys_by_kind['H'].points


def _full_prefix_attack_key(
    state: _TrendUpgradeState, attack: _SourceTurn, *, direction: WaveDirection | None,
    origin: _SourceTurn | None,
) -> _SourceTurn | None:
    """优化前的独立查询基线：全前缀选key，然后由冻结整波起点覆盖。"""
    tree = state.keys_by_kind[attack.kind]
    attack_position = state.positions[attack.index, attack.ordinal]
    key = tree.query(0, attack_position)
    attack_direction = 'up' if attack.kind == 'H' else 'down'
    if direction is not None and direction != attack_direction and origin is not None and origin.kind == attack.kind:
        origin_position = state.positions[origin.index, origin.ordinal]
        frozen = tree.query(origin_position, origin_position + 1)
        if frozen is not None:
            key = frozen
    return key


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('scenario', ('ordinary', 'late', 'revised', 'late_revised'))
def test_active_key_indices_match_full_prefix_queries_for_every_causal_snapshot(
    monkeypatch: pytest.MonkeyPatch, level: int, mirror: bool, scenario: str,
) -> None:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50,
              38, 54, 28, 40, 18, 32, 24, 48, 34, 58, 22, 40, 12, 30, 20, 46)
    source: list[dict[str, object]] = [dict(index=position * 3, ordinal=0,
        kind=('H' if mirror else 'L') if position % 2 == 0 else ('L' if mirror else 'H'),
        value=100 - price if mirror else price, time=str(position * 3), available_at=position * 3 + 1,
        trend_level=level - 1, state='confirmed', audit={'source_position': position})
        for position, price in enumerate(prices)]
    if scenario.startswith('late'):
        source[12]['available_at'] = 15 * 3 + 1
    indexed = _TrendJudgementState(level, _tail_reuse=True)
    baseline = _TrendJudgementState(level, _tail_reuse=True)
    produced_candidates = False

    for end in range(1, len(source) + 1):
        prefix = deepcopy(source[:end])
        if 'revised' in scenario and end >= 23:
            prefix[9]['value'] = 53 if mirror else 47
            prefix[9]['audit'] = {'corrected_earlier_high': True}
        now = cast(int, prefix[-1]['index']) + 1
        actual = judge_trend(prefix, trend_level=level, asof=now, _append_state=indexed)
        with monkeypatch.context() as original:
            original.setattr(_TrendUpgradeState, '_attack_key', _full_prefix_attack_key)
            expected = judge_trend(prefix, trend_level=level, asof=now, _append_state=baseline)

        assert mutable_points(actual.points) == mutable_points(expected.points), (level, mirror, scenario, end)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates), (level, mirror, scenario, end)
        produced_candidates = produced_candidates or bool(actual.candidates)
        if end == 18:
            indexed.remember_source_flip()
            baseline.remember_source_flip()

    assert produced_candidates
