"""长回测的共享证明预算、许可外壳和分块遍历不改变历史语义。"""

from collections.abc import Callable, Sequence
from copy import deepcopy
from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_judgement, trend_publication
from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.trend_frozen_state import FrozenPrefixList
from wavequant.domain.market_structure.trend_judgement import (
    _FormalKeyBreakState, _TrendJudgementState, judge_trend, mutable_points,
)
from wavequant.domain.market_structure.trend_lines import _TrendPermissionCache, attach_trend_permissions
from wavequant.domain.market_structure.trend_point_version import PointVersion
from wavequant.domain.models.model import Bar


def _measure(kind: str, level: int) -> Callable[[Sequence[object]], int]:
    if kind == 'ordinary':
        return _TrendJudgementState(level)._owned_value_bytes
    return _FormalKeyBreakState(level)._value_bytes


@pytest.mark.parametrize(('kind', 'level'), (
    ('ordinary', 1), ('ordinary', 2), ('ordinary', 3), ('formal', 2), ('formal', 3),
))
def test_shared_proof_budget_does_not_rescan_or_charge_the_request_pool(
    monkeypatch: pytest.MonkeyPatch, kind: str, level: int,
) -> None:
    shared = {'evidence': [float(index) for index in range(2000)], 'clock': (1, 2)}
    measure = _measure(kind, level)
    standalone_cost = measure([shared])
    with owned_evidence_scope() as owner:
        owner.publish(shared)
        assert owner.reference(shared) is not None
        charged = owner.memory_used
        original_size = trend_judgement.getsizeof
        forbidden_ids = {id(shared), id(shared['evidence']), id(shared['clock'])}

        def without_old_graph(value: object) -> int:
            assert id(value) not in forbidden_ids, 'shared proof was walked again'
            return original_size(value)

        with monkeypatch.context() as scoped:
            scoped.setattr(trend_judgement, 'getsizeof', without_old_graph)
            for index in range(100):
                assert measure([{'day': index, 'proof': shared}]) < standalone_cost
        assert owner.memory_used == charged
        # 另一请求没有该证明的所有权，必须按原规则完整计费。
        with owned_evidence_scope():
            assert measure([shared]) == standalone_cost
    assert measure([shared]) == standalone_cost


@pytest.mark.parametrize(('kind', 'level'), (('ordinary', 1), ('formal', 2)))
def test_refused_evidence_keeps_full_budget_and_unowned_children_are_charged(kind: str, level: int) -> None:
    measure = _measure(kind, level)
    small = {'clock': [1, 2]}
    large = {'audit': 'x' * 100_000}
    expected = measure([large])
    with owned_evidence_scope(memory_bytes=4096) as owner:
        owner.publish(small)
        owner.publish(large)
        assert owner.reference(small) is not None
        assert owner.reference(large) is None
        assert measure([large]) == expected
        assert measure([{'shared': small, 'private': large}]) >= expected
        assert owner.memory_used <= 4096


@pytest.mark.parametrize('level', (1, 2, 3))
def test_reused_state_rebuilds_its_budget_when_the_request_owner_changes(level: int) -> None:
    prices = (20., 30., 10., 20., 14., 26., 18., 35.)
    source = [dict(index=index, ordinal=0, kind='H' if index % 2 else 'L',
        value=value, time=str(index), available_at=index + 1, trend_level=level - 1,
        state='confirmed', audit={'proof': [index]}) for index, value in enumerate(prices)]
    state = _TrendJudgementState(level, _tail_reuse=True)
    for _ in range(2):
        previous_runs = state.cold_runs
        with owned_evidence_scope() as owner:
            for point in source:
                owner.publish(point)
            actual = judge_trend(source, trend_level=level, _append_state=state)
            expected = judge_trend(source, trend_level=level)
            assert mutable_points(actual.points) == mutable_points(expected.points)
            assert mutable_points(actual.candidates) == mutable_points(expected.candidates)
            assert state.cold_runs == previous_runs + 1
            assert state.memory_used >= state._owned_bytes()
    previous_runs = state.cold_runs
    actual = judge_trend(source, trend_level=level, _append_state=state)
    expected = judge_trend(source, trend_level=level)
    assert state.cold_runs == previous_runs + 1
    assert mutable_points(actual.points) == mutable_points(expected.points)
    assert mutable_points(actual.candidates) == mutable_points(expected.candidates)


