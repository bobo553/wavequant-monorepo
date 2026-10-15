"""区间剪枝保持首次破位、等值极值和真实观察日边界。"""

from dataclasses import replace
from datetime import datetime, timedelta
from fractions import Fraction

import pytest

from wavequant.domain.market_structure.trend_market_index import _MarketRangeIndex
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.whole_wave_entry import select_wave_entry


def market() -> tuple[Bar, ...]:
    prices = ((12., 10., 11.), (12., 10., 11.), (14., 11., 12.),
              (13., 9., 10.), (14., 9., 10.), (15., 10., 14.))
    return tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'WINDOW', close,
                     high, low, close, 100.) for index, (high, low, close) in enumerate(prices))


def test_every_extreme_window_matches_original_scan_and_first_equal_position() -> None:
    bars = market()
    owner = _MarketRangeIndex(bars)
    for stop in range(1, len(bars) + 1):
        for start in range(stop):
            window = range(start, stop)
            assert owner.extreme_position(start, stop, high=True) == max(window, key=lambda index: bars[index].high)
            assert owner.extreme_position(start, stop, high=False) == min(window, key=lambda index: bars[index].low)
            assert owner.lowest_close_position(start, stop) == min(window, key=lambda index: bars[index].close)


@pytest.mark.parametrize('floor', (Fraction(10), Fraction(21, 2), Fraction(11), Fraction(35, 3)))
def test_close_guard_keeps_exact_fraction_and_first_failure_in_every_prefix(floor: Fraction) -> None:
    bars = market()
    owner = _MarketRangeIndex(bars)
    for stop in range(len(bars) + 1):
        for start in range(stop + 1):
            expected = next((index for index in range(start, stop)
                             if Fraction(str(bars[index].close)) < floor), None)
            assert owner.first_close_below(start, stop, floor) == expected


def test_future_break_and_equal_floor_cannot_close_an_earlier_window() -> None:
    bars = market()
    owner = _MarketRangeIndex(bars)
    assert owner.first_close_below(0, 3, Fraction(11)) is None
    assert owner.first_close_below(0, 4, Fraction(11)) == 3
    assert owner.first_close_below(3, 5, Fraction(10)) is None
    assert owner.extreme_position(0, 3, high=False) == 0
    assert owner.extreme_position(1, 3, high=False) == 1


def test_inexact_integer_and_special_thresholds_do_not_authorize_index_pruning() -> None:
    bars = (replace(market()[0], low=2**53 + 1),)
    owner = _MarketRangeIndex(bars)
    assert not owner.supports_price(2**53 + 1)
    for price in (True, float('nan'), float('inf'), Fraction(1, 3)):
        assert not _MarketRangeIndex(market()).supports_price(price)


@pytest.mark.parametrize('start,stop', ((-1, 1), (0, 7), (2, 1)))
def test_extreme_queries_reject_out_of_market_windows(start: int, stop: int) -> None:
    with pytest.raises(ValueError):
        _MarketRangeIndex(market()).extreme_position(start, stop, high=True)


@pytest.mark.parametrize('mature', (False, True))
@pytest.mark.parametrize('basis', ('alternation_low', 'minimum_close'))
def test_entry_window_index_preserves_result_and_ignores_future_break(mature: bool, basis: str) -> None:
    prices = ((10., 5., 7.), (10., 7., 8.), (10., 7., 8.), (12., 9., 11.),
              (11., 9., 10.), (11.5, 9.5, 11.), (11.7, 9.8, 11.2), (30., 4., 20.))
    bars = tuple(Bar(datetime(2026, 1, 1) + timedelta(days=index), 'ENTRY', close - .2,
                     high, low, close, 100.) for index, (high, low, close) in enumerate(prices))
    context = EntryContext(2, 0, 0, 0, 9., 1, 1, 10., 0, 5., 2, 2, 7.,
                           maturity_index=3 if mature else None)
    options = dict(bars=bars, attack=5, low_index=4, asof=6, first_basis=basis)
    expected = select_wave_entry((context,), (context,), **options)
    actual = select_wave_entry((context,), (context,), _market_index=_MarketRangeIndex(bars), **options)
    assert actual == expected
    if mature or basis == 'alternation_low':
        assert actual[0] is not None
    assert select_wave_entry((context,), (context,), bars=bars[:7], attack=5, low_index=4,
                             asof=6, first_basis=basis) == actual
