"""A known secondary/primary low chain can confirm its N on the breakout day."""

from dataclasses import asdict, replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.models.model import Bar
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.hierarchical_entry import EntryContext
from wavequant.domain.strategies.nested_alternation_breakout import nested_alternation_breakout


def huaci_sample() -> tuple[list[Bar], dict[str, int], SystemStrategy]:
    raw = json.loads((Path(__file__).parent / "fixtures/huaci_2024_alternation_breakout.json").read_text("utf-8"))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], *values[:5],
            adjustment_factor=values[5], buyable=bool(values[6]), sellable=bool(values[7]),
            close_buyable=values[8], nonflat_close_buyable=values[9])
        for day, *values in raw["bars"]
    ]
    dates = {str(bar.timestamp.date()): i for i, bar in enumerate(bars)}
    config = SystemStrategy(
        pivot_mode="lecture_causal", entry_policy="hierarchical_two_buy_points",
        buy_point_definition="whole_flip_wave_v3", first_pullback_threshold=None,
        preflight_reward_risk=False, strict_n_attack_quality=False,
    )
    return bars, dates, config


def test_huaci_known_secondary_then_primary_low_enters_on_january_17() -> None:
    bars, dates, config = huaci_sample()
    result = generate_system_signals(bars, config)
    now = dates["2024-01-17"]
    signal = next((s for s in result.signals if s.bar_index == now and s.side == "LONG"), None)
    assert signal is not None
    assert signal.reason == "system_nested_alternation_breakout"
    assert signal.reference_price == pytest.approx(14.919459671064129)
    assert signal.invalidation_price == pytest.approx(13.500038855136498)
    proof = next(e for e in result.audit if e["bar_index"] == now and e["event"] == "long_transition_evidence")
    assert proof["secondary_low_date"] == "2023-10-23"
    assert proof["secondary_known_date"] == "2023-11-08"
    assert proof["primary_low_date"] == "2023-12-28"
    assert proof["primary_known_date"] == "2024-01-05"
    assert proof["breakout_high"] == pytest.approx(13.904107554561154)
    assert proof["confirmation_volume"] == 4_557_338


def chain_sample() -> tuple[list[Bar], EntryContext, EntryContext]:
    bars = [Bar(datetime(2024, 1, 1) + timedelta(days=i), "TEST", 12, 12.5, 11.5, 12, 100) for i in range(13)]
    bars[0] = replace(bars[0], open=8.5, high=9, low=8, close=8.5)
    bars[2] = replace(bars[2], high=15)
    bars[4] = replace(bars[4], low=10)
    bars[6] = replace(bars[6], high=13)
    bars[8] = replace(bars[8], low=11)
    bars[11] = replace(bars[11], high=13.8, close=13.8, volume=200)
    secondary = EntryContext(2, 0, 0, 1, 10, 3, 2, 15, 0, 8, 5, 4, 10)
    primary = EntryContext(1, 4, 4, 3, 12, 7, 6, 13, 4, 10, 9, 8, 11)
    return bars, secondary, primary


def test_breakout_does_not_require_an_opening_gap_and_touching_support_is_valid() -> None:
    bars, secondary, primary = chain_sample()
    bars[11] = replace(bars[11], low=primary.alternation_low_price)
    assert bars[11].open < bars[10].high
    assert nested_alternation_breakout(bars, (secondary, primary), (secondary, primary),
                                      now=11, origin=4, pullback=8) is not None


