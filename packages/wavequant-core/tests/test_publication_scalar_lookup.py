"""发布预算中的数值叶仍收费，但不查询永远无法命中的引用表。"""

import pytest
from io import BytesIO

from wavequant.domain.market_structure import trend_publication_cache as module
from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure import trend_lines


@pytest.mark.parametrize('graph', (False, True))
def test_publication_scalar_shortcut_preserves_complete_charge(
    monkeypatch: pytest.MonkeyPatch, graph: bool,
) -> None:
    with owned_evidence_scope() as owner:
        shared = {'proof': [3, 4]}
        owner.publish(shared)
        values = {'shared': shared, 'numbers': [*range(12_000), None, True, -0., 2 ** 256]}
        cache = module._PublicationCache()

        def measure():
            return cache._new_graph((values,), 8 * 1024 * 1024) if graph else module._publication_size(
                values, 8 * 1024 * 1024)

        with monkeypatch.context() as baseline:
            baseline.setattr(module, '_UNINDEXED_SCALAR_TYPES', ())
            expected = measure()
        reference = owner.reference

        def checked(value: object) -> int | None:
            assert type(value) not in (int, float, bool, type(None))
            return reference(value)

        monkeypatch.setattr(owner, 'reference', checked)
        actual = measure()
        assert actual == expected
        assert actual is not None


def test_prepared_pickler_preserves_bytes_without_scalar_reference_queries(monkeypatch: pytest.MonkeyPatch) -> None:
    class PreviousPickler(trend_lines._PreparedPickler):
        def persistent_id(self, value: object) -> int | None:
            return self._reference(value) if self._reference is not None else None

    with owned_evidence_scope() as owner:
        shared = {'proof': [1, 2]}
        owner.publish(shared)
        numbers = [*range(12_000), None, False, True, -0., 2 ** 256]
        value = {'left': shared, 'right': shared, 'numbers': numbers, 'again': numbers}
        before = BytesIO()
        PreviousPickler(before).dump(value)
        reference = owner.reference

        def checked(item: object) -> int | None:
            assert type(item) not in (int, float, bool, type(None))
            return reference(item)

        monkeypatch.setattr(owner, 'reference', checked)
        after = BytesIO()
        trend_lines._PreparedPickler(after).dump(value)
        assert after.getvalue() == before.getvalue()
        restored = owner.loads(after.getvalue())
        assert restored == value
        assert restored['left'] is restored['right']
        assert restored['numbers'] is restored['again']
