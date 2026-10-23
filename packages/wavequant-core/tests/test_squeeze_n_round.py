"""首 N 的结构归属与信号消费独立于较新 N、资格及账户退出。"""

from dataclasses import replace

import pytest

from wavequant.domain.strategies.integrated_strategy import DailySignalState, _handoff_n_owners
from wavequant.domain.strategies.squeeze_n_round import (
    SqueezeN, SqueezeNRound, SqueezeRoundState, joint_squeeze_confirmations,
)


def first_n() -> SqueezeN:
    return SqueezeN(origin=0, neckline=1, pullback=2, attack=3, known_at=3, defense=10.0)


def later_n(attack: int = 7) -> SqueezeN:
    return SqueezeN(origin=4, neckline=5, pullback=6, attack=attack, known_at=attack, defense=12.0)


def event(index: int, fields: dict[str, object] | None) -> dict[str, object]:
    assert fields is not None
    return dict(event='squeeze_round_changed', bar_index=index, **fields)


def test_first_n_owns_confirmation_before_and_after_buy_signal() -> None:
    round_state = SqueezeNRound()
    first, later = first_n(), later_n()
    assert round_state.select(3, (later, first)) is not None
    assert round_state.state.owner == first
    assert round_state.rejection(first) is None
    assert round_state.rejection(later) == 'squeeze_requires_first_n'
    assert round_state.select(7, (later,)) is None
    assert round_state.observe_low(7, 11.0) is None
    round_state.consume(first, 8)
    for candidate in (first, later):
        assert round_state.rejection(candidate) == 'first_n_squeeze_already_emitted'
    assert round_state.state.owner is first
    assert round_state.state.emitted_at == 8


@pytest.mark.parametrize('low', (10.0, 10.01, 11.99))
def test_touch_and_breaking_only_newer_n_low_do_not_release_owner(low: float) -> None:
    round_state = SqueezeNRound()
    first = first_n()
    round_state.select(3, (first,))
    round_state.consume(first, 4)
    assert round_state.observe_low(9, low) is None
    assert round_state.select(9, (later_n(),)) is None
    assert round_state.state.owner == first
    assert round_state.state.owner.defense == 10.0


def test_strict_low_break_retires_old_and_waits_for_post_break_attack() -> None:
    round_state = SqueezeNRound()
    first = first_n()
    round_state.select(3, (first,))
    round_state.consume(first, 4)
    broken = round_state.observe_low(8, 9.99)
    assert broken is not None and broken['broken_owner_defense'] == 10.0
    assert round_state.state.owner is None
    # 日线没有先破低后突破的分钟顺序，不能用同日新 N 立刻重开。
    stale = replace(later_n(), known_at=10)
    assert round_state.select(10, (first, stale, later_n(8))) is None
    assert round_state.rejection(stale) == 'squeeze_waiting_for_new_n_after_defense_break'
    assert round_state.select(10, (later_n(9),)) is None
    fresh = replace(later_n(9), neckline=7, pullback=8)
    assert round_state.select(10, (fresh,)) is not None
    assert round_state.rejection(fresh) is None
    round_state.consume(fresh, 11)
    assert round_state.state.broken_at == 8
    assert round_state.state.emitted_at == 11


def test_future_candidate_and_retroactive_low_cannot_mutate_new_owner() -> None:
    round_state = SqueezeNRound()
    first = replace(first_n(), known_at=5)
    assert round_state.select(4, (first,)) is None
    round_state.select(5, (first,))
    # 收盘才知道的 N 不能假装在当日更早的最低价时已经存在。
    assert round_state.observe_low(5, 9.0) is None
    assert round_state.observe_low(6, 9.0) is not None


def test_consumed_round_survives_owner_handoff_expiry_and_qualification_close() -> None:
    state = DailySignalState()
    first = first_n()
    state.squeeze_round.select(3, (first,))
    state.squeeze_round.consume(first, 4)
    state.canonical_n_owners = {3: 'old-source'}
    state.emitted_attacks.add(3)
    _handoff_n_owners(state, {}, {})
    assert 3 not in state.emitted_attacks
    assert state.squeeze_round.select(20, ()) is None
    assert state.squeeze_round.close_unemitted() is None
    assert state.squeeze_round.rejection(later_n(21)) == 'first_n_squeeze_already_emitted'
    assert state.squeeze_round.observe_low(22, 9.9) is not None
    assert state.squeeze_round.select(23, (replace(later_n(23), neckline=21, pullback=22),)) is not None


def test_pending_owner_closes_with_qualification_without_fabricating_price_break() -> None:
    round_state = SqueezeNRound()
    round_state.select(3, (first_n(),))
    closed = round_state.close_unemitted()
    assert closed is not None
    assert closed['reason'] == 'unemitted_first_n_qualification_closed'
    assert round_state.state == SqueezeRoundState()


def test_minute_checkpoint_excludes_current_day_break_and_does_not_mutate_daily_state() -> None:
    daily = SqueezeNRound()
    first = first_n()
    rows = [event(3, daily.select(3, (first,))), event(4, daily.consume(first, 4))]
    rows.append(event(8, daily.observe_low(8, 9.9)))
    minute = SqueezeNRound(SqueezeRoundState.before(rows, 8))
    assert minute.state.owner == first and minute.state.emitted_at == 4
    assert minute.observe_low(8, 10.1) is None
    assert minute.rejection(later_n()) == 'first_n_squeeze_already_emitted'
    assert daily.state.owner is None
    assert minute.observe_low(8, 9.99) is not None
    assert SqueezeRoundState.before(rows, 9) == daily.state


