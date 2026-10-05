"""V3 squeeze entry strength is independent of regime and target observations."""

from dataclasses import replace
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from wavequant.domain.market_state.candle_strength import strong_bullish_candle, strong_bullish_candle_evidence
from wavequant.domain.models.model import Bar
from wavequant.domain.strategies import integrated_strategy, shallow_base_breakout
from wavequant.domain.strategies.combined_a_entry import CombinedAContext, CombinedAHigh, combined_a_entry_history
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import WAVE_PROFILES, whole_wave_profile


STRENGTH_REASON = "entry_requires_strong_bullish_candle"
XiangyangData = tuple[tuple[Bar, ...], dict[str, int], SystemStrategy]


@pytest.fixture(scope="module")
def xiangyang_bars() -> XiangyangData:
    raw = json.loads((Path(__file__).parent / "fixtures/xiangyang_2023_shared_edge_n.json").read_text("utf-8"))
    bars = tuple(Bar(datetime.fromisoformat(day), raw["symbol"], *values) for day, *values in raw["bars"])
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    return bars, dates, config


@pytest.mark.parametrize("prices,accepted", [
    ((5.7, 5.90, 5.66, 5.871), True),
    ((5.700001, 5.90, 5.66, 5.871), False),
    ((5.8, 6.2, 5.7, 6.1), True),
    ((5.800001, 6.2, 5.7, 6.1), False),
    ((5.8, 6.25, 5.75, 6.15), True),
    ((5.8, 6.250001, 5.75, 6.15), False),
], ids=["body_3pct_equal", "body_3pct_below", "body_60pct_equal", "body_60pct_below",
        "shadow_20pct_equal", "shadow_20pct_above"])
def test_strong_shape_boundaries_gate_real_n_entries(
    xiangyang_bars: XiangyangData, prices: tuple[float, float, float, float], accepted: bool,
) -> None:
    bars, dates, config = xiangyang_bars
    now, attack = dates["2023-06-15"], dates["2023-06-12"]
    data = list(bars[:now + 1])
    data[now] = replace(data[now], open=prices[0], high=prices[1], low=prices[2], close=prices[3])
    assert strong_bullish_candle(data[now]) is accepted
    generated = generate_system_signals(data, config)
    assert any(event["event"] == "regime_confirmation" and event["bar_index"] == now
               and event.get("attack") == attack for event in generated.audit)
    entries = [signal for signal in generated.signals if signal.side == "LONG" and signal.bar_index == now]
    assert bool(entries) is accepted
    if accepted:
        assert len(entries) == 1 and entries[0].trigger_timestamp == bars[attack].timestamp
        proof = next(event for event in generated.audit if event["event"] == "long_signal"
                     and event["bar_index"] == now)
        assert proof["confirmation_strong_bullish"] is True
    else:
        proof = next(event for event in generated.audit if event["event"] == "entry_rejected"
                     and event["bar_index"] == now and event.get("attack") == attack
                     and event["reason"] == STRENGTH_REASON)
        assert proof["confirmation_strong_bullish"] is False
        assert proof["minimum_body_open_ratio"] == 0.03
        assert proof["minimum_body_range_ratio"] == 0.6
        assert proof["maximum_upper_shadow_ratio"] == 0.2


def test_weak_confirmation_does_not_consume_later_fresh_strong_squeeze(xiangyang_bars: XiangyangData) -> None:
    bars, dates, config = xiangyang_bars
    weak, strong, attack = dates["2023-06-15"], dates["2023-06-16"], dates["2023-06-12"]
    data = list(bars[:strong + 1])
    data[strong] = replace(data[strong], open=5.8, high=6.0, low=5.7, close=6.0, volume=20_000_000)
    generated = generate_system_signals(data, config)
    entries = [signal for signal in generated.signals if signal.side == "LONG"
               and signal.trigger_timestamp == bars[attack].timestamp]
    assert [signal.bar_index for signal in entries] == [strong]
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == weak
               and event.get("attack") == attack and event["reason"] == STRENGTH_REASON
               for event in generated.audit)
    assert any(event["event"] == "regime_confirmation" and event["bar_index"] == strong
               and event.get("attack") == attack for event in generated.audit)
    prefix = generate_system_signals(data[:weak + 1], config)
    assert prefix.signals == [signal for signal in generated.signals if signal.bar_index <= weak]
    assert prefix.audit == [event for event in generated.audit if event["bar_index"] <= weak]


