"""首 N 的结构归属与信号消费独立于较新 N、资格及账户退出。"""

from dataclasses import replace

import pytest

from wavequant.domain.strategies.integrated_strategy import DailySignalState, _handoff_n_owners
from wavequant.domain.strategies.squeeze_n_round import SqueezeN, SqueezeNRound, SqueezeRoundState


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
