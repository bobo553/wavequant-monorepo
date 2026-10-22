"""生产端显式引用与原生点外壳、复制域和最终浅标注的兼容边界。"""

from copy import deepcopy
from gc import get_referrers
from typing import Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence_producer import (
    current_producer_records, producer_record_scope,
)
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords
from wavequant.domain.market_structure.trend_level_upgrades import _promoted_point
from wavequant.domain.market_structure.trend_levels import (
    _completion_evidence, _ordinary_point, _source_turns, _strip_source_license,
)


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _points(level: int) -> list[dict[str, object]]:
    return [dict(index=index, ordinal=0, kind='L' if index % 2 == 0 else 'H',
                 value=price, time=str(index), available_at=index + 1, trend_level=level - 1,
                 confirmed_by={'source': [index]})
            for index, price in enumerate((10.0, 20.0, 8.0, 18.0, 9.0, 22.0))]


def test_exact_dict_carrier_reuses_fields_across_scopes_without_protocol_keys() -> None:
    proof = {'items': [1, 2]}
    original: dict[str, object] = {'proof': proof, 'again': proof}
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        with producer_record_scope(owner) as scope:
            first = scope.record_point(original)
            assert type(first.value) is dict
            assert original == {'proof': proof, 'again': proof}
        with producer_record_scope(owner, inputs=[first]) as scope:
            copied = dict(original)
            copied['known'] = 3
            second = scope.record_point(copied)
            assert second.field_reference('proof') == first.field_reference('proof')
            assert second.field_reference('again') == first.field_reference('again')
            root = owner.write_list([first.reference, second.reference])
        values = cast(list[object], owner.materialize(root))
        assert _mapping(values[0])['proof'] is _mapping(values[1])['proof']
        assert _mapping(values[1])['proof'] is _mapping(values[1])['again']
        fresh = cast(list[object], owner.materialize(root))
        assert _mapping(fresh[1])['proof'] is not _mapping(values[1])['proof']


def test_new_graph_keeps_cross_field_aliases_and_distinct_equal_children() -> None:
    shared = {'values': [1]}
    distinct = {'values': [1]}
    point: dict[str, object] = {'confirmed_by': shared, 'nested': {'confirmed_by': shared}, 'distinct': distinct}
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        record = scope.record_point(point)
        restored = _mapping(owner.materialize(record.reference))
        assert restored == point
        assert restored['confirmed_by'] is _mapping(restored['nested'])['confirmed_by']
        assert restored['confirmed_by'] is not restored['distinct']


def test_shallow_copy_after_cache_eviction_writes_only_the_new_shell() -> None:
    original: dict[str, object] = {'proof': [{'index': index} for index in range(200)]}
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        with producer_record_scope(owner) as scope:
            first = scope.record_point(original)
        before = owner.record_count
        with producer_record_scope(owner, inputs=[first], memory_bytes=32 * 1024) as scope:
            second = scope.record_point(dict(original))
            assert owner.record_count == before + 1
            assert second.field_reference('proof') == first.field_reference('proof')
        assert owner.materialize(second.reference) == original


def test_final_shallow_mutation_invalidates_only_old_shell_and_tracks_current_output() -> None:
    proof = {'items': [1]}
    point: dict[str, object] = {'proof': proof, 'state': 'old'}
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        first = scope.record_point(point)
        point['state'] = 'final'
        assert scope.lookup(point) is None
        second = scope.record_point(point)
        assert second.reference != first.reference
        assert second.field_reference('proof') == first.field_reference('proof')
        assert scope.lookup(point) is second
        assert scope.outputs == (second,)
        assert _mapping(owner.materialize(first.reference))['state'] == 'old'
        assert _mapping(owner.materialize(second.reference))['state'] == 'final'


def test_key_identity_is_preserved_when_key_is_also_a_value() -> None:
    key = 'noninterned proof key ' * 20
    point: dict[str, object] = {key: key}
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        first = scope.record_point(point)
        second = scope.record_point(dict(point))
        restored = _mapping(owner.materialize(second.reference))
        assert next(iter(restored)) is restored[key]
        assert first.fields[0][0] is key


