"""长回测的索引外围成本回归：因果前缀、首个严格突破及尾部恢复。"""

from collections.abc import Sequence
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
import json
from typing import cast

import pytest

from wavequant.domain.market_structure.secondary_trend import _first_close_break_below
from wavequant.domain.market_structure.alternation_duration import filter_duration_landmarks
from wavequant.domain.market_structure.trend_confirmation import prepare_confirmation_context, qualify_uptrend
from wavequant.domain.market_structure.trend_judgement import _frozen_point, judge_trend, mutable_points
from wavequant.domain.market_structure.trend_history_changes import TrendChangeRows
from wavequant.domain.market_structure.trend_landmarks import (
    bear_bull_alternation_lows, bearish_turn_signals, bullish_turn_signals, post_alternation_bull_highs,
)
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _TrendJudgementCache, _annotate_permissions
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.market_structure.trend_prefix_view import _PrefixView, _VisibleReferences
from wavequant.domain.models.model import Bar
from wavequant.domain.market_structure import trend_readonly_cache
from wavequant.domain.market_structure import trend_lines
from wavequant.domain.market_structure.trend_levels import _completed_source_point
from wavequant.infrastructure.persistence.calculation_chunk import EvidenceJsonCache, TrendChunk, restore_calculation_chunk
from wavequant.infrastructure.persistence.calculation_step_cache import _encode_json

from .test_confirmation_context_prefix_reuse import _sample
from .test_trend_append_state import _points
from .test_trend_landmarks import point
from .test_trend_lifecycle import scenario
from .test_trend_history_performance_reuse import VALUES, _completed_point, _source


def test_hierarchy_projection_keeps_daily_coordinates_and_does_not_cache_partial_views(monkeypatch) -> None:
    prices = (*VALUES, 45, 51, 48)
    bars = tuple(Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', price,
        price + 1, price - 1, price, 100) for index, price in enumerate(prices))
    source = _source()
    prepared = trend_lines.prepare_trend_lines(source)
    cache = trend_lines._TrendPermissionCache(bars)

    def coordinates(view):
        return [(stroke['source_path'], [tuple(point[field] for field in
            ('index', 'ordinal', 'kind', 'value', 'available_at')) for point in stroke['points']])
            for stroke in view['qualified_strokes']]

    def forbidden(*args, **kwargs):
        raise AssertionError('a hierarchy projection must not create trading attachments')

    for end in range(len(source), len(bars) + 1):
        cold = trend_lines.attach_trend_permissions(prepared, bars[:end])
        with monkeypatch.context() as patch:
            patch.setattr(trend_lines, '_annotate_permissions', forbidden)
            patch.setattr(trend_lines, 'confirmed_trend_legs', forbidden)
            patch.setattr(trend_lines, 'bear_bull_alternation_lows', forbidden)
            light = trend_lines.attach_trend_permissions(prepared, bars[:end],
                _permission_cache=cache, _owned_candidates=True, _hierarchy_only=True)
        assert coordinates(light) == coordinates(cold)
        full = trend_lines.attach_trend_permissions(prepared, bars[:end],
            _permission_cache=cache, _owned_candidates=True)
        assert full == cold
    with pytest.raises(ValueError, match='N confirmation'):
        trend_lines.attach_trend_permissions(prepared, bars,
            n_target_trend_confirmation_enabled=True, _hierarchy_only=True)


def test_completion_validation_does_not_serialize_inherited_audit() -> None:
    class Audit:
        def __reduce_ex__(self, protocol):
            raise AssertionError('completion must not serialize unrelated audit')

    source = _completed_point()
    audit = Audit()
    source['audit'] = audit
    projected = _completed_source_point(source)
    assert projected is not None and projected['audit'] is audit
    source['wave_completion_evidence'] = {}
    with pytest.raises(ValueError):
        _completed_source_point(source)


def test_saturated_readonly_cache_refuses_new_objects_without_traversing(monkeypatch) -> None:
    cache = trend_readonly_cache._ReadonlySnapshotCache[dict[str, object]](
        memory_bytes=8192, max_entries=1, evict=False)
    cache.put('first', None, dict(proof='retained'))

    def forbidden(*args):
        raise AssertionError('a full non-evicting cache must not walk new history')

    monkeypatch.setattr(trend_readonly_cache, '_builtin_size', forbidden)
    cache.put('second', None, dict(proof='new'))
    assert cache.get('first', None) == (dict(proof='retained'),)
    assert cache.get('second', None) is None


