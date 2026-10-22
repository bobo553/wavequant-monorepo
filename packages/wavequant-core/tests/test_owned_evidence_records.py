"""不可变磁盘记录的引用、复制域、预算和原子失败边界。"""

from math import copysign
from typing import BinaryIO, Never, cast

import pytest

from wavequant.domain.market_structure.owned_evidence_records import OwnedEvidenceRecords, RecordRef, _DecodedRecord


def _mapping(value: object) -> dict[str, object]:
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    return cast(list[object], value)


def test_new_graph_preserves_aliases_types_order_and_independent_public_reads() -> None:
    shared = {'values': [1, 2], 'pair': ('same', 7)}
    distinct = {'values': [1, 2], 'pair': ('same', 7)}
    source = {'first': shared, 'again': shared, 'distinct': distinct, 'tuple': (shared,)}
    with OwnedEvidenceRecords() as records:
        root = records.capture_new(source)
        restored = _mapping(records.materialize(root))
        assert restored == source
        assert list(restored) == list(source)
        assert restored['first'] is restored['again']
        assert restored['first'] is not restored['distinct']
        assert type(restored['tuple']) is tuple
        assert cast(tuple[object, ...], restored['tuple'])[0] is restored['first']
        _sequence(_mapping(restored['first'])['values']).append(-1)
        fresh = _mapping(records.materialize(root))
        assert fresh == source
        assert fresh['first'] is not restored['first']
        _sequence(shared['values']).append(999)
        assert _mapping(records.materialize(root)) == fresh


def test_new_graph_identity_memo_ends_with_each_capture() -> None:
    source = {'items': [1, 2]}
    with OwnedEvidenceRecords() as records:
        first = records.capture_new(source)
        second = records.capture_new(source)
        assert first != second
        root = records.write_list([first, first, second])
        restored = _sequence(records.materialize(root))
        assert restored[0] is restored[1]
        assert restored[0] == restored[2]
        assert restored[0] is not restored[2]
        assert _mapping(restored[0])['items'] is not _mapping(restored[2])['items']


def test_typed_records_reuse_old_children_and_keep_distinct_equal_records() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        value = records.write_scalar('shared')
        equal = records.write_scalar('shared')
        assert value != equal
        child = records.write_list([value])
        first = records.write_dict([(records.write_scalar('first'), child), (records.write_scalar('again'), child)])
        later = records.capture_new({'previous': first, 'latest': child})
        restored = _mapping(records.materialize(later))
        previous = _mapping(restored['previous'])
        assert previous == {'first': ['shared'], 'again': ['shared']}
        assert previous['first'] is previous['again'] is restored['latest']


def test_clone_domains_keep_internal_aliases_without_sharing_mutable_children() -> None:
    shared = {'values': [1, 2]}
    with OwnedEvidenceRecords() as records:
        original = records.capture_new([shared, shared])
        copied = records.clone(original)
        other_copy = records.clone(original)
        root = records.write_list([original, copied, copied, other_copy])
        restored = _sequence(records.materialize(root))
        assert restored == [[shared, shared]] * 4
        assert restored[1] is restored[2]
        assert restored[0] is not restored[1]
        assert restored[1] is not restored[3]
        for entry in restored:
            assert _sequence(entry)[0] is _sequence(entry)[1]
        _sequence(_mapping(_sequence(restored[1])[0])['values']).append(99)
        assert _mapping(_sequence(restored[0])[0])['values'] == [1, 2]
        assert _mapping(_sequence(restored[3])[0])['values'] == [1, 2]
        assert records.materialize(root) == [[shared, shared]] * 4


def test_nested_clone_domains_are_relative_to_the_containing_clone() -> None:
    with OwnedEvidenceRecords() as records:
        original = records.capture_new({'values': [1]})
        copied = records.clone(original)
        parent = records.write_tuple([copied, copied])
        root = records.write_list([copied, records.clone(parent), records.clone(parent)])
        restored = _sequence(records.materialize(root))
        left = cast(tuple[object, ...], restored[1])
        right = cast(tuple[object, ...], restored[2])
        assert type(left) is tuple
        assert left[0] is left[1]
        assert right[0] is right[1]
        assert left[0] is not right[0]
        assert left[0] is not restored[0]


