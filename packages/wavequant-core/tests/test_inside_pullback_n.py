"""A confirmed bearish inside child supplies B/C without borrowing the mother high."""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, NStatus, PivotRef, observe_n,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar


def sample():
    rows = [(7.87, 8.10, 7.60, 7.69), (7.94, 7.94, 7.32, 7.75),
            (7.68, 7.74, 7.55, 7.66), (7.63, 7.90, 7.56, 7.87),
            (7.86, 7.86, 7.65, 7.66), (7.61, 7.96, 7.59, 7.94)]
    return [Bar(datetime(2026, 7, 20) + timedelta(days=i), "TEST", *row, 1000)
            for i, row in enumerate(rows)]


def setup():
    return NSetup("TEST", "1d", Direction.UP, PivotRef(1, 2), PivotRef(2, 2), PivotRef(2, 3),
                  "lecture_causal", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
                  allow_confirmation_bar=True, allow_inside_pullback=True, staged_defense=True)


def test_inside_child_completes_the_joint_attack_using_its_own_neckline():
    bars = sample()
    waiting = observe_n(bars[:3], setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert waiting.status == NStatus.AWAIT_ANCHORS
    n = observe_n(bars[:4], setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert n.completion is not None and n.completion.bar_index == 3
    assert n.anchors is not None and n.anchors.neckline_extreme == 7.74
    assert n.targets is not None
    assert (n.targets.box_anchor, n.targets.one_p, n.targets.two_t) == pytest.approx((7.90, 8.48, 9.06))
    full = observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert (full.completion, full.targets) == (n.completion, n.targets)


@pytest.mark.parametrize("change", [
    {"allow_inside_pullback": False}, {"allow_inside_pullback": 1},
    {"source": "strict_polyline"}, {"direction": Direction.DOWN},
    {"allow_mother_pullback": True},
])
def test_inside_path_requires_an_explicit_separate_positive_lecture_policy(change):
    with pytest.raises(ValueError):
        replace(setup(), **change)


@pytest.mark.parametrize("index,change", [
    (1, {"close": 7.94}), (2, {"close": 7.68}), (2, {"close": 7.70}),
    (2, {"high": 7.95}), (2, {"low": 7.31}),
    (2, {"high": 7.94, "low": 7.32}),
])
def test_doji_bullish_noncontained_or_identical_child_cannot_supply_ordered_b_c(index, change):
    bars = sample()
    bars[index] = replace(bars[index], **change)
    with pytest.raises(ValueError):
        observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


@pytest.mark.parametrize("change", [{"high": 7.74, "close": 7.70}, {"close": 7.66}])
def test_touch_or_only_one_break_does_not_complete_the_n(change):
    bars = sample()[:4]
    bars[-1] = replace(bars[-1], **change)
    n = observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert n.status == NStatus.FORMING and n.completion is None


def test_inside_child_cannot_bypass_the_original_outside_mother_validation():
    n = replace(setup(), allow_inside_pullback=False, allow_mother_pullback=True)
    with pytest.raises(ValueError, match="bearish outside"):
        observe_n(sample(), n, timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


def test_child_pullback_broken_before_confirmation_cannot_complete():
    bars = sample()[:4]
    bars[-1] = replace(bars[-1], low=7.54)
    with pytest.raises(ValueError, match="exceeded before its confirmation"):
        observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)


def test_shared_high_inside_child_remains_separate_from_strict_outside_policy():
    bars = sample()[:4]
    bars[2] = replace(bars[2], high=7.94)
    bars[3] = replace(bars[3], high=7.96, close=7.95)
    n = observe_n(bars, setup(), timeframe="1d", milestone_basis=MilestoneBasis.EXTREME)
    assert n.completion is not None and n.completion.bar_index == 3
    assert n.anchors is not None and n.anchors.neckline_extreme == 7.94
