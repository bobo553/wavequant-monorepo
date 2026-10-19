"""固化版本、变化尾部与完整冷判定的因果等价；开发阶段仅新增用例。"""

from copy import deepcopy
from contextlib import ExitStack
from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.prepared_point_history import PreparedPointHistory
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList, FrozenPrefixMap
from wavequant.domain.market_structure.trend_judgement import _TrendJudgementState, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_levels import _TrendReversalState, _source_turns
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
from wavequant.domain.market_structure.trend_cascade import _TrendCascadeCache, prepare_trend_cascade
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _TrendJudgementCache


def _source(level: int, mirror: bool = False) -> list[dict[str, object]]:
    prices = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 36, 42, 30, 36, 24, 31, 26, 50,
              38, 54, 28, 40, 18, 32, 24, 48, 34, 58, 22, 40, 12, 30, 20, 46)
    return [dict(index=index * 3, ordinal=0, time=str(index * 3), available_at=index * 3 + 1,
        kind=('H' if mirror else 'L') if index % 2 == 0 else ('L' if mirror else 'H'),
        value=100 - value if mirror else value, trend_level=level - 1, state='confirmed', audit={'original': index})
        for index, value in enumerate(prices)]


def test_frozen_sequence_snapshots_share_history_and_isolate_future_and_revisions() -> None:
    current = FrozenPrefixList(range(1000))
    current.freeze(990)
    saved = current.copy()
    assert saved._archive is current._archive
    assert len(saved._tail) == 10
    current.extend((1000, 1001))
    current.freeze(1001)
    assert list(saved) == list(range(1000))
    saved[-1] = -1
    saved[2] = -2
    saved.freeze(len(saved))
    assert list(current) == list(range(1002))
    assert saved[2] == -2 and saved[-1] == -1
    assert saved._archive is not current._archive
    del saved[10:]
    saved.extend((20, 21))
    assert list(saved) == [0, 1, -2, 3, 4, 5, 6, 7, 8, 9, 20, 21]


def test_frozen_index_retains_old_knowledge_and_branches_without_future_entries() -> None:
    current = FrozenPrefixMap([('key', 1), ('origin', 2)])
    current.freeze()
    saved = current.copy()
    current['key'] = 3
    current['future'] = 4
    current.freeze()
    assert dict(saved) == {'key': 1, 'origin': 2}
    assert saved._archive is not current._archive
    saved['origin'] = 20
    saved['branch'] = 30
    saved.freeze()
    assert dict(current) == {'key': 3, 'origin': 2, 'future': 4}
    assert dict(saved) == {'key': 1, 'origin': 20, 'branch': 30}
    assert list(saved) == ['key', 'origin', 'branch']
    assert len(saved) == 3


def test_index_versions_are_reclaimed_after_older_checkpoints_are_replaced() -> None:
    current = FrozenPrefixMap([('active', 0)])
    current.freeze()
    original = current.copy()
    for value in range(1, 1000):
        saved = current.copy()
        current['active'] = value
        current.freeze()
        assert saved['active'] == value - 1
        assert current['active'] == value
        assert original['active'] == 0
        assert current._archive.root is not None
        assert len(current._archive.root.entries) == 1
        assert current._archive.root.left is current._archive.root.right is None


@pytest.mark.parametrize('level', (1, 2, 3))
def test_boundary_uses_newer_key_position_and_preserves_its_later_knowledge(level: int) -> None:
    source = _source(level)
    turns = _source_turns(source, level - 1)
    state = _TrendJudgementState(level)
    state._ordinary = _TrendReversalState(level)
    state._ordinary.extend(turns)
    state._upgrade = _TrendUpgradeState(turns, [], trend_level=level)
    state._upgrade.last_fall_high_position = 9
    state._upgrade.last_rise_low_position = 12
    state._upgrade.prefix_known = 91
    state._freeze_prefix()
    assert state.frozen_boundary == (36, 0)
    assert state.frozen_known == 91
    assert isinstance(state._ordinary.turns, FrozenPrefixList)
    assert state._ordinary.turns.frozen_count == 12
    assert state._ordinary.turns[12].index == 36
    state._upgrade.last_fall_high_position = 17
    state._upgrade.prefix_known = 100
    state._freeze_prefix()
    assert state.frozen_boundary == (51, 0) and state.frozen_known == 100
    assert state._ordinary.turns.frozen_count == 17
    # 当前整波 key 回到更早位置时显式改变当前边界，保留全部来源。
    state._upgrade.last_fall_high_position = 3
    state._upgrade.last_rise_low_position = 4
    state._freeze_prefix()
    assert state.frozen_boundary == (12, 0)
    assert state.frozen_invalidations == 1
    assert len(state._ordinary.turns) == len(source)


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_every_prefix_and_tail_revision_match_cold_and_keep_earlier_snapshots(level: int, mirror: bool) -> None:
    source = _source(level, mirror)
    state = _TrendJudgementState(level, _tail_reuse=True, memory_bytes=32 * 1024 * 1024)
    saved = []
    for end in range(1, len(source) + 1):
        current = deepcopy(source[:end])
        actual = judge_trend(current, trend_level=level, _append_state=state)
        cold = judge_trend(current, trend_level=level)
        assert mutable_points(actual.points) == mutable_points(cold.points)
        assert mutable_points(actual.candidates) == mutable_points(cold.candidates)
        saved.append((actual, mutable_points(actual.points), mutable_points(actual.candidates)))
        current[-1]['audit'] = {'tail_revision': end}
        revised = judge_trend(current, trend_level=level, _append_state=state)
        expected = judge_trend(current, trend_level=level)
        assert mutable_points(revised.points) == mutable_points(expected.points)
        assert mutable_points(revised.candidates) == mutable_points(expected.candidates)
    for actual, points, candidates in saved:
        assert mutable_points(actual.points) == points
        assert mutable_points(actual.candidates) == candidates
    assert state.tail_restores > 0


