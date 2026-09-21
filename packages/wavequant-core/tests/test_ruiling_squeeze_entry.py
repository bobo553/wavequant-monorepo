"""Regression for the supplied July N and August squeeze, using adjusted prices."""

from dataclasses import replace
from datetime import datetime

import pytest

from wavequant.domain.market_structure.n_shape import (
    BoxAnchorMode,
    MilestoneBasis,
    NSetup,
    PivotRef,
    observe_n,
)
from wavequant.domain.market_structure.price_action import Direction
from wavequant.domain.models.model import Bar
from wavequant.domain.models.config import StrategyConfig
from wavequant.application.analytics.backtest import run_portfolio
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import WAVE_PROFILES, whole_wave_profile


def ruiling_bars() -> list[Bar]:
    rows = [
        ("2026-07-15", 10.531473, 11.490073, 10.478947, 11.174917),
        ("2026-07-16", 11.174917, 11.949676, 10.872892, 11.096128),
        ("2026-07-17", 11.358758, 11.476942, 10.111264, 10.137527),
        ("2026-07-20", 10.492078, 10.597130, 9.769845, 9.979949),
        ("2026-07-21", 9.993081, 10.176922, 8.995086, 9.625398),
        ("2026-07-22", 9.520346, 9.664793, 9.060743, 9.244585),
        ("2026-07-23", 9.152664, 9.638530, 9.152664, 9.546609),
        ("2026-07-24", 9.428426, 9.559741, 9.310242, 9.375900),
        ("2026-07-27", 9.257716, 10.071870, 9.257716, 10.045607),
        ("2026-07-28", 9.874897, 10.583999, 9.730451, 9.927423),
        ("2026-07-29", 9.940555, 10.019344, 9.494083, 9.861766),
        ("2026-07-30", 9.782977, 9.927423, 9.402163, 9.441557),
        ("2026-07-31", 9.612267, 10.019344, 9.612267, 9.953686),
        ("2026-08-03", 10.071870, 10.373895, 9.822371, 10.334500),
        ("2026-08-04", 10.426421, 10.820366, 10.321369, 10.794103),
        ("2026-08-05", 10.859761, 11.135522, 10.767840, 11.135522),
        ("2026-08-06", 11.529468, 11.660783, 11.174917, 11.503205),
        ("2026-08-07", 11.660783, 12.474937, 11.306232, 12.383016),
    ]
    return [Bar(datetime.fromisoformat(day), "sz.300154", o, h, low, c, 10_000_000) for day, o, h, low, c in rows]


@pytest.mark.parametrize("inverse", [False, True])
def test_lecture_origin_opposite_wick_is_before_n_impulse(inverse: bool) -> None:
    bars = ruiling_bars()
    if inverse:
        bars = [replace(b, open=30 - b.open, high=30 - b.low, low=30 - b.high, close=30 - b.close) for b in bars]
    setup = NSetup(
        "sz.300154",
        "1d",
        Direction.DOWN if inverse else Direction.UP,
        PivotRef(4, 5),
        PivotRef(5, 6),
        PivotRef(6, 7),
        "lecture_causal",
        BoxAnchorMode.ATTACK_VIRTUAL_EXTREME,
    )
    result = observe_n(bars, setup, timeframe="1d", milestone_basis=MilestoneBasis.CLOSE)
    assert result.completion is not None
    assert bars[result.completion.bar_index].timestamp == datetime(2026, 7, 27)
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe_n(bars, replace(setup, source="strict_polyline"), timeframe="1d", milestone_basis=MilestoneBasis.CLOSE)
    interior = list(bars)
    interior[6] = replace(bars[6], **({"low": 19} if inverse else {"high": 11}))
    with pytest.raises(ValueError, match="neckline is not the extreme"):
        observe_n(interior, setup, timeframe="1d", milestone_basis=MilestoneBasis.CLOSE)