def test_read_domain_proxies_merge_by_target_and_keep_other_reads_independent() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        child = records.capture_new({'values': [1, 2]})
        original = records.capture_new({'left': child, 'right': child})
        first_domain, other_domain = records.new_read_domain(), records.new_read_domain()
        first = records.domain_ref(original, first_domain)
        again = records.domain_ref(original, first_domain)
        other = records.domain_ref(original, other_domain)
        assert first != again and records.same_reference(first, again)
        assert not records.same_reference(first, other)
        first_fields = dict(records.dictionary_items(first))
        again_fields = dict(records.dictionary_items(again))
        assert records.same_reference(first_fields['left'], again_fields['right'])
        root = records.capture_new([original, first, again, other,
                                    first_fields['left'], again_fields['right']])
        values = _sequence(records.materialize(root))
        assert values[1] is values[2]
        assert _mapping(values[1])['left'] is _mapping(values[1])['right'] is values[4] is values[5]
        assert values[0] is not values[1] and values[1] is not values[3]
        assert _mapping(values[0])['left'] is not values[4]
        assert _mapping(values[3])['left'] is not values[4]
        _sequence(_mapping(values[4])['values']).append(99)
        assert _mapping(_mapping(values[3])['left'])['values'] == [1, 2]
        assert _mapping(_sequence(records.materialize(root))[4])['values'] == [1, 2]


def test_nested_read_domains_preserve_order_and_clone_relative_identity() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        original = records.capture_new({'values': [1]})
        inner, outer = records.new_read_domain(), records.new_read_domain()
        scoped = records.domain_ref(original, inner)
        left = records.domain_ref(scoped, outer)
        same = records.domain_ref(records.domain_ref(original, inner), outer)
        reversed_domains = records.domain_ref(records.domain_ref(original, outer), inner)
        copied = records.clone(left)
        assert records.same_reference(left, same)
        assert not records.same_reference(left, reversed_domains)
        restored = _sequence(records.materialize(records.write_list(
            [left, same, reversed_domains, copied, copied, records.clone(left)])))
        assert restored[0] is restored[1] and restored[3] is restored[4]
        assert restored[0] is not restored[2] and restored[0] is not restored[3]
        assert restored[3] is not restored[5]
        assert len({id(_mapping(value)['values']) for value in (restored[0], restored[2], restored[3], restored[5])}) == 4


def test_clone_and_domain_shallow_children_rejoin_the_original_clone_domain() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        shared = records.capture_new({'values': [1]})
        original = records.capture_new({'left': shared, 'right': shared})
        copied = records.clone(original)
        explicit_copy = records.domain_ref(original, copied)
        assert records.same_reference(copied, explicit_copy)
        domain = records.new_read_domain()
        scoped = records.domain_ref(copied, domain)
        first = dict(records.dictionary_items(scoped))
        second = dict(records.dictionary_items(records.domain_ref(explicit_copy, domain)))
        assert records.same_reference(first['left'], second['right'])
        root = records.capture_new([original, copied, explicit_copy, scoped, first['left'], second['right']])
        values = _sequence(records.materialize(root))
        assert values[1] is values[2]
        assert values[1] is not values[3]
        assert _mapping(values[3])['left'] is _mapping(values[3])['right'] is values[4] is values[5]
        assert _mapping(values[0])['left'] is not values[4]
        assert _mapping(values[1])['left'] is not values[4]


def test_domain_markers_unknown_refs_and_foreign_refs_are_strictly_rejected() -> None:
    class Unknown:
        def __getattr__(self, name: str) -> Never:
            raise AssertionError('unknown reference hook must not execute')

    with OwnedEvidenceRecords(cache_bytes=1024) as records, OwnedEvidenceRecords() as other:
        value = records.capture_new({'values': [1]})
        marker = records.new_read_domain()
        foreign = other.new_read_domain()
        before = records.record_count
        with pytest.raises(ValueError, match='not materialized values'):
            records.materialize(marker)
        for target, domain in ((value, value), (value, foreign), (foreign, marker), (cast(RecordRef, Unknown()), marker)):
            with pytest.raises(ValueError):
                records.domain_ref(target, domain)
        assert records.record_count == before
        for _ in range(20):
            scoped = records.domain_ref(value, records.new_read_domain())
            assert records.materialize(scoped) == {'values': [1]}
            records.dictionary_refs(scoped)
            assert records.cache_memory_used <= 1024


