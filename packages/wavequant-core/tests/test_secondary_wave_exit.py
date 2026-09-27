"""A resisted level-two C wave must use only already-known A/B anchors."""

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


def guofang():
    raw = json.loads((Path(__file__).parent / "fixtures/guofang_2020_secondary_wave.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    return bars, {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}


def test_guofang_known_abc_target_and_resisted_break_clear_on_july_15():
    bars, dates = guofang()
    history, _ = hierarchical_history(bars)
    risks = trend_flip_exit_history(bars, history)
    assert not any(dates["2020-07-07"] <= index < dates["2020-07-15"] for index in risks)
    risk = risks[dates["2020-07-15"]]
    assert risk["reason"] == "secondary_wave_target_resistance_clear"
    assert risk["exit_fraction"] == 1
    assert risk["execution_model"] == "same_day_close"
    assert risk["trend_origin_date"] == "2020-04-28"
    assert risk["trend_key_date"] == "2020-06-04"
    assert risk["wave_b_date"] == "2020-06-12"
    assert risk["trend_resistance_dates"] == ["2020-07-10", "2020-07-13"]
    assert risk["trend_indecision_date"] == "2020-07-14"
    assert risk["wave_equal_target"] == pytest.approx(5.9501408392)
    assert bars[dates["2020-07-13"]].high < risk["wave_equal_target"] <= bars[dates["2020-07-14"]].high
    assert bars[dates["2020-07-15"]].close < risk["trend_indecision_low"]

    prefix = bars[: dates["2020-07-15"] + 1]
    assert trend_flip_exit_history(prefix, hierarchical_history(prefix)[0]) == {
        index: row for index, row in risks.items() if index < len(prefix)
    }

    decision = dates["2020-07-06"]
    signal = Signal(bars[decision].timestamp, bars[0].symbol, decision, "LONG", bars[decision].close,
                    4.0, "fixture", bars[decision].timestamp, 0, None, "fixture", 100)
    config = StrategyConfig(trend_flip_adverse_exit=True, exit_on_target=False,
                            risk_fraction=0.1, max_position_weight=0.8, max_participation=1)
    result = run_portfolio({bars[0].symbol: bars}, [signal], config)
    sales = [order for order in result.orders if order["side"] == "SELL" and order["status"] == "filled"]
    assert len(sales) == 1
    assert sales[0]["timestamp"] == bars[dates["2020-07-15"]].timestamp.isoformat()
    assert sales[0]["reason"] == risk["reason"]
    assert sales[0]["position_closed"] is True


def synthetic(*, future_key=False):
    rows = [
        (4.9, 5.1, 4.67, 4.92),
        (5.38, 5.70, 5.29, 5.50),
        (4.94, 5.09, 4.92, 5.01),
        (5.40, 5.65, 5.30, 5.58),
        (5.54, 5.81, 5.44, 5.61),
        (5.61, 5.83, 5.56, 5.75),
        (5.75, 5.96, 5.59, 5.80),
        (5.75, 5.80, 5.55, 5.57),
    ]
    bars = [Bar(datetime(2020, 7, 1) + timedelta(days=i), "TEST", *row, 1000)
            for i, row in enumerate(rows)]
    origin = dict(index=0, kind="L", value=4.67, available_at=2)
    high = dict(index=1, kind="H", value=5.70, available_at=4 if future_key else 2)
    pullback = dict(index=2, kind="L", value=4.92, available_at=2)
    history = {i: {1: [high, pullback], 2: [origin]} for i in range(3, len(bars))}
    return bars, history


@pytest.mark.parametrize("missing", ["first_resistance", "second_resistance", "target", "twin_shadows", "close_break", "future_key"])
def test_secondary_wave_exit_requires_each_causal_condition(missing):
    bars, history = synthetic(future_key=missing == "future_key")
    if missing == "first_resistance":
        bars[4] = replace(bars[4], open=5.58, high=5.72, low=5.55, close=5.71)
    elif missing == "second_resistance":
        bars[5] = replace(bars[5], high=5.78, low=5.56, close=5.75)
    elif missing == "target":
        bars[6] = replace(bars[6], high=5.94)
    elif missing == "twin_shadows":
        bars[6] = replace(bars[6], low=5.72)
    elif missing == "close_break":
        bars[7] = replace(bars[7], open=5.75, close=5.61)
    assert not trend_flip_exit_history(bars, history)


def test_secondary_wave_exit_synthetic_positive_path():
    bars, history = synthetic()
    risks = trend_flip_exit_history(bars, history)
    assert list(risks) == [7]
    assert risks[7]["trend_resistance_dates"] == ["2020-07-05", "2020-07-06"]
