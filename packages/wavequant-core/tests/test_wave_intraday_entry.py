from dataclasses import replace
from datetime import timedelta

import pytest

from .test_completed_wave_recovery import sample, config
from wavequant.application.analytics.intraday_entry import resolve_consolidation_entries
from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.strategies.integrated_strategy import generate_system_signals, pivot_history
from wavequant.domain.strategies.wave_continuation import wave_gap_entry
from wavequant.domain.market_structure.wave_projection import WaveProjectionSetup
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar


def scenario():
    bars, dates = sample()
    now = dates["2020-07-20"]
    bars = bars[: now + 1]
    b = bars[-1]
    minute = [
        MinuteBar(b.timestamp.replace(hour=9, minute=35), b.open, 3.18, b.open, 3.17, 12_000_000),
        MinuteBar(b.timestamp.replace(hour=9, minute=40), 3.17, 3.24, 3.16, 3.22, bars[-2].volume - 12_000_000),
        MinuteBar(b.timestamp.replace(hour=9, minute=45), 3.22, 3.30, 3.20, 3.29, 1_000_000),
        MinuteBar(
            b.timestamp.replace(hour=9, minute=50), 3.29, b.high, 3.28, b.close, b.volume - bars[-2].volume - 1_000_000
        ),
    ]
    return bars, now, minute


@pytest.mark.parametrize("late_failure", [False, True])
def test_c_wave_uses_first_cumulative_volume_trigger_without_final_candle(late_failure):
    bars, now, minute = scenario()
    if late_failure:
        bars[-1] = replace(bars[-1], low=2.5, close=2.6)
        minute[-1] = replace(minute[-1], low=2.5, close=2.6)
    strategy = replace(config(), volume_filter=True)
    full = generate_system_signals(bars, strategy)

    def load(bar):
        if bar.timestamp == bars[-1].timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    result, executions, _ = resolve_consolidation_entries(bars, full, strategy, load, daily_fallback=True)
    entry = executions[bars[-1].symbol, now]
    assert entry["timing"]["decision_timestamp"].endswith("09:45:00")
    assert entry["timing"]["observed_volume"] == bars[-2].volume + 1_000_000
    assert entry["price"] == 3.29
    proof = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "long_signal")
    assert proof["wave_gap_trigger"] == "volume"
    assert proof["wave_breakout_date"] == "2020-07-14"
    assert proof["wave_gap_high"] < proof["wave_breakout_high"]
    assert proof["wave_gap_volume"] < bars[-1].volume
    account = run_portfolio(
        {bars[-1].symbol: bars},
        result.signals,
        StrategyConfig(entry_at_close=True, net_reward_risk_filter=False),
        entry_executions=executions,
    )
    order = next(o for o in account.orders if o["timestamp"].startswith("2020-07-20") and o["side"] == "BUY")
    assert order["status"] == "filled"
    assert order["execution_model"] == "intraday_5m_next_open"


def test_c_wave_breakout_needs_no_volume_and_does_not_borrow_future_pivot():
    bars, now, _ = scenario()
    strategy = replace(config(), volume_filter=True)
    result = generate_system_signals(bars, strategy)
    row = next(
        e
        for e in result.audit
        if e["event"] == "wave_continuation_ready"
        and e["attack_index"] == next(i for i, b in enumerate(bars) if str(b.timestamp.date()) == "2020-06-01")
    )
    setup = WaveProjectionSetup(**{k: row[k] for k in WaveProjectionSetup.__dataclass_fields__})
    pivots = pivot_history(bars, strategy)[0][now - 1]
    bars[-1] = replace(bars[-1], high=3.46, close=3.45, volume=1)
    proof = wave_gap_entry(bars, setup, now, pivots=pivots)
    assert proof["wave_gap_trigger"] == "breakout"
    assert proof["wave_breakout_date"] == "2020-07-14"
    assert wave_gap_entry(bars, setup, now, pivots=[replace(p, confirmed_index=now) for p in pivots]) is None
    # Shift the same geometry to a 20%-limit security/day for execution testing.
    bars = [replace(b, timestamp=b.timestamp + timedelta(days=365), symbol="sz.300562") for b in bars]
    result = generate_system_signals(bars, strategy)
    b = bars[-1]
    minute = [
        MinuteBar(b.timestamp.replace(hour=9, minute=35), b.open, 3.46, b.open, 3.45, 1),
        MinuteBar(b.timestamp.replace(hour=9, minute=40), 3.45, 3.46, 3.44, 3.45, 1),
    ]

    def load(bar):
        if bar.timestamp == b.timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    resolved, executions, _ = resolve_consolidation_entries(bars, result, strategy, load, daily_fallback=True)
    assert executions[b.symbol, now]["timing"]["decision_timestamp"].endswith("09:35:00")
    assert any(e.get("wave_gap_trigger") == "breakout" and e["bar_index"] == now for e in resolved.audit)


