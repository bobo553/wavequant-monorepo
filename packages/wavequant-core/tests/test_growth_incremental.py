from copy import deepcopy
from random import Random
from datetime import date, timedelta
from typing import Literal

from wavequant.domain.market_structure.trend_levels import Moment

import pytest

from wavequant.domain.market_structure.trend_lines import _annotate_geometry, _GeometryAnnotations, _definition_dates
from wavequant.domain.market_structure.trend_judgement import _CandidateBucket, _JudgementLedger, _TrendJudgementState
Clock = Literal['index', 'numeric', 'date']
START = date(2026, 1, 1)
REFERENCE_FIELDS = ('index', 'ordinal', 'time', 'kind', 'value', 'available_at', 'label')


def _moment(index: int, clock: Clock) -> Moment:
    return index if clock == 'index' else str(index) if clock == 'numeric' else (START + timedelta(days=index)).isoformat()


def _points(level: int, mirror: bool = False, clock: Clock = 'index') -> list[dict[str, object]]:
    # 初始 H 晚知，随后的 L 已早知；五号 H 翻向后，六号 L 的交替观察
    # 仍须等到完整上下文十号日才能成立，各点自己的日期不能随之延后。
    values = (30, 20, 28, 18, 32, 27, 35)
    known = (8, 3, 4, 5, 10, 7, 11)
    points: list[dict[str, object]] = []
    for index, (value, available) in enumerate(zip(values, known), start=1):
        kind = 'H' if index % 2 else 'L'
        if mirror:
            kind, value = ('L' if kind == 'H' else 'H'), 60 - value
        point: dict[str, object] = dict(index=index, ordinal=0, time=(START + timedelta(days=index)).isoformat(),
            kind=kind, value=value, available_at=_moment(available, clock), available_index=available,
            label=f'source-{kind}{index}', state='reversal', trend_level=level, source_level=level-1)
        reference = {field: point[field] for field in REFERENCE_FIELDS}
        point.update(source_confirmation=dict(available_at=point['available_at'], origin=deepcopy(reference)),
            definition_evidence=[deepcopy(reference)],
            upgrade_evidence=dict(available_at=point['available_at'], confirmed_by=deepcopy(reference)),
            confirmed_by=deepcopy(reference), confirmation_rule='independent_structure_fact',
            source_reversal_available_at=point['available_at'], audit=dict(own_known=point['available_at']))
        points.append(point)
    return points




@pytest.mark.parametrize('level', [1, 2, 3])
@pytest.mark.parametrize('mirror', [False, True])
@pytest.mark.parametrize('clock', ['index', 'numeric', 'date'])
def test_annotation_prefix_and_revisions_match_full_replay(level, mirror, clock):
    cache = _GeometryAnnotations()
    original = _points(level, mirror, clock)
    rows = [original[:end] for end in range(1, len(original)+1)]
    rows += [original, original[:-2], original]
    changed = deepcopy(original)
    changed[0]['available_at'] = changed[1]['available_at']
    rows += [changed, original]
    for row in rows:
        expected, actual = deepcopy(row), deepcopy(row)
        dates = _definition_dates(row)
        _annotate_geometry(expected, 'TEST', dates, trend_level=level)
        _annotate_geometry(actual, 'TEST', dates, trend_level=level, _cache=cache)
        assert actual == expected


def test_annotation_unchanged_prefix_does_not_replay_price_state(monkeypatch):
    from wavequant.domain.market_structure.trend_structure import TrendPriceState
    original = _points(1, False, 'index')
    cache = _GeometryAnnotations()
    _annotate_geometry(deepcopy(original), 'TEST', _definition_dates(original), trend_level=1, _cache=cache)
    calls = []
    append = TrendPriceState.append
    def counted(self, kind, price):
        calls.append((kind, price))
        return append(self, kind, price)
    monkeypatch.setattr(TrendPriceState, 'append', counted)
    _annotate_geometry(deepcopy(original), 'TEST', _definition_dates(original), trend_level=1, _cache=cache)
    assert calls == []


