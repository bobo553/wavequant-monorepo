"""Post-five-top reattacks wait for a causally available next response."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.five_top_rebreak import FIVE_TOP_REBREAK_PENDING, five_top_rebreak_history
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


@pytest.fixture(scope="module")
def xinhua_october():
    raw = json.loads((Path(__file__).parent / "fixtures/xinhuawenxuan_2026_trend.json").read_text(encoding="utf-8"))
    bars = [Bar(datetime.fromisoformat(day), raw["symbol"], *values)
            for day, *values in raw["bars"] if day <= "2019-10-23"]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    config = replace(SystemStrategy(**profile["strategy"]), volume_filter=False)
    return bars, config, profile["scenarios"]["base"]["execution"], generate_system_signals(bars, config)


def test_real_october_16_strong_reattack_cannot_buy_before_its_response(xinhua_october):
    bars, _, _, result = xinhua_october
    now = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2019-10-16")
    bar = bars[now]
    assert (bar.open, bar.high, bar.low, bar.close) == pytest.approx((14.8927170467, 16.1996504605,
                                                                  14.8927170467, 16.1996504605))
    assert bar.volume == 10_636_787
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in result.signals)
    risk = next(event for event in result.audit if event["event"] == "entry_rejected"
                and event["bar_index"] == now and event["reason"] == "wave_five_top_rebreak_response_pending")
    assert risk["wave_rebreak_date"] == "2019-10-16"
    assert risk["wave_response_date"] is None
    assert risk["wave_response_status"] == "await_next_session"


def test_real_prefix_and_partial_day_cache_never_borrow_october_17(xinhua_october):
    bars, config, _, full = xinhua_october
    now = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2019-10-16")
    completed = bars[:now + 1]
    prefix = generate_system_signals(completed, config)
    assert prefix.signals == [signal for signal in full.signals if signal.bar_index <= now]
    assert prefix.audit == [event for event in full.audit if event["bar_index"] <= now]
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:now], replace(bars[now], high=15.5, close=15.1, volume=3_000_000)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in early.signals)
    replay = generate_system_signals(completed, config, chart_history_cache=cache)
    assert replay.signals == prefix.signals
    assert replay.audit == prefix.audit


def test_formal_account_does_not_submit_october_16_buy(xinhua_october):
    bars, config, execution, generated = xinhua_october

    def missing_minutes(bar):
        raise MinuteCoverageError(str(bar.timestamp.date()), None, None)

    result = single_stock_result(bars, asdict(config), execution, generated, minute_loader=missing_minutes)
    assert not any(order["side"] == "BUY" and order["timestamp"].startswith("2019-10-16")
                   for order in result["orders"])


def sample():
    values = [(8, 9, 7, 8.5), (8.5, 10.5, 8.4, 10), (10, 12.5, 9.9, 12.5),
              (12.5, 12.6, 11.4, 11.8), (11.8, 13.2, 11.8, 13), (13, 14, 12.9, 14),
              (13.9, 14, 12, 12.8)]
    bars = [Bar(datetime(2020, 1, 1) + timedelta(days=index), "sh.601811", *ohlc, 100)
            for index, ohlc in enumerate(values)]
    event = dict(event="wave_projection_target_reached", bar_index=2, attack=1, origin_index=0,
                 reached_stage="five_top", reached_target=12.0, a_origin=7.0, state="stacking")
    return bars, event


def test_direct_strong_continuation_has_no_wait_and_weakness_starts_a_new_episode():
    bars, event = sample()
    bars[3] = replace(bars[3], open=12.5, high=13.5, low=12.5, close=13.5)
    direct = five_top_rebreak_history(bars[:4], [event])
    assert not direct.risks
    assert direct.confirmations[3]["wave_rebreak_path"] == "direct_strong_continuation"
    result = five_top_rebreak_history(sample()[0], [event])
    assert result.risks[4]["wave_response_status"] == "await_next_session"
    assert result.confirmations[5]["wave_rebreak_path"] == "next_strong_candle"
    assert result.risks[6]["reason"] == FIVE_TOP_REBREAK_PENDING


def test_entries_below_an_old_five_top_keep_their_existing_gates():
    bars, event = sample()
    result = five_top_rebreak_history(bars, [event])
    assert bars[3].close < event["reached_target"]
    assert 3 not in result.risks
    assert 4 in result.risks


def test_strong_goal_touch_can_continue_directly_above_without_an_intervening_oscillation():
    bars, event = sample()
    bars[2] = replace(bars[2], close=12.0)
    bars[3] = replace(bars[3], open=12.0, low=12.0, high=13.5, close=13.5)
    history = five_top_rebreak_history(bars[:4], [event])
    assert not history.risks
    assert history.confirmations[3]["wave_oscillation_date"] is None


@pytest.mark.parametrize("response", [(13, 13.2, 12.1, 12.6), (12.7, 13.1, 12.6, 13.0),
                                      (13.01, 13.3, 12.9, 13.2), (13, 14.0, 11.79, 14)])
def test_weak_equal_close_resisted_and_broken_support_responses_do_not_release(response):
    bars, event = sample()
    bars[5] = replace(bars[5], open=response[0], high=response[1], low=response[2], close=response[3])
    result = five_top_rebreak_history(bars, [event])
    assert 5 in result.risks and 5 not in result.confirmations


def test_failed_response_cannot_use_a_strong_third_bar_or_inherited_squeeze_label():
    bars, event = sample()
    bars[5] = replace(bars[5], open=12.8, high=12.9, low=12.3, close=12.5)
    bars[6] = replace(bars[6], open=12.5, high=14.0, low=12.5, close=14.0)
    unrelated = dict(event="regime_confirmation", bar_index=6, attack=1, regime="轧空")
    result = five_top_rebreak_history(bars, [event, unrelated])
    assert result.risks[5]["wave_response_status"] == "response_failed"
    assert result.risks[6]["wave_response_status"] == "await_next_session"
    assert result.risks[6]["wave_response_date"] is None


def test_next_clean_record_close_defeats_this_attack_resistance_without_strong_body():
    bars, event = sample()
    bars[4] = replace(bars[4], open=11.8, high=14.2, low=11.7, close=12.8)
    bars[5] = replace(bars[5], open=14.15, high=14.4, low=14, close=14.3)
    result = five_top_rebreak_history(bars, [event])
    assert 4 in result.risks and 5 not in result.risks
    proof = result.confirmations[5]
    assert proof["wave_rebreak_path"] == "next_resistance_failed_squeeze"
    assert proof["confirmation_strong_bullish"] is False
    assert proof["rebreak_resistance_patterns"] == ["long_upper_shadow"]
    assert proof["response_resistance_patterns"] == []


def test_next_strong_candle_can_defeat_a_lower_open_resistance():
    bars, event = sample()
    bars[5] = replace(bars[5], open=12.9, high=14.0, low=12.8, close=14.0)
    result = five_top_rebreak_history(bars, [event])
    assert 5 not in result.risks
    assert result.confirmations[5]["response_resistance_patterns"] == ["direct_lower_open"]
    assert result.confirmations[5]["wave_rebreak_path"] == "next_strong_candle"


def test_exact_five_top_touch_is_not_a_strict_rebreak_and_real_next_session_is_required():
    bars, event = sample()
    bars[4] = replace(bars[4], high=12.6, close=12.0)
    assert 4 not in five_top_rebreak_history(bars[:5], [event]).risks
    bars, event = sample()
    bars[5] = replace(bars[5], timestamp=bars[5].timestamp + timedelta(days=2))
    bars[6] = replace(bars[6], timestamp=bars[6].timestamp + timedelta(days=2))
    assert five_top_rebreak_history(bars, [event]).confirmations[5]["wave_response_date"] == "2020-01-08"


@pytest.mark.parametrize("change", [dict(bar_index=6), dict(reached_stage="two_t"),
                                    dict(reached_target=0), dict(reached_target=True),
                                    dict(reached_target=float("nan")), dict(origin_index=1)])
def test_unknown_future_wrong_stage_and_malformed_goals_do_not_backdate_risk(change):
    bars, event = sample()
    assert not five_top_rebreak_history(bars[:6], [dict(event, **change)]).risks


def test_same_day_milestone_never_blocks_its_own_candle_and_prefixes_are_identical():
    bars, event = sample()
    full = five_top_rebreak_history(bars, [event])
    assert 2 not in full.risks
    for end in range(1, len(bars) + 1):
        prefix = five_top_rebreak_history(bars[:end], [event])
        assert prefix.risks == {index: risk for index, risk in full.risks.items() if index < end}
        assert prefix.confirmations == {index: proof for index, proof in full.confirmations.items() if index < end}


def test_origin_break_is_permanent_and_dated_invalidation_does_not_borrow_future():
    bars, event = sample()
    invalidated = dict(event, event="wave_projection_invalidated", bar_index=4, state="invalidated")
    result = five_top_rebreak_history(bars, [event, invalidated, dict(event, bar_index=5)])
    assert 4 not in result.risks and 5 not in result.risks
    assert 4 in five_top_rebreak_history(bars[:5], [event, dict(invalidated, bar_index=5)]).risks
    bars[3] = replace(bars[3], low=6.99)
    assert not five_top_rebreak_history(bars, [event, dict(event, bar_index=5)]).risks


def test_goal_upgrade_cannot_erase_old_five_top_and_another_n_is_independent():
    bars, event = sample()
    upgrade = dict(event, bar_index=3, reached_stage="ten_full", reached_target=20)
    other = dict(event, bar_index=3, attack=2, origin_index=1, a_origin=8.0)
    result = five_top_rebreak_history(bars, [event, upgrade, other])
    assert result.risks[4]["wave_five_top_target"] == 12.0
    assert result.risks[4]["wave_n_date"] == str(bars[2].timestamp.date())


@pytest.mark.parametrize("channel", ["ordinary", "combined", "shallow"])
def test_global_guard_blocks_every_channel_without_erasing_structural_evidence(xinhua_october, monkeypatch, channel):
    bars, config, _, _ = xinhua_october
    now = next(index for index, bar in enumerate(bars) if str(bar.timestamp.date()) == "2019-10-16")
    proof = dict(stop=14.0, target=20.0, counter_ratio=.3, breakout_volume_multiple=2.0)
    if channel == "combined":
        monkeypatch.setattr("wavequant.domain.strategies.integrated_strategy.combined_a_entry_history",
                            lambda *args, **kwargs: ([], {now: proof}))
    elif channel == "shallow":
        monkeypatch.setattr("wavequant.domain.strategies.shallow_base_breakout.shallow_base_history",
                            lambda *args, **kwargs: ([], {now: proof}))
    result = generate_system_signals(bars[:now + 1], config)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in result.signals)
    assert any(event["bar_index"] == now and event["event"] == "n_completed" for event in result.audit)
    assert any(event["bar_index"] == now and event.get("candidate_channel") == "global_five_top_rebreak_guard"
               and event.get("reason") == FIVE_TOP_REBREAK_PENDING for event in result.audit)
