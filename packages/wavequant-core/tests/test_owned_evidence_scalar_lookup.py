"""未登记的标准数值叶跳过引用查找，保留编码、准入及预算语义。"""

from collections.abc import Callable, Sequence
from io import BytesIO
from typing import Never, cast

import pytest

from wavequant.domain.market_structure import owned_evidence, trend_judgement
from wavequant.domain.market_structure.owned_evidence import (
    OwnedEvidence, _EvidenceWriter, _SignatureWriter, owned_evidence_scope, owned_signature,
)


class _PreviousEvidenceWriter(_EvidenceWriter):
    def persistent_id(self, value: object) -> int | None:
        return None if value is self.root else self.owner.reference(value)


class _PreviousSignatureWriter(_SignatureWriter):
    def persistent_id(self, value: object) -> int | None:
        if type(value) is str:
            return None
        return None if value is self.root else self.owner.reference(value)


def _forbid_scalar_lookup(evidence: OwnedEvidence, monkeypatch: pytest.MonkeyPatch) -> None:
    original = evidence.reference

    def reference(value: object) -> int | None:
        assert type(value) not in (int, float, bool, type(None)), 'an unindexed scalar reached the reference table'
        return original(value)

    monkeypatch.setattr(evidence, 'reference', reference)


@pytest.mark.parametrize('signature', (False, True))
def test_scalar_shortcut_preserves_exact_payload_and_signature_bytes(
    signature: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with owned_evidence_scope() as evidence:
        shared: dict[str, object] = {'values': list(range(300)), 'label': 'shared-label'}
        evidence.publish(shared)
        scalars: list[object] = [None, False, True, 0, 1, 2 ** 256, -7, 0., -0., 1.25,
                                 float('inf'), float('-inf'), float('nan')]
        payload = {'first': shared, 'second': shared, 'scalars': scalars, 'again': scalars}
        writer: Callable[[BytesIO], _EvidenceWriter]
        if signature:
            writer = lambda stream: _PreviousSignatureWriter(stream, evidence, root=payload)
        else:
            writer = lambda stream: _PreviousEvidenceWriter(stream, evidence)
        expected = evidence.dumps(payload, writer=writer)

        _forbid_scalar_lookup(evidence, monkeypatch)
        actual = owned_signature(payload) if signature else evidence.dumps(payload)

        assert actual == expected and actual is not None
        restored = cast(dict[str, object], evidence.loads(actual))
        assert restored['first'] is restored['second']
        assert restored['scalars'] is restored['again']
        assert restored['first'] == shared


def test_scalar_lookup_shortcut_does_not_change_publication_charges_or_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared: list[object] = [257, 1024, 2 ** 160, -0., 2.5, True, False, None]
    source = {'first': shared, 'second': shared, 'pair': (shared, 'label')}
    baseline = OwnedEvidence(memory_bytes=64 * 1024)
    with monkeypatch.context() as previous:
        previous.setattr(owned_evidence, '_UNINDEXED_SCALAR_TYPES', ())
        baseline.publish(source)
    expected_bytes = baseline.memory_used
    expected_payload = baseline.dumps({'proof': source})
    optimized = OwnedEvidence(memory_bytes=64 * 1024)
    _forbid_scalar_lookup(optimized, monkeypatch)

    assert optimized.finite(source)
    optimized.publish(source)
    assert optimized.memory_used == expected_bytes
    assert optimized.reference(source) is not None and optimized.reference(shared) is not None
    assert optimized.loads(optimized.dumps({'proof': source})) == baseline.loads(expected_payload)
    before = optimized.memory_used
    optimized.publish(source)
    assert optimized.memory_used == before
    baseline.close()
    optimized.close()


@pytest.mark.parametrize('bad', (float('nan'), float('inf'), float('-inf')))
def test_nonfinite_scalars_still_fail_admission_without_reference_lookups(
    bad: float, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with owned_evidence_scope() as evidence:
        _forbid_scalar_lookup(evidence, monkeypatch)
        source = {'valid': [1, 2.5, None], 'bad': bad}
        assert not evidence.finite(source)
        evidence.publish(source)
        assert evidence.memory_used == 0 and evidence.reference(source) is None


class _Unknown:
    def __reduce__(self) -> Never:
        raise AssertionError('unknown serialization hook must not run')


class _IntegerSubclass(int):
    def __reduce__(self) -> Never:
        raise AssertionError('integer subclass serialization hook must not run')


class _FloatSubclass(float):
    def __reduce__(self) -> Never:
        raise AssertionError('float subclass serialization hook must not run')


class _StringSubclass(str):
    def __reduce__(self) -> Never:
        raise AssertionError('string subclass serialization hook must not run')


@pytest.mark.parametrize('bad', (_Unknown(), _IntegerSubclass(3), _FloatSubclass(1.5), _StringSubclass('audit')))
def test_unknown_values_and_scalar_subclasses_keep_the_rejection_path(
    bad: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with owned_evidence_scope() as evidence:
        seen: list[object] = []
        original = evidence.reference

        def reference(value: object) -> int | None:
            seen.append(value)
            return original(value)

        monkeypatch.setattr(evidence, 'reference', reference)
        source = {'audit': bad}
        assert not evidence.finite(source)
        evidence.publish(source)
        assert evidence.memory_used == 0 and evidence.reference(source) is None
        assert owned_signature(source) is None
        assert any(value is bad for value in seen)


@pytest.mark.parametrize('kind', ('ordinary', 'formal'))
@pytest.mark.parametrize('incremental', (False, True))
def test_budget_walks_skip_scalar_reference_lookups_but_keep_their_cost(
    kind: str, incremental: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    with owned_evidence_scope() as evidence:
        shared = {'values': [1, 2, 3]}
        evidence.publish(shared)
        roots: list[object] = [shared, shared, [None, True, False, 257, 2 ** 256, 2.5, -0.]]
        measure: Callable[[Sequence[object]], int]
        if kind == 'ordinary':
            state = trend_judgement._TrendJudgementState(1)
            measure = lambda values: state._owned_value_bytes(values, _incremental=incremental)
        else:
            measure = lambda values: trend_judgement._FormalKeyBreakState._value_bytes(
                values, _incremental=incremental)
        with monkeypatch.context() as previous:
            previous.setattr(trend_judgement, '_UNINDEXED_SCALAR_TYPES', ())
            expected = measure(roots)

        _forbid_scalar_lookup(evidence, monkeypatch)
        assert measure(roots) == expected
