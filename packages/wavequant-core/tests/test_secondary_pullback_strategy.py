"""Real-history recovery of an observed B after a directly promoted secondary A."""

from dataclasses import asdict, replace
from datetime import datetime
import json
from pathlib import Path
from typing import cast

import pytest

from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


CHANNEL = "secondary_deep_pullback_reclaim"
REASON = "system_" + CHANNEL


def entry_evidence(result):
    return [row for row in result.audit if row["event"] == "long_transition_evidence"]


def no_minutes(bar):
    raise MinuteCoverageError(str(bar.timestamp.date()), None, None, "fixture_daily_close")


@pytest.fixture(scope="module")
def xiangyang_pullback():
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2025_trend_break.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["rows"] if day <= "2022-08-23"]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": StrategyConfig().to_dict()}}})
    config = SystemStrategy(**profile["strategy"])
    entry = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2022-07-19")
    cache = {"source_bars": tuple(bars)}
    prior = generate_system_signals(bars[:entry], config, chart_history_cache=cache)
    daily = [(end, generate_system_signals(bars[:end], config, chart_history_cache=cache))
             for end in range(entry + 1, len(bars) + 1)]
    generated = generate_system_signals(bars, config)
    return bars, config, profile["scenarios"]["base"]["execution"], entry, prior, daily, generated


def test_real_body_recovery_is_dated_without_inventing_a_positive_n_or_formal_b(xiangyang_pullback):
    bars, _, _, entry, prior, daily, generated = xiangyang_pullback
    assert bars[0].timestamp.year == 2018
    assert bars[-1].timestamp.date().isoformat() == "2022-08-23"
    previous, confirmation = bars[entry - 1], bars[entry]
    assert (previous.open, previous.high, previous.low, previous.close, previous.volume) == (
        4.89, 5.08, 4.81, 5.06, 10_050_400)
    assert (confirmation.open, confirmation.high, confirmation.low, confirmation.close, confirmation.volume) == (
        5.08, 5.57, 5.03, 5.45, 33_044_829)
    assert previous.volume < bars[entry - 2].volume
    assert confirmation.open == previous.high and confirmation.low < previous.high
    assert not any(signal.side == "LONG" and signal.bar_index == entry - 1 for signal in prior.signals)
    longs = [signal for signal in generated.signals if signal.side == "LONG" and signal.reason == REASON
             and signal.bar_index <= entry]
    assert [signal.bar_index for signal in longs if str(signal.timestamp.date()) >= "2022-06-24"] == [entry]
    signal = next(signal for signal in longs if signal.bar_index == entry)
    assert signal.reference_price == 5.45
    assert signal.invalidation_price == 4.81
    assert signal.target_price == 6.67
    assert signal.trigger_timestamp == confirmation.timestamp
    resumed = daily[0][1]
    proof = next(row for row in resumed.audit if row["event"] == "long_transition_evidence"
                 and row.get("buy_point_type") == CHANNEL and row["bar_index"] == entry)
    assert proof["reclaim_type"] == "body"
    assert proof["unfilled_gap"] is False
    assert proof["observed_low"] is True
    assert proof["formal_alternation"] is False
    assert proof["requires_positive_n"] is False
    assert proof["secondary_origin_date"] == "2022-04-27"
    assert proof["secondary_origin_low"] == 4.06
    assert proof["secondary_high_date"] == "2022-06-24"
    assert proof["secondary_high_known_date"] == "2022-07-04"
    assert proof["secondary_high"] == 6.67
    assert proof["secondary_key_date"] == "2020-11-20"
    assert proof["secondary_key_high"] == 5.76
    assert proof["secondary_pullback_low_date"] == "2022-07-18"
    assert proof["secondary_pullback_low"] == 4.81
    assert proof["secondary_resistance_date"] == "2022-07-13"
    assert proof["secondary_resistance_known_date"] == "2022-07-14"
    assert proof["secondary_resistance_high"] == 5.05
    assert proof["confirmation_close"] == 5.45
    assert proof["reclaim_ceiling"] == 5.08
    assert proof["reclaim_ceiling_date"] == "2022-07-18"
    assert proof["breakout_volume"] == confirmation.volume
    assert proof["previous_volume"] == previous.volume
    assert proof["breakout_volume_multiple"] == pytest.approx(33_044_829 / 10_050_400)
    assert proof["reclaim_body_fraction"] == pytest.approx(.37 / 5.08)
    assert proof["reclaim_body_range_fraction"] == pytest.approx(.37 / .54)
    assert not any(row["event"] == "n_completed" and row.get("direction") == "up"
                   and row["bar_index"] == entry for row in resumed.audit)
    prefix = bars[:entry + 1]
    first = reversal_trends(lecture_drawing(prefix), prefix)
    second = secondary_trends(first, prefix)
    points = [point for stroke in second["strokes"] for point in stroke["points"]]
    high = next(point for point in points if point["kind"] == "H" and point["time"] == "2022-06-24")
    assert high["available_at"] == "2022-07-04"
    assert high["broken_key"]["time"] == "2020-11-20"
    assert high["broken_key"]["value"] == 5.76
    assert not any(point["kind"] == "L" and point["time"] == "2022-07-18" for point in points)


