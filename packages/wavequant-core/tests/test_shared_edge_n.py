"""Shared outside edges must retain causal N confirmation and fresh attacks."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef, observe_n,
)
from wavequant.domain.market_structure.polyline import teaching_inside, teaching_outside
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar


def shared_edge_sample(edge="low", direction=Direction.UP):
    rows = [
        (5.37, 5.38, 5.31, 5.32),
        (5.33, 5.46, 5.32, 5.33),
        (5.32, 5.48, 5.32, 5.44),
        (5.44, 5.71, 5.41, 5.67),
    ]
    if edge == "high":
        rows[2] = (5.32, 5.46, 5.315, 5.44)
    elif edge == "strict":
        rows[2] = (5.32, 5.48, 5.315, 5.44)
    bars = [
        Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *values, 1000)
        for i, values in enumerate(rows)
    ]
    if direction == Direction.DOWN:
        bars = [replace(bar, open=20-bar.open, high=20-bar.low,
                        low=20-bar.high, close=20-bar.close) for bar in bars]
    setup = NSetup(
        "TEST", "1d", direction, PivotRef(0, 1), PivotRef(1, 2), PivotRef(2, 2),
        "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
        allow_confirmation_bar=True, allow_outside_close=True, staged_defense=True,
    )
    return bars, setup


def observe(bars, setup, **kwargs):
    return observe_n(bars, setup, timeframe="1d",
                     milestone_basis=MilestoneBasis.EXTREME, **kwargs)


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
@pytest.mark.parametrize("edge", ["low", "high", "strict"])
def test_shared_outside_edge_waits_for_fresh_attack_after_same_day_confirmation(edge, direction):
    bars, setup = shared_edge_sample(edge, direction)
    assert teaching_outside(bars[1], bars[2])
    child = replace(bars[1], timestamp=bars[3].timestamp)
    assert teaching_inside(bars[2], child)
    forming = observe(bars[:3], setup)
    assert forming.status == NStatus.FORMING
    assert forming.completion is None
    completed = observe(bars, setup)
    assert completed.status == NStatus.COMPLETED
    assert completed.completion.bar_index == 3
    assert completed.anchors.known_at_index == 2
    defense = bars[2].low if direction == Direction.UP else bars[2].high
    assert completed.completion.defense == pytest.approx(defense)
    assert completed.targets.box_anchor == pytest.approx(
        bars[3].high if direction == Direction.UP else bars[3].low)
    assert observe(bars, setup, asof_index=2) == forming


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_close_cross_can_complete_on_the_known_c_close(direction):
    bars, setup = shared_edge_sample(direction=direction)
    closing = 5.47 if direction == Direction.UP else 20-5.47
    bars[2] = replace(bars[2], close=closing)
    completion = observe(bars[:3], setup)
    assert completion.status == NStatus.COMPLETED
    assert completion.completion.bar_index == 2
    assert observe(bars[:2], setup).status == NStatus.AWAIT_ANCHORS
    assert observe(bars, setup).completion == completion.completion


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_fresh_virtual_attack_does_not_require_close_beyond_the_virtual_level(direction):
    bars, setup = shared_edge_sample(direction=direction)
    closing = 5.45 if direction == Direction.UP else 20-5.45
    bars[3] = replace(bars[3], close=closing)
    result = observe(bars, setup)
    assert result.status == NStatus.COMPLETED
    assert result.completion.bar_index == 3
    assert result.completion.real_break and result.completion.virtual_break
    sign = 1 if direction == Direction.UP else -1
    assert sign*(bars[3].close-bars[1].close) > 0
    virtual_level = bars[1].high if direction == Direction.UP else bars[1].low
    assert sign*(bars[3].close-virtual_level) <= 0


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_exception_keeps_strict_source_and_explicit_policy(direction):
    bars, setup = shared_edge_sample(direction=direction)
    for incompatible in (
        replace(setup, allow_outside_close=False),
        replace(setup, source="strict_polyline"),
    ):
        with pytest.raises(ValueError, match="neckline is not the extreme"):
            observe(bars, incompatible)


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_cannot_borrow_future_confirmation_or_opposing_body(direction):
    bars, setup = shared_edge_sample(direction=direction)
    delayed = replace(setup, pullback=PivotRef(2, 3))
    assert observe(bars[:3], delayed).status == NStatus.AWAIT_ANCHORS
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe(bars, delayed)
    for opening in (bars[2].close, bars[2].close + (.01 if direction == Direction.UP else -.01)):
        changed = [*bars[:2], replace(bars[2], open=opening), bars[3]]
        with pytest.raises(ValueError, match="neckline is not the extreme"):
            observe(changed, setup)


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_excursion_does_not_replay_already_occupied_thresholds(direction):
    bars, setup = shared_edge_sample(direction=direction)
    intermediate = Bar(datetime(2026, 1, 3), "TEST", 5.34, 5.46, 5.33, 5.40, 1000)
    c = replace(bars[2], timestamp=datetime(2026, 1, 4), low=5.33, open=5.40, close=5.47)
    if direction == Direction.DOWN:
        intermediate = replace(intermediate, open=20-intermediate.open, high=20-intermediate.low,
                               low=20-intermediate.high, close=20-intermediate.close)
        c = replace(bars[2], timestamp=datetime(2026, 1, 4), high=20-5.33,
                    open=20-5.40, close=20-5.47)
    following = replace(bars[3], timestamp=datetime(2026, 1, 5))
    extended = [bars[0], bars[1], intermediate, c, following]
    altered = replace(setup, pullback=PivotRef(3, 3))
    # Before C the real level was already occupied. After C both are occupied,
    # so a later close above them cannot supply the missing fresh crossing.
    result = observe(extended, altered)
    assert result.status == NStatus.FORMING
    assert result.completion is None


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_shared_edge_origin_and_pullback_boundaries_still_stop_completion(direction):
    bars, setup = shared_edge_sample(direction=direction)
    origin = 5.30 if direction == Direction.UP else 20-5.30
    origin_break = replace(bars[3], **({"low": origin} if direction == Direction.UP else {"high": origin}))
    assert observe([*bars[:3], origin_break], setup).status == NStatus.INVALIDATED
    pullback = 5.315 if direction == Direction.UP else 20-5.315
    c_break = replace(bars[3], **({"low": pullback} if direction == Direction.UP else {"high": pullback}))
    assert observe([*bars[:3], c_break], setup).status == NStatus.PULLBACK_EXTENDED


@pytest.mark.parametrize("direction", [Direction.UP, Direction.DOWN])
def test_outside_c_exception_cannot_hide_an_intervening_neckline_excess(direction):
    bars, setup = shared_edge_sample(direction=direction)
    intermediate = Bar(datetime(2026, 1, 3), "TEST", 5.34, 5.47, 5.33, 5.40, 1000)
    if direction == Direction.DOWN:
        intermediate = replace(intermediate, open=20-intermediate.open, high=20-intermediate.low,
                               low=20-intermediate.high, close=20-intermediate.close)
    c = replace(bars[2], timestamp=datetime(2026, 1, 4))
    following = replace(bars[3], timestamp=datetime(2026, 1, 5))
    altered = replace(setup, pullback=PivotRef(3, 3))
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe([bars[0], bars[1], intermediate, c, following], altered)


def test_identical_high_and_low_is_not_an_outside_turn():
    bars, _ = shared_edge_sample()
    identical = replace(bars[2], high=bars[1].high, low=bars[1].low)
    assert not teaching_outside(bars[1], identical)
    assert not teaching_inside(bars[1], identical)