def test_domain_reference_size_rejection_does_not_publish_a_partial_proxy() -> None:
    with OwnedEvidenceRecords(cache_bytes=0, max_record_bytes=8) as records:
        value = records.write_list(())
        marker = records.new_read_domain()
        before_count, before_size = records.record_count, records.stored_bytes
        with pytest.raises(ValueError, match='size limit'):
            records.domain_ref(value, marker)
        assert (records.record_count, records.stored_bytes) == (before_count, before_size)
        assert records.materialize(value) == []


def test_reference_identity_does_not_decode_wide_terminal_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        item = records.capture_new({'detail': [1, 2]})
        wide = records.write_list([item] * 8192)
        equal = records.write_list([item] * 8192)
        domain = records.new_read_domain()
        left = records.domain_ref(wide, domain)
        right = records.domain_ref(wide, domain)
        different = records.domain_ref(equal, domain)
        cloned = records.clone(left)
        explicit_clone = records.domain_ref(right, cloned)
        reader = records._record

        def proxy_only(offset: int) -> _DecodedRecord:
            if offset in (wide.offset, equal.offset):
                raise AssertionError('identity comparison decoded a historical proof payload')
            return reader(offset)

        before = records.record_count
        with monkeypatch.context() as patch:
            patch.setattr(records, '_record', proxy_only)
            assert records.same_reference(wide, wide)
            assert records.same_reference(left, right)
            assert records.same_reference(cloned, explicit_clone)
            assert not records.same_reference(left, different)
            assert not records.same_reference(left, cloned)
        assert records.record_count == before


def test_clone_preserves_builtin_deepcopy_sharing_for_immutable_atoms_and_tuples() -> None:
    text = 'not interned ' * 1000
    immutable = (text, b'nonempty bytes', (9, None))
    mutable = ([1],)
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        original = records.capture_new({'text': text, 'immutable': immutable, 'mutable': mutable})
        root = records.write_list([original, records.clone(original)])
        restored = _sequence(records.materialize(root))
        left, right = _mapping(restored[0]), _mapping(restored[1])
        assert left['text'] is right['text']
        assert left['immutable'] is right['immutable']
        assert cast(tuple[object, ...], left['immutable'])[0] is left['text']
        assert left['mutable'] is not right['mutable']
        assert cast(tuple[object, ...], left['mutable'])[0] is not cast(tuple[object, ...], right['mutable'])[0]


def test_appending_references_does_not_decode_or_rewrite_the_old_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        root = records.capture_new({'large': 'x' * 100_000, 'child': [1, 2, 3]})
        key = records.write_scalar('key')
        before_count, before_size = records.record_count, records.stored_bytes

        def unexpected_decode(offset: int) -> Never:
            raise AssertionError('linking an old reference must not read its graph')

        with monkeypatch.context() as patch:
            patch.setattr(records, '_record', unexpected_decode)
            wrapper = records.write_list([root])
            copied = records.clone(root)
            mapping = records.write_dict([(key, root)])
        assert records.record_count == before_count + 3
        assert records.stored_bytes - before_size < 128
        assert _sequence(records.materialize(wrapper))[0] == records.materialize(copied)
        assert _mapping(records.materialize(mapping))['key'] == records.materialize(root)


