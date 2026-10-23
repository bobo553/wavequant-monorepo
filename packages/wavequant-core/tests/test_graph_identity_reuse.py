"""身份复用不改变共享对象计费、历史恢复和预算拒绝。"""

from sys import getsizeof
import builtins

import pytest

from wavequant.domain.market_structure.owned_evidence import owned_evidence_scope
from wavequant.domain.market_structure import owned_evidence, prepared_graph_history
from wavequant.domain.market_structure.prepared_graph_history import PreparedGraphHistory
from wavequant.domain.market_structure.trend_judgement import _FormalKeyBreakState, _TrendJudgementState


def test_budget_visitors_reuse_identity_and_charge_owned_alias_once(monkeypatch: pytest.MonkeyPatch) -> None:
    with owned_evidence_scope() as owner:
        child = {'proof': list(range(1000))}
        owner.publish(child)
        assert owner.reference(child) is not None
        root = [child, child]

        def repeated_lookup(value: object) -> int | None:
            raise AssertionError('budget visitor already has the object identity')

        monkeypatch.setattr(owner, 'reference', repeated_lookup)
        assert _FormalKeyBreakState._value_bytes([root]) == getsizeof(root) + 8
        state = _TrendJudgementState(1)
        assert state._owned_value_bytes([root]) == getsizeof(root) + 8


def test_graph_identity_frames_keep_historical_aliases_and_atomic_refusal() -> None:
    history = PreparedGraphHistory()
    try:
        shared = {'values': list(range(1000))}
        first = history.capture({'left': shared, 'right': shared})
        assert first is not None
        second = history.capture({'left': shared, 'tail': {'value': 1001}})
        assert second is not None
        restored = history.restore(first)
        assert isinstance(restored, dict)
        assert restored['left'] is restored['right']
        assert history.restore(second) == {'left': shared, 'tail': {'value': 1001}}
        used, size = history.memory_used, history.stored_bytes
        cycle: dict[str, object] = {}
        cycle['cycle'] = cycle
        assert history.capture(cycle) is None
        assert history.memory_used == used and history.stored_bytes == size
        assert history.restore(first) == {'left': shared, 'right': shared}
    finally:
        history.close()


def test_publish_reuses_container_identity_through_registration(monkeypatch: pytest.MonkeyPatch) -> None:
    root = {'left': [1001, 1002], 'right': [1003]}
    visits: dict[int, int] = {}

    def counted(value: object) -> int:
        identity = builtins.id(value)
        visits[identity] = visits.get(identity, 0) + 1
        return identity

    with owned_evidence_scope() as owner:
        monkeypatch.setattr(owned_evidence, 'id', counted, raising=False)
        owner.publish(root)
        # 根的快速命中查询另取一次；离开帧和登记不能再次取身份。
        assert visits[builtins.id(root)] == 2
        assert visits[builtins.id(root['left'])] == 1
        assert visits[builtins.id(root['right'])] == 1
        payload = owner.dumps(root)
        assert owner.loads(payload) == root


def test_history_write_resolves_admitted_edges_without_reidentifying(monkeypatch: pytest.MonkeyPatch) -> None:
    history = PreparedGraphHistory()
    root = {'left': [1001, 1002], 'right': [1003]}
    visits: dict[int, int] = {}

    def counted(value: object) -> int:
        identity = builtins.id(value)
        visits[identity] = visits.get(identity, 0) + 1
        return identity

    try:
        monkeypatch.setattr(prepared_graph_history, 'id', counted, raising=False)
        payload = history.capture(root)
        assert payload is not None
        assert visits[builtins.id(root)] == 1
        assert visits[builtins.id(root['left'])] == 1
        assert visits[builtins.id(root['right'])] == 1
        assert history.restore(payload) == root
    finally:
        history.close()
