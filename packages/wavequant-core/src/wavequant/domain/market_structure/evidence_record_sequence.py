"""证据点引用的持久序列；写入只访问变化叶和树路径。"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .owned_evidence_records import OwnedEvidenceRecords, RecordRef, _TUPLE, _VECTOR


_BLOCK = 32


@dataclass(frozen=True, slots=True)
class EvidenceSequence:
    reference: RecordRef
    root: RecordRef
    capacity: int
    length: int


def domain_sequence(store: OwnedEvidenceRecords, sequence: EvidenceSequence,
                    domain: RecordRef) -> EvidenceSequence:
    """同次恢复的所有数组共用域；根和树都不展开旧点或证明。"""
    validate_evidence_sequence(store, sequence)
    return EvidenceSequence(store.domain_ref(sequence.reference, domain),
                            store.domain_ref(sequence.root, domain), sequence.capacity, sequence.length)


def validate_evidence_sequence(store: OwnedEvidenceRecords, sequence: EvidenceSequence) -> None:
    if type(sequence) is not EvidenceSequence:
        raise TypeError('point references require an evidence sequence')
    if (type(sequence.capacity) is not int or not 1 <= sequence.capacity <= 2 ** 58
            or sequence.capacity & (sequence.capacity - 1) or type(sequence.length) is not int
            or not 0 <= sequence.length <= sequence.capacity * _BLOCK):
        raise ValueError('evidence sequence has an invalid layout')
    _, record, domains = store._resolved(sequence.reference)
    if record.kind != _VECTOR:
        raise ValueError('evidence sequence metadata must match its recorded root')
    expected_root, _, root_domains = store._resolved(RecordRef(store.namespace, record.children[1]))
    actual_root, _, actual_domains = store._resolved(sequence.root)
    if (record.kind != _VECTOR or record.scalar != sequence.length
            or record.children[0] != sequence.capacity
            or expected_root != actual_root or domains + root_domains != actual_domains):
        raise ValueError('evidence sequence metadata must match its recorded root')


def iter_point_refs(store: OwnedEvidenceRecords, sequence: EvidenceSequence, *,
                    start: int = 0, stop: int | None = None) -> Iterator[RecordRef]:
    """读取指定区间的块路径；不读取点值或区间外的历史分支。"""
    validate_evidence_sequence(store, sequence)
    root = store._validate_ref(sequence.root)
    end = sequence.length if stop is None else stop
    if type(start) is not int or type(end) is not int or not 0 <= start <= end <= sequence.length:
        raise IndexError('point reference interval is outside its evidence sequence')
    pending: list[tuple[int, int, int, tuple[int, ...]]] = [(root, sequence.capacity, 0, ())]
    while pending:
        offset, width, begin, inherited = pending.pop()
        if begin >= end or begin + width * _BLOCK <= start:
            continue
        _, node, local = store._resolved(RecordRef(store.namespace, offset))
        domains = inherited + local
        if node.kind != _TUPLE:
            raise ValueError('evidence sequence requires private tuple nodes')
        if width == 1:
            count = min(_BLOCK, sequence.length - begin)
            if not count <= len(node.children) <= _BLOCK:
                raise ValueError('evidence sequence is missing visible leaf values')
            for child in node.children[max(0, start - begin):min(count, end - begin)]:
                reference = RecordRef(store.namespace, child)
                for marker in reversed(domains):
                    reference = store.domain_ref(reference, RecordRef(store.namespace, marker))
                yield reference
        else:
            if len(node.children) != 2:
                raise ValueError('evidence sequence requires two-child branches')
            half = width // 2
            pending.append((node.children[1], half, begin + half * _BLOCK, domains))
            pending.append((node.children[0], half, begin, domains))


def point_ref_at(store: OwnedEvidenceRecords, sequence: EvidenceSequence, index: int) -> RecordRef:
    if type(index) is not int or not 0 <= index < sequence.length:
        raise IndexError('point reference index is outside its evidence sequence')
    return next(iter_point_refs(store, sequence, start=index, stop=index + 1))


def derive_evidence_sequence(
    store: OwnedEvidenceRecords, suffix: Sequence[RecordRef], *,
    previous: EvidenceSequence | None = None, start: int = 0,
) -> EvidenceSequence:
    """用显式点引用替换后缀；新根没有指向上一版本的Python对象链。

    调用方必须已认证 start 之前的版本相同。这里仅验证请求与树结构，
    不重新读取旧点的值；公开未知输入不能凭这个参数跳过领域验证。
    单条记录分别提交；中途失败不发布新根，旧根仍有效，可能留下本次
    未被根引用的路径记录，随整个请求存储关闭释放。
    """
    if previous is not None and type(previous) is not EvidenceSequence:
        raise TypeError('evidence sequence requires its own prior version')
    if type(suffix) not in (list, tuple):
        raise TypeError('evidence sequence suffix requires an exact list or tuple')
    if previous is not None:
        validate_evidence_sequence(store, previous)
    if type(start) is not int or not 0 <= start <= (previous.length if previous else 0):
        raise ValueError('evidence sequence requires a valid retained prefix')
    for reference in suffix:
        store._validate_ref(reference)
    empty: RecordRef | None = None

    def empty_node() -> RecordRef:
        nonlocal empty
        if empty is None:
            empty = store.write_tuple(())
        return empty

    def children(root: RecordRef) -> tuple[RecordRef, ...]:
        node = store.record_view(root)
        if node.kind != _TUPLE:
            raise ValueError('evidence sequence requires a private tuple node')
        return tuple(RecordRef(store.namespace, offset) for offset in node.children)

    def block(root: RecordRef, width: int, position: int) -> tuple[RecordRef, ...]:
        while width > 1:
            nodes = children(root)
            if len(nodes) != 2:
                raise ValueError('evidence sequence is missing a retained branch')
            width //= 2
            if position < width:
                root = nodes[0]
            else:
                root, position = nodes[1], position - width
        values = children(root)
        if len(values) > _BLOCK:
            raise ValueError('evidence sequence leaf exceeds its block size')
        return values

    def replace(root: RecordRef, width: int, position: int, leaf: RecordRef) -> RecordRef:
        if width == 1:
            return leaf
        nodes = children(root)
        if not nodes:
            left = right = empty_node()
        elif len(nodes) == 2:
            left, right = nodes
        else:
            raise ValueError('evidence sequence requires a two-child branch')
        half = width // 2
        if position < half:
            left = replace(left, half, position, leaf)
        else:
            right = replace(right, half, position - half, leaf)
        return store.write_tuple((left, right))

    root = previous.root if previous is not None else empty_node()
    capacity = previous.capacity if previous is not None else 1
    length = start + len(suffix)
    position, offset = divmod(start, _BLOCK)
    consumed = 0
    while consumed < len(suffix):
        prefix = block(root, capacity, position)[:offset] if offset else ()
        if len(prefix) != offset:
            raise ValueError('evidence sequence is missing retained leaf values')
        count = min(_BLOCK - offset, len(suffix) - consumed)
        values = (*prefix, *suffix[consumed:consumed + count])
        while position >= capacity:
            root = store.write_tuple((root, empty_node()))
            capacity *= 2
        leaf = store.write_tuple(values)
        root = replace(root, capacity, position, leaf)
        consumed += count
        position += 1
        offset = 0
    reference = store._write_vector(root, capacity, length)
    return EvidenceSequence(reference, root, capacity, length)
