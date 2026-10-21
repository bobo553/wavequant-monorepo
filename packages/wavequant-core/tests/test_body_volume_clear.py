"""An engulfing bearish body clears independently of wave target attainment."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.two_t_resistance import (
    BODY_VOLUME_CLEAR, HoldingResistanceHistory, holding_resistance_history,
)


def sample() -> list[Bar]:
    rows = [(8.3, 8.4, 7.7, 7.9, 100), (7.9, 8.7, 7.7, 8.47, 200),
            (8.47, 9.32, 7.73, 7.83, 150)]
    return [Bar(datetime(2025, 1, 1) + timedelta(days=index), 'sh.601086', *row)
            for index, row in enumerate(rows)]


@pytest.mark.parametrize('volume,qualifies', [(99, False), (100, False), (101, True), (201, True)])
def test_no_target_needed_and_volume_alternatives_are_strict(volume: float, qualifies: bool) -> None:
    bars = sample()
    bars[-1] = replace(bars[-1], volume=volume)
    risk = holding_resistance_history(bars, []).get(2)
    assert (risk is not None) is qualifies
    if risk is not None:
        assert risk['reason'] == BODY_VOLUME_CLEAR
        assert risk['exit_fraction'] == 1.0
        assert risk['bearish_reference_date'] == '2025-01-01'


@pytest.mark.parametrize('opened,closed,qualifies', [
    (8.47, 7.83, True), (8.48, 7.9, True), (8.47, 7.9, True),
    (8.46, 7.83, False), (8.48, 7.91, False), (7.83, 8.47, False),
])
def test_body_coverage_ignores_wicks_and_includes_both_equal_edges(
    opened: float, closed: float, qualifies: bool,
) -> None:
    bars = sample()
    bars[-1] = replace(bars[-1], open=opened, close=closed)
    assert (2 in holding_resistance_history(bars, [])) is qualifies


def test_new_qualification_window_uses_known_bearish_volume_without_backfilling_risk() -> None:
    bars = sample()
    stream = HoldingResistanceHistory(bars, start_index=2)
    risk = stream.advance(2, [])
    assert list(risk) == [2]
    assert risk[2]['bearish_reference_date'] == '2025-01-01'
    assert stream.advance(2, []) == holding_resistance_history(bars, [])
    assert holding_resistance_history(bars[:2], []) == {}


def test_previous_volume_can_qualify_without_an_earlier_bearish_bar() -> None:
    bars = sample()
    bars[0] = replace(bars[0], open=7.8)
    bars[2] = replace(bars[2], volume=201)
    risk = holding_resistance_history(bars, [])[2]
    assert risk['bearish_reference_date'] is None
    assert risk['volume_above_previous'] is True
    assert risk['volume_above_last_bearish'] is False


def test_previous_doji_and_zero_volume_benchmarks_do_not_qualify() -> None:
    bars = sample()
    bars[1] = replace(bars[1], open=bars[1].close)
    assert holding_resistance_history(bars, []) == {}
    bars = sample()
    bars[0] = replace(bars[0], volume=0)
    bars[1] = replace(bars[1], volume=0)
    assert holding_resistance_history(bars, []) == {}


def test_guofang_january_3_frozen_prices_and_volume_trigger_without_projection() -> None:
    raw = json.loads((Path(__file__).parent / 'fixtures/guofang_2025_five_top_low_open.json')
                     .read_text(encoding='utf-8'))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row['timestamp'])))
            for row in raw['bars'] if '2024-12-30' <= row['timestamp'][:10] <= '2025-01-03']
    risk = holding_resistance_history(bars, [])[3]
    assert risk['reason'] == BODY_VOLUME_CLEAR
    assert risk['observed_volume'] == 77_049_749
    assert risk['previous_volume'] == 61_793_000
    assert risk['bearish_reference_volume'] == 57_477_873
    assert risk['bearish_reference_date'] == '2024-12-30'
    assert risk['volume_above_previous'] is True
    assert risk['volume_above_last_bearish'] is True
    assert bars[-1].close / bars[-1].adjustment_factor == pytest.approx(5.73)


def test_account_clears_at_close_without_targets_and_blocks_same_day_reentry() -> None:
    bars = [replace(bar, volume=bar.volume * 100_000) for bar in sample()]
    buy = Signal(bars[1].timestamp, bars[1].symbol, 1, 'LONG', bars[1].close, 7.0,
                 'fixture', bars[1].timestamp, 0, None, 'fixture', 12.0)
    repeat = replace(buy, bar_index=2, timestamp=bars[2].timestamp, reference_price=bars[2].close)
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, exit_on_target=False,
        wave_exhaustion_exit=True, max_hold_bars=100, max_participation=1, slippage_bps_per_side=0)
    account = run_portfolio({bars[0].symbol: bars}, [buy, repeat], config)
    sells = [order for order in account.orders if order['side'] == 'SELL' and order['status'] == 'filled']
    assert len(sells) == 1
    assert sells[0]['reason'] == BODY_VOLUME_CLEAR
    assert sells[0]['timestamp'] == bars[2].timestamp.isoformat()
    assert sells[0]['execution_model'] == 'same_day_close'
    assert sells[0]['remaining_quantity'] == 0
    assert not account.open_positions
    assert not any(order['side'] == 'BUY' and order['status'] == 'filled'
                   and order['timestamp'] == bars[2].timestamp.isoformat() for order in account.orders)
