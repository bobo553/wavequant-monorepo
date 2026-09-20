"""Turning-wave boundaries, same-bar ambiguity and causal strategy integration."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup, wave_projection_history
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals


def bars_with(*extra: tuple[float, float, float, float]) -> list[Bar]:
    rows = [
        (8.5, 9, 8, 8.5),
        (10, 12, 9.5, 11),
        (10.7, 11, 10, 10.5),
        (10.8, 12.6, 10.4, 12.2),
        (12.2, 14, 12, 13.8),
        (14, 17.3, 13.5, 17.1),
        (17.2, 21.8, 17, 21.8),
        *extra,
    ]
    return [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]


SETUP = WaveProjectionSetup(0, 3, 5, 8, 12.6, 21.8, 10.4)


def test_two_t_reaches_ready_but_does_not_immediately_assert_extension():
    assert not wave_projection_history(bars_with()[:6], SETUP)
    events = wave_projection_history(bars_with(), SETUP)
    assert [(e.event, e.bar_index, e.target) for e in events] == [("wave_projection_ready", 6, None)]


def test_squeeze_confirmation_is_required_even_if_two_t_was_already_touched():
    bars = bars_with((21.8, 22, 21, 22), (22, 23, 21.5, 23))
    setup = replace(SETUP, squeeze_index=8)
    assert not wave_projection_history(bars, setup, asof_index=7)
    assert wave_projection_history(bars, setup)[0].bar_index == 8


def test_no_pullback_stacks_frozen_box_and_advances_continuously():
    events = wave_projection_history(bars_with((21.8, 24, 21, 23), (23, 35.6, 22, 35)), SETUP)
    assert events[1].state == "stacking"
    assert events[1].target == 35.6  # X12.6 + 5H(4.6)
    assert events[1].target_stage == "five_top"
    assert events[-1].reached_target == 35.6
    assert events[-1].target == 63.2  # F35.6 + (F35.6 - L8)
    assert events[-1].target_stage == "ten_full"
    assert events[-1].crossed_boxes == 1


def test_named_large_goals_do_not_replace_nearest_entry_risk_targets():
    bars = bars_with((21.8, 24, 21, 23), (23, 26.4, 22, 26))
    nearest = wave_projection_history(bars, SETUP, target_policy="nearest_box")
    assert nearest[1].target == 26.4
    assert nearest[-1].target == 31
    named = wave_projection_history(bars, SETUP)
    assert named[-1].target == 35.6
    assert named[-1].target_stage == "five_top"


def test_equal_close_and_wick_only_above_record_do_not_confirm_stacking():
    events = wave_projection_history(bars_with((21.8, 30, 21, 21.8)), SETUP)
    assert len(events) == 1


def test_confirmation_high_cannot_retroactively_hit_a_new_target():
    events = wave_projection_history(bars_with((21.8, 40, 21, 22)), SETUP)
    assert events[-1].target == 35.6
    assert events[-1].event == "wave_projection_stack"


def test_pullback_held_defense_then_fresh_attack_pushes_equal_a_wave():
    bars = bars_with((21, 21.5, 18, 19), (19, 20, 17, 18), (18, 22, 17.5, 21))
    events = wave_projection_history(bars, SETUP)
    assert events[1].state == "pullback" and events[1].target is None
    push = events[-1]
    assert push.state == "pushing" and push.bar_index == 9
    assert (push.a_high, push.a_high_index, push.b_low, push.b_low_index) == (21.8, 6, 17, 8)
    assert push.target == 30.8  # B17 + A(21.8 - 8)


def test_a_high_remains_frozen_while_c_advances_and_then_stacks():
    bars = bars_with((21, 21.5, 18, 19), (19, 20, 17, 18), (18, 22, 17.5, 21), (21, 31, 20, 30.8))
    event = wave_projection_history(bars, SETUP)[-1]
    assert event.reached_target == 30.8 and event.target == 54
    assert event.reached_stage == "five_top" and event.target_stage == "ten_full"
    assert event.a_high == 31 and event.projection_span == 23


def test_ten_full_uses_new_b_and_the_enlarged_five_top_wave():
    bars = bars_with((22, 24, 21, 23), (23, 35.6, 22, 35.6), (34, 35, 30, 31), (31, 32, 28, 29), (29, 34, 28.5, 33))
    events = wave_projection_history(bars, SETUP)
    assert events[-1].target_stage == "ten_full"
    assert events[-1].state == "pushing"
    assert events[-1].b_low == 28
    assert events[-1].projection_span == 27.6
    assert events[-1].target == 55.6  # New B28 + (F35.6 - L8).
    assert all(e.target_stage == "five_top" for e in events if e.bar_index < 8)
    for end in range(len(bars)):
        assert wave_projection_history(bars[: end + 1], SETUP) == tuple(e for e in events if e.bar_index <= end)


def test_chart_n_click_exposes_dated_five_ten_levels_and_pauses_only_the_live_stage():
    from dataclasses import asdict
    from types import SimpleNamespace
    from wavequant.interfaces.charts.visualization import ChartRepository

    bars = bars_with((22, 24, 21, 23), (23, 35.6, 22, 35.6), (34, 35, 30, 31))
    audit = [
        dict(
            event="n_completed",
            bar_index=3,
            origin=0,
            neckline=1,
            pullback=2,
            defense=10.4,
            direction="up",
            timestamp=bars[3].timestamp.isoformat(),
        )
    ]
    audit.extend(
        dict(asdict(e), timestamp=bars[e.bar_index].timestamp.isoformat()) for e in wave_projection_history(bars, SETUP)
    )
    result = SimpleNamespace(audit=audit, counts={})

    def levels(end):
        view = ChartRepository.render_theory(
            None,
            bars[: end + 1],
            SystemStrategy(),
            result,
            bars[end].timestamp.date().isoformat(),
            geometry=dict(tertiary_trends={}),
        )
        return next(e for e in view["events"] if e["event"] == "n_completed")["levels"][6:]

    assert levels(6) == []
    assert len(levels(7)) == 1 and levels(7)[0]["stage"] == "five_top"
    assert levels(7)[0]["available_at"] == bars[7].timestamp.date().isoformat()
    five, ten = levels(8)
    assert five["price"] == 35.6 and five["status"] == "已满足"
    assert ten["price"] == 63.2 and ten["stage"] == "ten_full"
    assert ten["available_at"] == bars[8].timestamp.date().isoformat()
    assert levels(9)[0]["status"] == "已满足"
    assert levels(9)[1]["status"] == "回调暂停"


def test_a_high_bar_low_is_not_assumed_to_be_a_subsequent_b_low():
    bars = bars_with((21, 24, 10.5, 20), (20, 21, 18, 19), (19, 23, 18, 22))
    events = wave_projection_history(bars, SETUP)
    assert events[1].b_low is None
    assert events[-1].b_low == 18
    assert events[-1].target == 31.8  # B18 + 3H13.8, not the A-high-bar low.


@pytest.mark.parametrize("low,invalid", [(10.4, False), (10.39, True)])
def test_defense_equality_holds_breach_wins_over_upside_and_never_revives(low: float, invalid: bool):
    bars = bars_with((21, 22, 18, 19), (19, 20, low, 18), (18, 50, 18, 49))
    events = wave_projection_history(bars, SETUP)
    assert (events[-1].state == "invalidated") is invalid
    if invalid:
        assert events[-1].bar_index == 8 and events[-1].target is None


def test_defense_breach_before_two_t_prevents_later_activation():
    bars = bars_with((22, 30, 21, 29))
    bars[5] = replace(bars[5], low=10.39)
    assert not wave_projection_history(bars, SETUP)


def test_same_bar_support_break_wins_over_reaching_the_active_target():
    events = wave_projection_history(bars_with((22, 24, 21, 23), (23, 40, 10.39, 39)), SETUP)
    assert events[-1].state == "invalidated"
    assert events[-1].target is None
    assert not any(e.event == "wave_projection_target_reached" for e in events)


@pytest.mark.parametrize("low,close", [(16, 21), (17.5, 20)])
def test_new_b_low_or_equal_breakout_close_cannot_confirm_reattack(low: float, close: float):
    events = wave_projection_history(bars_with((21, 21.5, 18, 19), (19, 20, 17, 18), (18, 22, low, close)), SETUP)
    assert events[-1].state == "pullback"
    assert not any(e.event == "wave_projection_push" for e in events)


def test_large_gap_crosses_boxes_in_one_event_without_unbounded_loop():
    events = wave_projection_history(bars_with((22, 1000001, 22, 1000000)), SETUP)
    assert len(events) == 3
    assert events[-1].crossed_boxes == 1  # Only the previously known stage can be hit.
    assert events[-1].target > 1000000


def test_new_pullback_suspends_old_target_even_if_same_bar_high_reaches_it():
    bars = bars_with((22, 24, 21, 23), (23, 27, 19, 22))
    events = wave_projection_history(bars, SETUP)
    assert events[-1].state == "pullback" and events[-1].target is None
    assert not any(e.event == "wave_projection_target_reached" for e in events)


def test_full_history_matches_every_prefix_including_future_invalidation():
    bars = bars_with((22, 24, 21, 23), (23, 26.4, 22, 26), (25, 26, 18, 20), (20, 25, 19, 24), (24, 45, 10, 40))
    full = wave_projection_history(bars, SETUP)
    for i in range(len(bars)):
        assert wave_projection_history(bars[: i + 1], SETUP) == tuple(e for e in full if e.bar_index <= i)
        assert wave_projection_history(bars, SETUP, asof_index=i) == tuple(e for e in full if e.bar_index <= i)


def test_invalid_future_bars_are_not_read_by_asof():
    bars = bars_with((22, 1, -1, 100))
    assert wave_projection_history(bars, SETUP, asof_index=6) == wave_projection_history(bars[:7], SETUP)


def test_invalid_anchor_geometry_is_rejected():
    with pytest.raises(ValueError):
        replace(SETUP, two_t=22)
    with pytest.raises(ValueError):
        replace(SETUP, squeeze_index=3)


def test_v3_integration_publishes_targets_after_entry_ttl_without_changing_legacy():
    from tests.test_integrated_strategy import fixture

    bars = fixture()
    for price in range(19, 40, 2):
        bars.append(
            Bar(bars[-1].timestamp + timedelta(days=1), "TEST", price - 1, price + 1, price - 1.5, price, 1000000)
        )
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        volume_filter=False,
        pattern_ttl=5,
        volume_lookback=3,
    )
    full = generate_system_signals(bars, config)
    events = [e for e in full.audit if e["event"].startswith("wave_projection_")]
    assert events
    assert any(e["bar_index"] > e["attack"] + config.pattern_ttl for e in events)
    assert any(e["projection_label"] == "叠箱" and e["target"] is not None for e in events)
    for end in (15, 18, len(bars)):
        prefix = generate_system_signals(bars[:end], config)
        assert [e for e in prefix.audit if e["event"].startswith("wave_projection_")] == [
            e for e in events if e["bar_index"] < end
        ]
    old = generate_system_signals(bars, replace(config, buy_point_definition="legacy_v2"))
    assert not any(e["event"].startswith("wave_projection_") for e in old.audit)
