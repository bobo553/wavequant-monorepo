"""一级趋势的结构 N 所有权不因交易资格、后续底点或失效而重复分配。"""

from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.bottom_n_targets import DeclineStart
from wavequant.domain.market_structure.structural_n_ownership import StructuralNOwnership
from wavequant.domain.models.model import Bar


def _bars(lows: Sequence[float]) -> list[Bar]:
    return [
        Bar(datetime(2024, 1, 1) + timedelta(days=index), 'TEST',
            low + 1, low + 2, low, low + 1.5, 100)
        for index, low in enumerate(lows)
    ]


def _observe_days(ownership: StructuralNOwnership, start: int, end: int,
                  declines: Sequence[DeclineStart] = (), *, epoch: int = 0) -> None:
    for index in range(start, end + 1):
        ownership.observe(index, epoch, [decline for decline in declines if decline.known_at <= index])


def test_same_trend_rejects_different_sources_and_later_successful_n() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 6, 8, 9]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])
    first = ownership.select(epoch=0, origin=1, attack=3, identity='source-a:window-2:first')
    duplicate = ownership.select(epoch=0, origin=1, attack=3, identity='source-b:window-3:same-n')
    _observe_days(ownership, 4, 6, [DeclineStart(0, 1)])
    later = ownership.select(epoch=0, origin=1, attack=6, identity='source-a:window-2:later-n')

    assert first.selected
    assert first.bottom_index == 1
    for rejected in (duplicate, later):
        assert not rejected.selected
        assert rejected.owner == first.owner
        assert rejected.trend_id == first.trend_id
        assert rejected.reason == 'trend_already_has_successful_n'


@pytest.mark.parametrize('origins', [(2, 1), (1, 2)])
def test_internal_higher_bottom_cannot_steal_owner_regardless_of_candidate_order(
    origins: tuple[int, int],
) -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 8]))
    _observe_days(ownership, 0, 4, [DeclineStart(0, 1)])
    decisions = {
        origin: ownership.select(epoch=0, origin=origin, attack=4, identity=f'origin-{origin}')
        for origin in origins
    }

    assert decisions[1].selected
    assert decisions[1].bottom_index == 1
    assert not decisions[2].selected
    assert decisions[2].decline_index == 0
    if origins[0] == 2:
        assert decisions[2].reason == 'origin_is_not_decline_floor'
        assert decisions[2].bottom_index == 1


def test_strictly_lower_bottom_does_not_restart_a_consumed_trend() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 4, 5, 6]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])
    first = ownership.select(epoch=0, origin=1, attack=3, identity='before-origin-break')
    _observe_days(ownership, 4, 6, [DeclineStart(0, 1)])
    later = ownership.select(epoch=0, origin=4, attack=6, identity='after-origin-break')

    assert first.selected
    assert not later.selected
    assert later.owner == first.owner
    assert later.trend_id == first.trend_id
    assert later.reason == 'trend_already_has_successful_n'


def test_qualification_gap_and_reopening_do_not_erase_owner_or_decline_history() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 6, 7, 8]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])
    first = ownership.select(epoch=0, origin=1, attack=3, identity='path-a:window-2')
    _observe_days(ownership, 4, 5)
    ownership.observe(6, 0, [DeclineStart(0, 1)])
    reopened = ownership.select(epoch=0, origin=1, attack=3, identity='path-a:window-6')

    assert first.selected
    assert not reopened.selected
    assert reopened.owner == first.owner
    assert reopened.trend_id == first.trend_id


def test_equal_lows_keep_earliest_floor_and_do_not_consume_owner_on_rejection() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 5, 7]))
    _observe_days(ownership, 0, 4, [DeclineStart(0, 1)])
    repeated_low = ownership.select(epoch=0, origin=3, attack=4, identity='repeated-equal-low')
    original_low = ownership.select(epoch=0, origin=1, attack=4, identity='earliest-low')

    assert not repeated_low.selected
    assert repeated_low.bottom_index == 1
    assert repeated_low.reason == 'origin_is_not_decline_floor'
    assert original_low.selected
    assert original_low.bottom_index == 1