def test_completion_evidence_uses_an_independent_domain_for_each_deepcopied_field() -> None:
    shared = {'items': [1]}
    point: dict[str, object] = {'index': 1, 'definition_evidence': shared, 'upgrade_evidence': shared}
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        original = scope.record_point(point)
        result = _completion_evidence(point)
        record = scope.lookup(result)
        assert record is not None
        root = owner.write_list([original.reference, record.reference])
        restored = cast(list[object], owner.materialize(root))
        source, completion = map(_mapping, restored)
        assert source['definition_evidence'] is source['upgrade_evidence']
        assert completion['definition_evidence'] is not completion['upgrade_evidence']
        assert completion['definition_evidence'] is not source['definition_evidence']
        assert completion == result


def test_source_license_stripping_reuses_retained_fields_and_clones_confirmation() -> None:
    proof = {'items': [1]}
    point: dict[str, object] = {'index': 1, 'confirmed_by': proof, 'audit': proof, 'trend_level': 1,
                                'wave_completion_evidence': {'closed': True}}
    expected = _strip_source_license(point)
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        first = scope.record_point(point)
        result = _strip_source_license(point)
        record = scope.lookup(result)
        assert record is not None
        assert result == expected
        assert record.field_reference('audit') == first.field_reference('audit')
        restored = _mapping(owner.materialize(record.reference))
        assert restored['confirmed_by'] is restored['audit']
        assert restored['source_confirmation'] is not restored['audit']
        assert 'trend_level' not in restored
        assert 'wave_completion_evidence' not in restored


@pytest.mark.parametrize('level', [1, 2, 3])
def test_ordinary_production_preserves_original_result_and_definition_aliases(level: int) -> None:
    points = _points(level)
    turns = _source_turns(points, level - 1)
    expected = _ordinary_point(turns[2], turns[2:6], turns[5], trend_level=level,
                               direction='down', current='up', prefix_known=6, key=None, owned=True)
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        for point in points:
            scope.record_point(point)
        turns = _source_turns(points, level - 1)
        result = _ordinary_point(turns[2], turns[2:6], turns[5], trend_level=level,
                                 direction='down', current='up', prefix_known=6, key=None, owned=True)
        record = scope.lookup(result)
        assert record is not None and type(result) is dict
        restored = _mapping(owner.materialize(record.reference))
        assert restored == result == expected
        if level == 1:
            assert restored['definition_evidence'] is restored['confirmed_by']
        completion = _mapping(restored['wave_completion_evidence'])
        assert completion['definition_evidence'] is not restored['definition_evidence']


@pytest.mark.parametrize('level', [1, 2, 3])
def test_promotion_keeps_shared_attack_and_distinct_equal_key_references(level: int) -> None:
    points = _points(level)
    turns = _source_turns(points, level - 1)
    expected = _promoted_point(turns[2], attack=turns[5], origin=turns[2], key=turns[1],
                               trend_level=level, known=6, endpoint=False)
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        for point in points:
            scope.record_point(point)
        turns = _source_turns(points, level - 1)
        result = _promoted_point(turns[2], attack=turns[5], origin=turns[2], key=turns[1],
                                 trend_level=level, known=6, endpoint=False)
        record = scope.lookup(result)
        assert record is not None and type(result) is dict
        restored = _mapping(owner.materialize(record.reference))
        proof = _mapping(restored['upgrade_evidence'])
        assert restored == result == expected
        assert restored['confirmed_by'] is proof['confirmed_by']
        assert restored['broken_key'] == proof['broken_key']
        assert restored['broken_key'] is not proof['broken_key']
        assert _mapping(restored['wave_completion_evidence'])['upgrade_evidence'] is not proof


def test_public_input_has_no_ambient_scope_and_preserves_deepcopy_hooks() -> None:
    class Audit:
        def __deepcopy__(self, memo: dict[int, object]) -> str:
            return 'copied publicly'

    assert current_producer_records() is None
    assert _strip_source_license({'confirmed_by': Audit()})['source_confirmation'] == 'copied publicly'


