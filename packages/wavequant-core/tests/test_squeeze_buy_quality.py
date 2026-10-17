"""Squeeze recognition remains independent of the current candle's buy quality."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.attack_quality import squeeze_buy_rejection


@pytest.mark.parametrize('opened,high,closed,volume,accepted', [
    (10.0, 10.4, 10.2, 101, False),  # Exactly 2% is insufficient.
    (10.0, 10.6, 10.3, 101, True),  # Equal body and upper shadow qualifies.
    (10.0, 10.61, 10.3, 101, False),
    (10.0, 10.6, 10.3, 100, False),
    (10.0, 10.6, 10.3, 99, False),
    (10.6, 10.9, 10.55, 101, True),  # Gap route does not require a bullish body.
    (10.6, 10.9, 10.55, 100, False),
    (10.5, 10.9, 10.55, 101, False),  # Opening at yesterday's high is not a gap.
])
def test_price_routes_share_mandatory_volume(
    opened: float, high: float, closed: float, volume: float, accepted: bool,
) -> None:
    previous = Bar(datetime(2024, 2, 26), 'sh.601086', 10.0, 10.5, 9.8, 10.0, 100)
    bar = Bar(datetime(2024, 2, 27), previous.symbol, opened, high, 9.8, closed, volume)
    assert (squeeze_buy_rejection(bar, previous) is None) is accepted


def test_missing_or_zero_volume_reference_cannot_qualify() -> None:
    bar = Bar(datetime(2024, 2, 27), 'sh.601086', 10.0, 10.6, 9.8, 10.3, 101)
    assert squeeze_buy_rejection(bar, None) is not None
    assert squeeze_buy_rejection(bar, replace(bar, volume=0)) is not None


def test_guofang_february_27_is_not_a_buy_candle() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_five_top_low_open.json')
                     .read_text(encoding='utf-8'))
    previous, bar = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
        for row in raw['bars'] if '2024-02-26' <= row['timestamp'][:10] <= '2024-02-27']
    assert bar.volume > previous.volume
    assert bar.close > previous.close * 1.02
    assert bar.close - bar.open < bar.high - bar.close
    assert bar.open < previous.high
    assert squeeze_buy_rejection(bar, previous) == 'squeeze_buy_requires_gain_body_or_gap'


@pytest.mark.parametrize('yesterday_volume,pre_n_volume,current_volume,accepted', [
    (200, 100, 150, True), (100, 200, 150, True),
    (200, 150, 150, False), (150, 200, 150, False),
    (200, 0, 150, False), (0, 100, 150, True),
])
@pytest.mark.parametrize('gap', [False, True])
def test_each_price_route_accepts_either_candidate_volume_reference(
    yesterday_volume: float, pre_n_volume: float, current_volume: float, accepted: bool, gap: bool,
) -> None:
    previous = Bar(datetime(2024, 2, 26), 'sh.601086', 10, 10.5, 9.8, 10, yesterday_volume)
    before_attack = replace(previous, timestamp=datetime(2024, 2, 20), volume=pre_n_volume)
    bar = Bar(datetime(2024, 2, 27), previous.symbol,
              10.6 if gap else 10, 10.9 if gap else 10.6, 9.8,
              10.55 if gap else 10.3, current_volume)
    assert (squeeze_buy_rejection(bar, previous, before_attack) is None) is accepted


def test_pre_n_reference_is_candidate_local_and_cannot_come_from_future() -> None:
    previous = Bar(datetime(2024, 2, 26), 'sh.601086', 10, 10.5, 9.8, 10, 200)
    bar = Bar(datetime(2024, 2, 27), previous.symbol, 10, 10.6, 9.8, 10.3, 150)
    low_reference = replace(previous, timestamp=datetime(2024, 2, 20), volume=100)
    high_reference = replace(low_reference, volume=180)
    assert squeeze_buy_rejection(bar, previous, low_reference) is None
    assert squeeze_buy_rejection(bar, previous, high_reference) is not None
    assert squeeze_buy_rejection(bar, previous, replace(low_reference, timestamp=bar.timestamp)) is not None
    assert squeeze_buy_rejection(bar, previous) is not None


def test_pre_n_volume_cannot_bypass_candle_shape() -> None:
    previous = Bar(datetime(2024, 2, 26), 'sh.601086', 10, 10.5, 9.8, 10, 200)
    before_attack = replace(previous, timestamp=datetime(2024, 2, 20), volume=100)
    bar = Bar(datetime(2024, 2, 27), previous.symbol, 10, 10.61, 9.8, 10.3, 150)
    assert squeeze_buy_rejection(bar, previous, before_attack) == 'squeeze_buy_requires_gain_body_or_gap'