def test_cache_eviction_keeps_old_refs_valid_and_never_rewrites_history() -> None:
    with OwnedEvidenceRecords(cache_bytes=1024) as records:
        oldest = records.capture_new({'detail': 'x' * 4096, 'items': [0, 1]})
        roots = [oldest]
        for day in range(40):
            roots.append(records.capture_new({'day': day, 'previous': oldest}))
        before_count, before_size = records.record_count, records.stored_bytes
        for root in roots:
            records.materialize(root)
            assert records.cache_memory_used <= 1024
        records.clear_cache()
        assert records.cache_memory_used == 0
        assert records.materialize(oldest) == {'detail': 'x' * 4096, 'items': [0, 1]}
        assert records.record_count == before_count
        assert records.stored_bytes == before_size
        latest = records.write_list([oldest])
        assert records.record_count == before_count + 1
        assert records.stored_bytes - before_size < 64
        assert _sequence(records.materialize(latest))[0] == records.materialize(oldest)


def test_deep_reference_chain_materializes_without_recursive_graph_walk() -> None:
    with OwnedEvidenceRecords(cache_bytes=0) as records:
        root = records.write_scalar(None)
        for _ in range(1500):
            root = records.write_list([root])
        restored = records.materialize(root)
        for _ in range(1500):
            assert type(restored) is list
            assert len(_sequence(restored)) == 1
            restored = _sequence(restored)[0]
        assert restored is None


@pytest.mark.parametrize('value', [None, False, True, 0, -(1 << 2048), 1 << 2048, -0.0, 2.5, '\ud800', b'\x00\xff'])
def test_scalar_roundtrip_keeps_exact_type_and_value(value: object) -> None:
    with OwnedEvidenceRecords() as records:
        root = records.capture_new(value)
        restored = records.materialize(root)
        assert type(restored) is type(value)
        assert restored == value
        if type(value) is float and value == 0:
            assert copysign(1, cast(float, restored)) == copysign(1, value)


def test_unknown_objects_and_builtin_subclasses_never_execute_hooks() -> None:
    class Unknown:
        def __reduce__(self) -> Never:
            raise AssertionError('serialization hook must not run')

        def __sizeof__(self) -> Never:
            raise AssertionError('size hook must not run')

    class UnknownList(list[object]):
        def __iter__(self) -> Never:
            raise AssertionError('iteration hook must not run')

        def __len__(self) -> Never:
            raise AssertionError('length hook must not run')

    with OwnedEvidenceRecords() as records:
        saved = records.capture_new({'keep': [1]})
        before_count, before_size = records.record_count, records.stored_bytes
        for unknown in (Unknown(), UnknownList()):
            with pytest.raises(TypeError, match='exact builtin'):
                records.capture_new(['written first', unknown])
            with pytest.raises(TypeError, match='exact list or tuple'):
                records.write_list(cast(list[RecordRef], unknown))
            assert records.record_count == before_count
            assert records.stored_bytes == before_size
            assert records.materialize(saved) == {'keep': [1]}


def test_scope_budget_nonfinite_cycle_and_bad_keys_rollback_the_entire_new_graph() -> None:
    cycle: list[object] = ['new']
    cycle.append(cycle)
    with OwnedEvidenceRecords() as records:
        saved = records.capture_new({'keep': [1]})
        before_count, before_size = records.record_count, records.stored_bytes
        for value in (['new', float('inf')], ['new', float('nan')], cycle, {'new': {(1, 2): 3}}):
            with pytest.raises((TypeError, ValueError)):
                records.capture_new(value)
            assert records.record_count == before_count
            assert records.stored_bytes == before_size
        with pytest.raises(ValueError, match='scoped memory budget'):
            records.capture_new(['first', 'second'], memory_bytes=4000)
        assert records.record_count == before_count
        assert records.stored_bytes == before_size
        assert records.materialize(saved) == {'keep': [1]}
        assert records.materialize(records.capture_new({'next': saved})) == {'next': {'keep': [1]}}


class _FailingWrite:
    """在真实临时文件上模拟一次中途短写，随后允许撤销和恢复。"""

    def __init__(self, stream: BinaryIO, allowance: int) -> None:
        self.stream = stream
        self.allowance: int | None = allowance

    def write(self, payload: bytes | memoryview) -> int | None:
        if self.allowance is None:
            return self.stream.write(payload)
        if self.allowance == 0:
            self.allowance = None
            raise OSError('injected partial write')
        written = self.stream.write(payload[:self.allowance])
        if written is not None:
            self.allowance -= written
        return written

    def read(self, size: int = -1) -> bytes:
        return self.stream.read(size)

    def seek(self, offset: int, whence: int = 0) -> int:
        return self.stream.seek(offset, whence)

    def truncate(self, size: int | None = None) -> int:
        return self.stream.truncate(size)

    def close(self) -> None:
        self.stream.close()


