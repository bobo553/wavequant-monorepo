"""有限性收据只复用同请求不可变来源的实际旧子图。"""

from copy import deepcopy
from typing import cast

import pytest

from wavequant.domain.market_structure.finite_source_receipts import FiniteSourceReceipts
from wavequant.domain.market_structure.owned_evidence import (
    CertifiedSource, OwnedEvidence, owned_evidence_scope, owned_signature,
)
from wavequant.domain.market_structure.trend_lines import _OwnedSourceInputs
from wavequant.domain.market_structure.trend_point_version import PointVersion


def _source(owner: OwnedEvidence, points: list[dict[str, object]],
            previous: CertifiedSource | None = None, start: int = 0) -> CertifiedSource:
    keys = [owned_signature(point) for point in points]
    assert all(key is not None for key in keys)
    return CertifiedSource.from_tail(points, cast(list[bytes], keys), owner, previous, start)


def _point(index: int, proof: object) -> dict[str, object]:
    return dict(index=index, ordinal=0, time=str(index), available_at=index,
                kind='H' if index % 2 else 'L', value=20 + index, audit=proof)


def test_new_tail_skips_exact_old_proof_but_not_an_equal_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        leaf = list(range(128))
        proof = dict(left=leaf, right=leaf)
        first = _source(owner, [_point(0, proof)])
        receipts = FiniteSourceReceipts(owner)
        assert receipts.check(first, 0)
        assert receipts.retain(first, 0, limit=64 * 1024) is receipts.token
        visits: list[int] = []
        original = owner.reference

        def reference(value: object) -> int | None:
            visits.append(id(value))
            return original(value)

        monkeypatch.setattr(owner, 'reference', reference)
        following = _source(owner, [_point(1, proof)], first, 1)
        visits.clear()
        assert receipts.check(following, 1)
        assert id(leaf) not in visits
        assert receipts.stats['subtree_skips'] == 1
        copied = deepcopy(proof)
        independent = _source(owner, [_point(2, copied)])
        visits.clear()
        assert receipts.check(independent, 0)
        assert id(copied['left']) in visits
        assert owner.finite(independent[0])


@pytest.mark.parametrize('invalid', ('nan', 'infinite_key', 'bytes', 'cycle', 'unsealed'))
def test_known_proof_cannot_hide_invalid_new_tail(invalid: str) -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        proof = dict(values=[1, 2, 3])
        first = _source(owner, [_point(0, proof)])
        receipts = FiniteSourceReceipts(owner)
        assert receipts.check(first, 0)
        receipts.retain(first, 0, limit=64 * 1024)
        point = _point(1, proof)
        if invalid == 'nan':
            point['new'] = float('nan')
        elif invalid == 'infinite_key':
            point['new'] = {float('inf'): 1}
        elif invalid == 'bytes':
            point['new'] = b'not-json'
        elif invalid == 'cycle':
            point['new'] = point
        else:
            point['new'] = PointVersion([dict(value=1)])
        # 来源签名本身不证明有限性，不能据签名字节签发收据。
        following = CertifiedSource([point], [b'signature-is-not-a-proof'], owner, None)
        assert not receipts.check(following, 0)
        assert receipts.retain(following, 0, limit=64 * 1024) is None
        assert not owner.finite(point)


def test_registration_requires_a_real_check_and_same_sealed_owner() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        receipts = FiniteSourceReceipts(owner)
        first = _source(owner, [_point(0, dict(values=[1]))])
        assert receipts.retain(first, 0, limit=64 * 1024) is None
        assert receipts.check(first, 0)
        first._sealed = False
        assert receipts.retain(first, 0, limit=64 * 1024) is None
        first.seal()
        with owned_evidence_scope(memory_bytes=0) as other:
            foreign = _source(other, [first[0]])
            assert receipts.check(foreign, 0)
            assert receipts.retain(foreign, 0, limit=64 * 1024) is None
        assert receipts.check(first, 0)
        assert receipts.retain(first, 0, limit=64 * 1024) is receipts.token
    assert not receipts.knows(first[0], owner)


def test_replaced_tail_keeps_shared_root_until_last_current_point_is_removed() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        proof = dict(values=[1, 2, 3])
        first = _source(owner, [_point(0, proof)])
        adapter = _OwnedSourceInputs()
        adapter.prepare(first, ())
        receipts = adapter._finite_receipts
        assert receipts is not None and receipts.knows(proof, owner)
        # 旧尾退出当前来源，但新尾仍引用同一 proof。
        replacement = _source(owner, [_point(1, proof)], first, 0)
        result = adapter.prepare(replacement, ())['path-0']
        assert list(result) == list(replacement)
        assert receipts.knows(proof, owner)
        assert not receipts.knows(first[0], owner)
        assert receipts.stats['subtree_skips'] == 1
        empty = _source(owner, [], replacement, 0)
        assert adapter.prepare(empty, ()) == {}
        assert not receipts.knows(proof, owner)
        assert not receipts._points and not receipts._nodes


def test_budget_refusal_preserves_old_receipts_then_revokes_them_without_rebuilding() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        first = _source(owner, [_point(0, dict(values=[1]))])
        receipts = FiniteSourceReceipts(owner)
        assert receipts.check(first, 0)
        token = receipts.retain(first, 0, limit=64 * 1024)
        assert token is receipts.token
        before = receipts.memory_used
        second = _source(owner, [_point(1, dict(values=[2]))])
        assert receipts.check(second, 0)
        assert receipts.retain(second, 0, limit=before) is None
        assert receipts.blocked and receipts.knows(first[0], owner)
        assert receipts.memory_used == before
        assert not receipts.fit(0)
        assert not receipts.knows(first[0], owner)
        receipts.release(first[0], token)
        assert not receipts._points and not receipts._nodes
        assert receipts.check(second, 0)
        assert receipts.retain(second, 0, limit=64 * 1024) is None
        assert receipts.stats['checks'] == 3
        assert receipts.stats['budget_rejections'] == 2
        assert receipts.stats['peak_memory_bytes'] >= before


def test_public_mutable_input_cannot_borrow_a_prior_internal_receipt() -> None:
    with owned_evidence_scope(memory_bytes=0) as owner:
        source = _source(owner, [_point(0, dict(values=[1]))])
        adapter = _OwnedSourceInputs()
        adapter.prepare(source, ())
        public = deepcopy(list(source))
        cast(dict[str, object], public[0]['audit'])['values'] = [float('inf')]
        actual = adapter.prepare(public, ())['path-0']
        assert type(actual) is list
        assert not adapter._entries and adapter._finite_receipts is None
