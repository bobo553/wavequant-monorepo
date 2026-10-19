"""受控版本、增量结果账本与独立冷计算的完整证据差分。"""

from copy import deepcopy
from sys import getsizeof
from types import MappingProxyType
from collections.abc import Mapping, Sequence

import pytest

from wavequant.domain.market_structure import trend_judgement as module
from wavequant.domain.market_structure.trend_judgement import (
    _JudgementLedger, _SourceVersionOwner, _finalize_judgement, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_level_upgrades import _TrendUpgradeState
from wavequant.domain.market_structure.trend_levels import _SourceTurn, _TrendReversalState, _source_turns
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache


def test_budget_counts_slotted_frozen_evidence_and_template_once() -> None:
    state = module._TrendJudgementState(1)
    data = MappingProxyType({'audit': ('nested evidence',)})
    template = '{"audit":["nested evidence"]}'
    point = module._FrozenPoint(data, template)
    expected = getsizeof(point) + state._owned_value_bytes([data, template])
    assert state._owned_value_bytes([point]) == expected
    assert state._owned_value_bytes([point, point, data, template]) == expected


def _point(index: int, kind: str, price: float, level: int) -> dict[str, object]:
    return dict(index=index, ordinal=0, kind=kind, value=price, time=str(index),
                available_at=index + 1, trend_level=level, state='confirmed', audit={'version': 1})


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('mirror', (False, True))
def test_owned_revisions_match_cold_points_and_audit_at_every_prefix(level: int, mirror: bool) -> None:
    prices = (20., 30., 10., 20., 14., 26., 18., 35., 28., 45., 36., 42., 30., 36.,
              24., 31., 26., 50., 38., 54., 28., 40., 18., 32., 24., 48., 34., 58.)
    source = [_point(i * 3, ('H' if mirror else 'L') if i % 2 == 0 else ('L' if mirror else 'H'),
                     100 - value if mirror else value, level - 1) for i, value in enumerate(prices)]
    cache = _TrendJudgementCache(tail_reuse=True, state_memory_bytes=32 * 1024 * 1024)
    snapshots = []
    for end in range(1, len(source) + 1):
        raw = deepcopy(source[:end])
        if end >= 17:
            raw[9]['value'] = 53. if mirror else 47.
            raw[9]['audit'] = {'version': 2}
        actual = cache.judge(raw, path='path', trend_level=level, _owned_source=True)
        expected = judge_trend(raw, trend_level=level)
        assert mutable_points(actual.points) == mutable_points(expected.points), (level, mirror, end)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates), (level, mirror, end)
        snapshots.append((actual, mutable_points(actual.points), mutable_points(actual.candidates)))
        if end == 15:
            cache.remember_source_flip(level, 'path')
    for result, points, candidates in snapshots:
        assert mutable_points(result.points) == points
        assert mutable_points(result.candidates) == candidates


def test_unchanged_version_skips_parsing_and_append_only_parses_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    source = [_point(i * 3, 'L' if i % 2 == 0 else 'H', 10. if i % 2 == 0 else 20., 0)
              for i in range(30)]
    owner = _SourceVersionOwner(1, 8 * 1024 * 1024)
    first = owner.prepare(source)
    assert first is not None
    lengths: list[int] = []
    original = module._source_turns

    def parse(points: Sequence[Mapping[str, object]], level: int, *, retain_geometry: bool = False,
              accept_direction_facts: bool = False, _position_offset: int = 0) -> list[_SourceTurn]:
        lengths.append(len(points))
        return original(points, level, retain_geometry=retain_geometry,
                        accept_direction_facts=accept_direction_facts, _position_offset=_position_offset)

    monkeypatch.setattr(module, '_source_turns', parse)
    assert owner.prepare(deepcopy(source)) is first
    assert lengths == []
    source.append(_point(90, 'L', 11., 0))
    second = owner.prepare(source)
    assert second is not None and second.parent is first.token
    assert lengths == [2]
    assert second.changed_from == 30
    assert second.source[0] is first.source[0]
    source[4]['audit'] = {'version': 2}
    revised = owner.prepare(source)
    assert revised is not None and revised.changed_from == 4
    assert revised.source[4]['audit'] == {'version': 2}
    assert first.source[4]['audit'] == {'version': 1}


def test_version_rewinds_and_future_cutoffs_do_not_reuse_later_results() -> None:
    source = [_point(0, 'L', 10., 0), _point(3, 'H', 20., 0),
              _point(6, 'L', 12., 0), _point(9, 'H', 23., 0)]
    cache = _TrendJudgementCache(tail_reuse=True)
    cache.judge(source, path='p', trend_level=1, _owned_source=True)
    for raw, cutoff in ((source[:2], None), (source, 4), (source, 10)):
        actual = cache.judge(raw, path='p', trend_level=1, asof=cutoff, _owned_source=True)
        expected = judge_trend(raw, trend_level=1, asof=cutoff)
        assert mutable_points(actual.points) == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_inserting_earlier_opposite_point_restores_previously_hidden_extreme(level: int) -> None:
    ordinary = _TrendReversalState(level)
    upgrade = _TrendUpgradeState([], [], trend_level=level)
    ledger = _JudgementLedger()
    events: list[dict[str, object]] = []
    points = [_point(0, 'H', 10., level), _point(2, 'H', 12., level), _point(1, 'L', 8., level)]
    for step, raw in enumerate(points):
        turn = _source_turns([raw], level)[0]
        identity = turn.index, turn.ordinal, turn.kind, turn.value
        upgrade.merged[identity] = turn
        upgrade.updated_merged = {identity: turn}
        actual, _ = ledger.update(ordinary, upgrade, events, (0, 1, 2), initial=step == 0, level=level)
        merged = [dict(upgrade.merged[key].source) for key in sorted(upgrade.merged)]
        expected = _finalize_judgement([], merged, events, (0, 1, 2), level)
        assert mutable_points(actual.points) == mutable_points(expected.points)
        assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert [point['index'] for point in actual.points] == [0, 1, 2]
    before = mutable_points(actual.points)
    events.append(dict(points[1], available_at=7, wave_completed_at=7,
                       wave_completion_policy='ledger_fixture', wave_completion_evidence={'known': 7}))
    upgrade.updated_merged = {}
    completed, _ = ledger.update(ordinary, upgrade, events, (0, 1, 2), initial=False, level=level)
    expected = _finalize_judgement([], merged, events, (0, 1, 2), level)
    assert mutable_points(completed.points) == mutable_points(expected.points)
    assert mutable_points(completed.candidates) == mutable_points(expected.candidates)
    assert completed.points[-1]['available_at'] == 3
    assert completed.points[-1]['wave_completed_at'] == 7
    assert mutable_points(actual.points) == before