def test_daily_fallback_is_explicit_and_missing_minute_strict_mode_raises():
    bars, now, _ = scenario()
    strategy = replace(config(), volume_filter=True)
    result = generate_system_signals(bars, strategy)
    resolved, executions, fallbacks = resolve_consolidation_entries(bars, result, strategy, None, daily_fallback=True)
    assert not executions
    assert any(s.side == "LONG" and s.bar_index == now for s in resolved.signals)
    fallback = next(f for f in fallbacks if f["date"] == "2020-07-20")
    assert fallback["purpose"] == "wave_continuation_entry"
    assert fallback["execution_model"] == "same_day_close"
    with pytest.raises(MinuteCoverageError):
        resolve_consolidation_entries(bars, result, strategy, None, daily_fallback=False)


def test_completed_daily_wave_does_not_request_minutes_again_for_same_a():
    bars, now, _ = scenario()
    strategy = replace(config(), volume_filter=True)
    result = generate_system_signals(bars, strategy)
    proof = next(e for e in result.audit if e["event"] == "long_signal" and e["bar_index"] == now)
    # Another gap after the confirmed C stays below the same A high.
    next_bar = replace(
        bars[-1],
        timestamp=bars[-1].timestamp + timedelta(days=1),
        open=3.34,
        low=3.34,
        high=3.50,
        close=3.48,
        volume=40_000_000,
    )
    bars.append(next_bar)
    # Limit the observer to the proven original wave; other N setups have their own eligibility.
    result.audit[:] = [
        e
        for e in result.audit
        if e["event"] != "n_completed"
        and (e["event"] != "wave_continuation_ready" or e["attack_index"] == proof["attack"])
    ]
    loaded = []

    def load(bar):
        loaded.append(bar.timestamp)
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    resolve_consolidation_entries(bars, result, strategy, load, daily_fallback=True)
    assert next_bar.timestamp not in loaded


def test_old_structural_episode_never_replays_minutes_in_new_episode():
    from .test_wave_continuation import sample as huaci_sample

    bars, dates, _ = huaci_sample()
    strategy = replace(config(), volume_filter=True)
    result = generate_system_signals(bars, strategy)
    # These older Ns keep their price defense, but the global engine already
    # retired their structural episode before August. They cannot request 48 replays.
    result.audit[:] = [
        e
        for e in result.audit
        if e["event"] != "n_completed"
        and (e["event"] != "wave_continuation_ready" or str(bars[e["attack_index"]].timestamp.date()) < "2026-01-01")
    ]
    loaded = []

    def load(bar):
        loaded.append(str(bar.timestamp.date()))
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    resolve_consolidation_entries(bars, result, strategy, load, daily_fallback=True)
    assert "2026-08-11" not in loaded


def test_huaci_early_body_minute_keeps_later_daily_gap_confirmation():
    from .test_wave_continuation import sample as huaci_sample
    from wavequant.domain.strategies.integrated_strategy import SystemStrategy

    bars, dates, _ = huaci_sample()
    body, gap = dates["2026-08-26"], dates["2026-09-15"]
    bars = bars[: gap + 1]
    strategy = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3",
        first_pullback_threshold=None,
        strict_n_attack_quality=False,
        preflight_reward_risk=False,
        volume_filter=True,
    )
    daily = generate_system_signals(bars, strategy)
    candle = bars[body]
    early_volume = 2_100_000
    minute = [
        MinuteBar(
            candle.timestamp.replace(hour=9, minute=35), candle.open, candle.high, candle.low, 16.70, early_volume
        ),
        MinuteBar(candle.timestamp.replace(hour=9, minute=40), 16.70, 16.70, 16.60, 16.65, 800_000),
        MinuteBar(
            candle.timestamp.replace(hour=9, minute=45),
            16.65,
            16.65,
            16.40,
            candle.close,
            candle.volume - early_volume - 800_000,
        ),
    ]

    def load(bar):
        if bar.timestamp == candle.timestamp:
            return minute
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture")

    resolved, executions, _ = resolve_consolidation_entries(bars, daily, strategy, load, daily_fallback=True)
    assert (candle.symbol, body) in executions
    confirmations = [
        e
        for e in resolved.audit
        if e["event"] == "long_signal" and e.get("wave_entry_path") and e["bar_index"] in (body, gap)
    ]
    assert [e["bar_index"] for e in confirmations] == [body, gap]
    assert [(e["bar_index"], e["wave_confirmation_phase"]) for e in confirmations] == [(body, "body"), (gap, "gap")]
    assert confirmations[1]["wave_gap_high"] > confirmations[0]["wave_gap_high"]
    assert [(s.bar_index, s.reason) for s in resolved.signals if s.bar_index in (body, gap) and s.side == "LONG"] == [
        (body, "system_wave_push_gap"),
        (gap, "system_wave_push_gap"),
    ]
    prefix_bars = bars[: body + 1]
    prefix_daily = generate_system_signals(prefix_bars, strategy)
    prefix, prefix_executions, _ = resolve_consolidation_entries(
        prefix_bars, prefix_daily, strategy, load, daily_fallback=True
    )
    assert (candle.symbol, body) in prefix_executions
    assert prefix.signals == [s for s in resolved.signals if s.bar_index <= body]
    assert prefix.audit == [e for e in resolved.audit if e["bar_index"] <= body]