def test_same_day_selection_and_emission_restore_last_published_state() -> None:
    daily = SqueezeNRound()
    first = first_n()
    rows = [event(3, daily.select(3, (first,))), event(3, daily.consume(first, 3))]
    assert SqueezeRoundState.before(rows, 3) == SqueezeRoundState()
    assert SqueezeRoundState.before(rows, 4) == daily.state
    with pytest.raises(ValueError, match='unconsumed first N'):
        daily.consume(later_n(), 8)


def joint_confirmation() -> dict[str, object]:
    return dict(event='trend_lifecycle_state', state='空多交替', trend_level=2, source_path='original-path',
        bar_index=5, alternation_index=5, flip_index=0, flip_known_index=1, counter_index=2, flip_confirmed=True,
        confirmation_rule='positive_n_and_squeeze_after_qualified_b',
        confirmation_evidence=dict(source_path='original-path', trend_level=2, bar_index=5, candidate_index=5,
            n_origin=0, n_neckline=1, n_pullback=2, attack=3))


@pytest.mark.parametrize('rule', [
    'positive_n_and_squeeze_after_qualified_b',
    'double_volume_positive_n_squeeze_whole_flip_pullback',
])
def test_joint_confirmation_only_admits_exact_n_in_its_own_window(rule: str) -> None:
    row = dict(joint_confirmation(), confirmation_rule=rule)
    windows = joint_squeeze_confirmations([row], 2)
    first = replace(first_n(), known_at=5)
    assert ('original-path', 5, first.identity) in windows
    assert ('other-path', 5, first.identity) not in windows
    assert ('original-path', 6, first.identity) not in windows
    for coordinate in ('origin', 'neckline', 'pullback', 'attack'):
        other = replace(first, **{coordinate: getattr(first, coordinate) + 1})
        assert ('original-path', 5, other.identity) not in windows
    assert not joint_squeeze_confirmations([joint_confirmation()], 1)
    assert not joint_squeeze_confirmations([joint_confirmation()], 3)
    round_state = SqueezeNRound()
    assert round_state.select(4, (first,)) is None
    assert round_state.select(5, (first,)) is not None
    round_state.consume(first, 5)
    assert round_state.close_unemitted() is None
    assert round_state.select(6, (later_n(),)) is None
    assert round_state.rejection(first) == 'first_n_squeeze_already_emitted'


@pytest.mark.parametrize('neckline,pullback', [(0, 2), (1, 1), (1, 3)])
def test_joint_confirmation_preserves_explicit_shared_edge_n(neckline: int, pullback: int) -> None:
    row = joint_confirmation()
    proof = row['confirmation_evidence']
    assert isinstance(proof, dict)
    proof.update(n_neckline=neckline, n_pullback=pullback)
    assert ('original-path', 5, (0, neckline, pullback, 3)) in joint_squeeze_confirmations([row], 2)


@pytest.mark.parametrize('field,value', [
    ('flip_confirmed', False), ('flip_known_index', 6), ('alternation_index', 4),
    ('state', '转多确认'), ('confirmation_rule', 'volume_bullish_polyline_last_fall_high_breakout'),
    ('source_path', 'other-path'), ('confirmation_evidence', None),
])
def test_joint_confirmation_rejects_unconfirmed_future_and_unrelated_evidence(field: str, value: object) -> None:
    row = joint_confirmation()
    row[field] = value
    assert not joint_squeeze_confirmations([row], 2)


@pytest.mark.parametrize('field,value', [
    ('n_neckline', None), ('n_origin', -1), ('n_pullback', 0), ('attack', 6),
    ('bar_index', 6), ('candidate_index', 4), ('trend_level', 3), ('source_path', 'other-path'),
])
@pytest.mark.parametrize('rule', [
    'positive_n_and_squeeze_after_qualified_b',
    'double_volume_positive_n_squeeze_whole_flip_pullback',
])
def test_joint_confirmation_requires_complete_causal_coordinates(field: str, value: object, rule: str) -> None:
    row = dict(joint_confirmation(), confirmation_rule=rule)
    proof = row['confirmation_evidence']
    assert isinstance(proof, dict)
    proof[field] = value
    assert not joint_squeeze_confirmations([row], 2)


@pytest.mark.parametrize('level', (1, 2, 3))
def test_joint_squeeze_opens_qualification_and_owns_same_close(level: int) -> None:
    from wavequant.domain.strategies.trading_intervals import TradingEligibility

    gate = TradingEligibility(level)
    row = dict(joint_confirmation(), trend_level=level, counter_price=10.0)
    proof = dict(row['confirmation_evidence'], trend_level=level,
                 confirmation_source='positive_n_squeeze_polyline_key_breakout')
    row['confirmation_evidence'] = proof
    assert not gate.active
    assert gate.observe(row) is True
    assert gate.active_intervals[0].start_index == 5
    assert gate.active_intervals[0].start_index != 3
    windows = joint_squeeze_confirmations([row], level)
    first = replace(first_n(), known_at=5)
    assert ('original-path', 5, first.identity) in windows
    round_state = SqueezeNRound()
    assert round_state.select(4, (first,)) is None
    assert round_state.select(5, (first,)) is not None
    round_state.consume(first, 5)
    assert round_state.state.emitted_at == gate.active_intervals[0].start_index
    assert gate.observe(row) is None
    assert len(gate.active_intervals) == 1
    assert round_state.rejection(first) == 'first_n_squeeze_already_emitted'
