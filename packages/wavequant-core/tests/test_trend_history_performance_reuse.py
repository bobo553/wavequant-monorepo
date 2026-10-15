"""请求内趋势复用保持具体类型拒绝、真实前缀和可变导出隔离。"""

from collections.abc import Iterator, Mapping, Sequence
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from math import copysign
import pickle
from random import Random
from typing import Never, SupportsIndex, cast

import pytest

from wavequant.domain.market_structure import trend_confirmation, trend_lines, trend_levels, trend_publication
from wavequant.domain.market_structure.trend_evidence_reuse import (
    _CompletionProjectionCache, _completion_cache, _completion_projection_scope,
)
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.market_structure.trend_judgement import (
    TrendJudgement, _TrendJudgementState, _frozen_point, _frozen_point_scope, mutable_points,
)
from wavequant.domain.models.model import Bar


VALUES = (20, 30, 10, 20, 14, 26, 18, 35, 28, 45, 38, 42, 32, 36, 24, 29, 26, 50)


@pytest.mark.parametrize('state', ('seed', 'developing', 'display'))
def test_geometry_cache_keeps_unknown_ignored_point_copy_failures(state: str) -> None:
    class Audit:
        calls = 0

        def __deepcopy__(self, memo: dict[int, object]) -> Never:
            self.calls += 1
            raise RuntimeError('original unknown audit copy')

    cache = trend_lines._TrendJudgementCache()
    source = _source()
    cache.judge(source, path='path', trend_level=1)
    audit = Audit()
    ignored: dict[str, object] = dict(state=state, audit=audit, trend_level=0)
    if state == 'display':
        ignored['display_only'] = True
    source.append(ignored)
    for judge in (lambda: trend_lines.judge_trend(source, trend_level=1),
                  lambda: cache.judge(source, path='path', trend_level=1)):
        with pytest.raises(RuntimeError, match='original unknown audit copy'):
            judge()
    assert audit.calls == 2


def test_geometry_cache_does_not_add_unknown_mapping_or_state_hooks() -> None:
    events: list[str] = []

    class State:
        def __deepcopy__(self, memo: dict[int, object]) -> 'State':
            events.append('copy')
            return self

        def __eq__(self, other: object) -> bool:
            events.append('equal')
            return other == 'seed'

    class Point(dict[str, object]):
        def get(self, key: str, default: object = None) -> object:
            events.append('get')
            return super().get(key, default)

        def __iter__(self) -> Iterator[str]:
            events.append('iterate')
            return super().__iter__()

    source = _source()
    cache = trend_lines._TrendJudgementCache()
    cache.judge(source, path='path', trend_level=1)
    source.append(Point(state=State(), trend_level=0))
    trend_lines.judge_trend(source, trend_level=1)
    cold = list(events)
    events.clear()
    cache.judge(source, path='path', trend_level=1)
    assert events == cold


@pytest.mark.parametrize('nonfinite', (float('nan'), float('inf'), -float('inf')))
def test_geometry_cache_preserves_current_nonfinite_audit_leaf_identity(nonfinite: float) -> None:
    source = _source()
    for point in source:
        point['audit'] = dict(metadata=[nonfinite])
    cache = trend_lines._TrendJudgementCache()
    cache.judge(source, path='path', trend_level=1)
    replacement = float(str(nonfinite))
    assert replacement is not nonfinite
    for point in source:
        point['audit'] = dict(metadata=[replacement])
    observed = cache.judge(source, path='path', trend_level=1)
    expected = trend_lines.judge_trend(source, trend_level=1)
    assert len(observed.points) == len(expected.points) > 0
    for judged_point in observed.points:
        audit = cast(Mapping[str, object], judged_point['audit'])
        assert cast(Sequence[object], audit['metadata'])[0] is replacement


def _source() -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H', value=price,
                 time=str(index), available_at=index + 1, state='confirmed', trend_level=0,
                 label=f'source-{index}', audit=dict(tags=['original'])) for index, price in enumerate(VALUES)]


@pytest.mark.parametrize(('field', 'position', 'value'), (
    ('index', 1, True), ('ordinal', 1, False), ('available_at', 1, 2.0),
    ('index', 1, 1.0), ('trend_level', 0, False),
))
def test_geometry_cache_never_hides_a_same_value_invalid_type(
    field: str, position: int, value: object,
) -> None:
    source = _source()
    cache = trend_lines._TrendJudgementCache()
    trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    source[position][field] = value
    with pytest.raises(ValueError):
        trend_lines.prepare_trend_lines(source)
    with pytest.raises(ValueError):
        trend_lines.prepare_trend_lines(source, _judgement_cache=cache)