def test_stroke_encoding_reuses_leg_points_and_keeps_revisions() -> None:
    cache = EvidenceJsonCache()
    points = [dict(index=i, evidence=dict(history=list(range(100)))) for i in range(10)]
    stroke = dict(points=points, structural_legs=[dict(points=[dict(left), dict(right)])
        for left, right in zip(points, points[1:])], confirmed_legs=[])
    assert cache.encode(stroke, _encode_json, stroke=True) == _encode_json(stroke)
    assert cache.reused_bytes > 0
    revised = deepcopy(stroke)
    revised_legs = cast(list[dict[str, object]], revised['structural_legs'])
    revised_points = cast(list[dict[str, object]], revised_legs[0]['points'])
    cast(dict[str, list[int]], revised_points[0]['evidence'])['history'][0] = -1
    assert cache.encode(revised, _encode_json, stroke=True) == _encode_json(revised)
    assert cache.encode(stroke, _encode_json, stroke=True) == _encode_json(stroke)


def test_point_chunks_deduplicate_complete_proofs_and_restore_independent_rows() -> None:
    chunk, cache = TrendChunk(), EvidenceJsonCache()
    points = [dict(index=index, proof=dict(text='证据' * 1000)) for index in range(3)]
    stroke = dict(id='path', points=points,
        structural_legs=[dict(points=points[:2]), dict(points=points[1:])], confirmed_legs=[])
    row = dict(bar_index=0, strokes=[stroke])
    expected = deepcopy(row)
    encoded, _ = chunk.encode(row, cache, _encode_json)
    assert len(chunk.values) == 3
    cast(dict[str, str], points[1]['proof'])['text'] = '修订证明'
    revised = dict(row, bar_index=1)
    encoded_revision, _ = chunk.encode(revised, cache, _encode_json)
    restored = cast(list[dict[str, object]], restore_calculation_chunk(
        json.loads(chunk.finish([encoded, encoded_revision]))))
    assert restored == [expected, revised]
    assert len(chunk.values) == 4
    restored_stroke = cast(list[dict[str, object]], restored[0]['strokes'])[0]
    restored_point = cast(list[dict[str, object]], restored_stroke['points'])[0]
    cast(dict[str, str], restored_point['proof'])['text'] = 'caller mutation'
    restored_leg = cast(list[dict[str, object]], restored_stroke['structural_legs'])[0]
    leg_point = cast(list[dict[str, object]], restored_leg['points'])[0]
    assert cast(dict[str, str], leg_point['proof'])['text'] == '证据' * 1000


@pytest.mark.parametrize('reference', [-1, 1, True, '0'])
def test_point_chunk_rejects_invalid_reference(reference) -> None:
    value = dict(format='trend_point_refs_v2', values=[dict(index=0)],
        rows=[dict(row=dict(bar_index=0), strokes=[dict(row={}, points=[reference])])])
    with pytest.raises(ValueError, match='point reference'):
        restore_calculation_chunk(value)


def test_permission_annotation_cache_tracks_early_revision_and_clock_mapping() -> None:
    cache = trend_readonly_cache._ReadonlySnapshotCache[list[dict[str, object]]](
        memory_bytes=1024 * 1024, max_entries=4)
    source: list[dict[str, object]] = [dict(index=i, ordinal=0, time=str(i), available_at=i, kind='L' if i % 2 == 0 else 'H',
                   value=value, label=f'point-{i}') for i, value in enumerate((10, 20, 14, 22, 12, 25))]
    dates: dict[object, int] = {i: i for i in range(10)}
    cases = [deepcopy(source), deepcopy(source), deepcopy(source)]
    cases[1][0]['value'] = 9
    cases[2][0]['available_at'] = 1
    for values in cases:
        cold, borrowed = deepcopy(values), deepcopy(values)
        _annotate_permissions(cold, 'TEST', dates, trend_level=1)
        _annotate_permissions(borrowed, 'TEST', dates, trend_level=1,
            _cache=cache, _cache_key=(1, 'path'))
        assert borrowed == cold
        repeated = deepcopy(values)
        _annotate_permissions(repeated, 'TEST', dates, trend_level=1,
            _cache=cache, _cache_key=(1, 'path'))
        assert repeated == cold
    dates = {i: i + 1 for i in range(10)}
    cold, borrowed = deepcopy(source), deepcopy(source)
    _annotate_permissions(cold, 'TEST', dates, trend_level=1)
    _annotate_permissions(borrowed, 'TEST', dates, trend_level=1,
        _cache=cache, _cache_key=(1, 'path'))
    assert borrowed == cold


