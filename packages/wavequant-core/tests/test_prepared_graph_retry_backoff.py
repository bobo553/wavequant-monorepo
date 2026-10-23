"""单图可容纳但版本暂存超限时，冷成功不能重置失败退避。"""

from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.trend_lines import _ClosedPreparedPaths, _prepared_payload


def test_first_cold_capture_and_normal_warm_updates_keep_retry_disabled() -> None:
    with owned_evidence_scope(memory_bytes=0), _ClosedPreparedPaths(_owned_history=True) as paths:
        shared = dict(proof=[1, 2, 3])
        for day in range(8):
            original = dict(day=day, shared=shared)
            payload = _prepared_payload(paths.encode(original))
            assert payload is not None and PreparedGraphHistory.is_payload(payload)
            assert paths.load_encoded(payload) == original
            assert paths._graph_retry_delay == paths._graph_retry_remaining == 0
        assert paths._graph_history is not None and paths._graph_history.refusals == 0
        assert paths.graph_skipped_captures == 0


def test_alternating_warm_refusal_and_cold_success_retains_backoff() -> None:
    with owned_evidence_scope(memory_bytes=0), _ClosedPreparedPaths(_owned_history=True) as paths:
        shared = dict(proof='x' * 4000)
        initial = dict(day=0, shared=shared)
        old_payload = _prepared_payload(paths.encode(initial))
        assert old_payload is not None and PreparedGraphHistory.is_payload(old_payload)
        history = paths._graph_history
        assert history is not None and paths._graph_retry_delay == 0
        # 每一日单图的计费相同且可容纳，旧根与新根暂存则超预算。
        history._limit = history.memory_used
        limit = history._limit
        for day in range(1, 65):
            original = dict(day=day, shared=shared)
            payload = _prepared_payload(paths.encode(original))
            assert payload is not None and paths.load_encoded(payload) == original
            assert history.memory_used <= limit
        assert history.captures <= 14
        assert paths.graph_skipped_captures >= 50
        assert paths._graph_retry_delay == 32
        assert paths.load_encoded(old_payload) == initial

        for day in range(65, 97):
            smaller = dict(day=day)
            payload = _prepared_payload(paths.encode(smaller))
            assert payload is not None and paths.load_encoded(payload) == smaller
            if PreparedGraphHistory.is_payload(payload):
                break
        else:
            raise AssertionError('a smaller graph must recover within the existing retry bound')
        assert paths._graph_retry_delay == 32
        payload = _prepared_payload(paths.encode(dict(day=day + 1)))
        assert payload is not None and PreparedGraphHistory.is_payload(payload)
        assert paths._graph_retry_delay == paths._graph_retry_remaining == 0
        assert history.memory_used <= limit
        assert paths.load_encoded(old_payload) == initial