def test_later_strong_shape_cannot_borrow_an_old_squeeze_confirmation(xiangyang_bars: XiangyangData) -> None:
    bars, dates, config = xiangyang_bars
    now, attack = dates["2023-06-16"], dates["2023-06-12"]
    data = list(bars[:now + 1])
    data[now] = replace(data[now], open=5.75, high=5.96, low=5.70, close=5.95, volume=20_000_000)
    assert strong_bullish_candle(data[now])
    assert data[now].close < data[now - 1].high
    generated = generate_system_signals(data, config)
    assert not any(signal.side == "LONG" and signal.bar_index == now
                   and signal.trigger_timestamp == bars[attack].timestamp for signal in generated.signals)
    assert not any(event["event"] == "regime_confirmation" and event["bar_index"] == now
                   and event.get("attack") == attack for event in generated.audit)


def test_partial_bar_cache_rechecks_strength_without_borrowing_final_close(xiangyang_bars: XiangyangData) -> None:
    bars, dates, config = xiangyang_bars
    now = dates["2023-06-15"]
    complete = list(bars[:now + 1])
    complete[now] = replace(complete[now], open=5.7, high=5.9, low=5.66, close=5.871)
    cache = {"source_bars": tuple(complete)}
    partial = [*complete[:-1], replace(complete[now], close=5.82)]
    early = generate_system_signals(partial, config, chart_history_cache=cache)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in early.signals)
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == now
               and event["reason"] == STRENGTH_REASON for event in early.audit)
    fresh = generate_system_signals(complete, config)
    replay = generate_system_signals(complete, config, chart_history_cache=cache)
    assert any(signal.side == "LONG" and signal.bar_index == now for signal in replay.signals)
    assert replay.signals == fresh.signals
    assert replay.audit == fresh.audit


@pytest.mark.parametrize("variant", tuple(WAVE_PROFILES))
def test_v3_variants_cannot_disable_shape_by_disabling_volume_filter(
    xiangyang_bars: XiangyangData, variant: str,
) -> None:
    bars, dates, _ = xiangyang_bars
    now, attack = dates["2023-06-15"], dates["2023-06-12"]
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}}, variant)
    definition = profile["definition"]
    assert "strong_bullish_candle_required_for_squeeze_entries_and_additions" in definition["primary_filters"]
    assert len(definition["primary_filters"]) == len(set(definition["primary_filters"]))
    candle_rule = definition["squeeze_entry_candle"]
    for condition in ("v3_common_n_squeeze_channels", "body_ge_3pct_open", "ge_60pct_range",
                      "upper_shadow_le_20pct_range", "inclusive", "independent_of_volume_and_reward_risk_switches",
                      "regime_and_projection_unchanged", "rejection_does_not_consume_n",
                      "independent_combined_a_and_shallow_channels_keep_own_shape_rules"):
        assert condition in candle_rule
    config = SystemStrategy(**profile["strategy"])
    generated = generate_system_signals(bars[:now + 1], replace(config, volume_filter=False))
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in generated.signals)
    proof = next(event for event in generated.audit if event["event"] == "entry_rejected"
                 and event["bar_index"] == now and event.get("attack") == attack
                 and event["reason"] == STRENGTH_REASON)
    assert proof["confirmation_body_open_ratio"] == pytest.approx(0.13 / 5.73)
    assert proof["confirmation_body_range_ratio"] == pytest.approx(0.13 / 0.32)
    assert proof["confirmation_upper_shadow_ratio"] == pytest.approx(0.12 / 0.32)


def test_flat_candle_evidence_preserves_unavailable_ratios() -> None:
    evidence = strong_bullish_candle_evidence(Bar(datetime(2025, 1, 1), "TEST", 10, 10, 10, 10, 100))
    assert evidence["confirmation_strong_bullish"] is False
    assert evidence["confirmation_body_open_ratio"] == 0
    assert evidence["confirmation_body_range_ratio"] is None
    assert evidence["confirmation_upper_shadow_ratio"] is None


def test_strong_entry_gate_does_not_block_a_weak_candle_risk_exit(xiangyang_bars: XiangyangData) -> None:
    bars, dates, config = xiangyang_bars
    now = dates["2023-06-27"]
    assert not strong_bullish_candle(bars[now])
    generated = generate_system_signals(bars[:now + 1], config)
    assert any(signal.side == "EXIT" and signal.bar_index == now
               and "wave_two_t_resistance_volume_clear" in signal.reason for signal in generated.signals)
    assert any(event["event"] == "exit_signal" and event["bar_index"] == now for event in generated.audit)


@pytest.mark.parametrize("volume_filter,accepted", [(True, False), (False, True)])
def test_strong_candle_does_not_waive_the_selected_volume_gate(
    xiangyang_bars: XiangyangData, volume_filter: bool, accepted: bool,
) -> None:
    bars, dates, config = xiangyang_bars
    now = dates["2023-06-15"]
    data = list(bars[:now + 1])
    data[now] = replace(data[now], open=5.7, high=5.9, low=5.66, close=5.871, volume=0)
    assert strong_bullish_candle(data[now])
    generated = generate_system_signals(data, replace(config, volume_filter=volume_filter))
    assert any(signal.side == "LONG" and signal.bar_index == now for signal in generated.signals) is accepted
    if not accepted:
        assert any(event["event"] == "entry_rejected" and event["bar_index"] == now
                   and event["reason"] == "attack_volume_unavailable_or_low" for event in generated.audit)


