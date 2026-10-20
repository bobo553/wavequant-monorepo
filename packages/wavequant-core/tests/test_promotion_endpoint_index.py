"""Candidate indexes must not retain old authorizations across versions."""

from wavequant.domain.market_structure.trend_cascade import _PromotionEndpoints
from wavequant.domain.market_structure.trend_point_version import PointVersion


def _point(index, endpoint=False):
    return dict(index=index, upgrade_evidence=dict(role='endpoint' if endpoint else 'origin'))


def test_same_sealed_version_avoids_full_candidate_iteration(monkeypatch):
    index = _PromotionEndpoints()
    source = PointVersion(_point(i, i in (7, 90)) for i in range(100)).seal()
    expected = [source[7], source[90]]
    assert list(index.points(source)) == expected

    def forbidden(self):
        raise AssertionError('same candidate version was scanned twice')

    monkeypatch.setattr(PointVersion, '__iter__', forbidden)
    assert list(index.points(source)) == expected


def test_revised_or_mutable_inputs_recompute_endpoint_selection():
    index = _PromotionEndpoints()
    source = PointVersion([_point(0, True), _point(1)]).seal()
    assert list(index.points(source)) == [source[0]]
    revised = PointVersion([_point(0), _point(1, True)], previous=source, start=0).seal()
    assert list(index.points(revised)) == [revised[1]]
    mutable = [_point(0, True)]
    assert list(index.points(mutable)) == mutable
    mutable[0]['upgrade_evidence']['role'] = 'origin'
    assert list(index.points(mutable)) == []
    assert list(index.points(source)) == [source[0]]


def test_budget_exhaustion_changes_only_reuse_not_results():
    index = _PromotionEndpoints(memory_bytes=1)
    source = PointVersion(_point(i, i % 2 == 0) for i in range(20)).seal()
    expected = [source[i] for i in range(0, 20, 2)]
    assert list(index.points(source)) == expected
    assert list(index.points(source)) == expected
    assert not index.entries
