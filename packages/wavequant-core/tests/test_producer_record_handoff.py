"""记录载体直接消费生产证明；重复调用不重新捕获或解析已完成的根。"""

from copy import deepcopy
from typing import cast
from unittest.mock import patch

import pytest

from wavequant.domain.market_structure.owned_evidence_producer import PointRecord, producer_record_scope
from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords
from wavequant.domain.market_structure.trend_levels import _completion_evidence, _stamp_wave_completion
from wavequant.domain.market_structure.trend_point_version import PointVersion, record_point_version


def test_new_point_receives_writer_fields_without_readback_or_public_revalidation() -> None:
    shared: dict[str, object] = {'items': [1, 2]}
    point: dict[str, object] = {'proof': shared, 'alias': shared, 'separate': deepcopy(shared)}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        with patch.object(store, 'dictionary_refs', side_effect=AssertionError('new root was read back')), patch.object(
            PointRecord, '__post_init__', side_effect=AssertionError('producer proof was revalidated'),
        ):
            record = scope.ensure_record(point)
        assert record.matches(point)
        assert scope.stats['captures'] == 1 and scope.stats['field_decodes'] == 0
        assert scope.stats['new_nodes'] == store.record_count
        restored = cast(dict[str, object], store.materialize(record.reference))
        assert restored == point
        assert restored['proof'] is restored['alias'] and restored['proof'] is not restored['separate']


def test_existing_point_reuses_the_actual_carrier_without_capture_or_decode() -> None:
    point: dict[str, object] = {'proof': {'items': [1]}}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        record = scope.ensure_record(point)
        count, used, visits = store.record_count, scope.memory_used, scope.stats['capture_node_visits']
        with patch.object(scope, '_capture', side_effect=AssertionError('known root was captured')), patch.object(
            store, 'dictionary_refs', side_effect=AssertionError('known root was decoded'),
        ):
            assert scope.ensure_record(point) is record
            assert scope.record_point(point) is record
            assert scope.ensure_value(point) is record.reference
            assert scope.reference_of(point) is record.reference
        assert store.record_count == count and scope.memory_used == used
        assert scope.stats['capture_node_visits'] == visits
        assert scope.stats['known_root_reuses'] == 3


def test_changed_point_writes_only_its_new_shell_and_scalar() -> None:
    point: dict[str, object] = {'proof': [{'index': index} for index in range(30)], 'state': 'before'}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        first = scope.ensure_record(point)
        before, expanded = store.record_count, scope.stats['container_expansions']
        point['state'] = 'after'
        assert scope.reference_of(point) is None
        with patch.object(store, 'dictionary_refs', side_effect=AssertionError('changed root was read back')):
            second = scope.ensure_record(point)
        assert store.record_count == before + 2
        assert scope.stats['container_expansions'] == expanded + 1
        assert first.field_reference('proof') == second.field_reference('proof')
        combined = cast(list[dict[str, object]], store.materialize(store.write_list([first.reference, second.reference])))
        assert combined[0]['state'] == 'before' and combined[1]['state'] == 'after'
        assert combined[0]['proof'] is combined[1]['proof']


def test_generic_field_capture_reuses_aliases_and_preserves_independent_clone_domains() -> None:
    shared: dict[str, object] = {'nested': [1]}
    source: list[object] = [shared, shared]
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        reference = scope.ensure_value(source)
        assert scope.outputs == () and scope.reference_of(source) is reference
        captures = scope.stats['captures']
        assert scope.ensure_value(source) is reference
        assert scope.stats['captures'] == captures
        first, second = deepcopy(source), deepcopy(source)
        scope.bind_clone(first, source, reference)
        scope.bind_clone(second, source, reference)
        record = scope.ensure_record({'original': source, 'first': first, 'second': second})
        restored = cast(dict[str, list[object]], store.materialize(record.reference))
        assert restored['original'][0] is restored['original'][1]
        assert restored['first'][0] is restored['first'][1]
        assert restored['second'][0] is restored['second'][1]
        assert restored['first'][0] is not restored['second'][0]
        assert restored['original'][0] is not restored['first'][0]


def test_generic_scalar_and_non_point_dict_do_not_relax_point_input_contract() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        for value in (None, True, 1001, 1.25, b'proof', 'proof', (1, 2), {3: [4]}):
            reference = scope.ensure_value(value)
            assert store.materialize(reference) == value
        with pytest.raises(TypeError, match='string keys'):
            scope.ensure_record(cast(dict[str, object], {3: [4]}))
        with pytest.raises(TypeError, match='exact dictionary'):
            scope.ensure_record(cast(dict[str, object], [1]))