def test_unknown_types_cycles_and_small_budget_do_not_publish_a_root() -> None:
    class Audit:
        def __reduce_ex__(self, protocol: int) -> Never:
            raise AssertionError('recording must not execute serialization hooks')

        def __sizeof__(self) -> Never:
            raise AssertionError('recording must not execute size hooks')

    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as scope:
        with pytest.raises(TypeError, match='exact builtin'):
            scope.record_point({'audit': Audit()})
        assert scope.outputs == ()
        cyclic: dict[str, object] = {}
        cyclic['self'] = cyclic
        with pytest.raises(ValueError, match='cycle'):
            scope.record_point(cyclic)
        assert scope.outputs == ()
        assert scope.lookup(cyclic) is None
        with producer_record_scope(owner, memory_bytes=0) as small:
            with pytest.raises(ValueError, match='memory limit'):
                small.record_point({})
            assert small.outputs == ()


def test_scope_restores_context_and_drops_identity_maps_after_exception() -> None:
    with OwnedEvidenceRecords() as owner, producer_record_scope(owner) as outer:
        record = outer.record_point({'proof': [1]})
        with pytest.raises(RuntimeError, match='stop'):
            with producer_record_scope(owner, inputs=[record]) as inner:
                assert current_producer_records() is inner
                raise RuntimeError('stop')
        assert current_producer_records() is outer
        assert inner.outputs == ()
        with pytest.raises(ValueError, match='closed'):
            inner.lookup(record.value)
    assert current_producer_records() is None


def test_foreign_or_changed_carriers_cannot_be_bound() -> None:
    with OwnedEvidenceRecords() as left, OwnedEvidenceRecords() as right:
        with producer_record_scope(left) as scope:
            point: dict[str, object] = {'proof': [1]}
            record = scope.record_point(point)
        with pytest.raises(ValueError, match='this record owner'):
            with producer_record_scope(right, inputs=[record]):
                raise AssertionError('foreign input was admitted')
        point['proof'] = deepcopy(point['proof'])
        with pytest.raises(ValueError, match='unchanged point'):
            with producer_record_scope(left, inputs=[record]):
                raise AssertionError('changed input was admitted')


def test_many_small_record_calls_share_one_scope_budget() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as owner, producer_record_scope(owner, memory_bytes=32 * 1024) as scope:
        accepted = []
        for index in range(128):
            point: dict[str, object] = {'number': index + 1000}
            try:
                accepted.append(scope.record_point(point))
            except ValueError as error:
                assert 'memory limit' in str(error)
                assert scope.lookup(point) is None
                break
        else:
            pytest.fail('per-call capture limits allowed unbounded cumulative bindings')
        assert accepted and scope.outputs == tuple(accepted)
        assert scope.memory_used <= scope.memory_bytes
        assert scope.peak_bytes <= scope.memory_bytes


def test_retained_large_values_are_charged_after_each_capture_commits() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as owner, producer_record_scope(owner, memory_bytes=512 * 1024) as scope:
        accepted = []
        for index in range(16):
            # 每份新图都小于单次捕获上限；旧 bytes 仍被 scope 的绑定强持有。
            point: dict[str, object] = {'payload': bytes([index]) * (64 * 1024)}
            try:
                accepted.append(scope.record_point(point))
            except ValueError as error:
                assert 'memory limit' in str(error)
                assert scope.lookup(point) is None
                break
        else:
            pytest.fail('large retained values escaped the cumulative scope budget')
        assert accepted
        assert len(accepted) * 64 * 1024 <= scope.memory_bytes
        assert scope.outputs == tuple(accepted)


def test_replacing_one_shell_does_not_retain_its_obsolete_point_record() -> None:
    point: dict[str, object] = {'proof': {'old': [1]}, 'state': 'old'}
    with OwnedEvidenceRecords(cache_bytes=0) as owner, producer_record_scope(owner) as scope:
        first = scope.record_point(point)
        point['proof'] = {'new': [2]}
        point['state'] = 'current'
        second = scope.record_point(point)
        assert scope.lookup(point) is second and scope.outputs == (second,)
        # 旧子图可由有界 bindings 持有到作用域结束，但 outputs 不能继续
        # 保留失效 PointRecord 及其旧字段快照。
        referrers = get_referrers(first)
        assert all(value is not scope._outputs and value is not scope._points for value in referrers)
        assert _mapping(owner.materialize(first.reference))['state'] == 'old'
        assert _mapping(owner.materialize(second.reference))['state'] == 'current'


