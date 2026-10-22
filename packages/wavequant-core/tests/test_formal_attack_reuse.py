"""正式开放端点的缓存必须与冷判定逐点、逐份证据一致。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_judgement
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _with_formal_key_breaks, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_lines import _TrendJudgementCache
from wavequant.domain.market_structure.trend_options import ordinary_trend_completion


FIGURE = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26)
VALUES = (*FIGURE, 50, 30, 60, 32, 70, 28, 55, 24)
START = date(2026, 1, 1)


def _source(values: Sequence[int], level: int, *, mirror: bool, dates: bool) -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, kind=('H' if mirror else 'L') if index % 2 == 0 else
                 ('L' if mirror else 'H'), value=100-value if mirror else value,
                 time=(START + timedelta(days=index)).isoformat(),
                 available_at=(START + timedelta(days=index+1)).isoformat() if dates else index+1,
                 state='confirmed', trend_level=level, label=f'p{index}')
            for index, value in enumerate(values)]


def _formal_source(level: int, *, mirror: bool = False, dates: bool = False) -> list[dict[str, object]]:
    source = _source(VALUES, level-1, mirror=mirror, dates=dates)
    for index in (17, 19, 21):
        lower = judge_trend(_source(VALUES[:index+1], level-2, mirror=mirror, dates=dates), trend_level=level-1)
        source[index] = next(point for point in mutable_points(lower.points) if point['index'] == index)
        assert 'wave_completed_at' not in source[index]
    return source


@pytest.mark.parametrize('level', (2, 3))
@pytest.mark.parametrize('mirror', (False, True))
@pytest.mark.parametrize('dates', (False, True))
def test_owned_formal_prefixes_preserve_complete_cold_evidence(level: int, mirror: bool, dates: bool) -> None:
    source = _formal_source(level, mirror=mirror, dates=dates)
    cache = _TrendJudgementCache(tail_reuse=True)
    for end in range(17, len(source)+1):
        before = deepcopy(source[:end])
        observed = cache.judge(source[:end], path='same-path', trend_level=level, _owned_source=True)
        expected = judge_trend(source[:end], trend_level=level)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
        assert source[:end] == before


def test_formal_tail_revision_reparses_the_boundary_without_copying_the_old_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _formal_source(2)[:18]
    base = judge_trend(source, trend_level=2)
    cache = _FormalKeyBreakState(2)
    _with_formal_key_breaks(source, base, _append_state=cache)
    parsed: list[int] = []
    original = trend_judgement._source_turns

    def record(points: Sequence[Mapping[str, object]], source_level: int, *, retain_geometry: bool = False,
               accept_direction_facts: bool = False, _position_offset: int = 0,
               formal_key_attacks: bool = False) -> list[trend_judgement._SourceTurn]:
        if formal_key_attacks:
            parsed.append(len(points))
        return original(points, source_level, retain_geometry=retain_geometry,
                        accept_direction_facts=accept_direction_facts, _position_offset=_position_offset,
                        formal_key_attacks=formal_key_attacks)

    monkeypatch.setattr(trend_judgement, '_source_turns', record)
    source[-1]['audit_note'] = ['new audit evidence']
    observed = _with_formal_key_breaks(source, base, _append_state=cache)
    assert parsed and sum(parsed) <= 2
    expected = _with_formal_key_breaks(source, base)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('mutation', ('old_clock', 'truncate', 'completion'))
def test_formal_revisions_do_not_inherit_later_or_replaced_knowledge(mutation: str) -> None:
    source = _formal_source(2)
    cache = _TrendJudgementCache(tail_reuse=True)
    cache.judge(source, path='same-path', trend_level=2, _owned_source=True)
    revised = deepcopy(source)
    if mutation == 'old_clock':
        revised[12]['available_at'] = 18
    elif mutation == 'truncate':
        revised = revised[:18]
    else:
        lower = judge_trend(_source(VALUES, 0, mirror=False, dates=False))
        # 真正的后知闭合来自下级判断，不能由缓存自行补结束时钟。
        closed = next(point for point in mutable_points(lower.points) if point['index'] == 21)
        assert 'wave_completed_at' in closed
        revised[21] = closed
    observed = cache.judge(revised, path='same-path', trend_level=2, _owned_source=True)
    expected = judge_trend(revised, trend_level=2)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)


def test_formal_cache_does_not_hide_a_mutated_proof_and_recovers_after_rejection() -> None:
    source = _formal_source(2)[:18]
    base = judge_trend(source, trend_level=2)
    cache = _FormalKeyBreakState(2)
    _with_formal_key_breaks(source, base, _append_state=cache)
    forged = deepcopy(source)
    proof = cast(dict[str, object], forged[-1]['upgrade_evidence'])
    cast(dict[str, object], proof['broken_key'])['available_at'] = 100
    with pytest.raises(ValueError):
        _with_formal_key_breaks(forged, base, _append_state=cache)
    recovered = _with_formal_key_breaks(source, base, _append_state=cache)
    expected = _with_formal_key_breaks(source, base)
    assert mutable_points(recovered.candidates) == mutable_points(expected.candidates)


def test_formal_state_budget_discards_reuse_without_discarding_evidence() -> None:
    source = _formal_source(2)[:18]
    base = judge_trend(source, trend_level=2)
    cache = _FormalKeyBreakState(2, memory_bytes=1)
    observed = _with_formal_key_breaks(source, base, _append_state=cache)
    expected = _with_formal_key_breaks(source, base)
    assert mutable_points(observed.points) == mutable_points(expected.points)
    assert mutable_points(observed.candidates) == mutable_points(expected.candidates)
    assert cache.memory_used == 0


@pytest.mark.parametrize('level', (2, 3))
def test_formal_cache_rule_switch_does_not_carry_evidence_between_modes(level: int) -> None:
    source = _formal_source(level)
    cache = _TrendJudgementCache(tail_reuse=True)
    for enabled in (False, True, False):
        with ordinary_trend_completion(enabled):
            observed = cache.judge(source, path='same-path', trend_level=level, _owned_source=True)
            expected = judge_trend(source, trend_level=level)
        assert mutable_points(observed.points) == mutable_points(expected.points)
        assert mutable_points(observed.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('hidden_count', (1, 2, 3))
def test_a_previously_hidden_leading_source_is_revalidated_before_the_old_first_turn(hidden_count: int) -> None:
    source = _formal_source(2)[:18]
    hidden = deepcopy(source)
    for point in hidden[:hidden_count]:
        point['display_only'] = True
    formal = _FormalKeyBreakState(2)
    cache = _TrendJudgementCache(tail_reuse=True)
    _with_formal_key_breaks(hidden, judge_trend(hidden, trend_level=2), _append_state=formal)
    cache.judge(hidden, path='same-path', trend_level=2, _owned_source=True)
    base = judge_trend(source, trend_level=2)
    direct = _with_formal_key_breaks(source, base, _append_state=formal)
    expected = _with_formal_key_breaks(source, base)
    observed = cache.judge(source, path='same-path', trend_level=2, _owned_source=True)
    assert mutable_points(direct.points) == mutable_points(expected.points)
    assert mutable_points(direct.candidates) == mutable_points(expected.candidates)
    assert mutable_points(observed.points) == mutable_points(base.points)
    assert mutable_points(observed.candidates) == mutable_points(base.candidates)


def test_all_filtered_sources_can_become_visible_without_reusing_an_empty_projection() -> None:
    source = _formal_source(2)[:18]
    hidden = [dict(point, display_only=True) for point in source]
    formal = _FormalKeyBreakState(2)
    cache = _TrendJudgementCache(tail_reuse=True)
    empty = judge_trend(hidden, trend_level=2)
    assert not empty.points and not empty.candidates
    _with_formal_key_breaks(hidden, empty, _append_state=formal)
    cache.judge(hidden, path='same-path', trend_level=2, _owned_source=True)
    base = judge_trend(source, trend_level=2)
    direct = _with_formal_key_breaks(source, base, _append_state=formal)
    expected = _with_formal_key_breaks(source, base)
    observed = cache.judge(source, path='same-path', trend_level=2, _owned_source=True)
    assert mutable_points(direct.points) == mutable_points(expected.points)
    assert mutable_points(direct.candidates) == mutable_points(expected.candidates)
    assert mutable_points(observed.points) == mutable_points(base.points)
    assert mutable_points(observed.candidates) == mutable_points(base.candidates)


def test_a_new_same_bar_ordinal_inside_a_filtered_gap_matches_cold_evidence() -> None:
    source = _formal_source(2)[:18]
    source[3]['display_only'] = True
    formal = _FormalKeyBreakState(2)
    cache = _TrendJudgementCache(tail_reuse=True)
    before = judge_trend(source, trend_level=2)
    _with_formal_key_breaks(source, before, _append_state=formal)
    cache.judge(source, path='same-path', trend_level=2, _owned_source=True)
    # 同一 K 线内 ordinal 表示有序高低事件，不能只按 index 将新增来源去重。
    inserted = dict(index=2, ordinal=1, time=source[2]['time'], available_at=3,
                    kind='H', value=18, state='confirmed', trend_level=1, label='gap-high')
    revised = [*source[:3], inserted, *source[3:]]
    base = judge_trend(revised, trend_level=2)
    direct = _with_formal_key_breaks(revised, base, _append_state=formal)
    expected = _with_formal_key_breaks(revised, base)
    observed = cache.judge(revised, path='same-path', trend_level=2, _owned_source=True)
    assert mutable_points(direct.points) == mutable_points(expected.points)
    assert mutable_points(direct.candidates) == mutable_points(expected.candidates)
    assert mutable_points(observed.points) == mutable_points(base.points)
    assert mutable_points(observed.candidates) == mutable_points(base.candidates)

@pytest.mark.parametrize('level', (2, 3))
def test_certified_formal_source_borrows_proof_and_public_source_stays_isolated(level: int) -> None:
    from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
    from wavequant.domain.market_structure.trend_evidence_reuse import _geometry_signature

    source = _formal_source(level)[:18]
    proof = {'observations': [1, 2, 3]}
    source[0]['audit_note'] = proof
    base = judge_trend(source, trend_level=level)
    before = deepcopy(source)
    public_state = _FormalKeyBreakState(level)
    expected = public_state.calculate(source, base)
    assert public_state._turns[0].source['audit_note'] == proof
    assert public_state._turns[0].source['audit_note'] is not proof
    with owned_evidence_scope() as owner:
        keys = [_geometry_signature(point) for point in source]
        assert all(key is not None for key in keys)
        certified = CertifiedSource(source, cast(list[bytes], keys), owner, None)
        state = _FormalKeyBreakState(level)
        actual = state.calculate(certified, base, _certified_keys=certified.keys)
        assert state._turns[0].source['audit_note'] is proof
        assert actual == expected
        assert source == before
        assert actual is not None
        exported = mutable_points(actual.candidates)
        for point in exported:
            if 'audit_note' in point:
                cast(dict[str, object], point['audit_note'])['observations'] = []
        assert source == before
