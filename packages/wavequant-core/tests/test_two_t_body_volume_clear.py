"""A post-two-T body reversal uses the last bearish session's volume."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.two_t_resistance import two_t_resistance_history
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


REASON = "wave_two_t_body_volume_clear"


def sample():
    rows = [
        (5.30, 5.40, 5.00, 5.10, 100),
        (5.10, 5.49, 5.08, 5.48, 800),
        (5.48, 5.65, 5.46, 5.62, 900),
        (5.60, 5.62, 5.55, 5.60, 5),
        (5.50, 5.75, 5.40, 5.60, 900),
        (5.61, 5.73, 5.47, 5.49, 101),
    ]
    bars = [Bar(datetime(2026, 1, 5) + timedelta(days=i), "sh.600825", *row)
            for i, row in enumerate(rows)]
    event = dict(event="wave_projection_ready", attack=1, origin_index=0, bar_index=2, two_t=5.60)
    return bars, event


@pytest.mark.parametrize("symbol", ["sh.600825", "sz.000001", "sh.601811"])
def test_later_small_body_engulf_clears_without_wick_or_previous_day_volume_requirements(symbol):
    bars, event = sample()
    bars = [replace(bar, symbol=symbol) for bar in bars]
    clear = two_t_resistance_history(bars, [event])[5]
    assert clear["reason"] == REASON
    assert clear["exit_fraction"] == 1.0
    assert clear["execution_model"] == "same_day_close"
    assert clear["bearish_reference_date"] == str(bars[0].timestamp.date())
    assert clear["bearish_reference_volume"] == 100
    assert clear["previous_volume"] == 900
    assert clear["observed_volume"] == 101
    assert clear["wave_reached_date"] == str(bars[2].timestamp.date())
    assert bars[5].high < bars[4].high and bars[5].close > bars[4].low
    assert (bars[5].open - bars[5].close) / bars[5].open < .05


def test_a_later_bearish_candle_replaces_the_old_bearish_volume_reference():
    bars, event = sample()
    bars[3] = replace(bars[3], open=5.61, volume=200)
    assert two_t_resistance_history(bars, [event]).get(5, {}).get("reason") != REASON
    bars[5] = replace(bars[5], volume=201)
    clear = two_t_resistance_history(bars, [event])[5]
    assert clear["bearish_reference_date"] == str(bars[3].timestamp.date())
    assert clear["bearish_reference_volume"] == 200


@pytest.mark.parametrize("opened,closed,qualifies", [
    (5.60, 5.49, True), (5.61, 5.50, True),
    (5.60, 5.50, False), (5.59, 5.49, False), (5.61, 5.51, False),
])
def test_body_coverage_allows_one_equal_edge_but_requires_an_expanding_body(opened, closed, qualifies):
    bars, event = sample()
    bars[5] = replace(bars[5], open=opened, close=closed)
    assert (two_t_resistance_history(bars, [event]).get(5, {}).get("reason") == REASON) is qualifies


@pytest.mark.parametrize("invalid", ["equal_volume", "lower_volume", "zero_reference", "no_bearish",
                                     "previous_bearish", "previous_doji", "not_reached", "same_day",
                                     "future", "invalidated_before", "invalidated_today"])
def test_invalid_volume_shape_or_target_cannot_clear(invalid):
    bars, event = sample()
    events = [event]
    if invalid in ("equal_volume", "lower_volume"):
        bars[5] = replace(bars[5], volume=100 if invalid == "equal_volume" else 99)
    elif invalid == "zero_reference":
        bars[0] = replace(bars[0], volume=0)
    elif invalid == "no_bearish":
        bars[0] = replace(bars[0], open=5.0)
    elif invalid in ("previous_bearish", "previous_doji"):
        bars[4] = replace(bars[4], open=5.7 if invalid == "previous_bearish" else 5.6)
    elif invalid == "not_reached":
        event["two_t"] = 10.0
    elif invalid in ("same_day", "future"):
        event["bar_index"] = 5 if invalid == "same_day" else 6
        event["two_t"] = 5.5
    else:
        events.append(dict(event="wave_projection_invalidated", attack=1, origin_index=0,
                           bar_index=4 if invalid == "invalidated_before" else 5))
    assert two_t_resistance_history(bars, events).get(5, {}).get("reason") != REASON


def test_latest_live_target_wins_and_future_invalidation_does_not_rewrite_prefix():
    bars, event = sample()
    later = dict(event, attack=2, bar_index=4, two_t=5.7)
    future = dict(event="wave_projection_invalidated", attack=2, origin_index=0, bar_index=6)
    risks = two_t_resistance_history(bars, [later, future, event])
    assert risks[5]["wave_n_date"] == str(bars[2].timestamp.date())
    assert risks == two_t_resistance_history(bars, [event, later, future])
    for cut in range(1, len(bars) + 1):
        prefix = two_t_resistance_history(bars[:cut], [later, future, event])
        assert prefix == {i: risk for i, risk in risks.items() if i < cut}


@pytest.mark.parametrize("signal_only", [False, True])
def test_full_exit_wins_over_reduction_cancels_reentry_and_obeys_t_plus_one(signal_only):
    bars, event = sample()
    bars = [replace(bar, volume=bar.volume * 100_000) for bar in bars]
    buy = Signal(bars[1].timestamp, bars[1].symbol, 1, "LONG", bars[1].close, 4.9,
                 "fixture", bars[1].timestamp, 0, None, "fixture", 9.0)
    repeat = replace(buy, bar_index=5, timestamp=bars[5].timestamp, reference_price=bars[5].close)
    signals = [buy, repeat]
    if signal_only:
        signals.insert(1, replace(repeat, side="EXIT", reason=REASON))
    config = StrategyConfig(entry_at_close=True, allow_add_on=True, exit_on_target=False,
                            wave_exhaustion_exit=True, volume_down_exit=True, max_hold_bars=100,
                            max_participation=1, slippage_bps_per_side=0)
    events = {} if signal_only else {bars[0].symbol: [event]}
    account = run_portfolio({bars[0].symbol: bars}, signals, config, wave_events=events)
    sell = next(order for order in account.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert sell["reason"] == REASON
    assert sell["timestamp"] == bars[5].timestamp.isoformat()
    assert sell["price"] == bars[5].close
    assert sell["execution_model"] == "same_day_close"
    assert sell["remaining_quantity"] == 0
    assert not account.open_positions
    assert not any(order["side"] == "BUY" and order["status"] == "filled"
                   and order["timestamp"] == bars[5].timestamp.isoformat() for order in account.orders)
    # A position bought at today's open cannot be sold before the next session.
    next_open = run_portfolio({bars[0].symbol: bars}, [replace(buy, bar_index=4, timestamp=bars[4].timestamp)],
                              replace(config, entry_at_close=False), wave_events={bars[0].symbol: [event]})
    assert any(order["side"] == "SELL" and order["reason"] == "T+1" for order in next_open.orders)
    assert next_open.open_positions


@pytest.fixture(scope="module")
def xinhua():
    raw = json.loads((Path(__file__).parent / "fixtures/xinhua_2024_mother_pullback.json").read_text(encoding="utf8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], opening, high, low, close, volume,
                adjustment_factor=factor, **raw["permissions"][day])
            for day, opening, high, low, close, volume, factor, _raw_close in raw["bars"]
            if day <= "2023-12-19"]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    config = replace(SystemStrategy(**profile["strategy"]), volume_filter=False)
    return bars, config, raw["execution"], generate_system_signals(bars, config)


def test_real_december_18_exit_and_formal_account_prefix_match(xinhua):
    bars, config, execution, result = xinhua
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2023-12-18")
    exits = [signal for signal in result.signals if signal.side == "EXIT" and signal.bar_index == now]
    assert len(exits) == 1 and REASON in exits[0].reason.split("|")
    proof = next(event for event in result.audit if event["event"] == "target_resistance_observed"
                 and event["bar_index"] == now)
    assert proof["wave_n_date"] == "2023-11-02"
    assert proof["wave_reached_date"] == "2023-12-11"
    assert proof["bearish_reference_date"] == "2023-12-08"
    assert proof["bearish_reference_volume"] == 23_328_933
    assert proof["observed_volume"] == 69_475_932 < proof["previous_volume"]
    assert bars[now].close / bars[now].adjustment_factor == pytest.approx(5.27)
    prefix = generate_system_signals(bars[:now + 1], config)
    assert prefix.signals == [signal for signal in result.signals if signal.bar_index <= now]
    assert prefix.audit == [event for event in result.audit if event["bar_index"] <= now]

    def missing_minutes(bar):
        raise MinuteCoverageError(str(bar.timestamp.date()), "2026-07-21", "2026-09-30")

    full = single_stock_result(bars, asdict(config), execution, result, minute_loader=missing_minutes)
    account_prefix = single_stock_result(bars[:now + 1], asdict(config), execution, prefix,
                                         minute_loader=missing_minutes)
    sells = [order for order in full["orders"] if order["side"] == "SELL" and order["status"] == "filled"
             and order["timestamp"].startswith("2023-12-18")]
    assert len(sells) == 1 and sells[0]["reason"] == REASON
    assert sells[0]["remaining_quantity"] == 0
    assert sells[0]["raw_price"] == pytest.approx(5.27 * (1 - execution["slippage_bps_per_side"] / 10_000))
    assert account_prefix["orders"] == [order for order in full["orders"]
                                         if order["timestamp"] <= bars[now].timestamp.isoformat()]
