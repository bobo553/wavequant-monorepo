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


def sample():
    raw = json.loads((Path(__file__).parent / "fixtures/xidian_2025_trend_flip.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"]]
    return bars, {str(b.timestamp.date()): i for i, b in enumerate(bars)}


def test_xidian_first_adverse_candle_exits_with_known_tertiary_key_and_prefix():
    bars, dates = sample()
    risks = trend_flip_exit_history(bars, hierarchical_history(bars)[0])
    assert dates["2025-08-11"] not in risks
    assert dates["2025-08-12"] not in risks
    risk = risks[dates["2025-08-13"]]
    assert risk["trend_key_date"] == "2023-08-11"
    assert risk["trend_level"] == 3
    assert risk["trend_key_high"] == pytest.approx(36.3315397)
    assert risk["trend_resistance_dates"] == ["2025-08-11", "2025-08-12"]
    assert risk["trend_adverse_patterns"] == ["bearish_body", "close_below_previous", "low_below_previous"]
    prefix = bars[: dates["2025-08-13"] + 1]
    assert trend_flip_exit_history(prefix, hierarchical_history(prefix)[0]) == {
        i: r for i, r in risks.items() if i < len(prefix)
    }
    decision = dates["2025-08-07"]
    signal = Signal(
        bars[decision].timestamp,
        bars[0].symbol,
        decision,
        "LONG",
        bars[decision].close,
        20,
        "fixture",
        bars[decision].timestamp,
        0,
        None,
        "fixture",
        100,
    )
    config = StrategyConfig(
        trend_flip_adverse_exit=True,
        exit_on_target=False,
        risk_fraction=0.1,
        max_position_weight=0.8,
        max_participation=1,
    )
    same_day_rebuy = replace(
        signal,
        timestamp=bars[dates["2025-08-13"]].timestamp,
        bar_index=dates["2025-08-13"],
        reference_price=bars[dates["2025-08-13"]].close,
    )
    kwargs = dict(signals=[signal, same_day_rebuy], config=config)
    full = run_portfolio({bars[0].symbol: bars}, **kwargs)
    short = run_portfolio({bars[0].symbol: prefix}, **kwargs)
    assert full.orders == short.orders
    buy, clear = [o for o in full.orders if o["status"] == "filled"]
    assert clear["quantity"] == buy["quantity"]
    assert clear["remaining_quantity"] == 0
    assert clear["timestamp"] == clear["signal_timestamp"] == bars[dates["2025-08-13"]].timestamp.isoformat()


@pytest.mark.parametrize("case", ["resisted", "future_key", "wick_only", "unresisted", "resolved", "new_low_point"])
def test_only_resisted_close_breaks_arm_risk_and_clean_attack_resolves(case):
    rows = [
        (9.5, 10, 9, 9.5),
        (9.4, 11, 9.4, 10.5),
        (10.4, 11, 10, 10.8),
        (10.9, 11, 10.1, 10.2),
        (10.3, 10.5, 9.9, 10),
    ]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=i), "TEST", *row, 1000) for i, row in enumerate(rows)]
    key = dict(index=0, kind="H", value=10, available_at=0)
    history = {i: {3: [key]} for i in range(len(bars))}
    if case == "future_key":
        key["available_at"] = 4
    elif case == "wick_only":
        bars[1] = replace(bars[1], close=10)
        bars[2] = replace(bars[2], close=10, open=10)
        bars[3] = replace(bars[3], close=9.9, low=9.9)
    elif case == "unresisted":
        bars[1] = replace(bars[1], open=9.5, high=10.5)
        bars[2] = replace(bars[2], open=10.5, high=10.8, low=10.5)
    elif case == "resolved":
        bars[3] = replace(bars[3], open=10.8, high=11.4, low=10.5, close=11.3)
    elif case == "new_low_point":
        history[2] = {3: [key, dict(index=1, kind="L", value=9.4, available_at=2)]}
    risks = trend_flip_exit_history(bars, history)
    if case in ("resisted", "new_low_point"):
        assert 3 in risks and risks[3]["exit_fraction"] == 1
    else:
        assert not risks


def test_secondary_high_alone_does_not_arm_tertiary_flip_rule():
    bars, _ = sample()
    history = hierarchical_history(bars)[0]
    only_secondary = {i: {2: levels[2]} for i, levels in history.items()}
    assert not trend_flip_exit_history(bars, only_secondary)
