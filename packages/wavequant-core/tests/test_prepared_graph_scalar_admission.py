"""历史图内嵌标量只随父记录认证，不为上万叶子建立重复访问。"""

from typing import cast
from unittest.mock import Mock

import pytest

from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory


def test_large_scalar_proof_needs_only_container_references(monkeypatch: pytest.MonkeyPatch) -> None:
    history = PreparedGraphHistory()
    references: list[object] = []
    original = history._token

    def token(value: object) -> tuple[str, object]:
        references.append(value)
        return original(value)

    monkeypatch.setattr(history, '_token', token)
    shared = list(range(12_000)) + [True, False, None, -0.0, 1 << 4096]
    graph = dict(first=shared, again=shared, pair=(1, 2.5))
    try:
        payload = history.capture(graph)
        assert payload is not None
        assert len(references) < 20
        assert all(type(value) not in (int, float, bool, type(None)) for value in references)
        restored = cast(dict[str, object], history.restore(payload))
        assert restored == graph
        assert restored['first'] is restored['again']
        assert type(restored['pair']) is tuple
    finally:
        history.close()


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf')])
def test_nonfinite_inline_leaf_keeps_prior_history_atomic(bad: float) -> None:
    history = PreparedGraphHistory()
    try:
        first = history.capture(dict(value=[1, 2]))
        assert first is not None
        usage = history.memory_used, history.stored_bytes, history.retained_nodes
        assert history.capture(dict(value=[*range(12_000), bad])) is None
        assert (history.memory_used, history.stored_bytes, history.retained_nodes) == usage
        assert history.restore(first) == dict(value=[1, 2])
    finally:
        history.close()


def test_many_new_nodes_write_contiguous_blocks_and_retain_old_offsets() -> None:
    history = PreparedGraphHistory()
    stream = history._stream
    counted = Mock(wraps=stream)
    counted.closed = False
    history._stream = counted
    try:
        first = history.capture(dict(points=[dict(index=index, value=index + .5) for index in range(2000)]))
        assert first is not None
        assert history.written_nodes > 2000
        assert counted.seek.call_count == 1
        assert counted.write.call_count < 10
        second = history.capture(dict(points=[dict(index=2001, value=2001.5)]))
        assert second is not None
        restored = cast(dict[str, list[dict[str, object]]], history.restore(first))
        assert len(restored['points']) == 2000
        assert restored['points'][-1] == dict(index=1999, value=1999.5)
        assert history.restore(second) == dict(points=[dict(index=2001, value=2001.5)])
    finally:
        history.close()


@pytest.mark.parametrize('failed_write', (1, 2))
def test_failed_buffered_write_rolls_back_node_table_and_offsets(failed_write: int) -> None:
    history = PreparedGraphHistory()
    try:
        first = history.capture(dict(value=[1, 2]))
        assert first is not None
        usage = history.memory_used, history.stored_bytes, history.retained_nodes
        stream = history._stream
        faulty = Mock(wraps=stream)
        faulty.closed = False
        writes = 0

        def interrupted(data: bytes | bytearray) -> int:
            nonlocal writes
            writes += 1
            if writes == failed_write:
                raise OSError('test interrupted write')
            return stream.write(data)

        faulty.write.side_effect = interrupted
        history._stream = faulty
        with pytest.raises(OSError, match='interrupted write'):
            history.capture(dict(value=[dict(index=index, price=index + .5) for index in range(2000)]))
        history._stream = stream
        assert (history.memory_used, history.stored_bytes, history.retained_nodes) == usage
        assert history.restore(first) == dict(value=[1, 2])
        assert history.capture(dict(value=[5, 6])) is not None
    finally:
        history.close()