@pytest.mark.parametrize('stream_name,allowance', [('_data', 7), ('_index', 26)])
def test_partial_io_failure_keeps_old_roots_and_does_not_publish_partial_records(
    monkeypatch: pytest.MonkeyPatch, stream_name: str, allowance: int,
) -> None:
    with OwnedEvidenceRecords() as records:
        saved = records.capture_new({'keep': [1]})
        before_count, before_size = records.record_count, records.stored_bytes
        stream = cast(BinaryIO, getattr(records, stream_name))
        monkeypatch.setattr(records, stream_name, _FailingWrite(stream, allowance))
        with pytest.raises(OSError, match='injected partial write'):
            records.capture_new(['first', 'second'])
        assert records.record_count == before_count
        assert records.stored_bytes == before_size
        assert records.materialize(saved) == {'keep': [1]}
        next_root = records.capture_new({'new': saved})
        assert records.materialize(next_root) == {'new': {'keep': [1]}}


def test_rollback_io_failure_closes_store_instead_of_reusing_partial_offsets(monkeypatch: pytest.MonkeyPatch) -> None:
    class UnrecoverableWrite(_FailingWrite):
        def truncate(self, size: int | None = None) -> Never:
            raise OSError('injected rollback failure')

    with OwnedEvidenceRecords() as records:
        saved = records.write_scalar('old')
        monkeypatch.setattr(records, '_data', UnrecoverableWrite(records._data, 1))
        with pytest.raises(OSError, match='rollback failure'):
            records.write_scalar('new')
        with pytest.raises(ValueError, match='closed'):
            records.materialize(saved)
        with pytest.raises(ValueError, match='closed'):
            records.write_scalar('later')


def test_foreign_uncommitted_misaligned_and_closed_refs_are_rejected() -> None:
    with OwnedEvidenceRecords() as outer, OwnedEvidenceRecords() as inner:
        saved = outer.capture_new({'keep': [1]})
        foreign = inner.write_scalar('foreign')
        invalid = (
            foreign,
            RecordRef(outer.namespace, -1),
            RecordRef(outer.namespace, 1),
            RecordRef(outer.namespace, 1 << 60),
            RecordRef(outer.namespace, True),
        )
        for reference in invalid:
            with pytest.raises(ValueError, match='committed record'):
                outer.materialize(reference)
            with pytest.raises(ValueError, match='committed record'):
                outer.capture_new(['new', reference])
        assert outer.materialize(saved) == {'keep': [1]}
        outer.close()
        outer.close()
        with pytest.raises(ValueError, match='closed'):
            outer.materialize(saved)
        with pytest.raises(ValueError, match='closed'):
            outer.write_scalar(1)
        with pytest.raises(ValueError, match='closed'):
            outer.clone(saved)


def test_size_limit_and_non_scalar_dictionary_keys_do_not_create_new_records() -> None:
    with OwnedEvidenceRecords(max_record_bytes=8) as records:
        child = records.write_list([])
        scalar = records.write_scalar('key')
        before_count, before_size = records.record_count, records.stored_bytes
        with pytest.raises(ValueError, match='size limit'):
            records.write_scalar('界' * 4)
        with pytest.raises(ValueError, match='size limit'):
            records.write_list([child, scalar])
        assert records.record_count == before_count
        assert records.stored_bytes == before_size
    with OwnedEvidenceRecords() as records:
        child = records.write_list([])
        scalar = records.write_scalar('value')
        before_count, before_size = records.record_count, records.stored_bytes
        with pytest.raises(TypeError, match='scalar records'):
            records.write_dict([(child, scalar)])
        assert records.record_count == before_count
        assert records.stored_bytes == before_size