def test_geometry_cache_preserves_valid_price_type_and_source_position_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    cache = trend_lines._TrendJudgementCache()
    calls = 0
    original = trend_lines.judge_trend

    def counted(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                asof: int | str | None = None, _append_state: _TrendJudgementState | None = None,
                _certified_standard: bool = False) -> TrendJudgement:
        nonlocal calls
        calls += 1
        return original(points, trend_level=trend_level, asof=asof, _append_state=_append_state,
                        _certified_standard=_certified_standard)

    monkeypatch.setattr(trend_lines, 'judge_trend', counted)
    first = trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    assert trend_lines.prepare_trend_lines(deepcopy(source), _judgement_cache=cache) == first
    assert calls == 1
    source[2]['value'] = 10.0
    changed = trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    assert calls == 2
    cold = trend_lines.prepare_trend_lines(source)
    assert changed == cold
    changed_source = cast(list[dict[str, object]], cast(list[dict[str, object]], changed['source_strokes'])[0]['points'])
    assert type(changed_source[2]['value']) is float
    source.insert(0, dict(source[0], state='seed'))
    assert trend_lines.prepare_trend_lines(source, _judgement_cache=cache) == trend_lines.prepare_trend_lines(source)


def test_developing_tail_reuses_only_geometry_and_each_export_owns_nested_values(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    source.append(dict(source[-1], index=len(source), state='developing'))
    cache = trend_lines._TrendJudgementCache()
    original = trend_lines.judge_trend
    calls = 0

    def counted(points: Sequence[Mapping[str, object]], *, trend_level: int = 1,
                asof: int | str | None = None, _append_state: _TrendJudgementState | None = None,
                _certified_standard: bool = False) -> TrendJudgement:
        nonlocal calls
        calls += 1
        return original(points, trend_level=trend_level, asof=asof, _append_state=_append_state,
                        _certified_standard=_certified_standard)

    monkeypatch.setattr(trend_lines, 'judge_trend', counted)
    first = trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    source[-1]['value'] = 51
    second = trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    assert calls == 1
    assert second == trend_lines.prepare_trend_lines(source)
    first_points = cast(list[dict[str, object]], cast(list[dict[str, object]], first['strokes'])[0]['points'])
    second_points = cast(list[dict[str, object]], cast(list[dict[str, object]], second['strokes'])[0]['points'])
    cast(list[str], cast(dict[str, object], first_points[0]['audit'])['tags']).append('changed')
    assert cast(dict[str, object], second_points[0]['audit'])['tags'] == ['original']
    assert cast(dict[str, object], first_points[1]['audit'])['tags'] == ['original']
    third = trend_lines.prepare_trend_lines(source, _judgement_cache=cache)
    assert third == trend_lines.prepare_trend_lines(source)


def test_public_mutable_points_never_shares_a_repeated_nested_audit() -> None:
    audit: dict[str, object] = dict(tags=['original'], keys={(1, 2)})
    points: tuple[Mapping[str, object], ...] = (dict(audit=audit), dict(audit=audit))
    first = mutable_points(points)
    second = mutable_points(points)
    cast(list[str], cast(dict[str, object], first[0]['audit'])['tags']).append('changed')
    cast(set[tuple[int, int]], cast(dict[str, object], first[0]['audit'])['keys']).add((3, 4))
    assert first[1] == second[0] == second[1] == dict(audit=audit)


def test_private_qualification_transfer_borrows_only_normalized_finite_evidence() -> None:
    source = _source()
    copied = trend_lines._qualification_candidates(source, False)
    owned = trend_lines._qualification_candidates(source, True)
    assert copied == owned == source
    assert owned[0] is not source[0] and owned[0]['audit'] is source[0]['audit']
    assert copied[0]['audit'] is not source[0]['audit']
    owned[0]['label'] = 'private-top-level'
    assert source[0]['label'] == 'source-0'


@pytest.mark.parametrize('audit', ((1, 2), {(1, 2)}, {1: 'numeric-key'}, float('inf')))
def test_private_qualification_transfer_preserves_original_container_normalization(audit: object) -> None:
    source = [dict(audit=audit)]
    observed = trend_lines._qualification_candidates(source, True)
    expected = mutable_points(source)
    assert observed == expected
    assert type(observed[0]['audit']) is type(expected[0]['audit'])
    if isinstance(audit, (tuple, set, dict)):
        assert observed[0]['audit'] is not audit


def test_private_qualification_transfer_keeps_unknown_copy_hooks() -> None:
    events: list[str] = []

    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> 'Audit':
            events.append('copy')
            return Audit()

        def __reduce__(self) -> Never:
            raise AssertionError('unknown serialization hook must not run')

    source = [dict(audit=Audit())]
    expected = mutable_points(source)
    cold_events = list(events)
    events.clear()
    observed = trend_lines._qualification_candidates(source, True)
    assert events == cold_events == ['copy']
    assert type(observed[0]['audit']) is type(expected[0]['audit'])
    assert observed[0]['audit'] is not source[0]['audit']


@pytest.mark.parametrize('n_enabled', (False, True))
def test_private_qualification_transfer_retains_full_licenses_and_n_copy_boundary(
    n_enabled: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', value,
                value + 1, value - 1, value, 100) for index, value in enumerate((*VALUES, 45))]
    judgement = trend_lines.judge_trend(source)
    candidates = mutable_points(judgement.candidates)
    original = deepcopy(candidates)
    owned_modes: list[bool] = []
    prepare = trend_lines._qualification_candidates

    def record(points: Sequence[Mapping[str, object]], owned: bool) -> list[dict[str, object]]:
        owned_modes.append(owned)
        return prepare(points, owned)

    monkeypatch.setattr(trend_lines, '_qualification_candidates', record)
    cold = trend_lines.qualify_trend_structure(candidates, source, bars, trend_level=1,
        n_target_trend_confirmation_enabled=n_enabled)
    transferred = trend_lines.qualify_trend_structure(candidates, source, bars, trend_level=1,
        n_target_trend_confirmation_enabled=n_enabled, _owned_candidates=True)
    assert transferred == cold
    assert candidates == original
    assert owned_modes == [False, not n_enabled]
    if cold:
        cast(list[str], cast(dict[str, object], cold[0]['audit'])['tags']).append('public-copy')
        assert candidates == original