@pytest.mark.parametrize("failure", [
    "equal_volume", "zero_previous_volume", "bearish", "doji", "equal_high", "resisted",
    "primary_low_broken", "secondary_low_broken", "secondary_confirmed_after_primary",
    "secondary_low_not_before_primary", "primary_known_today", "primary_known_future",
    "primary_retired", "secondary_retired", "wrong_n_origin", "wrong_n_pullback",
])
def test_incomplete_invalid_or_unmatched_chain_does_not_confirm(failure: str) -> None:
    bars, secondary, primary = chain_sample()
    origin, pullback = 4, 8
    current = (secondary, primary)
    if failure == "equal_volume":
        bars[11] = replace(bars[11], volume=100)
    elif failure == "zero_previous_volume":
        bars[10] = replace(bars[10], volume=0)
    elif failure == "bearish":
        bars[11] = replace(bars[11], open=13.9, high=14)
    elif failure == "doji":
        bars[11] = replace(bars[11], open=13.8)
    elif failure == "equal_high":
        bars[11] = replace(bars[11], close=13)
    elif failure == "resisted":
        bars[11] = replace(bars[11], high=16)
    elif failure == "primary_low_broken":
        bars[10] = replace(bars[10], low=10.99)
    elif failure == "secondary_low_broken":
        bars[5] = replace(bars[5], low=9.99)
    elif failure == "secondary_confirmed_after_primary":
        secondary = replace(secondary, alternation_index=10)
    elif failure == "secondary_low_not_before_primary":
        secondary = replace(secondary, alternation_low_index=8)
    elif failure == "primary_known_today":
        primary = replace(primary, alternation_index=11)
    elif failure == "primary_known_future":
        primary = replace(primary, alternation_index=12)
    elif failure == "primary_retired":
        current = (secondary,)
    elif failure == "secondary_retired":
        current = (primary,)
    elif failure == "wrong_n_origin":
        origin = 5
    elif failure == "wrong_n_pullback":
        pullback = 9
    if failure not in ("primary_retired", "secondary_retired"):
        current = (secondary, primary)
    assert nested_alternation_breakout(bars, (secondary, primary), current,
                                      now=11, origin=origin, pullback=pullback) is None


def test_huaci_signal_prefix_and_cached_partial_replay_are_identical_and_not_repeated() -> None:
    bars, dates, config = huaci_sample()
    full = generate_system_signals(bars, config)
    now = dates["2024-01-17"]
    prefix = generate_system_signals(bars[:now + 1], config)
    assert prefix.signals == [s for s in full.signals if s.bar_index <= now]
    assert prefix.audit == [e for e in full.audit if e["bar_index"] <= now]
    assert not any(s.side == "LONG" and s.bar_index > now for s in full.signals)
    ready = [e for e in full.audit if e["event"] == "wave_continuation_ready" and e["attack_index"] == now]
    assert ready and all(e["squeeze_index"] > now for e in ready)
    cache = {"source_bars": tuple(bars)}
    partial = [*bars[:now], replace(bars[now], high=13.9, close=13.9, volume=bars[now - 1].volume)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(s.side == "LONG" and s.bar_index == now for s in early.signals)
    completed = generate_system_signals(bars[:now + 1], config, chart_history_cache=cache)
    assert completed.signals == prefix.signals
    assert completed.audit == prefix.audit


def test_huaci_close_execution_and_dated_ledger_keep_limit_fill_constraints() -> None:
    from wavequant.application.analytics.backtest import run_portfolio
    from wavequant.application.analytics.trade_evidence import enrich_ledger
    from wavequant.domain.models.config import StrategyConfig

    bars, dates, config = huaci_sample()
    now = dates["2024-01-17"]
    bars = bars[:now + 1]
    generated = generate_system_signals(bars, config)
    signal = next(s for s in generated.signals if s.bar_index == now and s.side == "LONG")
    execution = StrategyConfig(entry_at_close=True, nonflat_limit_close_fill=True, exit_on_target=False)
    result = run_portfolio({signal.symbol: bars}, [signal], execution)
    enrich_ledger(bars, result, generated, asdict(config))
    order = result.orders[-1]
    assert order["status"] == "filled"
    assert order["timestamp"].startswith("2024-01-17")
    assert order["price"] == pytest.approx(14.919459671064129)
    assert order["raw_price"] == pytest.approx(14.40)
    assert order["fill_assumption"] == "nonflat_limit_close_without_queue_verification"
    assert order["entry_conditions"][0]["passed"] is True
    assert order["entry_conditions"][1]["passed"] is True
    assert order["entry_conditions"][3]["passed"] is True
    strict = run_portfolio({signal.symbol: bars}, [signal], replace(execution, nonflat_limit_close_fill=False))
    assert strict.orders[-1]["status"] == "cancelled"