def test_revision_before_boundary_rebuilds_without_reusing_old_audit() -> None:
    source = _source(1)
    state = _TrendJudgementState(1, _tail_reuse=True)
    for end in range(1, len(source) + 1):
        judge_trend(source[:end], _append_state=state)
    cold_runs = state.cold_runs
    source[0]['audit'] = {'earliest_revision': True}
    actual = judge_trend(source, _append_state=state)
    expected = judge_trend(source)
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert state.cold_runs > cold_runs


def test_long_tail_checkpoints_share_consumed_records_without_a_new_business_boundary() -> None:
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
        value=index + (20 if index % 2 else 10), time=str(index), available_at=index + 1,
        trend_level=0, state='confirmed') for index in range(120)]
    state = _TrendJudgementState(1, _tail_reuse=True, memory_bytes=32 * 1024 * 1024)
    for end in range(1, len(source) + 1):
        judge_trend(source[:end], _append_state=state)
    assert state._ordinary is not None and state._checkpoint is not None
    checkpoint = state._checkpoint[2]
    assert isinstance(state._ordinary.turns, FrozenPrefixList)
    assert isinstance(checkpoint.turns, FrozenPrefixList)
    assert checkpoint.turns._archive is state._ordinary.turns._archive
    assert len(checkpoint.turns._tail) == 0
    assert len(state._ordinary.turns._tail) == 1
    assert state.cold_runs == 1


def test_owned_history_delta_skips_old_blocks_and_retains_earlier_versions() -> None:
    points = [dict(index=index, ordinal=0, value=index, audit={'known': index}) for index in range(160)]
    with owned_evidence_scope() as evidence:
        history = PreparedPointHistory(evidence)
        try:
            first = history.pack(('points', 'path'), points, _changed_from=0)
            assert first is not None
            following = [*points, dict(index=160, ordinal=0, value=160, audit={'known': 160})]
            rows_before = history._rows.row_count
            second = history.pack(('points', 'path'), following, _owned_previous=points, _changed_from=160)
            assert second is not None
            assert history._rows.row_count - rows_before <= 5
            assert history.unpack(first) == points
            assert history.unpack(second) == following
            corrected = [dict(point, audit={'changed': True}) if index == 3 else point
                         for index, point in enumerate(following)]
            third = history.pack(('points', 'path'), corrected, _owned_previous=following, _changed_from=3)
            assert third is not None
            assert history.unpack(first) == points
            assert history.unpack(third) == corrected
            cast(dict[str, object], history.unpack(second)[0]['audit'])['known'] = -1
            assert history.unpack(second) == following
        finally:
            history.close()


@pytest.mark.parametrize('mirror', (False, True))
def test_owned_three_level_outputs_match_cold_and_never_mutate_previous_dates(mirror: bool) -> None:
    source = _source(1, mirror)
    saved: list[tuple[list[dict[str, object]], list[dict[str, object]]]] = []
    with ExitStack() as resources:
        resources.enter_context(owned_evidence_scope())
        caches = [_TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024) for _ in range(3)]
        cascade = resources.enter_context(_TrendCascadeCache(caches))
        paths = [resources.enter_context(_ClosedPreparedPaths(_owned_history=True)) for _ in range(3)]
        for end in range(1, len(source) + 1):
            actual = prepare_trend_cascade(source[:end], symbol='FROZEN', caches=caches,
                closed_paths=paths, _round_caches=cascade, _owned_source=True)
            expected = prepare_trend_cascade(source[:end], symbol='FROZEN')
            assert actual == expected, (mirror, end)
            saved.append((actual, deepcopy(actual)))
        for actual, expected in saved:
            assert actual == expected