def test_daily_cached_prefixes_and_full_suffix_keep_identical_signals_and_entry_evidence(xiangyang_pullback):
    bars, _, _, entry, prior, daily, generated = xiangyang_pullback
    assert prior.signals == [signal for signal in generated.signals if signal.bar_index < entry]
    assert entry_evidence(prior) == [row for row in entry_evidence(generated) if row["bar_index"] < entry]
    for end, replayed in daily:
        assert replayed.signals == [signal for signal in generated.signals if signal.bar_index < end], str(
            bars[end - 1].timestamp.date())
        assert entry_evidence(replayed) == [row for row in entry_evidence(generated) if row["bar_index"] < end], str(
            bars[end - 1].timestamp.date())
    assert not any(row.get("buy_point_type") == CHANNEL and row["event"] == "long_transition_evidence"
                   and row["bar_index"] >= entry for row in prior.audit)


def test_default_account_fills_the_same_day_with_real_net_reward_risk(xiangyang_pullback):
    bars, config, execution, entry, _, daily, _ = xiangyang_pullback
    assert execution["net_reward_risk_filter"] is True
    view = single_stock_result(bars[:entry + 1], asdict(config), execution, daily[0][1], minute_loader=no_minutes)
    orders = [order for order in view["orders"] if order["side"] == "BUY"
              and order["timestamp"].startswith("2022-07-19")]
    assert len(orders) == 1
    order = orders[0]
    assert order["status"] == "filled"
    assert order["signal_timestamp"].startswith("2022-07-19")
    assert order["execution_model"] == "same_day_close"
    assert order["price"] == pytest.approx(5.45 * 1.0005)
    assert order["stop_price"] == 4.81
    assert order["target_price"] == 6.67
    assert order["net_reward_risk_filter"] is True
    assert order["net_reward_risk"] >= config.minimum_reward_risk
    assert order["raw_shares"] > 0 and order["raw_shares"] % execution["lot_size"] == 0
    assert any(row.get("buy_point_type") == CHANNEL for row in order["decision_evidence"])


def test_the_secondary_a_target_does_not_skip_a_live_nearer_n_target(xiangyang_pullback):
    _, _, _, entry, _, daily, _ = xiangyang_pullback
    audit = daily[0][1].audit
    launch = next(row for row in audit if row["event"] == "n_completed" and row.get("direction") == "up"
                  and row["timestamp"].startswith("2022-06-20"))
    assert launch["target_eligible"] is True
    assert launch["one_p"] == 7.16 and launch["two_t"] == 8.53
    assert launch["one_p"] > 6.67
    earlier = [row for row in audit if row["event"] == "n_completed" and row.get("direction") == "up"
               and "2022-05-13" <= row["timestamp"][:10] <= "2022-06-02"]
    assert earlier and all(row["target_eligible"] is False for row in earlier)
    assert any(row["event"] == "n_target_source_retired" and row["attack"] == launch["bar_index"]
               and row["bar_index"] < entry for row in audit)


def test_higher_net_reward_requirement_rejects_execution_without_removing_the_signal(xiangyang_pullback):
    bars, config, execution, entry, _, _, _ = xiangyang_pullback
    strict = replace(config, minimum_reward_risk=3.0)
    assert strict.preflight_reward_risk is False
    generated = generate_system_signals(bars[:entry + 1], strict)
    signal = next(signal for signal in generated.signals if signal.side == "LONG" and signal.bar_index == entry)
    assert signal.reason == REASON and signal.minimum_reward_risk == 3.0
    view = single_stock_result(bars[:entry + 1], asdict(strict), execution, generated, minute_loader=no_minutes)
    order = next(order for order in view["orders"] if order["side"] == "BUY"
                 and order["timestamp"].startswith("2022-07-19"))
    assert order["status"] == "cancelled"
    assert order["reason"] == "insufficient_net_reward_risk"
    assert order["net_reward_risk"] < order["required_reward_risk"] == 3.0


def test_explicit_preflight_reward_gate_rejects_the_same_recovery(xiangyang_pullback):
    bars, config, _, entry, _, _, _ = xiangyang_pullback
    strict = replace(config, preflight_reward_risk=True, minimum_reward_risk=3.0)
    generated = generate_system_signals(bars[:entry + 1], strict)
    assert not any(signal.side == "LONG" and signal.bar_index == entry for signal in generated.signals)
    rejection = next(row for row in generated.audit if row["event"] == "entry_preflight_rejected"
                     and row.get("candidate_channel") == CHANNEL and row["bar_index"] == entry)
    assert rejection["reason"] == "insufficient_close_gross_reward_risk"
    assert rejection["gross_reward_risk"] == pytest.approx((6.67 - 5.45) / (5.45 - 4.81))
    assert rejection["required_reward_risk"] == 3.0


def test_switch_off_keeps_the_route_disabled_for_the_real_history(xiangyang_pullback):
    bars, config, _, entry, _, _, _ = xiangyang_pullback
    disabled = replace(config, secondary_pullback_entry_enabled=False)
    generated = generate_system_signals(bars[:entry + 1], disabled)
    assert not any(signal.reason == REASON and signal.bar_index == entry for signal in generated.signals)
    assert not any(row.get("buy_point_type") == CHANNEL for row in generated.audit)


def test_default_profile_enables_the_route_and_rejects_non_boolean_switches():
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    assert profile["strategy"]["secondary_pullback_entry_enabled"] is True
    assert CHANNEL in profile["definition"]["channels"]
    assert SystemStrategy().secondary_pullback_entry_enabled is False
    with pytest.raises(ValueError, match="secondary pullback entry switch"):
        replace(SystemStrategy(), secondary_pullback_entry_enabled=cast(bool, 1)).validate()