@pytest.mark.parametrize('cached', (False, True))
def test_private_preparation_transfer_preserves_all_recursive_fields_and_public_source_isolation(cached: bool) -> None:
    source = _source()
    for level in (1, 2, 3):
        private = deepcopy(source)
        original = deepcopy(private)
        cold = trend_lines.prepare_trend_lines(source, trend_level=level)
        cache = trend_lines._TrendJudgementCache() if cached else None
        with _frozen_point_scope():
            owned = trend_lines.prepare_trend_lines(private, trend_level=level,
                                                    _judgement_cache=cache, _owned_source=True)
        assert owned == cold
        assert private == original
        if level == 1:
            cold_source = cast(list[dict[str, object]], cast(list[dict[str, object]], cold['source_strokes'])[0]['points'])
            owned_source = cast(list[dict[str, object]], cast(list[dict[str, object]], owned['source_strokes'])[0]['points'])
            assert owned_source[0]['audit'] is private[0]['audit']
            assert cold_source[0]['audit'] is not source[0]['audit']
            cast(list[str], cast(dict[str, object], cold_source[0]['audit'])['tags']).append('public-change')
            assert source == original
        source = trend_lines.high_low_points_from_strokes(owned['strokes'])


@pytest.mark.parametrize('enabled', (False, True))
def test_private_permission_transfer_preserves_complete_fields_n_and_default_candidate_isolation(enabled: bool) -> None:
    source = _source()
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', value,
                value + 1, value - 1, value, 100) for index, value in enumerate((*VALUES, 45))]
    prepared = trend_lines.prepare_trend_lines(source)
    original = deepcopy(prepared)
    with _frozen_point_scope():
        cold = trend_lines.attach_trend_permissions(prepared, bars, n_target_trend_confirmation_enabled=enabled)
        owned = trend_lines.attach_trend_permissions(prepared, bars, n_target_trend_confirmation_enabled=enabled,
                                                     _owned_candidates=True)
    assert owned == cold
    assert prepared == original
    qualified = cast(list[dict[str, object]], cold['qualified_strokes'])
    assert qualified
    cold_points = cast(list[dict[str, object]], qualified[0]['points'])
    cast(list[str], cast(dict[str, object], cold_points[0]['audit'])['tags']).append('public-change')
    assert prepared == original


@pytest.mark.parametrize('boundary', ('prepare', 'attach'))
def test_private_stage_transfers_keep_unknown_copy_hooks_at_each_boundary(boundary: str) -> None:
    events: list[str] = []

    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> 'Audit':
            events.append('copy')
            return self

        def __reduce__(self) -> Never:
            raise AssertionError('unknown serialization hook')

    source = _source()
    if boundary == 'prepare':
        source[0]['audit'] = Audit()
        with _frozen_point_scope():
            cold = trend_lines.prepare_trend_lines(source)
            expected = list(events)
            events.clear()
            owned = trend_lines.prepare_trend_lines(source, _owned_source=True)
    else:
        bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', value,
                    value + 1, value - 1, value, 100) for index, value in enumerate((*VALUES, 45))]
        prepared = trend_lines.prepare_trend_lines(source)
        candidates = cast(list[dict[str, object]], cast(list[dict[str, object]], prepared['candidate_strokes'])[0]['points'])
        assert candidates
        candidates[0]['audit'] = Audit()
        with _frozen_point_scope():
            cold = trend_lines.attach_trend_permissions(prepared, bars)
            expected = list(events)
            events.clear()
            owned = trend_lines.attach_trend_permissions(prepared, bars, _owned_candidates=True)
    assert events == expected and events
    assert owned == cold