def test_dated_trend_read_skips_gap_and_future_and_restores_append_cursor() -> None:
    decoded: list[int] = []

    def decode(value: object) -> dict[str, object]:
        row = cast(dict[str, object], value)
        decoded.append(cast(int, row['known']))
        return row

    with TrendChangeRows(decode, row_count=1002) as history:
        for position in range(1001):
            history.write_change(position, dict(known=position, evidence=[position]))
        history.read_limit = 1000
        assert history._at(999) == dict(known=999, evidence=[999])
        assert decoded == [999]
        with pytest.raises(ValueError, match='readable prefix'):
            history._at(1000)
        history.write_change(1001, dict(known=1001, evidence=[1001]))
        history.read_limit = 1002
        assert history._at(1001) == dict(known=1001, evidence=[1001])
        assert decoded == [999, 1001]


def test_dated_trend_read_uses_predecessor_between_changes() -> None:
    with TrendChangeRows(lambda value: value, row_count=20) as history:
        history.write_change(0, dict(value='first'))
        history.write_change(10, dict(value='revised'))
        assert history._at(9) == dict(value='first')
        assert history._at(10) == dict(value='revised')
        assert history._at(19) == dict(value='revised')


def test_prefix_windows_keep_indexing_and_mask_future_without_reading_history() -> None:
    class Counted(Sequence[int]):
        reads = 0

        def __len__(self) -> int:
            return 100_000

        def __getitem__(self, index):
            assert isinstance(index, int), 'a view must not copy the underlying slice'
            self.reads += 1
            return index

    source = Counted()
    market = _PrefixView(source, range(90_000))
    hidden = _VisibleReferences(source, 80_000)
    late = market[89_990:]
    assert source.reads == 0
    assert late[0] == 89_990 and late[-1] == 89_999
    assert hidden[-1] is None and hidden[80_000] is None
    assert source.reads == 2
    assert tuple(hidden[79_998:80_002]) == (79_998, 79_999, None, None)
    assert source.reads == 4
    with pytest.raises(IndexError):
        _ = hidden[len(source)]
    with pytest.raises(IndexError):
        _ = market[len(market)]


@pytest.mark.parametrize('clock', ('integer', 'date', 'mixed'))
def test_indexed_confirmation_windows_match_all_cold_prefixes_and_nested_rewinds(clock) -> None:
    raw, source = _sample(clock=clock)
    owner = _MarketRangeIndex.build(tuple(raw))
    assert owner is not None
    bars = owner.bars
    market = _MarketPrefixIndex(owner, bars)
    full = prepare_confirmation_context(source, bars, len(bars) - 1, _market_index=market)
    for cutoff in range(len(bars)):
        projected = prepare_confirmation_context(source, bars, cutoff, parsed_context=full, _market_index=market)
        cold = prepare_confirmation_context(source, bars, cutoff)
        assert projected.bars == cold.bars
        assert projected.references == cold.references
        assert projected.ordered == cold.ordered
        actual = qualify_uptrend(source, source[1], 1, source[0], bars, cutoff, context=projected)
        assert actual == qualify_uptrend(source, source[1], 1, source[0], bars, cutoff, context=cold)
        assert isinstance(projected.bars, _PrefixView)
        assert projected.bars.source is bars
        if cutoff:
            prior = prepare_confirmation_context(source, bars, cutoff - 1,
                parsed_context=projected, _market_index=market)
            expected = prepare_confirmation_context(source, bars, cutoff - 1)
            assert prior.references == expected.references
            assert tuple(prior.bars or ()) == tuple(expected.bars or ())


