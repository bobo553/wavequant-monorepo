from dataclasses import replace
from datetime import datetime, timedelta

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy
from wavequant.infrastructure.market_data.minute import MinuteBar


def fixture(monkeypatch):
    day = datetime(2026, 8, 24)
    values = [
        (10, 11, 9, 10.9, 100000),
        (10.8, 11.1, 10, 10.7, 100000),
        (10.5, 10.8, 10.1, 10.6, 80000),
        (10.6, 10.8, 10.2, 10.7, 80000),
        (10.9, 11.8, 8, 8.5, 900000),
    ]
    bars = [Bar(day + timedelta(days=i), "sh.601086", *v) for i, v in enumerate(values)]
    candidate = dict(event="n_completed", direction="up", bar_index=0, known_at=0, n_level=1, defense=9)
    generated = SystemResult([], [candidate], {})
    minute = [
        MinuteBar(bars[4].timestamp.replace(hour=9, minute=35 + i * 5), *v)
        for i, v in enumerate(
            [
                (10.9, 11.1, 10.85, 11.05, 50000),
                (11.05, 11.2, 11.0, 11.15, 40000),
                (11.16, 11.3, 11.1, 11.25, 10000),
                (11.25, 11.3, 8, 8.5, 800000),
            ]
        )
    ]
    seen = []

    def generate(prefix, config):
        seen.append(prefix[-1])
        b = prefix[-1]
        s = Signal(b.timestamp, b.symbol, 4, "LONG", b.close, 9, "test", bars[0].timestamp, 0, 2, "轧空", 14)
        return SystemResult(
            [s],
            [
                dict(
                    event="long_signal",
                    bar_index=4,
                    timestamp=b.timestamp.isoformat(),
                    squeeze_confirmation="defended_n_consolidation_gap",
                )
            ],
            {},
        )

    monkeypatch.setattr("wavequant.application.analytics.intraday_entry.generate_system_signals", generate)
    return bars, generated, minute, seen


def test_intraday_confirmation_uses_cumulative_volume_not_end_of_day_and_survives_later_failure(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    result, executions, fallback = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), lambda _: minute, daily_fallback=False
    )
    entry = executions[bars[0].symbol, 4]
    assert seen[0].volume == 90000 and seen[0].low == 10.85
    assert entry["timing"]["decision_timestamp"].endswith("09:40:00")
    assert entry["price"] == 11.16
    assert not fallback
    config = StrategyConfig(entry_at_close=True, net_reward_risk_filter=False, max_participation=1)
    account = run_portfolio({bars[0].symbol: bars}, result.signals, config, entry_executions=executions)
    assert account.orders[0]["status"] == "filled"
    assert account.orders[0]["execution_model"] == "intraday_5m_next_open"
    assert account.open_positions[0]["pending_exit"] == "structural_stop_observed"
    assert not any(o["side"] == "SELL" and o["status"] == "filled" for o in account.orders)
    # A later daily exit cannot retrospectively cancel the earlier minute fill.
    exit_signal = replace(result.signals[0], side="EXIT", reason="risk")
    account = run_portfolio({bars[0].symbol: bars}, result.signals + [exit_signal], config, entry_executions=executions)
    assert account.orders[0]["status"] == "filled"


def test_no_intraday_trigger_from_volume_only_known_in_final_interval(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    minute = [replace(m, volume=100) for m in minute[:-1]] + minute[-1:]
    _, executions, _ = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), lambda _: minute, daily_fallback=False
    )
    assert not executions and not seen


def test_broken_prior_defense_never_loads_minutes(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    bars[2] = replace(bars[2], low=8.9)

    def load(_):
        raise AssertionError("invalidated N must not request minute data")

    _, executions, _ = resolve_consolidation_entries(bars, generated, SystemStrategy(), load, daily_fallback=False)
    assert not executions


def test_minute_limit_lock_is_not_replaced_by_later_close_fill(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    minute[2] = replace(minute[2], open=11.77, high=11.77, low=11.77, close=11.77)
    result, executions, _ = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), lambda _: minute, daily_fallback=False
    )
    account = run_portfolio(
        {bars[0].symbol: bars}, result.signals, StrategyConfig(entry_at_close=True), entry_executions=executions
    )
    assert len(account.orders) == 1
    assert account.orders[0]["reason"] == "not_buyable_intraday"


def test_user_nonflat_policy_uses_only_observed_range_and_preserves_limit_price(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    minute[2] = replace(minute[2], open=11.77, high=11.77, low=11.77, close=11.77)
    result, executions, _ = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), lambda _: minute, daily_fallback=False
    )
    assert executions[bars[0].symbol, 4]["timing"]["observed_nonflat_limit_buyable"] is True
    account = run_portfolio(
        {bars[0].symbol: bars},
        result.signals,
        StrategyConfig(
            entry_at_close=True, nonflat_limit_close_fill=True, net_reward_risk_filter=False, max_entry_gap=0.1
        ),
        entry_executions=executions,
    )
    buy = account.orders[0]
    assert buy["status"] == "filled", buy
    assert buy["price"] == 11.77
    assert buy["applied_slippage_bps"] == 0
    assert buy["fill_assumption"] == "observed_nonflat_limit_intraday_without_queue_verification"
    # The later low cannot turn an observed one-price market into an earlier nonflat fill.
    executions[bars[0].symbol, 4]["timing"]["observed_nonflat_limit_buyable"] = False
    rejected = run_portfolio(
        {bars[0].symbol: bars},
        result.signals,
        StrategyConfig(entry_at_close=True, nonflat_limit_close_fill=True, net_reward_risk_filter=False),
        entry_executions=executions,
    )
    assert rejected.orders[0]["reason"] == "not_buyable_intraday"


def test_missing_minutes_remain_explicit_daily_fallback(monkeypatch):
    bars, generated, minute, seen = fixture(monkeypatch)
    result, executions, fallbacks = resolve_consolidation_entries(
        bars, generated, SystemStrategy(), None, daily_fallback=True
    )
    assert not executions and not seen
    assert fallbacks[0]["purpose"] == "consolidation_entry"
    assert fallbacks[0]["execution_model"] == "same_day_close"