@pytest.mark.parametrize('boundary', ('prepare', 'attach'))
def test_private_stage_transfers_keep_tuple_to_list_normalization(boundary: str) -> None:
    source = _source()
    if boundary == 'prepare':
        source[0]['audit'] = ({'original': (1, True, 1.0)},)
        cold = trend_lines.prepare_trend_lines(source)
        owned = trend_lines.prepare_trend_lines(source, _owned_source=True)
        source_points = cast(list[dict[str, object]], cast(list[dict[str, object]], owned['source_strokes'])[0]['points'])
        assert type(source_points[0]['audit']) is list
    else:
        bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', value,
                    value + 1, value - 1, value, 100) for index, value in enumerate((*VALUES, 45))]
        prepared = trend_lines.prepare_trend_lines(source)
        candidates = cast(list[dict[str, object]], cast(list[dict[str, object]], prepared['candidate_strokes'])[0]['points'])
        for point in candidates:
            point['audit'] = ({'original': (1, True, 1.0)},)
        cold = trend_lines.attach_trend_permissions(prepared, bars)
        owned = trend_lines.attach_trend_permissions(prepared, bars, _owned_candidates=True)
        qualified = cast(list[dict[str, object]], owned['qualified_strokes'])
        assert qualified
        assert type(cast(list[dict[str, object]], qualified[0]['points'])[0]['audit']) is list
    assert owned == cold


def test_prefix_sessions_parse_dates_once_without_exposing_a_future_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index // 2), 'sz.test', 10, 11, 9, 10, 100)
            for index in range(12)]
    source = [dict(index=0, ordinal=0, kind='L', value=9, time='2024-01-01', available_at='2024-01-01')]
    original = trend_confirmation.session_date
    calls = 0

    def counted(bar: Bar) -> str:
        nonlocal calls
        calls += 1
        return original(bar)

    monkeypatch.setattr(trend_confirmation, 'session_date', counted)
    full = trend_confirmation.prepare_confirmation_context(source, bars, 11)
    for cutoff in range(12):
        projected = trend_confirmation.prepare_confirmation_context(source, bars, cutoff, parsed_context=full)
        assert projected.session_indices == {bar.timestamp.date().isoformat(): index
                                              for index, bar in enumerate(bars[:cutoff + 1])}
        assert projected.bars == tuple(bars[:cutoff + 1])
    assert calls == len(bars)


def test_closed_permissions_cache_reuses_only_its_exact_original_market(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    prices = (*VALUES, 51, 52)
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=index), 'sz.test', price, price, price, price, 100)
            for index, price in enumerate(prices)]
    for point in source:
        point['source_path_end_index'] = len(bars) - 1
    prepared = trend_lines.prepare_trend_lines(source)
    cache = trend_lines._TrendPermissionCache(bars)
    original = trend_lines.qualify_trend_structure
    calls = 0

    def counted(candidates: Sequence[Mapping[str, object]], points: Sequence[Mapping[str, object]],
                market: Sequence[Bar], *, trend_level: int,
                qualified_source: Sequence[Mapping[str, object]] | None = None,
                n_target_trend_confirmation_enabled: bool = False,
                    n_target_sink: list[dict[str, object]] | None = None,
                    _market_index: _MarketPrefixIndex | None = None) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        return original(candidates, points, market, trend_level=trend_level, qualified_source=qualified_source,
                        n_target_trend_confirmation_enabled=n_target_trend_confirmation_enabled,
                            n_target_sink=n_target_sink, _market_index=_market_index)

    monkeypatch.setattr(trend_lines, 'qualify_trend_structure', counted)
    first = trend_lines.attach_trend_permissions(prepared, bars[:-1], _permission_cache=cache)
    assert trend_lines.attach_trend_permissions(prepared, bars, _permission_cache=cache) == first
    assert calls == 1
    copied_bars = [deepcopy(bar) for bar in bars]
    assert trend_lines.attach_trend_permissions(prepared, copied_bars, _permission_cache=cache) == first
    assert calls == 2
    # 修改本次许可输出不能污染下一次 cache hit。
    qualified = cast(list[dict[str, object]], first['qualified_strokes'])
    assert qualified
    points = cast(list[dict[str, object]], qualified[0]['points'])
    points[0]['value'] = 1000
    assert trend_lines.attach_trend_permissions(prepared, bars, _permission_cache=cache) == (
        trend_lines.attach_trend_permissions(prepared, bars))