def test_wave_squeeze_entry_observation_cannot_bypass_the_shape_gate(xiangyang_bars: XiangyangData) -> None:
    bars, dates, config = xiangyang_bars
    now = dates["2023-06-12"]
    data = list(bars[:now + 1])
    data[now] = replace(data[now], open=5.56)
    assert not strong_bullish_candle(data[now])
    generated = generate_system_signals(data, config)
    wave = next(event for event in generated.audit if event["event"] == "wave_gap_observed"
                and event["bar_index"] == now)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in generated.signals)
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == now
               and event.get("attack") == wave["attack"] and event["reason"] == STRENGTH_REASON
               for event in generated.audit)


@pytest.mark.parametrize("route,strong", [("combined", False), ("combined", True),
                                          ("shallow", True), ("both", True)])
def test_independent_routes_preserve_their_own_shape_contract_and_emit_at_most_once(
    monkeypatch: pytest.MonkeyPatch, route: str, strong: bool,
) -> None:
    bars = [Bar(datetime(2025, 1, 1), "TEST", 10, 11, 9, 10, 100),
            Bar(datetime(2025, 1, 2), "TEST", 10.5, 11.2 if strong else 11.4, 10.4, 11.1, 200)]
    assert strong_bullish_candle(bars[-1]) is strong
    # Known independent observations exercise composition without borrowing the
    # squeeze shape contract; global risk gates and publication remain real.
    proof = dict(stop=9.0, target=15.0, counter_ratio=0.6, breakout_volume_multiple=2.0)
    combined = {1: dict(proof, buy_point_type="combined_a_pullback_breakout")} if route != "shallow" else {}
    shallow = {1: dict(proof, buy_point_type="shallow_base_breakout")} if route != "combined" else {}
    monkeypatch.setattr(integrated_strategy, "combined_a_entry_history", lambda *args, **kwargs: ([], combined))
    monkeypatch.setattr(shallow_base_breakout, "shallow_base_history", lambda *args, **kwargs: ([], shallow))
    config = SystemStrategy(**whole_wave_profile({"scenarios": {"base": {"execution": {}}}})["strategy"])
    config = replace(config, shallow_base_breakout_enabled=True)
    generated = generate_system_signals(bars, config)
    entries = [signal for signal in generated.signals if signal.side == "LONG"]
    assert len(entries) == 1
    expected = "system_shallow_base_breakout" if route == "shallow" else "system_combined_a_pullback_breakout"
    assert entries[0].reason == expected
    assert not any(event["event"] == "entry_rejected" and event["reason"] == STRENGTH_REASON
                   and event.get("candidate_channel") in {"combined_a_pullback_breakout", "shallow_base_breakout"}
                   for event in generated.audit)


def test_combined_a_preserves_its_separate_body_breakout_shape_rule() -> None:
    rows = [(4.5, 5, 4, 4.5), (6, 8, 5.8, 7.5), (7.2, 7.5, 6.8, 7),
            (6.5, 7, 6, 6.5), (8, 9, 7.8, 8.5), (6.5, 6.8, 6, 6.3),
            (6.7, 7.3, 6.6, 7.1), (7.1, 7.2, 6.8, 7), (7, 7.9, 6.9, 7.6), (8, 8.5, 8, 8.4)]
    bars = [Bar(datetime(2025, 1, 1) + timedelta(days=index), "TEST", *row,
                400 if index == 9 else 200 if index == 8 else 100) for index, row in enumerate(rows)]
    context = CombinedAContext("source", 0, 4, 1, 8, 3, 6, 4, 9, 5, 2, (3,),
                               (CombinedAHigh(6, 7.3, 7),))
    candidates = {index: (context,) for index in range(5, len(bars))}
    assert not strong_bullish_candle(bars[8])
    assert strong_bullish_candle(bars[9])
    events, proofs = combined_a_entry_history(bars, candidates)
    assert list(proofs) == [8]
    assert proofs[8]["combined_a_breakout_type"] == "body_breakout"
    assert [event["bar_index"] for event in events if event["event"] == "combined_a_pullback_breakout"] == [8]
    prefix_events, prefix_proofs = combined_a_entry_history(
        bars[:9], {i: value for i, value in candidates.items() if i < 9},
    )
    assert prefix_proofs == proofs
    assert prefix_events == [event for event in events if int(str(event["bar_index"])) < 9]
