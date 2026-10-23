"""单个证据拒绝也移交历史通道，不能等待剩余额度降到固定阈值。"""

from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import OwnedEvidence, owned_evidence_scope
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.prepared_point_history import PreparedPointHistory
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload
from wavequant.domain.market_structure.trend_point_version import PointVersion


def _prepared(points: PointVersion, *, other: PointVersion | None = None) -> dict[str, object]:
    stroke = dict(id='path', points=points, structural_legs=points)
    return dict(source_strokes=[stroke], candidate_strokes=[], structure_strokes=[],
                strokes=[dict(id='other', points=other)] if other is not None else [stroke],
                developing_strokes=[])


def test_publish_reports_budget_refusal_without_changing_its_return_contract() -> None:
    owner = OwnedEvidence(memory_bytes=16_384)
    try:
        oversized = dict(proof='x' * 24_000)
        assert owner.publish(oversized) is None
        assert owner.budget_refusals == 1
        assert owner.admission_remaining == 16_384 and owner.memory_used == 0
        assert owner.reference(oversized) is None
        assert owner.publish(dict(proof=float('nan'))) is None
        assert owner.budget_refusals == 1
        small = dict(value=1)
        owner.publish(small)
        assert owner.reference(small) is not None
        owner._limit = owner.memory_used
        assert owner.publish(small) is None
        assert owner.budget_refusals == 1
    finally:
        owner.close()


def test_point_history_reports_only_the_current_attempt_budget_refusal() -> None:
    owner = OwnedEvidence(memory_bytes=16_384)
    history = PreparedPointHistory(owner)
    try:
        oversized = PointVersion([dict(index=0, proof='x' * 24_000)]).seal()
        assert history.pack(('points', 'path'), oversized) is None
        assert history.budget_rejected and owner.admission_remaining >= 4096
        invalid = PointVersion([dict(index=0, proof=float('nan'))]).seal()
        assert history.pack(('points', 'path'), invalid) is None
        assert not history.budget_rejected
        assert history.pack(('points', 'path'), PointVersion([dict(index=0)]).seal()) is not None
        assert not history.budget_rejected
    finally:
        history.close()
        owner.close()


def test_refused_point_suffix_switches_once_and_preserves_old_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope(memory_bytes=16_384) as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        first = PointVersion([dict(index=0)]).seal()
        old_payload = _prepared_payload(paths.encode(_prepared(first)))
        assert old_payload is not None and not PreparedGraphHistory.is_payload(old_payload)
        revised = PointVersion([dict(index=1, proof='x' * 24_000)], previous=first, start=1).seal()
        original = _prepared(revised)
        payload = _prepared_payload(paths.encode(original))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert owner.admission_remaining >= 4096
        assert paths._point_history is not None and not paths._point_history._current

        def repeated_point_scan(*args: object, **kwargs: object) -> object:
            raise AssertionError('a refused suffix must not return to point history')

        monkeypatch.setattr(paths._point_history, 'pack', repeated_point_scan)
        following = PointVersion([dict(index=2)], previous=revised, start=2).seal()
        next_payload = _prepared_payload(paths.encode(_prepared(following)))
        assert next_payload is not None and PreparedGraphHistory.is_payload(next_payload)
        for stored, expected in ((old_payload, first), (payload, revised), (next_payload, following),
                                 (old_payload, first)):
            restored = paths.decode(paths.load_encoded(stored))
            stroke = cast(list[dict[str, object]], restored['source_strokes'])[0]
            assert stroke['points'] == list(expected)
            assert stroke['points'] is stroke['structural_legs']


def test_alias_budget_refusal_switches_before_disabling_point_packing() -> None:
    with owned_evidence_scope(memory_bytes=16_384) as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        shared = dict(index=0, proof='x' * 24_000)
        left, right = PointVersion([shared]).seal(), PointVersion([shared]).seal()
        payload = _prepared_payload(paths.encode(_prepared(left, other=right)))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert owner.admission_remaining == 16_384
        restored = paths.decode(paths.load_encoded(payload))
        left_points = cast(list[dict[str, object]], restored['source_strokes'])[0]['points']
        right_points = cast(list[dict[str, object]], restored['strokes'])[0]['points']
        assert left_points is not right_points
        assert cast(list[object], left_points)[0] is cast(list[object], right_points)[0]


def test_external_publication_can_force_transition_before_the_fixed_threshold() -> None:
    with owned_evidence_scope(memory_bytes=16_384) as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        first = PointVersion([dict(index=0)]).seal()
        old_payload = _prepared_payload(paths.encode(_prepared(first)))
        assert old_payload is not None and paths._graph_history is None
        external = dict(proof='outside' * 1000)
        owner.publish(external)
        reference = owner.reference(external)
        assert reference is not None and 4096 <= owner.admission_remaining < 12_000
        revised = PointVersion([dict(index=1, proof='y' * 12_000)], previous=first, start=1).seal()
        payload = _prepared_payload(paths.encode(_prepared(revised)))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert owner.admission_remaining >= 4096 and owner.reference(external) == reference
        restored = paths.decode(paths.load_encoded(old_payload))
        assert cast(list[dict[str, object]], restored['strokes'])[0]['points'] == list(first)


def test_point_index_budget_refusal_also_switches_with_available_evidence_budget() -> None:
    with owned_evidence_scope() as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        assert paths._point_history is not None
        paths._point_history._limit = 0
        points = PointVersion([dict(index=0)]).seal()
        payload = _prepared_payload(paths.encode(_prepared(points)))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert owner.budget_refusals == 0 and owner.admission_remaining >= 4096
        restored = paths.decode(paths.load_encoded(payload))
        assert cast(list[dict[str, object]], restored['strokes'])[0]['points'] == list(points)


def test_invalid_point_input_does_not_switch_history_channels() -> None:
    with owned_evidence_scope(memory_bytes=16_384) as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        points = PointVersion([dict(index=0, proof=float('nan'))]).seal()
        prepared = _prepared(points)
        result = paths.encode(prepared)
        assert paths._graph_history is None and owner.budget_refusals == 0
        assert result['source_strokes'] == prepared['source_strokes']
        assert paths._point_history is not None and not paths._point_history.budget_rejected


@pytest.mark.parametrize('bad', [object(), float('nan')])
def test_budget_short_circuit_does_not_certify_invalid_shared_points(bad: object) -> None:
    with owned_evidence_scope(memory_bytes=16_384) as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        shared = dict(index=0, bad=bad, proof='x' * 24_000)
        left, right = PointVersion([shared]).seal(), PointVersion([shared]).seal()
        original = _prepared(left, other=right)
        result = paths.encode(original)
        assert owner.budget_refusals == 1 and owner.admission_remaining == 16_384
        assert paths._graph_history is None
        assert result['source_strokes'] == original['source_strokes']
        assert result['strokes'] == original['strokes']