@pytest.mark.parametrize('level', (1, 2, 3))
def test_tail_revision_uses_nearer_checkpoint_and_retains_full_cold_evidence(level: int) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=level - 1, clock='integer')
    cache = _TrendJudgementCache(tail_reuse=True)
    cache.judge(source[:8], path='source', trend_level=level)
    cache.remember_source_flip(level, 'source')
    for end in range(9, len(source) + 1):
        cache.judge(source[:end], path='source', trend_level=level)
    state = cache._states[(level, 'source')]
    before = state.tail_restores, state.flip_restores
    revised = deepcopy(source)
    revised[-1]['audit'] = {'late_revision': True}
    actual = cache.judge(revised, path='source', trend_level=level)
    expected = judge_trend(revised, trend_level=level)
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
    assert (state.tail_restores, state.flip_restores) == (before[0] + 1, before[1])


@pytest.mark.parametrize('level', (1, 2, 3))
def test_bull_signal_and_shared_alternation_keep_first_strict_day(level: int) -> None:
    key = point(1, 'H', 20, '2026-01-03')
    origin = point(2, 'L', 10, '2026-01-04')
    high = point(3, 'H', 30, '2026-01-05')
    low = point(4, 'L', 18, '2026-01-08', observations=[dict(title='空多交替',
        available_at='2026-01-08', ratio=.6, flip_high=high, confirmed_bear_low=origin,
        broken_key=key, origin=origin)])
    strokes = [dict(id='same-path', points=[origin, high, low])]
    bars = tuple(Bar(datetime(2026, 1, day), 'TEST', 28., maximum, 27., close, 1000.)
        for day, maximum, close in ((7, 31., 29.), (8, 32., 31.), (9, 31., 30.),
                                    (10, 33., 30.), (11, 35., 31.)))
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    for end in range(1, len(bars) + 1):
        prefix = bars[:end]
        market = _MarketPrefixIndex(owner, prefix)
        lows = bear_bull_alternation_lows(strokes, trend_level=level, bars=prefix)
        actual = bullish_turn_signals(strokes, prefix, trend_level=level,
            _alternation_lows=lows, _market_index=market)
        assert actual == bullish_turn_signals(strokes, prefix, trend_level=level)
        assert post_alternation_bull_highs(strokes, trend_level=level, bars=prefix, _alternation_lows=lows) == (
            post_alternation_bull_highs(strokes, trend_level=level, bars=prefix))
        assert [signal['time'] for signal in actual] == (['2026-01-11'] if end == len(bars) else [])


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('defense', ('held', 'equal', 'broken'))
def test_bear_signal_index_preserves_defense_before_cross_and_future_cutoff(level: int, defense: str) -> None:
    raw, geometry = scenario(up=False)
    if defense != 'held':
        ceiling = geometry['strokes'][0]['points'][-1]['value']
        raw[5] = replace(raw[5], high=ceiling + (.01 if defense == 'broken' else 0))
    bars = tuple(raw)
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    for end in range(1, len(bars) + 1):
        prefix = bars[:end]
        actual = bearish_turn_signals(geometry['strokes'], prefix, trend_level=level,
            _market_index=_MarketPrefixIndex(owner, prefix))
        expected = bearish_turn_signals(geometry['strokes'], prefix, trend_level=level)
        assert actual == expected
        if end == len(bars):
            assert bool(actual) == (defense != 'broken')


def test_reanchor_close_index_keeps_same_day_confirmation_and_strict_equality() -> None:
    bars = tuple(Bar(datetime(2026, 1, day), 'TEST', 10., 12., 5., close, 1000.)
                 for day, close in ((1, 8.), (2, 7.), (3, 8.), (4, 8.), (5, 7.)))
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    low = dict(time='2026-01-01', available_at='2026-01-03', value=8.)
    for end in range(1, len(bars) + 1):
        prefix = bars[:end]
        indexed = _first_close_break_below(prefix, low, _session_dates=owner.session_dates,
            _ordered_dates=True, _market_index=_MarketPrefixIndex(owner, prefix))
        assert indexed == _first_close_break_below(prefix, low)
        assert (indexed['index'] if indexed else None) == (4 if end == 5 else None)


