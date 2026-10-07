"""Real-history entry, prefix replay and execution of secondary supply recovery."""

from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


@pytest.fixture(scope="module")
def xiangyang_reclaim():
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["rows"] if day <= "2025-03-21"]
    config = replace(SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"]),
                     preflight_reward_risk=False)
    cache: dict = {}
    # Save the pre-entry prefix checkpoint for an actual resumed session replay.
    entry = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2025-03-11")
    prior = generate_system_signals(bars[:entry], config, chart_history_cache=cache)
    resumed = generate_system_signals(bars[:entry + 1], config, chart_history_cache=cache)
    generated = generate_system_signals(bars, config)
    return bars, config, generated, entry, prior, resumed


def test_real_candidate_keeps_two_t_risk_and_waits_for_later_gap(xiangyang_reclaim):
    bars, _, generated, entry, _, _ = xiangyang_reclaim
    longs = [signal for signal in generated.signals if signal.side == "LONG"
             and signal.reason == "system_secondary_resistance_reclaim"
             and str(signal.timestamp.date()) >= "2025-02-28"]
    assert [signal.bar_index for signal in longs] == [entry]
    assert longs[0].reference_price == 11.44
    assert longs[0].invalidation_price == 7.43
    assert longs[0].target_price == 12.21
    rejected = next(event for event in generated.audit if event["event"] == "entry_rejected"
                    and event.get("candidate_channel") == "secondary_resistance_reclaim"
                    and event["timestamp"].startswith("2025-03-05"))
    assert rejected["reason"] == "wave_two_t_resistance_reduce"
    assert rejected["wave_reached_price"] == 9.53
    proof = next(event for event in generated.audit if event["event"] == "long_transition_evidence"
                 and event["bar_index"] == entry)
    assert proof["secondary_high_date"] == "2024-12-11"
    assert proof["secondary_attack_date"] == "2025-02-28"
    assert proof["secondary_resistance_date"] == "2025-03-03"
    assert proof["secondary_reclaim_type"] == "gap"
    assert proof["secondary_reclaim_unfilled_gap"] is True
    assert proof["secondary_reclaim_body_fraction"] == pytest.approx(.92 / 10.52)
    assert proof["secondary_reclaim_body_range_fraction"] >= .6
    assert bars[entry].low > bars[entry - 1].high


def test_full_suffix_and_incremental_prefix_keep_identical_signals_and_evidence(xiangyang_reclaim):
    _, _, generated, entry, prior, resumed = xiangyang_reclaim
    assert prior.signals == [signal for signal in generated.signals if signal.bar_index < entry]
    assert resumed.signals == [signal for signal in generated.signals if signal.bar_index <= entry]
    observed = [event for event in resumed.audit if event.get("buy_point_type") == "secondary_resistance_reclaim"]
    assert observed == [event for event in generated.audit if event.get("buy_point_type") == "secondary_resistance_reclaim"
                        and event["bar_index"] <= entry]


def test_account_fill_and_optional_reward_risk_gate_remain_real(xiangyang_reclaim):
    bars, config, generated, entry, _, resumed = xiangyang_reclaim
    profile = whole_wave_profile({"scenarios": {"base": {"execution": StrategyConfig().to_dict()}}})
    execution = StrategyConfig(**(profile['scenarios']['base']['execution'] | dict(
        net_reward_risk_filter=False, initial_capital=100_000, max_position_weight=1)))

    def no_minutes(bar):
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture_daily_close")

    view = single_stock_result(bars, asdict(config), asdict(execution), generated, minute_loader=no_minutes)
    orders = [order for order in view["orders"] if order["side"] == "BUY"
              and order["timestamp"].startswith("2025-03-11")]
    assert len(orders) == 1
    order = orders[0]
    assert order["status"] == "filled"
    assert order["raw_price"] == pytest.approx(11.44572)
    assert order["signal_timestamp"].startswith("2025-03-11")
    assert any(event.get("buy_point_type") == "secondary_resistance_reclaim" for event in order["decision_evidence"])
    assert all(condition["passed"] is True for condition in order["entry_conditions"][:4])
    strict = single_stock_result(bars[:entry + 1], asdict(config),
                                 asdict(replace(execution, net_reward_risk_filter=True)), resumed, minute_loader=no_minutes)
    assert not any(order["side"] == "BUY" and order["status"] == "filled"
                   and order["timestamp"].startswith("2025-03-11") for order in strict["orders"])


def test_global_default_enables_route_and_legacy_modes_leave_it_off():
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    assert profile["strategy"]["secondary_reclaim_entry_enabled"] is True
    assert "secondary_resistance_reclaim" in profile["definition"]["channels"]
    assert SystemStrategy().secondary_reclaim_entry_enabled is False
    with pytest.raises(ValueError, match="secondary reclaim entry switch"):
        replace(SystemStrategy(), secondary_reclaim_entry_enabled=cast(bool, 1)).validate()