@pytest.mark.parametrize('seeds', (False, True))
@pytest.mark.parametrize('invalid_level', (True, 1.0, 0, 4))
def test_empty_geometry_cache_preserves_asof_and_level_rejections(seeds: bool, invalid_level: object) -> None:
    source = [dict(point, state='seed') for point in _source()] if seeds else []
    cache = trend_lines._TrendJudgementCache()
    cache.judge(source, path='empty', trend_level=1, asof=0)
    with pytest.raises(ValueError):
        cache.judge(source, path='empty', trend_level=1, asof=-1)
    with pytest.raises(ValueError):
        cache.judge(source, path='empty', trend_level=cast(int, invalid_level), asof=0)


def test_geometry_signature_preserves_types_and_never_calls_unknown_pickle_hooks() -> None:
    calls: list[str] = []

    class Trap:
        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            calls.append('reduce_ex')
            raise AssertionError('unknown reduction must not run')

    class StringTrap(str):
        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            calls.append('string_reduce_ex')
            raise AssertionError('unknown subclass reduction must not run')

    assert trend_lines._geometry_signature({'audit': Trap()}) is None
    assert trend_lines._geometry_signature({'audit': StringTrap('same')}) is None
    assert calls == []
    assert len({trend_lines._geometry_signature(value) for value in (True, 1, 1.0)}) == 3
    assert trend_lines._geometry_signature((1, 2)) != trend_lines._geometry_signature([1, 2])


def test_json_frozen_point_exports_preserve_scalar_types_order_and_independent_containers() -> None:
    audit: dict[str, object] = dict(tags=['original'])
    data: dict[str, object] = dict(label='L', integer=1, decimal=1.0, negative=-0.0, enabled=True,
                                  unicode='\ud800', audit=audit, sequence=(1, {'tag': 'kept'}))
    nodes = (_frozen_point(data), _frozen_point(data))
    assert all(node.json_template is not None for node in nodes)
    first, second = mutable_points(nodes), mutable_points(nodes)
    assert list(first[0]) == list(data)
    assert type(first[0]['integer']) is int and type(first[0]['decimal']) is float
    assert type(first[0]['enabled']) is bool and copysign(1, cast(float, first[0]['negative'])) == -1
    assert first[0]['unicode'] == '\ud800'
    assert isinstance(first[0]['sequence'], list)
    cast(list[str], cast(dict[str, object], first[0]['audit'])['tags']).append('changed')
    assert first[1] == second[0] == second[1] == mutable_points((nodes[0],))[0]
    assert cast(dict[str, object], data['audit'])['tags'] == ['original']


def test_non_json_audit_and_large_integers_keep_the_original_copy_path() -> None:
    class Audit:
        def __init__(self, tags: list[str]) -> None:
            self.tags = tags

        def __deepcopy__(self, memo: dict[int, object]) -> 'Audit':
            return Audit(self.tags.copy())

    large = 10 ** 5000
    original = dict(audit=Audit(['original']), keyed={1: 'integer key'}, large=large,
                    raw=b'bytes', keys={(1, 2)})
    node = _frozen_point(original)
    assert node.json_template is None
    first, second = mutable_points((node,)), mutable_points((node,))
    cast(Audit, first[0]['audit']).tags.append('changed')
    assert cast(Audit, second[0]['audit']).tags == ['original']
    assert first[0]['keyed'] == {1: 'integer key'}
    assert first[0]['large'] == large and first[0]['keys'] == {(1, 2)}
    only_large = _frozen_point(dict(large=large))
    assert only_large.json_template is None and mutable_points((only_large,))[0]['large'] == large


def test_closed_prepared_references_authenticate_owner_and_export_each_row_independently() -> None:
    source = _source()
    for point in source:
        point['source_path_end_index'] = 20
    cache = trend_lines._ClosedPreparedPaths()
    prepared = trend_lines.prepare_trend_lines(source, _closed_paths=cache)
    encoded = cache.encode(prepared)
    wire = pickle.dumps(encoded, protocol=5)
    first, second = cache.decode(pickle.loads(wire)), cache.decode(pickle.loads(wire))
    assert first == second == trend_lines.prepare_trend_lines(source)
    first_source = cast(list[dict[str, object]], cast(list[dict[str, object]], first['source_strokes'])[0]['points'])
    cast(list[str], cast(dict[str, object], first_source[0]['audit'])['tags']).append('changed')
    assert cache.decode(pickle.loads(wire)) == second
    with pytest.raises(ValueError, match='stage'):
        trend_lines._ClosedPreparedPaths().decode(pickle.loads(wire))
    count = len(cache._objects)
    source[2]['value'] = 10.0
    revised = trend_lines.prepare_trend_lines(source, _closed_paths=cache)
    assert cache.decode(pickle.loads(pickle.dumps(cache.encode(revised)))) == trend_lines.prepare_trend_lines(source)
    assert len(cache._objects) == count
    assert cache.decode(pickle.loads(wire)) == second