def test_owned_exports_reuse_only_frozen_nested_evidence_and_isolate_annotations() -> None:
    frozen = _frozen_point(dict(index=1, audit={'proof': [1, 2, 3]}))
    cache = _TrendJudgementCache()
    first = cache.export_owned((frozen,))[0]
    first['label'] = 'caller annotation'
    second = cache.export_owned((frozen,))[0]
    assert second == mutable_points((frozen,))[0]
    assert first['audit'] is second['audit']
    public = mutable_points((frozen,))[0]
    cast(dict[str, object], public['audit'])['proof'] = []
    assert cache.export_owned((frozen,))[0]['audit'] == {'proof': [1, 2, 3]}


def test_closed_paths_borrow_only_internally_and_public_decode_remains_independent() -> None:
    cache = _ClosedPreparedPaths()
    source = [dict(index=0, audit={'proof': [1, 2]})]
    source_stroke: dict[str, object] = dict(id='p', points=source)
    candidates: dict[str, object] = dict(source_path='p', points=[])
    cache.put(1, 'p', source, 5, source_stroke, candidates, None, None)
    saved = cache.get(1, 'p', source, 5)
    assert saved is not None
    first = cache.restore(saved, borrow=True)
    assert cache.restore(saved, borrow=True)[0] is first[0]
    public = cache.restore(saved)
    cast(list[dict[str, object]], public[0]['points']).clear()
    assert first[0]['points'] == source


@pytest.mark.parametrize('broken', (False, True))
def test_short_pullback_duration_keeps_first_close_above_and_floor_boundary(broken: bool) -> None:
    bars = tuple(Bar(datetime(2026, 1, index + 1), 'TEST', 16., max(21., close),
        16. if not broken or index != 6 else 14.99, close, 1000.)
        for index, close in enumerate((16., 16., 16., 16., 20., 16., 20., 21., 19.)))
    landmark = dict(index=5, value=15., available_at='2026-01-06',
        confirmed_bear_low=dict(index=0, value=10.), confirmed_flip_high=dict(index=4, value=20.))
    owner = _MarketRangeIndex.build(bars)
    assert owner is not None
    for end in range(1, len(bars) + 1):
        prefix = bars[:end]
        actual = filter_duration_landmarks([landmark], prefix, _market_index=_MarketPrefixIndex(owner, prefix))
        assert actual == filter_duration_landmarks([landmark], prefix)
        assert bool(actual) == (end >= 8 and not broken)

@pytest.mark.parametrize('level', (1, 2, 3))
def test_restoring_tail_rolls_back_discarded_payload_charge(level: int) -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=level - 1, clock='integer')
    cache = _TrendJudgementCache(tail_reuse=True)
    for end in range(8, len(source) + 1):
        cache.judge(source[:end], path='source', trend_level=level)
    state = cache._states[level, 'source']
    assert state._checkpoint is not None
    expected = state._checkpoint_payload_upper_bytes
    state._payload_upper_bytes += 1_000_000
    before_walks = state.deep_budget_walks
    assert state._restore_tail(state._checkpoint[0])
    assert state._payload_upper_bytes == expected
    assert state.deep_budget_walks == before_walks
    assert state._owned_bytes() <= state.memory_used
    for revision in range(30):
        revised = deepcopy(source)
        revised[-1]['audit'] = {'revision': revision}
        actual = cache.judge(revised, path='source', trend_level=level)
        cold = judge_trend(revised, trend_level=level)
        assert actual == cold
    assert state._payload_upper_bytes < expected + 200_000
    assert state._owned_bytes() <= state.memory_used


def test_restoring_tail_keeps_charge_for_a_later_flip_checkpoint() -> None:
    source = _points((20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50),
                     level=0, clock='integer')
    cache = _TrendJudgementCache(tail_reuse=True)
    for end in range(8, len(source) + 1):
        cache.judge(source[:end], path='source', trend_level=1)
    state = cache._states[1, 'source']
    assert state._checkpoint is not None
    state.remember_source_flip()
    assert state._checkpoint_flip_token is not state._flip_budget_token
    expected = state._payload_upper_bytes
    assert state._restore_tail(state._checkpoint[0])
    assert state._payload_upper_bytes == expected
    assert state._owned_bytes() <= state.memory_used
