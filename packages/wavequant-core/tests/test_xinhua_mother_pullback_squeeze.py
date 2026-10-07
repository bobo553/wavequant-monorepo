"""Real bearish-mother B/C geometry must retain the March 5 defended N."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.domain.strategies.chart_entry_history import chart_entry_history
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar
from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result


class _Permissions(TypedDict):
    buyable: bool
    sellable: bool
    close_buyable: bool
    nonflat_close_buyable: bool


class _FrozenHistory(TypedDict):
    symbol: str
    source: str
    bars: list[tuple[str, float, float, float, float, float, float, float]]
    permissions: dict[str, _Permissions]
    strategy: dict[str, object]
    execution: dict[str, bool | int | float]


@dataclass(frozen=True)
class _XinhuaSample:
    bars: list[Bar]
    dates: dict[str, int]
    config: SystemStrategy
    generated: SystemResult
    execution: dict[str, bool | int | float]


@pytest.fixture(scope="module")
def xinhua_history() -> _XinhuaSample:
    path = Path(__file__).parent / "fixtures/xinhua_2024_mother_pullback.json"
    raw = cast(_FrozenHistory, json.loads(path.read_text(encoding="utf-8")))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], opening, high, low, close, volume,
            adjustment_factor=factor, **raw["permissions"][day])
        for day, opening, high, low, close, volume, factor, _raw_close in raw["bars"]
    ]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    config = replace(SystemStrategy(**profile["strategy"]), volume_filter=False)
    assert asdict(config) == raw["strategy"]
    assert len(bars) == 536
    assert str(bars[0].timestamp.date()) == "2022-01-04"
    assert str(bars[-1].timestamp.date()) == "2024-03-21"
    return _XinhuaSample(bars, dates, config, generate_system_signals(bars, config), raw["execution"])


def _longs_for_attack(result: SystemResult, attack: datetime) -> list[Signal]:
    return [signal for signal in result.signals if signal.side == "LONG" and signal.trigger_timestamp == attack]


def _missing_historical_minutes(bar: Bar) -> list[MinuteBar]:
    """Use the official missing-minute boundary and configured daily-close fallback."""
    raise MinuteCoverageError(str(bar.timestamp.date()), "2026-07-21", "2026-09-30")


def test_march_5_structural_n_does_not_measure_from_a_higher_local_bottom(xinhua_history: _XinhuaSample) -> None:
    sample = xinhua_history
    attack = sample.dates["2024-03-05"]
    published = []
    for asof in ("2024-03-04", "2024-03-05", "2024-03-20"):
        prefix = sample.bars[:sample.dates[asof] + 1]
        theory = ChartRepository.render_theory(
            None, prefix, sample.config, sample.generated, asof, geometry={"tertiary_trends": {}},
        )
        events = [event for event in theory["events"]
                  if event["event"] == "n_completed" and event["bar_index"] == attack]
        if asof == "2024-03-04":
            assert events == []
            continue
        assert len(events) == 1
        event = events[0]
        assert event["available_at"] == "2024-03-05"
        assert [point["time"] for point in event["shape"]] == [
            "2024-02-29", "2024-03-04", "2024-03-04", "2024-03-05",
        ]
        assert event["target_eligible"] is False
        assert event["target_bottom_date"] == "2024-02-08"
        assert event["target_bottom_date"] < event["shape"][0]["time"]
        assert event["one_p"] is None and event["two_t"] is None
        targets = [level for level in event["levels"]
                   if level.get("stage") in ("one_p", "two_t", "five_top", "ten_full")]
        assert targets == []
        published.append(targets)
    assert published[0] == published[1]
    assert all("levels" not in event for event in sample.generated.audit)


def test_march_5_mother_pullback_cannot_backdate_missing_hierarchy(xinhua_history: _XinhuaSample) -> None:
    sample = xinhua_history
    attack, now = sample.dates["2024-03-05"], sample.dates["2024-03-20"]
    completion = [event for event in sample.generated.audit
                  if event["event"] == "n_completed" and event["direction"] == "up"
                  and event["bar_index"] == attack]
    assert len(completion) == 1
    n = completion[0]
    assert n["origin"] == sample.dates["2024-02-29"]
    assert n["neckline"] == sample.dates["2024-03-04"]
    assert n["pullback"] == sample.dates["2024-03-04"]
    assert n["known_at"] == attack
    assert n["defense"] == pytest.approx(4.4210060069937)
    mother, child = sample.bars[sample.dates["2024-03-04"]], sample.bars[sample.dates["2024-03-01"]]
    assert mother.close < mother.open
    assert mother.high > child.high and mother.low < child.low
    assert all(bar.low >= n["defense"] for bar in sample.bars[attack + 1:now + 1])
    signals = _longs_for_attack(sample.generated, sample.bars[attack].timestamp)
    assert signals == []
    contexts, _ = chart_entry_history(sample.bars, audit=sample.generated.audit)
    assert contexts[attack] == contexts[now] == ()
    rejection = [event for event in sample.generated.audit
                 if event["event"] == "entry_rejected" and event["bar_index"] == now
                 and event.get("attack") == attack]
    assert [event["reason"] for event in rejection] == ["wave_no_alternation_at_attack"]
    assert n["target_eligible"] is False
    assert n["one_p"] is None and n["two_t"] is None
    candle = sample.bars[now]
    assert candle.high > sample.bars[attack].high
    assert candle.close > sample.bars[attack].close
    assert (candle.close - candle.open) / candle.open == pytest.approx(0.04310344827586207)
    assert (candle.close - candle.open) / (candle.high - candle.low) == pytest.approx(0.8695652173913043)
    assert candle.volume / sample.bars[now - 1].volume == pytest.approx(35_527_071 / 32_519_279)


def test_completed_prefix_and_partial_day_cache_preserve_march_20_rejection(
    xinhua_history: _XinhuaSample,
) -> None:
    sample = xinhua_history
    attack, now = sample.dates["2024-03-05"], sample.dates["2024-03-20"]
    completed = sample.bars[:now + 1]
    prefix = generate_system_signals(completed, sample.config)
    assert prefix.signals == [signal for signal in sample.generated.signals if signal.bar_index <= now]
    assert prefix.audit == [event for event in sample.generated.audit if event["bar_index"] <= now]
    cache: dict[str, object] = {"source_bars": tuple(sample.bars)}
    # The unfinished candle retains real open/low but has not closed above the attack's high.
    partial = [*sample.bars[:now], replace(sample.bars[now], high=sample.bars[now - 1].high,
                                         close=sample.bars[now - 1].close,
                                         volume=sample.bars[now].volume / 2)]
    early = generate_system_signals(partial, sample.config, chart_history_cache=cache)
    assert not any(signal.bar_index == now for signal in _longs_for_attack(early, sample.bars[attack].timestamp))
    replay = generate_system_signals(completed, sample.config, chart_history_cache=cache)
    assert replay.signals == prefix.signals
    assert replay.audit == prefix.audit
    assert _longs_for_attack(replay, sample.bars[attack].timestamp) == []
    assert any(event["event"] == "entry_rejected" and event["bar_index"] == now
               and event.get("attack") == attack and event["reason"] == "wave_no_alternation_at_attack"
               for event in replay.audit)


def test_broken_original_march_5_defense_cannot_revive_at_march_20(xinhua_history: _XinhuaSample) -> None:
    sample = xinhua_history
    attack, response = sample.dates["2024-03-05"], sample.dates["2024-03-07"]
    broken = [*sample.bars]
    broken[response] = replace(broken[response], low=sample.bars[attack].low - 0.01)
    generated = generate_system_signals(broken, sample.config)
    assert any(event["event"] == "n_completed" and event["direction"] == "up"
               and event["bar_index"] == attack for event in generated.audit)
    assert not _longs_for_attack(generated, sample.bars[attack].timestamp)


def test_formal_daily_close_account_keeps_valid_fills_and_rejects_march_20(xinhua_history: _XinhuaSample) -> None:
    sample = xinhua_history
    now = sample.dates["2024-03-20"]
    assert sample.config.strict_n_attack_quality is False
    assert sample.config.volume_filter is False
    assert sample.execution["initial_capital"] == 100_000
    assert sample.execution["max_position_weight"] == 1
    assert sample.execution["entry_at_close"] is True
    assert sample.execution["net_reward_risk_filter"] is False
    assert sample.execution["slippage_bps_per_side"] == 5
    assert sample.bars[now].close_buyable
    result = single_stock_result(
        sample.bars, asdict(sample.config), sample.execution, sample.generated,
        minute_loader=_missing_historical_minutes,
    )
    orders = [order for order in result["orders"]
              if order["side"] == "BUY" and order["timestamp"].startswith("2024-03-20")]
    assert orders == []
    assert sample.bars[now].close / sample.bars[now].adjustment_factor == pytest.approx(4.84)
    buys = [order for order in result["orders"] if order["side"] == "BUY" and order["status"] == "filled"]
    assert buys
    assert buys[0]["applied_slippage_bps"] == 0
    order = next(order for order in buys if order.get("applied_slippage_bps") != 0)
    decision = datetime.fromisoformat(order["signal_timestamp"])
    candle = sample.bars[sample.dates[str(decision.date())]]
    assert order["execution_model"] == "same_day_close"
    assert order["reference_price"] == pytest.approx(candle.close)
    assert order["raw_price"] == pytest.approx(
        candle.close / candle.adjustment_factor * (1 + sample.execution["slippage_bps_per_side"] / 10_000))
    assert order["raw_shares"] >= 100
    assert order["position_quantity_before"] == 0
    proof = next(event for event in order["decision_evidence"] if event["event"] == "long_signal")
    assert proof["target_source_attack"] is not None
    source = next(event for event in sample.generated.audit
                  if event["event"] == "n_completed" and event["bar_index"] == proof["target_source_attack"])
    assert source["target_eligible"] is True
