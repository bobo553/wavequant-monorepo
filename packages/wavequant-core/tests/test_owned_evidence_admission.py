"""证据准入只遍历新增图，拒绝与公开恢复仍保持完整语义。"""

from collections.abc import Iterator
from typing import Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.trend_point_version import PointVersion


def test_new_sequence_is_traversed_once_and_keeps_cross_field_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    proof = dict(values=[1, 2, 3])
    points = PointVersion([dict(index=index, proof=proof) for index in range(64)]).seal()
    original = PointVersion.__iter__
    traversals = 0

    def iterate(self: PointVersion) -> Iterator[dict[str, object]]:
        nonlocal traversals
        if self is points:
            traversals += 1
        return original(self)

    monkeypatch.setattr(PointVersion, '__iter__', iterate)
    with owned_evidence_scope() as owner:
        row = dict(points=points, same_points=points, proof=proof)
        owner.publish(row)
        assert traversals == 1
        assert owner.reference(row) is not None
        charged = owner.memory_used
        owner.publish(dict(previous=row))
        assert traversals == 1
        assert owner.memory_used > charged
        restored = cast(dict[str, object], owner.loads(owner.dumps(row)))
        left = cast(list[dict[str, object]], restored['points'])
        assert restored['points'] is restored['same_points']
        assert all(point['proof'] is restored['proof'] for point in left)
        cast(list[object], cast(dict[str, object], restored['proof'])['values']).append(99)
        fresh = cast(dict[str, object], owner.loads(owner.dumps(row)))
        assert cast(dict[str, object], fresh['proof'])['values'] == [1, 2, 3]
        assert proof['values'] == [1, 2, 3]


def test_over_budget_sequence_is_rejected_before_walking_its_history(monkeypatch: pytest.MonkeyPatch) -> None:
    points = PointVersion([dict(index=index, evidence='history') for index in range(1024)]).seal()
    original = PointVersion.__iter__
    traversals = 0

    def iterate(self: PointVersion) -> Iterator[dict[str, object]]:
        nonlocal traversals
        if self is points:
            traversals += 1
        return original(self)

    monkeypatch.setattr(PointVersion, '__iter__', iterate)
    with owned_evidence_scope(memory_bytes=4096) as owner:
        owner.publish(points)
        assert traversals == 0
        assert owner.reference(points) is None
        assert owner.reference(points[0]) is None
        assert owner.memory_used == 0


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), float('-inf'), {('tuple',): 1}])
def test_invalid_graph_never_partially_admits_earlier_valid_nodes(invalid: object) -> None:
    valid = dict(values=[1, 2, 3])
    row = dict(invalid=invalid, valid=valid)
    with owned_evidence_scope() as owner:
        original = dict(previous=[4, 5])
        owner.publish(original)
        saved = owner.dumps(original)
        charged = owner.memory_used
        owner.publish(row)
        assert owner.memory_used == charged
        assert owner.reference(row) is None
        assert owner.reference(valid) is None
        assert owner.reference(valid['values']) is None
        assert owner.loads(saved) == original
        assert not owner.finite(row)


def test_budget_refusal_does_not_consume_a_partially_valid_subgraph() -> None:
    oversized = dict(detail='x' * 30_000)
    valid = dict(values=[1, 2, 3])
    with owned_evidence_scope(memory_bytes=4096) as owner:
        owner.publish(dict(oversized=oversized, valid=valid))
        assert owner.memory_used == 0
        assert owner.reference(valid) is None
        assert owner.reference(oversized) is None
        owner.publish(valid)
        assert owner.reference(valid) is not None
        assert owner.loads(owner.dumps(valid)) == valid


def test_cycle_unknown_and_unsealed_inputs_are_rejected_without_hooks() -> None:
    class Unknown:
        def __reduce__(self) -> Never:
            raise AssertionError('admission must not run unknown serialization hooks')

    cycle: list[object] = []
    cycle.append(cycle)
    unsealed = PointVersion([dict(index=0)])
    with owned_evidence_scope() as owner:
        for value in (dict(unknown=Unknown()), dict(cycle=cycle), unsealed):
            owner.publish(value)
            assert owner.reference(value) is None
            assert owner.memory_used == 0
        unsealed.seal()
        owner.publish(unsealed)
        assert owner.reference(unsealed) is not None
        assert owner.loads(owner.dumps(unsealed)) == [dict(index=0)]
