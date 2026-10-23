"""池满后的历史仍保存增量图，旧日期、类型及别名不依赖驻留对象。"""

from typing import cast

import pytest

from wavequant.domain.market_structure.owned_evidence import CertifiedSource, owned_evidence_scope
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.prepared_point_history import PreparedPointReference
from wavequant.domain.market_structure.trend_frozen_state import _SequenceNode
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload
from wavequant.domain.market_structure.trend_point_version import PointVersion


def test_public_reads_preserve_aliases_types_order_and_isolation() -> None:
    history = PreparedGraphHistory()
    proof = dict(values=[1, 2.5, None], pair=('value', 7))
    equal = dict(values=[1, 2.5, None], pair=('value', 7))
    original = dict(first=proof, again=proof, distinct=equal, nested=[proof])
    try:
        payload = history.capture(original)
        assert payload is not None
        history.release_current()
        assert history.retained_nodes == history.memory_used == 0
        restored = cast(dict[str, object], history.restore(payload))
        assert restored == original and list(restored) == list(original)
        assert restored['first'] is restored['again']
        assert restored['first'] is not restored['distinct']
        assert cast(list[object], restored['nested'])[0] is restored['first']
        first = cast(dict[str, object], restored['first'])
        assert type(first['pair']) is tuple
        cast(list[object], first['values']).append(-1)
        assert history.restore(payload) == original
    finally:
        history.close()


def test_revisions_release_previous_roots_but_keep_old_disk_versions() -> None:
    history = PreparedGraphHistory(memory_bytes=64_000)
    snapshots: list[tuple[bytes, dict[str, object]]] = []
    try:
        for day in range(120):
            value: dict[str, object] = dict(day=day, proof=dict(text=str(day) * 100, known_at=day))
            payload = history.capture(value)
            assert payload is not None
            assert history.memory_used <= 64_000
            assert history.retained_nodes < 12
            snapshots.append((payload, value))
        history.release_current()
        for payload, expected in snapshots:
            assert history.restore(payload) == expected
    finally:
        history.close()


def test_point_version_append_visits_only_new_tree_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    first = PointVersion(dict(index=index, value=float(index)) for index in range(2048)).seal()
    appended = PointVersion([dict(index=2048, value=2048.)], previous=first, start=len(first)).seal()
    history = PreparedGraphHistory()
    try:
        old = history.capture(dict(points=first))
        assert old is not None
        written = history.written_nodes

        def reject_iteration(self: PointVersion) -> object:
            raise AssertionError('incremental graph cannot enumerate the full point version')

        monkeypatch.setattr(PointVersion, '__iter__', reject_iteration)
        new = history.capture(dict(points=appended))
        assert new is not None
        assert history.written_nodes - written < 40
        assert len(cast(dict[str, list[object]], history.restore(old))['points']) == 2048
        actual = cast(dict[str, list[dict[str, object]]], history.restore(new))['points']
        assert len(actual) == 2049 and actual[-1]['index'] == 2048
    finally:
        history.close()


def test_point_rewrite_and_truncation_never_expose_future_tail() -> None:
    shared = dict(evidence=[1, 2, 3])
    first = PointVersion(dict(index=index, proof=shared) for index in range(97)).seal()
    shortened = PointVersion(previous=first, start=33).seal()
    revised = PointVersion([dict(index=33, proof=shared, available_at=99)],
                           previous=shortened, start=33).seal()
    history = PreparedGraphHistory()
    try:
        payloads = [history.capture(dict(points=version)) for version in (first, shortened, revised)]
        assert all(payload is not None for payload in payloads)
        history.release_current()
        rows = [cast(dict[str, list[dict[str, object]]], history.restore(cast(bytes, payload)))['points']
                for payload in payloads]
        assert [len(row) for row in rows] == [97, 33, 34]
        assert 'available_at' not in rows[0][33]
        assert rows[2][33]['available_at'] == 99
        assert rows[2][0]['proof'] is rows[2][33]['proof']
    finally:
        history.close()


def test_refusal_is_atomic_and_large_inline_integer_is_charged() -> None:
    history = PreparedGraphHistory(memory_bytes=12_000)
    try:
        original = dict(value=[1, 2])
        saved = history.capture(original)
        assert saved is not None
        used, stored, nodes = history.memory_used, history.stored_bytes, history.retained_nodes
        assert history.capture(dict(small=['new'], huge=1 << 100_000)) is None
        assert (history.memory_used, history.stored_bytes, history.retained_nodes) == (used, stored, nodes)
        assert history.restore(saved) == original
        assert history.capture(dict(value=[3])) is not None
    finally:
        history.close()


def test_refused_graph_does_not_encode_or_write_partial_nodes(monkeypatch: pytest.MonkeyPatch) -> None:
    history = PreparedGraphHistory(memory_bytes=12_000)
    try:
        saved = history.capture(dict(value=1))
        assert saved is not None
        stored = history.stored_bytes

        def reject_encoding(*args: object, **kwargs: object) -> bytes:
            raise AssertionError('admission must finish before encoding any graph node')

        monkeypatch.setattr('wavequant.domain.market_structure.prepared_graph_history.pickle.dumps', reject_encoding)
        assert history.capture(dict(small=['new'], huge=dict(data=1 << 100_000))) is None
        assert history.stored_bytes == stored
        assert history.restore(saved) == dict(value=1)
    finally:
        history.close()