def test_checkpoint_shares_unchanged_buckets_without_cloning_history():
    ledger = _JudgementLedger()
    ledger.buckets[(1, 0)] = _CandidateBucket()
    cloned = _TrendJudgementState._copy_ledger(ledger)
    assert cloned is not ledger and cloned.buckets is not ledger.buckets
    assert cloned.buckets[(1, 0)] is ledger.buckets[(1, 0)]


@pytest.mark.parametrize('level', (1, 2, 3))
def test_annotation_long_prefix_rewind_and_early_clock_revision(level):
    random = Random(97)
    source = [dict(index=index, ordinal=0, time=str(index), available_at=index+1,
        kind='H' if index % 2 else 'L', value=random.randint(30, 50) if index % 2 else random.randint(5, 25))
        for index in range(160)]
    cache = _GeometryAnnotations()
    for end in (140, 141, 142, 160, 143, 145, 160):
        rows = deepcopy(source[:end])
        for early in (False, True, False):
            actual = deepcopy(rows)
            if early:
                actual[12]['available_at'] = 100
            expected = deepcopy(actual)
            dates = _definition_dates(actual)
            _annotate_geometry(expected, 'TEST', dates, trend_level=level)
            _annotate_geometry(actual, 'TEST', dates, trend_level=level, _cache=cache)
            assert actual == expected


def test_formal_tail_restores_checkpoint_and_preserves_memory_budget(monkeypatch):
    from wavequant.domain.market_structure.trend_judgement import (
        _FormalKeyBreakState, _with_formal_key_breaks, judge_trend, mutable_points,
    )
    values = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50, 30, 60)
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L', value=value,
        time=(START + timedelta(days=index)).isoformat(), available_at=index+1,
        state='confirmed', trend_level=1, label=f'p{index}') for index, value in enumerate(values)]
    for index in (17, 19):
        lower = judge_trend([dict(point, trend_level=0) for point in source[:index+1]], trend_level=1)
        source[index] = next(point for point in mutable_points(lower.points) if point['index'] == index)
    state = _FormalKeyBreakState(2)
    base = judge_trend(source[:18], trend_level=2)
    for end in (18, 19, 20):
        current = source[:end]
        _with_formal_key_breaks(current, base, _append_state=state)
    assert state._checkpoint is not None
    checkpoint = state._checkpoint
    restored = []
    original = _FormalKeyBreakState._copy_checkpoint
    def copy_checkpoint(self):
        if self is checkpoint:
            restored.append(True)
        return original(self)
    monkeypatch.setattr(_FormalKeyBreakState, '_copy_checkpoint', copy_checkpoint)
    revised = deepcopy(source[:20])
    revised[-1]['audit'] = ['changed tail']
    actual = _with_formal_key_breaks(revised, base, _append_state=state)
    expected = _with_formal_key_breaks(revised, base)
    assert restored
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert state._value_bytes(state._containers()) <= state.memory_used <= state._limit


def test_annotation_checkpoint_does_not_retain_ancestor_proof_trees():
    source = _points(1, False, 'index')
    source[0]['upgrade_evidence']['audit_history'] = list(range(100_000))
    cache = _GeometryAnnotations()
    actual = deepcopy(source)
    _annotate_geometry(actual, 'TEST', _definition_dates(actual), trend_level=1, _cache=cache)
    assert cache.keys and all(key[0] is not None and len(key[0]) == 32 for key in cache.keys)
    assert all('upgrade_evidence' not in shadow for shadow in cache.shadows)
    assert cache.costs[-1] < 100_000
    revised = deepcopy(source)
    revised[0]['upgrade_evidence']['audit_history'][0] = -1
    expected = deepcopy(revised)
    _annotate_geometry(expected, 'TEST', _definition_dates(expected), trend_level=1)
    _annotate_geometry(revised, 'TEST', _definition_dates(revised), trend_level=1, _cache=cache)
    assert revised == expected
