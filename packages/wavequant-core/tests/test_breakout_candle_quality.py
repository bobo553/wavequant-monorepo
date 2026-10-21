"""Breakout strength belongs to today's candle, across symbols and entry routes."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.candle_strength import bullish_breakout_rejection
from wavequant.domain.market_structure.polyline_lifecycle import PolylineBuyGate
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection

from .test_polyline_buy_gate import bar, bearish_points, breakout_bar


@pytest.mark.parametrize('opening,high,low,close,reason', [
    (9.99, 10.8, 9.99, 10.7, None),
    (10, 10.4, 10, 10.2, None),  # Exact 2% body and half range.
    (10, 10.4, 10, 10.199, 'breakout_requires_gap_or_medium_bullish_body'),
    (10, 10.6, 9.7, 10.4, 'breakout_requires_gap_or_medium_bullish_body'),
    (10.3, 10.6, 10.3, 10.35, 'breakout_requires_gap_or_medium_bullish_body'),
    (10.5, 10.6, 10.4, 10.5, 'breakout_requires_gap_or_medium_bullish_body'),
    (10.501, 10.6, 10.4, 10.5, 'volume_bearish_buy_blocked'),  # Gap cannot waive bearish volume.
])
def test_same_bar_strength_boundaries(
    opening: float, high: float, low: float, close: float, reason: str | None,
) -> None:
    previous = Bar(datetime(2026, 7, 15), 'sh.600000', 10, 10.5, 9.8, 10, 100)
    current = Bar(datetime(2026, 7, 16), previous.symbol, opening, high, low, close, 200)
    assert bullish_breakout_rejection(current, previous) == reason


@pytest.mark.parametrize('pre_n_volume', [50, 500])
def test_squeeze_volume_does_not_inherit_independent_breakout_double_volume(pre_n_volume: float) -> None:
    previous = Bar(datetime(2026, 7, 15), 'sh.600000', 10, 10.5, 9.8, 10, 100)
    current = Bar(datetime(2026, 7, 16), previous.symbol, 9.99, 10.8, 9.99, 10.7, 199)
    before_attack = replace(previous, timestamp=previous.timestamp - timedelta(days=2), volume=pre_n_volume)
    assert bullish_breakout_rejection(current, previous) == 'breakout_low_open'
    assert squeeze_buy_rejection(current, previous, before_attack) is None


def test_missing_or_non_prior_candle_cannot_supply_strength() -> None:
    current = Bar(datetime(2026, 7, 16), 'sh.600000', 10, 10.8, 10, 10.7, 200)
    for previous in (None, current, replace(current, timestamp=current.timestamp + timedelta(days=1))):
        assert bullish_breakout_rejection(current, previous) == 'breakout_previous_price_unavailable'


@pytest.mark.parametrize('symbol,scale', [('sz.301130', 1.0), ('sh.600000', 10.0)])
def test_low_open_squeeze_quality_is_shared_across_symbols_and_scales(symbol: str, scale: float) -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/xidian_2026_deep_squeeze.json').read_text(encoding='utf-8'))
    previous, current = [Bar(datetime.fromisoformat(row[0]), symbol,
        *(float(price) * scale for price in row[1:5]), float(row[5]))
        for row in raw['bars'] if row[0] in ('2026-07-15', '2026-07-16')]
    assert current.high > previous.high
    assert current.close > previous.close * 1.02 and current.volume > previous.volume
    assert current.open < previous.close
    assert bullish_breakout_rejection(current, previous) == 'breakout_low_open'
    assert squeeze_buy_rejection(current, previous) is None


@pytest.mark.parametrize('low_open', [False, True])
def test_weak_key_break_does_not_release_then_later_strong_rebreak_does(low_open: bool) -> None:
    gate, points = PolylineBuyGate(), bearish_points()
    for index in range(4):
        gate.advance(index, bar(index), points, 0)
    weak = (replace(breakout_bar(4, 9.1), open=8.19) if low_open
            else replace(bar(4, 9.1), open=8.2))
    gate.advance(4, weak, points, 0)
    assert gate.restrictions[4]['reason'] == (
        'breakout_low_open' if low_open else 'breakout_requires_gap_or_medium_bullish_body')
    assert not gate.released
    gate.advance(5, bar(5), points, 0)
    assert gate.restrictions[5]['reason'] == 'polyline_last_fall_high_not_broken'
    gate.advance(6, breakout_bar(6, 9.1), points, 0)
    assert 6 not in gate.restrictions and gate.released
    assert 4 in gate.restrictions  # Later strength never rewrites past rejection.


def test_strong_candle_below_key_does_not_pre_authorize_later_weak_break() -> None:
    gate, points = PolylineBuyGate(), bearish_points()
    for index in range(4):
        gate.advance(index, bar(index), points, 0)
    gate.advance(4, replace(bar(4), open=8.2, close=8.7, low=8.2), points, 0)
    gate.advance(5, replace(bar(5, 9.1), open=8.7, close=8.8, low=8.7), points, 0)
    assert gate.restrictions[5]['reason'] == 'breakout_requires_gap_or_medium_bullish_body'
    assert not gate.released


def test_released_polyline_still_leaves_current_candidate_candle_gate_active() -> None:
    gate, points = PolylineBuyGate(), bearish_points()
    for index in range(4):
        gate.advance(index, bar(index), points, 0)
    strong = breakout_bar(4, 9.1)
    gate.advance(4, strong, points, 0)
    current = replace(bar(5, 9.5), open=strong.close - 0.01, close=9.4, low=8.8, volume=199)
    gate.advance(5, current, points, 0)
    assert 5 not in gate.restrictions
    assert squeeze_buy_rejection(current, strong) is None
    assert squeeze_buy_rejection(replace(current, volume=strong.volume), strong) is not None


@pytest.mark.parametrize('volume,close,low,key,accepted', [
    (200, 10.7, 9.9, None, True),
    (199, 10.7, 9.9, None, False),
    (200, 10.5, 9.9, None, False),
    (200, 10.7, 8, None, False),
    (200, 10.7, 9.9, 10.7, False),
    (200, 10.7, 9.9, 10.6, True),
])
def test_low_open_exception_requires_all_same_bar_evidence(volume, close, low, key, accepted):
    previous = Bar(datetime(2025, 4, 2), 'sh.600000', 10, 10.5, 9.8, 10, 100)
    current = Bar(datetime(2025, 4, 3), previous.symbol, 9.9, 10.8, low, close, volume)
    assert (bullish_breakout_rejection(current, previous, breakout_level=key) is None) is accepted
    assert bullish_breakout_rejection(current, replace(previous, volume=0), breakout_level=key) is not None


def test_guofang_april3_double_volume_low_open_is_no_longer_a_candle_rejection():
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_five_top_low_open.json').read_text(encoding='utf-8'))
    previous, current = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
        for row in raw['bars'] if row['timestamp'][:10] in ('2025-04-02', '2025-04-03')]
    assert current.open < previous.close
    assert current.volume >= previous.volume * 2
    assert current.close > previous.high
    assert bullish_breakout_rejection(current, previous) is None
    assert squeeze_buy_rejection(current, previous) is None


@pytest.mark.parametrize('close,accepted', [(8.9, False), (9, False), (9.1, True)])
def test_low_open_exception_must_close_above_the_frozen_polyline_key(close, accepted):
    gate, points = PolylineBuyGate(), bearish_points()
    for index in range(4):
        gate.advance(index, bar(index), points, 0)
    current = replace(breakout_bar(4, 9.2), open=8.1, low=8, close=close, volume=200)
    gate.advance(4, current, points, 0)
    assert (4 not in gate.restrictions) is accepted
    assert bool(gate.released) is accepted