def test_new_confirmed_decline_after_original_attack_gets_its_own_owner() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 10, 6, 7, 8]))
    declines = [DeclineStart(0, 1), DeclineStart(4, 6)]
    _observe_days(ownership, 0, 3, declines)
    first = ownership.select(epoch=0, origin=1, attack=3, identity='first-trend')
    _observe_days(ownership, 4, 7, declines)
    following = ownership.select(epoch=0, origin=5, attack=7, identity='next-trend')

    assert first.selected and following.selected
    assert (first.decline_index, following.decline_index) == (0, 4)
    assert (first.bottom_index, following.bottom_index) == (1, 5)
    assert first.trend_id != following.trend_id
    assert following.owner == 'next-trend'


@pytest.mark.parametrize('reverse', [False, True])
def test_late_window_assigns_historical_n_by_origin_without_borrowing_later_bottom(
    reverse: bool,
) -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 10, 8, 4, 6, 7]))
    declines = [DeclineStart(0, 1), DeclineStart(4, 6)]
    _observe_days(ownership, 0, 7)
    ownership.observe(8, 0, list(reversed(declines)) if reverse else declines)
    historical = ownership.select(epoch=0, origin=1, attack=3, identity='old-window-discovered-late')
    recent = ownership.select(epoch=0, origin=6, attack=8, identity='recent-trend')

    assert historical.selected and recent.selected
    assert historical.decline_index == 0
    assert historical.bottom_index == 1
    assert recent.decline_index == 4
    assert recent.bottom_index == 6
    assert historical.trend_id != recent.trend_id


def test_late_decline_inside_frozen_n_cannot_split_out_another_owner() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 8, 9, 6, 7]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])
    first = ownership.select(epoch=0, origin=1, attack=3, identity='frozen-launch')
    _observe_days(ownership, 4, 5, [DeclineStart(0, 1), DeclineStart(2, 4)])
    internal = ownership.select(epoch=0, origin=4, attack=5, identity='late-internal-decline')

    assert first.selected
    assert not internal.selected
    assert internal.decline_index == 0
    assert internal.owner == first.owner
    assert internal.trend_id == first.trend_id


def test_missing_decline_cannot_invent_structural_target_ownership() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7]))
    _observe_days(ownership, 0, 3)
    result = ownership.select(epoch=0, origin=1, attack=3, identity='unconfirmed')

    assert not result.selected
    assert result.owner is None
    assert result.trend_id is None
    assert result.reason == 'confirmed_decline_unavailable'


def test_cross_trend_folded_n_cannot_consume_the_following_real_floor() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 10, 6, 7, 8, 9]))
    _observe_days(ownership, 0, 8, [DeclineStart(0, 1), DeclineStart(4, 6)])
    cross_trend = ownership.select(epoch=0, origin=1, attack=7, identity='cross-trend')
    following = ownership.select(epoch=0, origin=5, attack=8, identity='following-floor')

    assert not cross_trend.selected
    assert cross_trend.reason == 'crosses_confirmed_decline'
    assert following.selected and following.decline_index == 4


def test_future_decline_proof_is_rejected_before_it_can_grant_a_target() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 8]))
    _observe_days(ownership, 0, 2)

    with pytest.raises(ValueError, match='known level-one declines'):
        ownership.observe(3, 0, [DeclineStart(0, 4)])

    ownership.observe(3, 0, [])
    result = ownership.select(epoch=0, origin=1, attack=3, identity='future-proof')
    assert not result.selected
    assert result.owner is None


def test_future_attack_is_rejected_without_consuming_known_trend() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 8]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])

    with pytest.raises(ValueError, match='completed known N'):
        ownership.select(epoch=0, origin=1, attack=4, identity='future-attack')

    known = ownership.select(epoch=0, origin=1, attack=3, identity='known-attack')
    assert known.selected
    assert known.owner == 'known-attack'


def test_old_epoch_cannot_publish_a_new_owner_after_market_interruption() -> None:
    ownership = StructuralNOwnership(_bars([10, 5, 6, 7, 10, 4, 5, 6]))
    _observe_days(ownership, 0, 3, [DeclineStart(0, 1)])
    first = ownership.select(epoch=0, origin=1, attack=3, identity='before-interruption')
    _observe_days(ownership, 4, 7, [DeclineStart(4, 5)], epoch=1)

    with pytest.raises(ValueError, match='current epoch'):
        ownership.select(epoch=0, origin=1, attack=3, identity='stale-epoch')

    following = ownership.select(epoch=1, origin=5, attack=7, identity='after-interruption')
    assert first.selected and following.selected
    assert following.decline_index == 4
    assert first.trend_id != following.trend_id
