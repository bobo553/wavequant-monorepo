"""Defended C-wave entries retain their own lifetime across local line resets."""

from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def xianfeng_january():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text("utf-8"))
    # Include the secondary flip origin; August-only data cannot establish it.
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values[:5])
            for day, *values in raw["bars"] if "2025-01-06" <= day <= "2026-01-09"]
    now = next(i for i, bar in enumerate(bars) if str(bar.timestamp.date()) == "2026-01-06")
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, now, config, generate_system_signals(bars, config)


def test_january_6_known_secondary_context_and_volume_body_break_buy_across_local_reset(xianfeng_january):
    bars, now, _, result = xianfeng_january
    signal = next(s for s in result.signals if s.side == "LONG" and s.bar_index == now)
    assert (bars[now].open, bars[now].high, bars[now].low, bars[now].close, bars[now].volume) == (
        4.08, 4.58, 4.07, 4.44, 85_579_500)
    assert bars[now - 1].close < bars[now].open <= bars[now - 1].high
    assert signal.reason == "system_wave_push_gap"
    assert signal.reference_price == 4.44
    assert signal.invalidation_price == 3.54
    assert signal.target_price == 6.22
    assert signal.rvol == pytest.approx(85_579_500 / 16_738_400)
    event = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "long_signal")
    assert event["wave_local_epoch_recovered"]
    assert event["wave_n_epoch"] != event["entry_local_epoch"]
    assert event["wave_gap_trigger"] == "volume_body_breakout"
    assert event["wave_breakout_high"] == 4.17
    assert event["wave_b_low_date"] == "2025-12-17"
    proof = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "long_transition_evidence")
    assert proof["trend_level"] == 2
    assert str(bars[proof["alternation_low_index"]].timestamp.date()) == "2025-08-08"
    assert str(bars[proof["alternation_index"]].timestamp.date()) == "2025-08-12"
    assert proof["alternation_index"] < proof["attack"] < now
    assert proof["inverse_reentry_path"] == "completed_a_defended_b_gap_attack"


def test_completed_prefix_and_cached_partial_volume_replay_preserve_causal_signal(xianfeng_january):
    bars, now, config, full = xianfeng_january
    completed = bars[:now + 1]
    prefix = generate_system_signals(completed, config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= now]
    assert prefix.audit == [e for e in full.audit if e["bar_index"] <= now]
    before = generate_system_signals(bars[:now], config)
    assert before.signals == [s for s in prefix.signals if s.bar_index < now]
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:now], replace(bars[now], volume=bars[now - 1].volume)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(s.side == "LONG" and s.bar_index == now for s in early.signals)
    replay = generate_system_signals(completed, config, chart_history_cache=cache)
    assert replay.signals == prefix.signals
    assert replay.audit == prefix.audit


@pytest.mark.parametrize("missing", ["before_attack", "retired"])
def test_cross_epoch_wave_cannot_borrow_unknown_or_retired_alternation(xianfeng_january, monkeypatch, missing):
    from wavequant.domain.strategies.chart_entry_history import chart_entry_history

    bars, now, config, _ = xianfeng_january

    def unavailable(*args, **kwargs):
        history, events = chart_entry_history(*args, **kwargs)
        # Exact joint N/alternation confirmations are separately permitted;
        # this case removes that route as well as the frozen attack permission.
        history = {i: (() if (i < now if missing == "before_attack" else i == now) else
                       tuple(replace(ctx, confirmation_attack=None) for ctx in contexts)
                       if missing == "before_attack" else contexts)
                   for i, contexts in history.items()}
        return history, events

    monkeypatch.setattr("wavequant.domain.strategies.chart_entry_history.chart_entry_history", unavailable)
    result = generate_system_signals(bars[:now + 1], config)
    assert not any(s.side == "LONG" and s.bar_index == now for s in result.signals)
    expected = "wave_no_alternation_at_attack" if missing == "before_attack" else "wave_context_no_longer_live"
    assert any(e["bar_index"] == now and e.get("reason") == expected for e in result.audit)


@pytest.mark.parametrize("observer", ["five_top_entry_history", "two_t_resistance_history"])
def test_cross_epoch_confirmation_still_obeys_global_target_entry_risks(xianfeng_january, monkeypatch, observer):
    bars, now, config, _ = xianfeng_january
    risk = dict(reason="target_entry_test_risk", exit_fraction=.8)
    monkeypatch.setattr(f"wavequant.domain.strategies.integrated_strategy.{observer}",
                        lambda *args, **kwargs: {now: risk})
    result = generate_system_signals(bars[:now + 1], config)
    assert any(e["bar_index"] == now and e["event"] == "wave_gap_observed" for e in result.audit)
    assert not any(s.side == "LONG" and s.bar_index == now for s in result.signals)
    assert any(e["bar_index"] == now and e.get("reason") == "target_entry_test_risk" for e in result.audit)


def test_short_history_without_secondary_flip_cannot_create_january_6_buy(xianfeng_january):
    bars, now, config, _ = xianfeng_january
    short = [bar for bar in bars[:now + 1] if str(bar.timestamp.date()) >= "2025-08-01"]
    result = generate_system_signals(short, config)
    assert not any(s.side == "LONG" and s.bar_index == len(short) - 1 for s in result.signals)
    assert any(e["event"] == "wave_gap_observed" and e["bar_index"] == len(short) - 1 for e in result.audit)


def test_january_6_daily_fallback_executes_buy_with_dated_secondary_and_wave_evidence(xianfeng_january):
    from wavequant.domain.models.config import StrategyConfig
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
    from wavequant.interfaces.research_tools.stock_backtest import single_stock_result

    bars, now, config, generated = xianfeng_january
    profile = whole_wave_profile({"scenarios": {"base": {"execution": StrategyConfig().to_dict()}}})

    def no_minutes(bar):
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture_daily_close")

    view = single_stock_result(bars, config.__dict__, profile["scenarios"]["base"]["execution"],
                               generated, minute_loader=no_minutes)
    buy = next(o for o in view["orders"] if o["side"] == "BUY" and o["status"] == "filled"
               and o["timestamp"].startswith("2026-01-06"))
    assert buy["signal_timestamp"].startswith("2026-01-06")
    assert buy["net_reward_risk"] >= 1.5
    assert buy["quantity"] > 0
    proof = next(e for e in buy["decision_evidence"] if e["event"] == "long_transition_evidence")
    assert proof["trend_level"] == 2
    assert proof["alternation_index_date"] == "2025-08-12"
    signal = next(e for e in buy["decision_evidence"] if e["event"] == "long_signal")
    assert signal["wave_local_epoch_recovered"] and signal["wave_gap_volume"] == bars[now].volume