def test_closed_prepared_budget_falls_back_to_independent_stage_rows() -> None:
    source = _source()
    for point in source:
        point['source_path_end_index'] = 20
    cache = trend_lines._ClosedPreparedPaths(memory_bytes=1)
    prepared = trend_lines.prepare_trend_lines(source, _closed_paths=cache)
    decoded = cache.decode(pickle.loads(pickle.dumps(cache.encode(prepared))))
    assert decoded == trend_lines.prepare_trend_lines(source)
    assert cache._objects == [] and cache._memory_used == 0


def test_closed_prepared_templates_do_not_reuse_another_symbol() -> None:
    source = _source()
    for point in source:
        point['source_path_end_index'] = 20
    cache = trend_lines._ClosedPreparedPaths()
    first = trend_lines.prepare_trend_lines(source, symbol='FIRST', _closed_paths=cache)
    second = trend_lines.prepare_trend_lines(source, symbol='SECOND', _closed_paths=cache)
    assert first == trend_lines.prepare_trend_lines(source, symbol='FIRST')
    assert second == trend_lines.prepare_trend_lines(source, symbol='SECOND')
    assert len(cache._paths) == 2


@pytest.mark.parametrize('nonfinite', (float('nan'), float('inf'), -float('inf')))
def test_closed_prepared_nonfinite_audit_uses_current_cold_evidence(nonfinite: float) -> None:
    source = _source()
    for point in source:
        point.update(source_path_end_index=20, audit=dict(metadata=[nonfinite]))
    cache = trend_lines._ClosedPreparedPaths()
    trend_lines.prepare_trend_lines(source, _closed_paths=cache)
    assert cache._paths == {} and cache._objects == []
    current = float(str(nonfinite))
    for point in source:
        point['audit'] = dict(metadata=[current])
    prepared = trend_lines.prepare_trend_lines(source, _closed_paths=cache)
    source_points = cast(list[dict[str, object]], cast(list[dict[str, object]], prepared['source_strokes'])[0]['points'])
    for prepared_point in source_points:
        audit = cast(Mapping[str, object], prepared_point['audit'])
        assert cast(Sequence[object], audit['metadata'])[0] is current
    assert cache._paths == {} and cache._objects == []


@pytest.mark.parametrize('nonfinite', (float('nan'), float('inf'), -float('inf')))
def test_permission_cache_does_not_snapshot_nonfinite_source_or_payload(nonfinite: float) -> None:
    bars = [Bar(datetime(2024, 1, 1), 'test', 10, 11, 9, 10, 100)]
    cache = trend_lines._TrendPermissionCache(bars)
    prefix = cache.prefix(bars)
    assert prefix is not None
    source = _source()
    payload: tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]] = (
        [dict(audit=dict(metadata=[nonfinite]))], [], [], [])
    cache.put((1, 'path'), source, [], None, prefix, 0, False, payload)
    assert cache.get((1, 'path'), source, [], None, prefix, 0, False) is None
    source[0]['audit'] = dict(metadata=[nonfinite])
    cache.put((1, 'path'), source, [], None, prefix, 0, False, ([], [], [], []))
    assert cache.get((1, 'path'), source, [], None, prefix, 0, False) is None


def test_finite_certificates_reuse_only_known_standard_evidence_and_remain_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = trend_lines._FiniteEvidenceCertificates()
    original = trend_lines.json.dumps
    calls = 0

    def counted(value: object, *, allow_nan: bool = True) -> str:
        nonlocal calls
        calls += 1
        return original(value, allow_nan=allow_nan)

    monkeypatch.setattr(trend_lines.json, 'dumps', counted)
    evidence = dict(ordered=[1, True, 1.0, -0.0], keyed={2: ('tuple', '\ud800')})
    assert cache.certify(evidence) and cache.certify(deepcopy(evidence))
    assert calls == 1
    cache._bytes = 8 * 1024 * 1024
    before = set(cache._points)
    assert cache.certify(dict(new='version')) and cache.certify(dict(new='version'))
    assert calls == 3 and cache._points == before

    class Unknown:
        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            raise AssertionError('unknown hooks must not run')

    assert cache.certify(dict(audit=Unknown())) is False
    assert calls == 3
    assert cache.certify(dict(value=10 ** 5000)) is False
    assert calls == 4
    cycle: dict[str, object] = {}
    cycle['self'] = cycle
    assert cache.certify(cycle) is False
    assert calls == 5