@pytest.mark.parametrize('operation', ('bind', 'record', 'clone'))
def test_budget_rejection_does_not_publish_partial_bindings(operation: str) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        with producer_record_scope(owner) as producer:
            first = producer.record_point({'proof': {'one': [1]}})
            next_point: dict[str, object] = {'proof': {'two': [2]}}
            second = producer.record_point(next_point)
        with producer_record_scope(owner, inputs=[first]) as scope:
            bindings, points, outputs = dict(scope._bindings), dict(scope._points), scope.outputs
            charges = dict(scope._binding_bytes)
            used = scope.memory_used
            scope.memory_bytes = used
            with pytest.raises(ValueError, match='memory limit'):
                if operation == 'bind':
                    scope.bind(second)
                elif operation == 'record':
                    scope.record_point(next_point)
                else:
                    reference = second.field_reference('proof')
                    assert reference is not None
                    original = next_point['proof']
                    scope.bind_clone(deepcopy(original), original, reference)
            assert scope._bindings == bindings and scope._points == points
            assert scope._binding_bytes == charges
            assert scope.outputs == outputs and scope.memory_used == used
            assert scope.lookup(next_point) is None
            assert scope.lookup(first.value) is first


def test_clone_budget_counts_nested_copies_across_calls() -> None:
    source = {'items': [{'values': [index, index + 1]} for index in range(96)]}
    first, second = deepcopy(source), deepcopy(source)
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        reference = owner.capture_new(source)
        with producer_record_scope(owner, memory_bytes=512 * 1024) as scope:
            initial = scope.memory_used
            scope.bind_clone(first, source, reference)
            # 外壳只有一个字段，但其持有的复制容器不能成为预算外的历史图。
            assert scope.memory_used - initial > 32 * 1024
            assert id(first['items']) in scope._clone_bytes
            assert all(id(item) in scope._clone_bytes and id(item['values']) in scope._clone_bytes
                       for item in first['items'])
            used, count, stored = scope.memory_used, owner.record_count, owner.stored_bytes
            scope.memory_bytes = used + 4096
            with pytest.raises(ValueError, match='clone graph.*memory limit'):
                scope.bind_clone(second, source, reference)
            assert scope.memory_used == used
            assert id(second) not in scope._bindings and id(second) not in scope._clone_bytes
            assert owner.record_count == count and owner.stored_bytes == stored
            assert scope._reference(first) is not None


def test_nested_clone_budget_failure_writes_nothing_and_publishes_no_binding() -> None:
    source = {'items': [{'values': [index]} for index in range(128)]}
    copied = deepcopy(source)
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        reference = owner.capture_new(source)
        with producer_record_scope(owner) as scope:
            existing = scope.record_point({'accepted': {'value': 1}})
            bindings, binding_bytes = dict(scope._bindings), dict(scope._binding_bytes)
            clones, points, outputs = dict(scope._clone_bytes), dict(scope._points), scope.outputs
            used, peak = scope.memory_used, scope.peak_bytes
            count, stored = owner.record_count, owner.stored_bytes
            scope.memory_bytes = used + 4096
            with pytest.raises(ValueError, match='clone graph.*memory limit'):
                scope.bind_clone(copied, source, reference)
            assert scope._bindings == bindings and scope._binding_bytes == binding_bytes
            assert scope._clone_bytes == clones and scope._points == points
            assert scope.outputs == outputs and scope.lookup(existing.value) is existing
            assert scope.memory_used == used and scope.peak_bytes == peak
            assert scope._reference(copied) is None
            assert owner.record_count == count and owner.stored_bytes == stored


def test_immutable_deepcopy_identity_does_not_repeat_graph_charge_or_clone_write() -> None:
    source = ('immutable proof' * 1024, (1001, None, b'unchanged'))
    copied = deepcopy(source)
    assert copied is source
    with OwnedEvidenceRecords(cache_bytes=0) as owner:
        reference = owner.capture_new(source)
        with producer_record_scope(owner) as scope:
            count, stored = owner.record_count, owner.stored_bytes
            scope.bind_clone(copied, source, reference)
            assert scope._clone_bytes == {}
            assert scope._reference(copied) == reference
            used, peak = scope.memory_used, scope.peak_bytes
            bindings = dict(scope._bindings)
            scope.memory_bytes = used
            scope.bind_clone(deepcopy(source), source, reference)
            assert scope._bindings == bindings and scope._clone_bytes == {}
            assert scope.memory_used == used and scope.peak_bytes == peak
            assert owner.record_count == count and owner.stored_bytes == stored
