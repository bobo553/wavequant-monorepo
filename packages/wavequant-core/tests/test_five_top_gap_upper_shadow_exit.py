"""A prior five-top followed by a weak gap and dominant upper shadow clears."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies import wave_exhaustion_exit
from wavequant.domain.strategies import integrated_strategy
from wavequant.domain.strategies.two_t_resistance import two_t_resistance_history
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError


REASON = "wave_five_top_gap_upper_shadow_clear"


@pytest.fixture
def sample():
    bars = [
        Bar(datetime(2025, 4, 23), "sh.601086", 15.0, 16.0, 14.0, 16.0, 1_000_000),
        Bar(datetime(2025, 4, 24), "sh.601086", 18.0, 20.0, 17.0, 19.0, 1_000_000),
        Bar(datetime(2025, 4, 25), "sh.601086", 17.12, 19.54, 17.12, 17.12, 97_118_640),
        Bar(datetime(2025, 4, 28), "sh.601086", 18.0, 18.4, 17.8, 18.2, 1_000_000),
    ]
    reached = dict(event="wave_projection_target_reached", attack=0, bar_index=1,
                   reached_stage="five_top", reached_target=19.0)
    return bars, [reached]


def observe(bars, index, events):
    return wave_exhaustion_exit.observe_five_top_gap_upper_shadow_clear(bars, index, events)


def entry(bars, index=1):
    bar = bars[index]
    return Signal(bar.timestamp, bar.symbol, index, "LONG", bar.close, 1.0,
                  "fixture", bars[0].timestamp, 0, None, "fixture", 100.0)


def config(**changes):
    return StrategyConfig(entry_at_close=True, wave_exhaustion_exit=True, exit_on_target=False,
                          max_hold_bars=200, max_participation=1, slippage_bps_per_side=0, **changes)


def test_guofang_doji_clears_all_without_a_prior_reduction(sample):
    bars, events = sample
    for reduced in (False, True):
        decision = wave_exhaustion_exit.observe_wave_exhaustion(
            bars, 2, events, config(), reduced=reduced, entry_index=1)
        assert decision is not None
        assert decision["reason"] == REASON
        assert decision["exit_fraction"] == 1.0
        assert "exit_target_fraction" not in decision
        assert (decision["observed_open"], decision["observed_high"],
                decision["observed_low"], decision["observed_close"], decision["observed_volume"]) == (
                    17.12, 19.54, 17.12, 17.12, 97_118_640)
        assert decision["previous_close"] == 19.0
        assert decision["wave_upper_shadow_fraction"] == 1.0
        assert (decision["wave_reached_date"], decision["wave_reached_stage"],
                decision["wave_reached_price"], decision["execution_model"]) == (
                    "2025-04-24", "five_top", 19.0, "same_day_close")


@pytest.mark.parametrize("volume", [0, 1, 1_000_000])
@pytest.mark.parametrize("stage", ["five_top", "ten_full"])
def test_half_range_upper_shadow_is_inclusive_and_has_no_volume_condition(sample, volume, stage):
    bars, events = sample
    bars[2] = replace(bars[2], open=17.0, high=19.0, low=15.0, close=16.0, volume=volume)
    decision = observe(bars, 2, [dict(events[0], reached_stage=stage)])
    assert decision["reason"] == REASON
    assert decision["wave_upper_shadow_fraction"] == 0.5
    assert decision["exit_fraction"] == 1.0


def test_decimal_half_range_boundary_is_not_rejected_by_float_subtraction(sample):
    bars, events = sample
    bars[2] = replace(bars[2], low=14.70)
    decision = observe(bars, 2, events)
    assert decision is not None
    assert decision["wave_upper_shadow_fraction"] == 0.5


@pytest.mark.parametrize("change", ["bullish", "equal_open", "short_shadow", "flat"])
def test_clear_requires_a_lower_open_weak_body_and_dominant_upper_shadow(sample, change):
    bars, events = sample
    if change == "bullish":
        bars[2] = replace(bars[2], close=17.2)
    elif change == "equal_open":
        bars[2] = replace(bars[2], open=bars[1].close, close=bars[1].close)
    elif change == "short_shadow":
        bars[2] = replace(bars[2], open=17.0, high=18.99, low=15.0, close=16.0)
    else:
        bars[2] = replace(bars[2], high=17.12)
    assert observe(bars, 2, events) is None


@pytest.mark.parametrize("change", ["one_p", "two_t", "same_day", "future", "invalidated",
                                    "past_invalidated", "future_invalidated"])
def test_clear_uses_only_a_prior_live_five_top(sample, change):
    bars, events = sample
    reached = events[0]
    if change in ("one_p", "two_t"):
        events = [dict(reached, reached_stage=change)]
    elif change in ("same_day", "future"):
        events = [dict(reached, bar_index=2 if change == "same_day" else 3)]
    else:
        invalidated_index = {"invalidated": 2, "past_invalidated": 1, "future_invalidated": 3}[change]
        events = [*events, dict(reached, event="wave_projection_invalidated", bar_index=invalidated_index)]
    decision = observe(bars, 2, events)
    assert (decision is not None) == (change == "future_invalidated")


def test_owned_five_top_exits_at_same_close_and_prefix_has_identical_orders(sample):
    bars, events = sample
    portfolio = run_portfolio({bars[0].symbol: bars}, [entry(bars)], config(),
                              wave_events={bars[0].symbol: events})
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["execution_model"], clear["price"]) == (
        "2025-04-25", REASON, "same_day_close", 17.12)
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    prefix = run_portfolio({bars[0].symbol: bars[:3]}, [entry(bars)], config(),
                           wave_events={bars[0].symbol: events})
    assert prefix.orders == portfolio.orders


def test_global_exit_replay_clears_at_close_without_projection_metadata(sample):
    bars, _ = sample
    exit_signal = replace(entry(bars, 2), side="EXIT", reason=REASON)
    portfolio = run_portfolio({bars[0].symbol: bars[:3]}, [entry(bars), exit_signal],
                              replace(config(), wave_exhaustion_exit=False))
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["execution_model"]) == (
        "2025-04-25", REASON, "same_day_close")
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0


def test_unsellable_close_defers_full_exit_and_next_sellable_session_clears(sample):
    bars, events = sample
    bars[2] = replace(bars[2], sellable=False)
    portfolio = run_portfolio({bars[0].symbol: bars}, [entry(bars)], config(),
                              wave_events={bars[0].symbol: events})
    deferred = next(order for order in portfolio.orders if order["status"] == "deferred")
    assert (deferred["timestamp"][:10], deferred["reason"], deferred["decision_reason"],
            deferred["exit_fraction"]) == ("2025-04-25", "not_sellable", REASON, 1.0)
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert (clear["timestamp"][:10], clear["reason"], clear["remaining_quantity"]) == (
        "2025-04-28", REASON, 0)
    assert clear["quantity"] == buy["quantity"]


def test_same_day_open_buy_is_deferred_by_t1_then_fully_cleared(sample):
    bars, events = sample
    portfolio = run_portfolio({bars[0].symbol: bars}, [entry(bars)],
                              replace(config(), entry_at_close=False),
                              wave_events={bars[0].symbol: events})
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    deferred = next(order for order in portfolio.orders if order["reason"] == "T+1")
    assert deferred["decision_reason"] == REASON
    assert deferred["exit_fraction"] == 1.0
    assert buy["timestamp"][:10] == "2025-04-25"
    assert clear["timestamp"][:10] == "2025-04-28"
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0


def test_global_exit_cancels_same_day_entry_and_add_on_in_either_order(sample):
    bars, _ = sample
    new_entry = entry(bars, 2)
    exit_signal = replace(new_entry, side="EXIT", reason=REASON)
    for held in (False, True):
        for day_signals in ([new_entry, exit_signal], [exit_signal, new_entry]):
            signals = [entry(bars), *day_signals] if held else day_signals
            portfolio = run_portfolio({bars[0].symbol: bars[:3]}, signals,
                                      replace(config(allow_add_on=True), wave_exhaustion_exit=False))
            buys = [order for order in portfolio.orders if order["side"] == "BUY"
                    and order["status"] == "filled"]
            assert len(buys) == int(held)
            assert not any(order["timestamp"][:10] == "2025-04-25" for order in buys)


@pytest.mark.parametrize("ordinary", [False, True])
def test_existing_gap_volume_clear_keeps_its_reason_when_both_gap_rules_hold(sample, ordinary):
    bars, events = sample
    bars[0] = replace(bars[0], open=17.0, high=17.5)
    if ordinary:
        events.append(dict(event="wave_ordinary_entry", attack=0, bar_index=1,
                           one_p=20.0, a_high=21.0, two_t=22.0, target=30.0))
    assert observe(bars, 2, events) is not None
    decision = wave_exhaustion_exit.observe_wave_exhaustion(bars, 2, events, config(), entry_index=1)
    assert decision is not None
    assert decision["reason"] == "wave_five_top_gap_volume_clear"


def test_existing_child_volume_clear_keeps_its_reason_when_shadow_rule_also_holds(sample):
    bars, events = sample
    values = [(21, 23, 18, 20), (20, 22, 19, 21), (22, 24, 20, 22), (19, 23, 17, 18)]
    bars = [replace(bar, open=float(open_), high=float(high), low=float(low), close=float(close),
                    volume=2_000_000 if index == 3 else 1_000_000)
            for index, (bar, (open_, high, low, close)) in enumerate(zip(bars, values))]
    assert observe(bars, 3, events) is not None
    decision = wave_exhaustion_exit.observe_wave_exhaustion(bars, 3, events, config(), entry_index=2)
    assert decision is not None
    assert decision["reason"] == "wave_five_top_child_volume_clear"


def test_existing_abnormal_followthrough_keeps_its_reason_when_shadow_rule_also_holds(sample):
    bars, events = sample
    bars[1] = replace(bars[1], high=22.0)
    assert observe(bars, 2, events) is not None
    decision = wave_exhaustion_exit.observe_wave_exhaustion(bars, 2, events, config(), reduced=True, entry_index=0)
    assert decision is not None
    assert decision["reason"] == "wave_abnormal_followthrough_clear"


def test_existing_structural_stop_keeps_its_reason_when_shadow_rule_also_holds(sample):
    bars, events = sample
    signal = replace(entry(bars), invalidation_price=18.0)
    portfolio = run_portfolio({bars[0].symbol: bars}, [signal], config(),
                              wave_events={bars[0].symbol: events})
    clear = next(order for order in portfolio.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["reason"] == "structural_stop_observed"
    assert clear["remaining_quantity"] == 0


def test_existing_pressure_clear_keeps_its_reason_when_shadow_rule_also_holds():
    start = datetime(2025, 1, 1)
    bars = [Bar(start + timedelta(days=index), "sh.601086", 10, 11, 9, 10, 1_000_000)
            for index in range(20)]
    bars.extend(Bar(start + timedelta(days=index), "sh.601086", *values) for index, values in enumerate([
        (22, 23, 18, 19, 3_000_000), (17, 21, 16, 20, 1_000_000),
        (20, 22, 19, 21, 1_000_000), (19, 23, 19, 19, 1_000_000),
    ], start=20))
    events = [dict(event="wave_projection_target_reached", attack=21, bar_index=22,
                   reached_stage="five_top", reached_target=22.0)]
    assert observe(bars, 23, events) is not None
    signal = replace(entry(bars, 22), trigger_timestamp=bars[21].timestamp)
    portfolio = run_portfolio({bars[0].symbol: bars}, [signal], config(pressure_adverse_exit=True),
                              wave_events={bars[0].symbol: events}, positive_n_bars={bars[0].symbol: {21: 21}})
    clear = next(order for order in portfolio.orders if order["side"] == "SELL" and order["status"] == "filled")
    assert clear["reason"] == "pressure_adverse_clear"
    assert clear["remaining_quantity"] == 0


@pytest.fixture
def overlapping_target(sample):
    bars, events = sample
    bars[0] = replace(bars[0], open=17.0, high=17.5)
    bars[1] = replace(bars[1], open=16.0, high=23.0, low=15.0)
    bars[2] = replace(bars[2], open=18.0, high=22.0, low=14.0, close=16.0)
    return bars, events


@pytest.mark.parametrize("legacy", ["two_t", "c_equal"])
def test_existing_target_full_clear_keeps_its_reason_and_evidence_in_account(overlapping_target, legacy):
    bars, events = overlapping_target
    assert observe(bars, 2, events) is not None
    if legacy == "two_t":
        events.append(dict(event="wave_projection_ready", attack=0, bar_index=1, two_t=21.0))
        legacy_risk = two_t_resistance_history(bars, events)[2]
        assert legacy_risk["exit_fraction"] == 1.0
    else:
        events.append(dict(event="wave_c_equal_target", attack=0, bar_index=0, target=23.01, defense=13.0))
        equal_risk = wave_exhaustion_exit.observe_c_equal_near_risk(bars, 2, events)
        assert equal_risk is not None
        legacy_risk = equal_risk
        assert legacy_risk["reason"] == "wave_c_equal_near_volume_clear"
    portfolio = run_portfolio({bars[0].symbol: bars}, [entry(bars, 0)],
                              replace(config(allow_same_day_exit=True), entry_at_close=False),
                              wave_events={bars[0].symbol: events})
    clear = next(order for order in portfolio.orders if order["side"] == "SELL" and order["status"] == "filled"
                 and order["timestamp"] == bars[2].timestamp.isoformat())
    assert clear["reason"] == legacy_risk["reason"]
    assert clear["remaining_quantity"] == 0
    assert clear["exit_fraction"] == 1.0
    if legacy == "two_t":
        assert clear["wave_reached_stage"] == "two_t"
        assert clear["wave_reached_price"] == 21.0
    else:
        assert clear["wave_c_equal_target"] == 23.01


@pytest.mark.parametrize("legacy", ["two_t", "c_equal"])
def test_global_shadow_exit_yields_to_an_existing_target_full_clear(overlapping_target, monkeypatch, legacy):
    bars, events = overlapping_target
    shadow = observe(bars, 2, events)
    assert shadow is not None
    if legacy == "two_t":
        ready = dict(event="wave_projection_ready", attack=0, bar_index=1, two_t=21.0)
        legacy_risk = two_t_resistance_history(bars, [ready])[2]
        monkeypatch.setattr(integrated_strategy, "two_t_resistance_history", lambda *_args: {2: legacy_risk})
    else:
        equal = dict(event="wave_c_equal_target", attack=0, bar_index=0, target=23.01, defense=13.0)
        equal_risk = wave_exhaustion_exit.observe_c_equal_near_risk(bars, 2, [equal])
        assert equal_risk is not None
        legacy_risk = equal_risk
        monkeypatch.setattr(integrated_strategy, "observe_c_equal_near_risk",
                            lambda _bars, index, _events: legacy_risk if index == 2 else None)
    monkeypatch.setattr(integrated_strategy, "observe_five_top_upper_shadow_clear",
                        lambda _bars, index, _events: shadow if index == 2 else None)
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    result = generate_system_signals(bars, SystemStrategy(**profile["strategy"]))
    signal = next(signal for signal in result.signals if signal.side == "EXIT" and signal.bar_index == 2)
    assert legacy_risk["reason"] in signal.reason.split("|")
    assert REASON not in signal.reason.split("|")
    evidence = next(event for event in result.audit if event["event"] == "exit_signal" and event["bar_index"] == 2)
    assert evidence.get("wave_reached_stage") != "five_top"
    if legacy == "two_t":
        assert evidence["wave_reached_price"] == 21.0
    else:
        assert evidence["wave_c_equal_target"] == 23.01


@pytest.fixture(scope="module")
def real_guofang():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2025_five_top_low_open.json").read_text(encoding="utf-8"))
    bars = [Bar(**dict(row, timestamp=datetime.fromisoformat(row["timestamp"]))) for row in raw["bars"]
            if row["timestamp"][:10] >= "2024-01-01"]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    strategy = SystemStrategy(**raw["strategy"])
    full = generate_system_signals(bars, strategy)
    index = dates["2025-04-25"]
    prefix = generate_system_signals(bars[:index + 1], strategy)
    return bars, dates, full, prefix


def test_real_guofang_generates_prior_known_target_exit_and_causal_prefix(real_guofang):
    bars, dates, full, prefix = real_guofang
    index = dates["2025-04-25"]
    exit_signal = next(signal for signal in full.signals if signal.bar_index == index and signal.side == "EXIT")
    assert "wave_five_top_body_upper_shadow_clear" in exit_signal.reason.split("|")
    assert not any(signal.side == "LONG" and signal.bar_index == index for signal in full.signals)
    evidence = next(event for event in full.audit if event["bar_index"] == index and event["event"] == "exit_signal")
    assert evidence["exit_fraction"] == 1.0
    assert evidence["wave_reached_stage"] in ("five_top", "ten_full")
    assert evidence["wave_reached_date"] < "2025-04-25"
    assert evidence["wave_upper_shadow_fraction"] == 1.0
    assert evidence["mother_body_low"] == bars[index - 1].open
    assert evidence["mother_body_high"] == bars[index - 1].close
    assert evidence["child_body_low"] == evidence["child_body_high"] == bars[index].open
    assert tuple(round(getattr(bars[index], field), 2) for field in ("open", "high", "low", "close")) == (
        17.12, 19.54, 17.12, 17.12)
    assert bars[index].volume == 97_118_640
    assert bars[index].sellable is False
    assert bars[index].close / bars[index].adjustment_factor == pytest.approx(12.53)
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= index]
    prefix_exit = next(event for event in prefix.audit if event["bar_index"] == index and event["event"] == "exit_signal")
    assert prefix_exit == evidence


def test_real_guofang_opened_lower_limit_clears_at_the_same_close_with_an_explicit_model(real_guofang):
    bars, dates, full, _ = real_guofang
    index = dates["2025-04-25"]
    exit_signal = next(signal for signal in full.signals if signal.bar_index == index and signal.side == "EXIT")
    portfolio = run_portfolio({bars[0].symbol: bars}, [entry(bars, dates["2025-04-24"]), exit_signal],
                              replace(config(), wave_exhaustion_exit=False, nonflat_limit_close_fill=True),
                              wave_events={bars[0].symbol: [event for event in full.audit
                                                            if event["event"].startswith("wave_projection_")]})
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert clear["timestamp"][:10] == "2025-04-25"
    assert clear["remaining_quantity"] == 0
    assert clear["quantity"] == buy["quantity"]
    assert clear["reason"] == "wave_five_top_body_upper_shadow_clear"
    assert clear["execution_model"] == "same_day_close"
    assert clear["price"] / bars[index].adjustment_factor == pytest.approx(12.53)
    assert clear["fill_assumption"] == "nonflat_limit_close_sell_without_queue_verification"
    assert clear["applied_slippage_bps"] == 0.0


def test_real_guofang_global_exit_retains_the_missing_minute_fallback_after_a_below_lot_reduce(real_guofang):
    bars, dates, full, _ = real_guofang
    index = dates["2025-04-25"]
    exit_signal = next(signal for signal in full.signals if signal.bar_index == index and signal.side == "EXIT")
    buy_signal = replace(entry(bars, dates["2025-04-24"]), trigger_timestamp=bars[dates["2025-04-24"]].timestamp)

    def missing(_bar):
        raise MinuteCoverageError("2025-04-25", None, None)

    portfolio = run_portfolio(
        {bars[0].symbol: bars}, [buy_signal, exit_signal],
        replace(config(), initial_capital=100_000, risk_fraction=0.02, staged_exit_intraday=True,
                missing_minute_daily_fallback=True, nonflat_limit_close_fill=True),
        wave_events={bars[0].symbol: [event for event in full.audit
                                    if event["event"].startswith("wave_projection_")]}, minute_loader=missing,
    )
    assert any(order["side"] == "SELL" and order["reason"] == "reduction_below_one_lot"
               for order in portfolio.orders)
    buy, clear = [order for order in portfolio.orders if order["status"] == "filled"]
    assert buy["quantity"] * bars[dates["2025-04-24"]].adjustment_factor == pytest.approx(100)
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    assert clear["timestamp"][:10] == "2025-04-25"
    assert clear["price"] / bars[index].adjustment_factor == pytest.approx(12.53)
    assert clear["execution_model"] == "same_day_close"
    assert clear["decision_source"] == "strategy_exit_signal"
    assert clear["applied_slippage_bps"] == 0.0
    assert "execution_timestamp" not in clear
    fallback = next(evidence for evidence in portfolio.minute_fallbacks if evidence["date"] == "2025-04-25")
    assert fallback["purpose"] == "five_top_upper_shadow_exit"
    assert clear["minute_fallback"] == fallback