def _completed_point() -> dict[str, object]:
    source = _source()
    for index, point in enumerate(source):
        point.update(time=(datetime(2024, 1, 1) + timedelta(days=index)).date().isoformat(),
                     available_at=(datetime(2024, 1, 2) + timedelta(days=index)).date().isoformat())
    return next(point for point in mutable_points(trend_lines.judge_trend(source).points)
                if 'wave_completed_at' in point)


def test_completion_projection_reuses_validation_but_preserves_current_scalar_aliases() -> None:
    point = _completed_point()
    cache = _CompletionProjectionCache()
    calls = 0

    def validate(raw: Mapping[str, object]) -> dict[str, object] | None:
        nonlocal calls
        calls += 1
        return trend_levels._validate_completed_source_point(raw)

    expected = validate(point)
    first = cache.project(point, validate)
    current = cast(dict[str, object], pickle.loads(pickle.dumps(point)))
    second = cache.project(current, validate)
    assert calls == 2 and first == second == expected
    assert first is not second and second is not current
    assert pickle.dumps(second) == pickle.dumps(trend_levels._validate_completed_source_point(current))
    assert second is not None
    assert second['source_geometry_available_at'] is current['available_at']
    second['extra'] = 'changed'
    assert cache.project(current, validate) == expected
    assert calls == 2
    for field in ('available_at', 'wave_completed_at', 'wave_completion_evidence'):
        revised = deepcopy(point)
        revised[field] = False if field != 'wave_completion_evidence' else {}
        with pytest.raises(ValueError):
            cache.project(revised, validate)
    assert calls == 5


def test_completion_projection_none_latest_version_and_budget_are_bounded() -> None:
    cache = _CompletionProjectionCache(memory_bytes=1)
    point = _completed_point()
    assert cache.project(point, trend_levels._validate_completed_source_point) is not None
    assert cache._points == {} and cache._memory_used == 0
    cache = _CompletionProjectionCache()
    opened = dict(point)
    opened.pop('wave_completed_at')
    opened.pop('wave_completion_evidence')
    assert cache.project(opened, trend_levels._validate_completed_source_point) is None
    assert cache.project(opened, trend_levels._validate_completed_source_point) is None
    assert cache.project(point, trend_levels._validate_completed_source_point) is not None
    assert len(cache._points) == 1 and cache._memory_used <= cache._memory_bytes


def test_completion_scope_isolates_nested_threads_and_restores_after_errors() -> None:
    assert _completion_cache.get() is None
    with _completion_projection_scope() as outer:
        assert _completion_cache.get() is outer
        with _completion_projection_scope() as inner:
            assert inner is not outer and _completion_cache.get() is inner
        assert _completion_cache.get() is outer
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(_completion_cache.get).result() is None
        with pytest.raises(ValueError):
            with _completion_projection_scope():
                raise ValueError('test')
        assert _completion_cache.get() is outer
    assert _completion_cache.get() is None


def test_completion_unknown_metadata_falls_back_without_serialization_hooks() -> None:
    class Trap:
        def __reduce_ex__(self, protocol: SupportsIndex) -> Never:
            raise AssertionError('unknown serialization hook')

    point = _completed_point()
    point['audit'] = Trap()
    cache = _CompletionProjectionCache()
    assert cache.project(point, trend_levels._validate_completed_source_point) is not None
    assert cache._points == {}
    point['index'] = []
    assert cache.project(point, lambda raw: dict(raw)) == point
    assert cache._points == {}


def test_market_range_queries_preserve_strict_boundaries_and_never_see_later_bars() -> None:
    closes = (10, 10, 11, 10, 9, 10, 12, 8)
    bars = tuple(Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST', price,
                     price + 1, price - 1, price, 100) for index, price in enumerate(closes))
    owner = _MarketRangeIndex(bars)
    for stop in range(len(bars) + 1):
        prefix = _MarketPrefixIndex(owner, bars[:stop])
        for start in range(stop + 1):
            for price in (8.0, 9.0, 10.0, 11.0, 12.0):
                for up in (False, True):
                    expected = next((index for index in range(start, stop)
                        if (bars[index].high > price if up else bars[index].low < price)), None)
                    assert prefix.first_extreme(start, stop, price, high=up) == expected
                    crossing = next((index for index in range(max(1, start), stop)
                        if (bars[index - 1].close <= price < bars[index].close if up else
                            bars[index - 1].close >= price > bars[index].close)), None)
                    assert prefix.first_close_turn(start, stop, price, up=up) == crossing
        with pytest.raises(ValueError):
            prefix.first_extreme(0, stop + 1, 10, high=True)
    with pytest.raises(ValueError, match='original Bar'):
        _MarketPrefixIndex(owner, tuple(deepcopy(bar) for bar in bars))


