"""A new daily low through an inverse-N neckline prevents fresh entries."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_structure.polyline import LinePoint, PointKind, ReversalPoint
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile


@pytest.fixture(scope="module")
def xianfeng_january():
    raw = json.loads((Path(__file__).parent / "fixtures/xianfeng_2026_resistance.json").read_text("utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values[:5])
            for day, *values in raw["bars"] if "2025-01-06" <= day <= "2026-01-30"]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, dates, config


def test_january_26_lower_low_and_inverse_neckline_break_is_observation_without_buy_or_new_exit(xianfeng_january):
    bars, dates, config = xianfeng_january
    now = dates["2026-01-26"]
    result = generate_system_signals(bars[:now + 1], config)
    assert (bars[now].open, bars[now].high, bars[now].low, bars[now].close, bars[now].volume) == (
        4.97, 5.18, 4.81, 5.15, 71_396_200)
    assert bars[now - 1].low == 4.92
    assert not any(s.bar_index == now and s.side == "LONG" for s in result.signals)
    assert not any(s.bar_index == now and s.side == "EXIT" for s in result.signals)
    rejection = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "entry_rejected"
                     and e.get("reason") == "inverse_n_new_low_requires_observation")
    assert rejection["inverse_origin_date"] == "2026-01-20"
    assert rejection["inverse_origin_high"] == 5.24
    assert rejection["inverse_neckline_date"] == "2026-01-21"
    assert rejection["inverse_neckline_low"] == 4.91
    assert rejection["inverse_rebound_date"] == "2026-01-23"
    assert rejection["inverse_rebound_high"] == 5.16
    assert rejection["inverse_rebound_known_date"] == "2026-01-26"
    assert rejection["inverse_observed_low"] == 4.81
    assert not rejection["inverse_close_break"]
    assert any(s.side == "LONG" and s.bar_index == dates["2026-01-06"] for s in result.signals)


def sample():
    rows = [(11, 12, 10, 11.5), (10.5, 11, 8, 9), (9, 9.8, 8.2, 9.4),
            (9.5, 10, 8.5, 9.8), (9.7, 10, 8.4, 9.9), (9.9, 10.5, 7.9, 10.2)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=i), "TEST", *row, 100) for i, row in enumerate(rows)]
    points = tuple(ReversalPoint(LinePoint(i, 0, kind, price), known, "fixture")
                   for i, kind, price, known in ((0, PointKind.HIGH, 12, 1),
                                                (1, PointKind.LOW, 8, 2),
                                                (3, PointKind.HIGH, 10, 4)))
    return bars, points


def test_wick_break_is_entry_observation_even_with_bullish_close_and_no_volume_filter():
    from wavequant.domain.strategies.inverse_n_entry import inverse_n_low_entry_risk

    bars, points = sample()
    bars[-1] = replace(bars[-1], volume=0)
    proof = inverse_n_low_entry_risk(bars, 5, points)
    assert proof is not None
    assert proof["reason"] == "inverse_n_new_low_requires_observation"
    assert not proof["inverse_close_break"]
    assert proof == inverse_n_low_entry_risk([*bars, replace(bars[-1], timestamp=bars[-1].timestamp + timedelta(days=1))], 5, points)


@pytest.mark.parametrize("failure", ["touch_previous_low", "touch_neckline", "origin_reclaimed",
                                   "origin_touched", "neckline_already_broken", "rebound_extended",
                                   "rebound_known_future", "same_day_rebound", "wrong_kinds"])
def test_unbroken_unavailable_invalid_or_stale_inverse_cannot_block_entry(failure):
    from wavequant.domain.strategies.inverse_n_entry import inverse_n_low_entry_risk

    bars, points = sample()
    if failure == "touch_previous_low":
        bars[-1] = replace(bars[-1], low=bars[-2].low)
    elif failure == "touch_neckline":
        bars[-1] = replace(bars[-1], low=8)
    elif failure == "origin_reclaimed":
        bars[-1] = replace(bars[-1], high=12.01)
    elif failure == "origin_touched":
        bars[-1] = replace(bars[-1], high=12)
    elif failure == "neckline_already_broken":
        bars[2] = replace(bars[2], low=7.95)
    elif failure == "rebound_extended":
        bars[-2] = replace(bars[-2], high=10.1)
    elif failure == "rebound_known_future":
        points = (*points[:2], replace(points[2], confirmed_index=6))
    elif failure == "same_day_rebound":
        points = (*points[:2], ReversalPoint(LinePoint(5, 0, PointKind.HIGH, 10.5), 5, "fixture"))
    elif failure == "wrong_kinds":
        points = (points[1], points[0], points[2])
    assert inverse_n_low_entry_risk(bars, 5, points) is None


def test_prefix_and_cached_partial_day_never_backdate_or_reuse_missing_new_low(xianfeng_january):
    bars, dates, config = xianfeng_january
    now = dates["2026-01-26"]
    full = generate_system_signals(bars, config)
    prefix = generate_system_signals(bars[:now + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= now]
    assert prefix.audit == [e for e in full.audit if e["bar_index"] <= now]
    before = generate_system_signals(bars[:now], config)
    assert before.audit == [e for e in prefix.audit if e["bar_index"] < now]
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:now], replace(bars[now], low=4.93)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert any(s.side == "LONG" and s.bar_index == now for s in early.signals)
    assert not any(e["bar_index"] == now and e.get("reason") == "inverse_n_new_low_requires_observation"
                   for e in early.audit)
    completed = generate_system_signals(bars[:now + 1], config, chart_history_cache=cache)
    assert completed.signals == prefix.signals
    assert completed.audit == prefix.audit


def test_january_26_cannot_fill_or_add_even_with_optional_reward_risk_off(xianfeng_january):
    from wavequant.domain.models.config import StrategyConfig
    from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
    from wavequant.interfaces.research_tools.stock_backtest import single_stock_result

    bars, dates, config = xianfeng_january
    bars = bars[:dates["2026-01-26"] + 1]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": StrategyConfig().to_dict()}}})
    execution = dict(profile["scenarios"]["base"]["execution"], initial_capital=100000,
                     max_position_weight=1, net_reward_risk_filter=False)

    def no_minutes(bar):
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture_daily_close")

    view = single_stock_result(bars, config.__dict__, execution, minute_loader=no_minutes)
    assert not any(o["side"] == "BUY" and o["timestamp"].startswith("2026-01-26") for o in view["orders"])
    assert any(o["side"] == "BUY" and o["status"] == "filled" and o["timestamp"].startswith("2026-01-06")
               for o in view["orders"])


def test_global_observation_blocks_standalone_base_channel_with_volume_filter_off(xianfeng_january, monkeypatch):
    bars, dates, config = xianfeng_january
    now = dates["2026-01-26"]
    proof = dict(stop=4.2, target=8.0, counter_ratio=.62, breakout_volume_multiple=3)
    monkeypatch.setattr("wavequant.domain.strategies.shallow_base_breakout.shallow_base_history",
                        lambda *args, **kwargs: ([], {now: proof}))
    result = generate_system_signals(bars[:now + 1], replace(config, volume_filter=False))
    assert not any(s.side == "LONG" and s.bar_index == now for s in result.signals)
    assert any(e["bar_index"] == now and e.get("reason") == "inverse_n_new_low_requires_observation"
               for e in result.audit)
