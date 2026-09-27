from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.hierarchical_entry import hierarchical_history
from wavequant.domain.strategies.trend_flip_exit import trend_flip_exit_history


def lexin_bars():
    raw = json.loads((Path(__file__).parent / "fixtures/lexin_2026_squeeze.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    return bars[:dates["2026-07-06"] + 1], dates


def test_lexin_secondary_last_fall_high_reduces_once_then_clears_on_first_lower_close():
    bars, dates = lexin_bars()
    risks = trend_flip_exit_history(bars, hierarchical_history(bars)[0])
    warning = risks[dates["2026-07-02"]]
    assert warning["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert warning["trend_level"] == 2
    assert warning["trend_key_date"] == "2026-04-30"
    assert warning["trend_key_high"] == pytest.approx(bars[dates["2026-04-30"]].high)
    assert warning["exit_target_fraction"] == 0.8
    assert risks[dates["2026-07-03"]]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[dates["2026-07-06"]]["reason"] == "trend_last_fall_high_lower_close_clear"
    prefix = bars[:dates["2026-07-02"] + 1]
    assert trend_flip_exit_history(prefix, hierarchical_history(prefix)[0]) == {
        index: risk for index, risk in risks.items() if index < len(prefix)
    }

    decision = dates["2026-06-30"]
    signal = Signal(bars[decision].timestamp, bars[0].symbol, decision, "LONG", bars[decision].close,
                    10, "fixture", bars[decision].timestamp, 0, None, "fixture", 100)
    config = StrategyConfig(trend_flip_adverse_exit=True, exit_on_target=False, risk_fraction=0.1,
                            max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    filled = [order for order in result.orders if order["status"] == "filled"]
    assert [order["reason"] for order in filled[1:]] == [
        "trend_last_fall_high_upper_shadow_reduce", "trend_last_fall_high_lower_close_clear"
    ]
    assert filled[1]["timestamp"].startswith("2026-07-02")
    assert filled[1]["remaining_quantity"] > 0
    assert filled[2]["timestamp"].startswith("2026-07-06")
    assert filled[2]["remaining_quantity"] == 0
    assert not any(order["side"] == "SELL" and order["timestamp"].startswith("2026-07-03")
                   for order in result.orders)

    blocked_bars = bars.copy()
    blocked_bars[dates["2026-07-02"]] = replace(blocked_bars[dates["2026-07-02"]], sellable=False)
    retry = run_portfolio({bars[0].symbol: blocked_bars}, [signal], config)
    retry_fills = [order for order in retry.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert [order["timestamp"][:10] for order in retry_fills] == ["2026-07-03", "2026-07-06"]


@pytest.mark.parametrize("level", [2, 3])
def test_both_trend_levels_require_known_last_fall_high_and_close_break(level):
    rows = [(9.5, 10, 9, 9.5), (9.1, 9.4, 8.5, 9), (9.5, 11, 9.4, 10.2),
            (10.3, 11.2, 10, 10.5), (10.4, 10.6, 9.8, 10)]
    bars = [Bar(datetime(2026, 1, 1) + timedelta(days=index), "TEST", *row, 1000)
            for index, row in enumerate(rows)]
    high = dict(index=0, kind="H", value=10, available_at=1)
    low = dict(index=1, kind="L", value=8.5, available_at=1)
    history = {index: {level: [high, low]} for index in range(len(bars))}
    risks = trend_flip_exit_history(bars, history)
    assert risks[2]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[2]["trend_level"] == level
    assert risks[3]["reason"] == "trend_last_fall_high_upper_shadow_reduce"
    assert risks[4]["reason"] == "trend_last_fall_high_lower_close_clear"
    high_only = trend_flip_exit_history(bars, {index: {level: [high]} for index in history})
    assert not any(risk["reason"].startswith("trend_last_fall_high_") for risk in high_only.values())
    late_high = dict(high, available_at=2)
    assert trend_flip_exit_history(bars, {index: {level: [late_high, low]} for index in history}) == {}
