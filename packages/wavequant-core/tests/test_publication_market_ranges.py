"""整波守价查询保留相等、首个破位、端点及大整数的原始比较语义。"""

from datetime import datetime, timedelta
from typing import cast

import pytest

from wavequant.domain.market_structure import trend_publication as publication
from wavequant.domain.market_structure.trend_market_index import _MarketPrefixIndex, _MarketRangeIndex
from wavequant.domain.models.model import Bar


@pytest.mark.parametrize('up', (False, True))
def test_every_origin_and_endpoint_matches_original_guard_in_each_real_prefix(up: bool) -> None:
    prices = ((12., 10.), (13., 10.), (14., 11.), (12., 9.), (14., 10.), (16., 12.))
    bars = tuple(Bar(datetime(2026, 1, 1)+timedelta(days=index), 'RANGE', low,
                     high, low, low, 100.) for index, (high, low) in enumerate(prices))
    owner = _MarketRangeIndex(bars)
    for cutoff in range(len(bars)):
        market = bars[:cutoff+1]
        prefix = _MarketPrefixIndex(owner, market)
        for start in range(cutoff+1):
            origin: dict[str, object] = dict(index=start, value=bars[start].low if up else bars[start].high)
            proof: dict[str, object] = dict(origin=origin, direction='up' if up else 'down')
            for end in range(cutoff+1):
                endpoint = dict(index=end)
                assert publication._wave_holds(proof, market, endpoint, _market_index=prefix) == publication._wave_holds(
                    proof, market, endpoint)
                assert publication._held_wave_floor(origin, market, end, _market_index=prefix) == publication._held_wave_floor(
                    origin, market, end)


def test_future_break_cannot_end_an_earlier_prefix_and_equal_extreme_still_holds() -> None:
    bars = tuple(Bar(datetime(2026, 1, 1)+timedelta(days=index), 'RANGE', 11., 12., low, 11., 100.)
                 for index, low in enumerate((10., 10., 11., 9.)))
    prefix = _MarketPrefixIndex(_MarketRangeIndex(bars), bars)
    origin = dict(index=0, value=10.)
    proof: dict[str, object] = dict(origin=origin, direction='up')
    assert publication._wave_holds(proof, bars, dict(index=2), _market_index=prefix)
    assert not publication._wave_holds(proof, bars, dict(index=3), _market_index=prefix)
    assert publication._held_wave_floor(origin, bars, 1, _market_index=prefix)
    assert not publication._held_wave_floor(origin, bars, 3, _market_index=prefix)


def test_unrepresentable_integer_prices_keep_original_integer_comparisons(monkeypatch: pytest.MonkeyPatch) -> None:
    origin_price = 2**53+1
    bars = tuple(Bar(datetime(2026, 1, 1)+timedelta(days=index), 'RANGE', origin_price,
                     origin_price+4, low, origin_price, 100) for index, low in enumerate((origin_price, origin_price-1)))
    prefix = _MarketPrefixIndex(_MarketRangeIndex(bars), bars)
    assert not prefix.owner.exact_prices

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError('an inexact float tree was used for integer guard prices')

    monkeypatch.setattr(prefix.owner, 'first_extreme', forbidden)
    origin: dict[str, object] = dict(index=0, value=origin_price)
    proof: dict[str, object] = dict(origin=origin, direction='up')
    assert not publication._wave_holds(proof, bars, dict(index=1), _market_index=prefix)
    assert not publication._held_wave_floor(origin, bars, 1, _market_index=prefix)
    assert type(cast(dict[str, object], proof['origin'])['value']) is int