@pytest.mark.parametrize('frozen', (0, 1, 31, 32, 33, 65, 97))
@pytest.mark.parametrize(('start', 'stop'), (
    (0, None), (1, 33), (31, 66), (-10, None), (-70, -2), (90, 20), (150, 200),
))
def test_block_iteration_keeps_slice_order_aliases_and_old_versions(
    frozen: int, start: int, stop: int | None,
) -> None:
    shared: dict[str, object] = {'shared': [1]}
    source = [shared if index % 7 == 0 else {'index': index} for index in range(97)]
    current = FrozenPrefixList(source)
    current.freeze(frozen)
    saved = current.copy()
    current[32] = {'revised': True}
    del current[66:]
    current.extend([{'new': 66}, {'new': 67}])
    expected = source[slice(start, stop)]
    actual = list(saved.iter_range(start, stop))
    assert actual == expected
    assert all(left is right for left, right in zip(actual, expected, strict=True))
    assert list(current) == [*source[:32], {'revised': True}, *source[33:66], {'new': 66}, {'new': 67}]


def _permission_input(level: int) -> tuple[tuple[Bar, ...], dict[str, object]]:
    prices = ((10., 8., 9.), (8., 5., 6.), (9., 6., 8.), (11., 7., 10.5), (12., 8., 11.))
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'COST',
        (high + low) / 2, high, low, close, 100.) for index, (high, low, close) in enumerate(prices))
    source = PointVersion(dict(index=index, ordinal=0, kind=kind, value=value,
        time=bars[index].timestamp.date().isoformat(), available_at=index,
        state='confirmed', label=kind, audit={'proof': list(range(128))})
        for index, kind, value in ((0, 'H', 10.), (1, 'L', 5.))).seal()
    field = 'source_turn_position' if level == 1 else f'source_level{level - 1}_position'
    candidates = PointVersion(dict(point, **{field: index}, confirmation_rule='structure')
        for index, point in enumerate(source)).seal()
    stroke: dict[str, object] = dict(id='cost-path', source_path='cost-path', points=candidates)
    return bars, dict(trend_level=level, source_level=level - 1,
        source_strokes=[dict(id='cost-path', points=source)], candidate_strokes=[stroke],
        strokes=[stroke], developing_strokes=[], _path_market_end_indices={'cost-path': None})


@pytest.mark.parametrize('level', (1, 2, 3))
@pytest.mark.parametrize('enabled', (False, True))
def test_full_owned_permissions_match_public_and_do_not_annotate_cached_points(
    monkeypatch: pytest.MonkeyPatch, level: int, enabled: bool,
) -> None:
    bars, prepared = _permission_input(level)
    cache = _TrendPermissionCache(bars)
    snapshots: list[tuple[dict[str, object], dict[str, object]]] = []
    for stop in (4, 5):
        market = bars[:stop]
        expected = attach_trend_permissions(prepared, market,
            n_target_trend_confirmation_enabled=enabled)
        with monkeypatch.context() as scoped:
            if not enabled:
                def forbidden_copy(value: object) -> object:
                    raise AssertionError('owned permission publication copied the full proof tree')
                scoped.setattr(trend_publication, 'deepcopy', forbidden_copy)
            actual = attach_trend_permissions(prepared, market,
                n_target_trend_confirmation_enabled=enabled,
                _permission_cache=cache, _owned_candidates=True)
        assert actual == expected
        strokes = cast(list[dict[str, object]], actual['qualified_strokes'])
        assert strokes
        points = cast(list[dict[str, object]], strokes[0]['points'])
        if not enabled:
            checkpoint = cache.publication(level, 'cost-path').checkpoint
            assert checkpoint is not None and checkpoint.result
            assert points[0] is not checkpoint.result[0]
            assert points[0]['audit'] is checkpoint.result[0]['audit']
            original_label = checkpoint.result[0]['label']
            label = points[0]['label']
            points[0]['label'] = 'consumer annotation'
            assert checkpoint.result[0]['label'] == original_label
            points[0]['label'] = label
        snapshots.append((actual, deepcopy(actual)))
        # 普通公开调用仍交付独立证明，修改它不能污染下一次内部发布。
        exported = attach_trend_permissions(prepared, market,
            n_target_trend_confirmation_enabled=enabled, _permission_cache=cache)
        public_points = cast(list[dict[str, object]],
            cast(list[dict[str, object]], exported['qualified_strokes'])[0]['points'])
        cast(list[object], cast(dict[str, object], public_points[0]['audit'])['proof']).clear()
        assert points[0]['audit'] == {'proof': list(range(128))}
    assert all(actual == snapshot for actual, snapshot in snapshots)
