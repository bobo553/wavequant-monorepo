"""Exact strong bullish body and short upper-shadow boundaries."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.market_state.candle_strength import strong_bullish_candle
from wavequant.domain.models.model import Bar


@pytest.mark.parametrize("prices,accepted", [
    ((10, 10.32, 9.98, 10.3), True),
    ((10, 10.32, 9.98, 10.299999), False),
    ((10, 10.6, 9.6, 10.6), True),
    ((10, 10.6, 9.6, 10.599999), False),
    ((10, 10.9, 9.9, 10.7), True),
    ((10, 10.900001, 9.9, 10.7), False),
    ((4.63, 4.87, 4.63, 4.84), True),
    ((10, 10.4, 9.6, 10), False),
    ((10, 10.1, 9.8, 9.9), False),
    ((10, 10, 10, 10), False),
])
def test_strong_bullish_candle_uses_inclusive_exact_shape_boundaries(
    prices: tuple[float, float, float, float], accepted: bool,
) -> None:
    bar = Bar(datetime(2024, 3, 20), "TEST", *prices, 1000)
    assert strong_bullish_candle(bar) is accepted


def test_strong_bullish_candle_is_independent_of_volume_and_price_scale() -> None:
    bar = Bar(datetime(2024, 3, 20), "TEST", 10, 10.9, 9.9, 10.7, 1000)
    for volume in (0, 1, 1000000):
        assert strong_bullish_candle(replace(bar, volume=volume))
    assert strong_bullish_candle(replace(bar, open=20, high=21.8, low=19.8, close=21.4))
