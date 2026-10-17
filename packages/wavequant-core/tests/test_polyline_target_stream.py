"""Streamed targets retain the canonical breakout and milestone observations."""

from dataclasses import replace

import pytest

from .test_n_shape import fixture, mirror, setup
from wavequant.domain.market_structure.n_shape import MilestoneBasis, observe_n
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.validated_bars import ValidatedBars
from wavequant.domain.strategies.polyline_target_stream import advance_completed_n


@pytest.mark.parametrize('direction', (Direction.UP, Direction.DOWN))
@pytest.mark.parametrize('break_defense', (False, True))
def test_completed_target_updates_equal_canonical_prefixes(direction, break_defense):
    bars = fixture()
    if break_defense:
        bars[4] = replace(bars[4], low=9.0)
    if direction == Direction.DOWN:
        bars = list(map(mirror, bars))
    bars = ValidatedBars(bars)
    source = setup(direction=direction)
    current = observe_n(bars[:4], source, timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    assert current.completion is not None
    for index in range(4, len(bars)):
        current = advance_completed_n(current, bars[index], index, direction)
        assert current == observe_n(bars[:index + 1], source, timeframe='1d',
                                    milestone_basis=MilestoneBasis.EXTREME)


def test_completed_target_rejects_skipped_dates():
    bars = ValidatedBars(fixture())
    current = observe_n(bars[:4], setup(), timeframe='1d', milestone_basis=MilestoneBasis.EXTREME)
    with pytest.raises(ValueError, match='next close'):
        advance_completed_n(current, bars[5], 5, Direction.UP)