def test_invalid_graphs_do_not_invoke_unknown_hooks_or_damage_history() -> None:
    class Unknown:
        def __sizeof__(self) -> int:
            raise AssertionError('unknown size hook must not run')

        def __reduce__(self) -> object:
            raise AssertionError('unknown serialization hook must not run')

    cycle: list[object] = []
    cycle.append(cycle)
    history = PreparedGraphHistory()
    try:
        saved = history.capture(dict(value=1))
        assert saved is not None
        for value in (Unknown(), float('nan'), float('inf'), cycle, PointVersion([dict(index=0)]), _SequenceNode()):
            assert history.capture(dict(value=value)) is None
            assert history.restore(saved) == dict(value=1)
    finally:
        history.close()


def test_certified_source_hidden_certificates_are_in_budget() -> None:
    with owned_evidence_scope() as owner:
        source = CertifiedSource([dict(index=0)], [b'x' * 40_000], owner, None)
        history = PreparedGraphHistory(memory_bytes=12_000)
        try:
            assert history.capture(dict(points=source)) is None
            assert history.memory_used == history.stored_bytes == 0
        finally:
            history.close()


def test_foreign_payload_and_closed_store_are_rejected() -> None:
    left, right = PreparedGraphHistory(), PreparedGraphHistory()
    try:
        payload = left.capture(dict(value=1))
        assert payload is not None
        with pytest.raises(ValueError, match='request'):
            right.restore(payload)
        left.close()
        with pytest.raises(ValueError, match='closed'):
            left.restore(payload)
        with pytest.raises(ValueError, match='closed'):
            left.capture(dict(value=2))
    finally:
        left.close()
        right.close()


def test_closed_paths_use_graph_history_after_evidence_pool_refusal() -> None:
    with owned_evidence_scope(memory_bytes=0), _ClosedPreparedPaths(_owned_history=True) as paths:
        points = PointVersion([dict(index=0, proof=dict(values=[1, 2]))]).seal()
        stroke = dict(id='path', points=points, structural_legs=points)
        original = dict(source_strokes=[stroke], candidate_strokes=[stroke],
                        structure_strokes=[], strokes=[stroke], developing_strokes=[])
        payload = _prepared_payload(paths.encode(original))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert paths._graph_history is not None and paths._graph_history.refusals == 0
        actual = paths.decode(paths.load_encoded(payload))
        row = cast(list[dict[str, object]], actual['strokes'])[0]
        assert row['points'] is row['structural_legs']
        assert row['points'] == list(points)
        assert paths._point_history is not None and not paths._point_history._rows.closed


def test_point_rows_remain_readable_after_switching_to_graph_rows() -> None:
    with owned_evidence_scope() as owner, _ClosedPreparedPaths(_owned_history=True) as paths:
        points = PointVersion(dict(index=index, proof=dict(value=index)) for index in range(70)).seal()
        stroke = dict(id='path', points=points, structural_legs=points)
        original = dict(source_strokes=[stroke], candidate_strokes=[stroke],
                        structure_strokes=[], strokes=[stroke], developing_strokes=[])
        before = paths.encode(original)
        packed = cast(list[dict[str, object]], before['strokes'])[0]
        assert type(packed['points']) is PreparedPointReference
        old_payload = _prepared_payload(before)
        assert old_payload is not None
        # 仅测试强制准入额度耗尽，既有引用和磁盘行必须仍有效。
        owner._limit = owner.memory_used
        new_payload = _prepared_payload(paths.encode(original))
        assert new_payload is not None and PreparedGraphHistory.is_payload(new_payload)
        for payload in (old_payload, new_payload, old_payload):
            actual = paths.decode(paths.load_encoded(payload))
            row = cast(list[dict[str, object]], actual['strokes'])[0]
            assert row['points'] == list(points)
            assert row['points'] is row['structural_legs']
        assert paths._point_history is not None and not paths._point_history._current


def test_repeated_oversize_graphs_back_off_and_smaller_graph_recovers() -> None:
    with owned_evidence_scope(memory_bytes=0), _ClosedPreparedPaths(
            memory_bytes=4096, _owned_history=True) as paths:
        for day in range(64):
            original = dict(day=day, values=list(range(1000)))
            payload = _prepared_payload(paths.encode(original))
            assert payload is not None and not PreparedGraphHistory.is_payload(payload)
            assert paths.load_encoded(payload) == original
        history = paths._graph_history
        assert history is not None
        assert history.captures <= 8 and history.refusals == history.captures
        assert paths.graph_skipped_captures >= 56
        assert history.memory_used == history.stored_bytes == 0
        for _ in range(32):
            payload = _prepared_payload(paths.encode(dict(value=1)))
            assert payload is not None
            if PreparedGraphHistory.is_payload(payload):
                break
        else:
            raise AssertionError('a shrinking graph must recover without a new request')
        assert paths.load_encoded(payload) == dict(value=1)
        # 冷成功还不能证明下一版可增量容纳，必须再成功替换一个暖根。
        assert paths._graph_retry_delay == 32
        payload = _prepared_payload(paths.encode(dict(value=1)))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert paths.load_encoded(payload) == dict(value=1)
        assert paths._graph_retry_delay == 0
