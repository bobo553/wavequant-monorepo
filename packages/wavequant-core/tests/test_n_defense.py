"""Public N reference semantics, including gaps and historical lifetimes."""
from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.domain.market_structure.n_defense import selloff_high, squeeze_low
from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode, MilestoneBasis, NSetup, PivotRef, observe_n,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar


def bars() -> list[Bar]:
    rows = [(8.5, 9, 8, 8.5), (10, 12, 9.5, 11), (10.7, 11, 10, 10.5),
            (13, 14, 12.5, 13.5), (10, 11, 9, 10)]
    return [Bar(datetime(2026, 1, 1) + timedelta(days=index), "TEST", *row, 100)
            for index, row in enumerate(rows)]


def setup(direction: Direction) -> NSetup:
    return NSetup("TEST", "1d", direction, PivotRef(0, 0), PivotRef(1, 1), PivotRef(2, 2),
                  "confirmed_test_n", BoxAnchorMode.ATTACK_VIRTUAL_EXTREME)


def test_no_reference_before_completed_n() -> None:
    observation = observe_n(bars()[:3], setup(Direction.UP), timeframe="1d",
                            milestone_basis=MilestoneBasis.CLOSE)
    assert squeeze_low(observation) is None
    assert selloff_high(observation) is None


def test_gap_attack_uses_previous_close_and_does_not_require_volume_increase() -> None:
    observation = observe_n(bars()[:4], setup(Direction.UP), timeframe="1d",
                            milestone_basis=MilestoneBasis.CLOSE)
    reference = squeeze_low(observation)
    assert reference == 10.5
    assert reference < bars()[3].low
    with pytest.raises(ValueError, match="direction"):
        selloff_high(observation)


def test_inverse_n_reference_is_the_exact_price_mirror() -> None:
    inverse = [replace(bar, open=40-bar.open, high=40-bar.low, low=40-bar.high, close=40-bar.close)
               for bar in bars()[:4]]
    observation = observe_n(inverse, setup(Direction.DOWN), timeframe="1d",
                            milestone_basis=MilestoneBasis.CLOSE)
    reference = selloff_high(observation)
    assert reference == 29.5
    assert reference > inverse[3].high
    with pytest.raises(ValueError, match="direction"):
        squeeze_low(observation)


def test_later_breach_preserves_historical_reference_and_completion_time() -> None:
    before = observe_n(bars()[:4], setup(Direction.UP), timeframe="1d",
                       milestone_basis=MilestoneBasis.CLOSE)
    after = observe_n(bars(), setup(Direction.UP), timeframe="1d",
                      milestone_basis=MilestoneBasis.CLOSE)
    assert before.completion == after.completion
    assert squeeze_low(before) == squeeze_low(after) == 10.5
    assert after.first_defense_breach_index == 4


def test_literal_attack_virtual_price_is_distinct_from_staged_operational_defense() -> None:
    observation = observe_n(bars()[:4], setup(Direction.UP), timeframe="1d",
                            milestone_basis=MilestoneBasis.CLOSE)
    assert observation.completion is not None
    staged = replace(observation, completion=replace(observation.completion, defense=9.5))
    assert squeeze_low(staged) == 10.5
    assert staged.completion is not None and staged.completion.defense == 9.5


def test_reference_rejects_future_or_incomplete_attack_evidence() -> None:
    observation = observe_n(bars()[:4], setup(Direction.UP), timeframe="1d",
                            milestone_basis=MilestoneBasis.CLOSE)
    assert observation.completion is not None
    for completion in (replace(observation.completion, bar_index=4),
                       replace(observation.completion, real_break=False),
                       replace(observation.completion, virtual_break=False)):
        with pytest.raises(ValueError, match="completed"):
            squeeze_low(replace(observation, completion=completion))