def test_completion_records_new_fields_once_without_capturing_the_unfinished_point() -> None:
    shared: dict[str, object] = {'items': [1]}
    point: dict[str, object] = {'index': 1, 'definition_evidence': shared, 'upgrade_evidence': shared}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        captured: list[object] = []
        original_capture = scope._capture

        def capture(root: object) -> object:
            captured.append(root)
            return original_capture(root)

        with patch.object(scope, '_capture', side_effect=capture):
            completion = _completion_evidence(point, _record_fields=True)
        assert scope.lookup(point) is None
        assert sum(root is point for root in captured) == 0
        assert sum(root is shared for root in captured) == 1
        assert sum(root is completion for root in captured) == 1
        finished = scope.lookup(completion)
        assert finished is not None
        source = scope.ensure_record(point)
        restored = cast(list[dict[str, object]], store.materialize(store.write_list([
            source.reference, finished.reference,
        ])))
        assert restored[0]['definition_evidence'] is restored[0]['upgrade_evidence']
        assert restored[1]['definition_evidence'] is not restored[1]['upgrade_evidence']
        assert restored[1]['definition_evidence'] is not restored[0]['definition_evidence']


def test_untracked_validation_completion_does_not_record_or_consume_scope_budget() -> None:
    point: dict[str, object] = {'index': 1, 'available_at': 2, 'definition_evidence': {'items': [1]}}
    expected = deepcopy(point)
    _stamp_wave_completion(expected, closed=True)
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store, memory_bytes=0) as scope:
        used, stats = scope.memory_used, dict(scope.stats)
        with patch.object(scope, 'ensure_value', side_effect=AssertionError('validation wrote a field')), patch.object(
            scope, 'ensure_record', side_effect=AssertionError('validation recorded a point'),
        ):
            pure = _completion_evidence(point)
            _stamp_wave_completion(point, closed=True)
        assert point == expected and pure == expected['wave_completion_evidence']
        assert scope.lookup(point) is None and scope.outputs == ()
        assert store.record_count == 0 and store.stored_bytes == 0
        assert scope.memory_used == used and scope.stats == stats


def test_explicit_new_completion_does_not_hide_record_budget_errors() -> None:
    point: dict[str, object] = {'index': 1, 'available_at': 2, 'definition_evidence': {'items': [1]}}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store, memory_bytes=0) as scope:
        with pytest.raises(ValueError, match='memory limit'):
            _stamp_wave_completion(point, closed=True, _record_new=True)
        assert scope.lookup(point) is None and scope.outputs == ()
        assert store.record_count == 0 and store.stored_bytes == 0


def test_tracked_completion_still_refreshes_its_point_after_shallow_changes() -> None:
    point: dict[str, object] = {'index': 1, 'available_at': 2, 'definition_evidence': {'items': [1]}}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        previous = scope.ensure_record(point)
        _stamp_wave_completion(point, closed=True)
        current = scope.lookup(point)
        assert current is not None and current is not previous
        assert store.materialize(current.reference) == point
        assert 'wave_completion_evidence' not in cast(dict[str, object], store.materialize(previous.reference))


def test_view_decodes_each_root_once_and_public_constructor_stays_strict() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store:
        version = PointVersion([{'proof': [1], 'state': 'finished'}]).seal()
        with producer_record_scope(store) as scope:
            record_point_version(version, scope)
        with patch.object(store, 'dictionary_refs', wraps=store.dictionary_refs) as parsed, patch.object(
            PointRecord, '__post_init__', side_effect=AssertionError('view decoded its root twice'),
        ):
            record = version.point_records[0]
        assert parsed.call_count == 1 and record.matches(version[0])
        with pytest.raises(ValueError, match='original keys and references'):
            PointRecord(record.value, record.reference,
                        tuple((name, record.reference) for name, _ in record.fields), store)


def test_bare_dict_field_gets_one_shallow_decode_when_first_used_as_a_point() -> None:
    point: dict[str, object] = {'proof': [1]}
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        reference = scope.ensure_value(point)
        before = store.record_count
        record = scope.ensure_record(point)
        assert record.reference is reference and store.record_count == before
        assert scope.stats['field_decodes'] == 1 and scope.stats['captures'] == 1
        assert scope.ensure_record(point) is record
        point['state'] = 'changed'
        changed = scope.ensure_value(point)
        assert scope.ensure_record(point).reference is changed
        assert scope.reference_of(point) is changed


@pytest.mark.parametrize('invalid', ('unknown', 'cycle', 'nonfinite', 'budget'))
def test_failed_field_capture_rolls_back_storage_and_publishes_no_binding(invalid: str) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as store, producer_record_scope(store) as scope:
        prior = scope.ensure_record({'existing': [1]})
        value: list[object] = [{'new': [2]}]
        if invalid == 'unknown':
            value.append(object())
        elif invalid == 'cycle':
            value.append(value)
        elif invalid == 'nonfinite':
            value.append(float('inf'))
        else:
            scope.memory_bytes = scope.memory_used + 1024
        count, stored, used = store.record_count, store.stored_bytes, scope.memory_used
        bindings, outputs = dict(scope._bindings), scope.outputs
        with pytest.raises((TypeError, ValueError)):
            scope.ensure_value(value)
        assert (store.record_count, store.stored_bytes, scope.memory_used) == (count, stored, used)
        assert scope._bindings == bindings and scope.outputs == outputs
        assert scope.reference_of(value) is None and scope.lookup(prior.value) is prior