def test_squeeze_date_emits_signal_before_execution_reward_risk_check() -> None:
    bars = ruiling_bars()
    # This small fixture isolates N/squeeze timing; full hierarchy is verified on
    # the local adjusted history rather than fabricated with mocked permissions.
    config = SystemStrategy(
        pivot_mode="lecture_causal",
        entry_policy="legacy_n_continuation",
        volume_filter=False,
        max_counter_ratio=0.99,
        preflight_reward_risk=False,
        squeeze_pullback_entries=False,
    )
    result = generate_system_signals(bars, config)
    signal = next(s for s in result.signals if s.side == "LONG")
    assert signal.timestamp == datetime(2026, 8, 4)
    assert signal.trigger_timestamp == datetime(2026, 7, 27)
    assert signal.target_price is not None
    assert signal.minimum_reward_risk == 1.5
    assert (signal.target_price - signal.reference_price) / (
        signal.reference_price - signal.invalidation_price
    ) < config.minimum_reward_risk
    assert len([s for s in result.signals if s.side == "LONG" and s.trigger_timestamp == signal.trigger_timestamp]) == 1
    for cut in range(8, len(bars)):
        prefix = generate_system_signals(bars[: cut + 1], config)
        assert prefix.signals == [s for s in result.signals if s.bar_index <= cut]
    old = generate_system_signals(bars, replace(config, preflight_reward_risk=True))
    assert not any(s.side == "LONG" and s.timestamp == signal.timestamp for s in old.signals)
    executed = run_portfolio({"sz.300154": bars}, [signal], StrategyConfig(liquidity_lookback=3))
    assert not executed.trades
    assert any(row["reason"] == "insufficient_net_reward_risk" for row in executed.orders)
    unfiltered = run_portfolio(
        {"sz.300154": bars}, [signal], StrategyConfig(liquidity_lookback=3, net_reward_risk_filter=False)
    )
    assert not any(row["reason"] == "insufficient_net_reward_risk" for row in unfiltered.orders)
    filled = next(row for row in unfiltered.orders if row["side"] == "BUY" and row["status"] == "filled")
    assert filled["fee"] > 0
    assert filled["net_reward_risk"] < signal.minimum_reward_risk
    # Disabling this one filter does not permit buys at an exhausted target.
    exhausted = run_portfolio(
        {"sz.300154": bars}, [replace(signal, target_price=signal.invalidation_price)],
        StrategyConfig(liquidity_lookback=3, net_reward_risk_filter=False),
    )
    assert any(row["reason"] == "target_exhausted_at_open" for row in exhausted.orders)


@pytest.mark.parametrize("variant", WAVE_PROFILES)
def test_v3_defers_reward_risk_to_execution(variant: str) -> None:
    legacy = {"scenarios": {"base": {"execution": {}}}}
    profile = whole_wave_profile(legacy, variant)
    assert profile["strategy"]["preflight_reward_risk"] is False
    assert profile["strategy"]["minimum_reward_risk"] == 1.5
    assert "gross_rr_1_5" not in profile["definition"]["primary_filters"]
    assert "execution_price_net_rr_1_5" in profile["definition"]["primary_filters"]


def test_ruiling_august_7_target_exit_is_removed_without_future_wave_targets() -> None:
    bars = ruiling_bars()
    result = generate_system_signals(bars, SystemStrategy(
        pivot_mode="lecture_causal", entry_policy="legacy_n_continuation", volume_filter=False,
        max_counter_ratio=0.99, preflight_reward_risk=False, squeeze_pullback_entries=False,
    ))
    signal = next(s for s in result.signals if s.side == "LONG")
    config = StrategyConfig(liquidity_lookback=3, net_reward_risk_filter=False)
    before = run_portfolio({"sz.300154": bars}, result.signals, config)
    sell = next(o for o in before.orders if o["side"] == "SELL")
    assert sell["timestamp"] == "2026-08-07T00:00:00"
    assert sell["signal_timestamp"] == "2026-08-06T00:00:00"
    assert sell["reason"] == "target_observed"
    assert signal.target_price == pytest.approx(11.148654)
    after = run_portfolio({"sz.300154": bars}, result.signals, replace(config, exit_on_target=False))
    assert [o["side"] for o in after.orders] == ["BUY"]
    assert after.open_positions[0]["pending_exit"] is None
    assert after.orders[0]["target_price"] == signal.target_price