def test_market_index_does_not_parse_an_unknown_future_timestamp() -> None:
    class TrapDate(datetime):
        def date(self) -> Never:
            raise AssertionError('future timestamp hook')

    bars = (Bar(datetime(2024, 1, 1), 'TEST', 10, 10, 10, 10, 100),
            Bar(TrapDate(2024, 1, 2), 'TEST', 10, 10, 10, 10, 100))
    assert _MarketRangeIndex.build(bars) is None
    cache = trend_lines._TrendPermissionCache(bars)
    assert cache._market_index is None


def test_origin_lookup_preserves_first_equal_numeric_reference_and_knowledge_filter() -> None:
    points: list[dict[str, object]] = [
        dict(index=1, kind='L', value=10, available_at=4),
        dict(index=1.0, ordinal=False, kind='L', value=10.0, available_at=6),
        dict(index=1, ordinal=0, kind='H', value=10, available_at=6),
    ]
    lookup = trend_publication._OriginLookup(points)
    for reference in points:
        for kind in (None, 'L', 'H'):
            for known in (None, *points):
                expected = next((point for point in points if (kind is None or point['kind'] == kind)
                    and trend_publication._same_origin(dict(origin=reference), point)
                    and (known is None or trend_publication._moment(point) == trend_publication._moment(known))), None)
                assert lookup.find(reference, kind=kind, known_point=known) is expected


def test_origin_lookup_unknown_values_keep_the_original_comparison_path() -> None:
    point: dict[str, object] = dict(index=1, kind='H', value=[10], available_at=4)
    lookup = trend_publication._OriginLookup([point])
    assert lookup._index is None and lookup.find(dict(point)) is point
    missing = dict(index=1, value=10, available_at=4)
    lookup = trend_publication._OriginLookup([missing])
    with pytest.raises(KeyError):
        lookup.find(dict(missing), kind='H')


@pytest.mark.parametrize('seed', range(4))
@pytest.mark.parametrize('date_clock', (False, True))
@pytest.mark.parametrize('n_enabled', (False, True))
def test_indexed_direction_certificates_match_each_original_prefix(
    seed: int, date_clock: bool, n_enabled: bool,
) -> None:
    random = Random(seed)
    bars = tuple(Bar(datetime(2024, 1, 1) + timedelta(hours=12 * index), 'TEST', closing,
                     closing + random.randint(0, 5), closing - random.randint(0, 5), closing, 100)
                 for index, closing in enumerate(random.randint(80, 120) for _ in range(22)))
    owner = _MarketRangeIndex(bars)
    source: list[dict[str, object]] = []
    for index in range(1, 20, 2):
        kind = 'L' if len(source) % 2 == 0 else 'H'
        known = min(index + 1 + random.randint(0, 2), len(bars) - 1)
        source.append(dict(index=index, ordinal=0, time=bars[index].timestamp.date().isoformat(),
            kind=kind, value=bars[index].low if kind == 'L' else bars[index].high,
            available_at=bars[known].timestamp.date().isoformat() if date_clock else known,
            state='confirmed', label=f'{kind}{index}'))
    for stop in range(4, len(bars) + 1, 2):
        market = bars[:stop]
        prefix_index = _MarketPrefixIndex(owner, market)
        original = trend_confirmation.prepare_confirmation_context(source, market, stop - 1,
            n_target_trend_confirmation_enabled=n_enabled)
        indexed = trend_confirmation.prepare_confirmation_context(source, market, stop - 1,
            n_target_trend_confirmation_enabled=n_enabled, _market_index=prefix_index)
        assert original.references == indexed.references and original.ordered == indexed.ordered
        for position, origin in enumerate(source):
            for key in (None, *source[:position][-2:]):
                for qualify in (trend_confirmation.qualify_uptrend, trend_confirmation.qualify_downtrend):
                    assert qualify(source, origin, position, key, market, stop - 1,
                        n_target_trend_confirmation_enabled=n_enabled, context=original) == qualify(
                        source, origin, position, key, market, stop - 1,
                        n_target_trend_confirmation_enabled=n_enabled, context=indexed)
        with pytest.raises(ValueError, match='immutable Bar prefix'):
            trend_confirmation.prepare_confirmation_context(source, tuple(deepcopy(bar) for bar in market),
                stop - 1, _market_index=prefix_index)
