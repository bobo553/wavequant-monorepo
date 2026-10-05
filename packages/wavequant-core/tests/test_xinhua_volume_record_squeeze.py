"""The real February 22 lower-open volume record confirms the February 19 N."""

from dataclasses import asdict, dataclass, replace
from datetime import datetime
import json
from pathlib import Path
from typing import TypedDict, cast

import pytest

from wavequant.domain.models.model import Bar, Signal
from wavequant.domain.strategies.integrated_strategy import SystemResult, SystemStrategy, generate_system_signals
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile
from wavequant.infrastructure.market_data.akshare_history import MinuteCoverageError
from wavequant.infrastructure.market_data.minute import MinuteBar
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
    path = Path(__file__).parent / "fixtures/xinhua_2019_volume_record.json"
    raw = cast(_FrozenHistory, json.loads(path.read_text(encoding="utf-8")))
    bars = [
        Bar(datetime.fromisoformat(day), raw["symbol"], opening, high, low, close, volume,
            adjustment_factor=factor, **raw["permissions"][day])
        for day, opening, high, low, close, volume, factor, _raw_close in raw["bars"]
    ]
    dates = {str(bar.timestamp.date()): index for index, bar in enumerate(bars)}
    profile = whole_wave_profile({"scenarios": {"base": {"execution": {}}}})
    # The frozen v90 volume-record route remains a regression oracle. Current
    # v94 opening behavior is independently covered in test_n_opening_squeeze.
    config = replace(SystemStrategy(**profile["strategy"]), volume_filter=False, opening_gap_squeeze_enabled=False)
    assert asdict(config) == dict(raw["strategy"], opening_gap_squeeze_enabled=False)
    assert len(bars) == 277
    assert str(bars[0].timestamp.date()) == "2018-01-02"
    return _XinhuaSample(bars, dates, config, generate_system_signals(bars, config), raw["execution"])


def _longs_for_attack(result: SystemResult, attack: datetime) -> list[Signal]:
    return [signal for signal in result.signals if signal.side == "LONG" and signal.trigger_timestamp == attack]


def _missing_historical_minutes(bar: Bar) -> list[MinuteBar]:
    """Retain the official cache's missing-minute boundary and daily-close fallback."""
    raise MinuteCoverageError(str(bar.timestamp.date()), "2026-07-21", "2026-09-30")


def test_lower_open_volume_record_buys_february_22_without_repeating_february_25(
    xinhua_history: _XinhuaSample,
) -> None:
    sample = xinhua_history
    attack, now = sample.dates["2019-02-19"], sample.dates["2019-02-22"]
    current, previous = sample.bars[now], sample.bars[now - 1]
    assert current.open < previous.close
    assert current.close > previous.high
    assert current.volume > previous.volume
    assert current.close == current.high
    signals = _longs_for_attack(sample.generated, sample.bars[attack].timestamp)
    assert [str(signal.timestamp.date()) for signal in signals] == ["2019-02-22"]
    signal = signals[0]
    assert signal.reason == "system_transition_squeeze"
    assert signal.reference_price == current.close
    assert signal.invalidation_price == pytest.approx(5.480247218788627)
    # The earlier February 22 close has not reached 1P, the nearest live N target.
    assert signal.target_price == pytest.approx(6.915550061804695)
    assert signal.rvol == pytest.approx(102_869_684 / 52_192_242)
    proof = next(event for event in sample.generated.audit
                 if event["event"] == "long_signal" and event["bar_index"] == now and event["attack"] == attack)
    assert proof["squeeze_confirmation"] == "resistance_record_break"
    assert proof["n_resistance_date"] == "2019-02-20"
    assert proof["n_resistance_window_start"] == "2019-02-19"
    assert proof["n_resistance_window_end"] == "2019-02-20"
    transition = next(event for event in sample.generated.audit
                      if event["event"] == "long_transition_evidence" and event["bar_index"] == now)
    assert transition["confirmation_source"] == "resistance_record_break"


def test_completed_prefix_and_partial_day_cache_keep_the_real_confirmation_day(
    xinhua_history: _XinhuaSample,
) -> None:
    sample = xinhua_history
    now = sample.dates["2019-02-22"]
    completed = sample.bars[:now + 1]
    prefix = generate_system_signals(completed, sample.config)
    assert prefix.signals == [signal for signal in sample.generated.signals if signal.bar_index <= now]
    assert prefix.audit == [event for event in sample.generated.audit if event["bar_index"] <= now]
    cache: dict[str, object] = {"source_bars": tuple(sample.bars)}
    attack_bar = sample.bars[sample.dates["2019-02-19"]]
    # Keep the real open/low while the partial range has not closed above the resistance record.
    partial = [*sample.bars[:now], replace(sample.bars[now], high=attack_bar.high, close=attack_bar.close,
                                         volume=sample.bars[now - 1].volume)]
    early = generate_system_signals(partial, sample.config, chart_history_cache=cache)
    assert not any(signal.side == "LONG" and signal.bar_index == now for signal in early.signals)
    replay = generate_system_signals(completed, sample.config, chart_history_cache=cache)
    assert replay.signals == prefix.signals
    assert replay.audit == prefix.audit
    assert [str(signal.timestamp.date()) for signal in _longs_for_attack(
        replay, sample.bars[sample.dates["2019-02-19"]].timestamp)] == ["2019-02-22"]
    equal_volume = [*sample.bars[:now], replace(sample.bars[now], volume=sample.bars[now - 1].volume)]
    unfiltered = generate_system_signals(equal_volume, sample.config)
    filtered = generate_system_signals(equal_volume, replace(sample.config, volume_filter=True))
    assert [str(signal.timestamp.date()) for signal in _longs_for_attack(
        unfiltered, attack_bar.timestamp)] == ["2019-02-22"]
    assert not _longs_for_attack(filtered, attack_bar.timestamp)


def test_official_2018_start_account_fills_the_real_limit_close_at_raw_6_35(
    xinhua_history: _XinhuaSample,
) -> None:
    sample = xinhua_history
    now = sample.dates["2019-02-22"]
    assert not sample.bars[now].close_buyable
    assert sample.bars[now].nonflat_close_buyable
    assert sample.execution["initial_capital"] == 100_000
    assert sample.execution["max_position_weight"] == 1
    assert sample.execution["entry_at_close"] is True
    assert sample.execution["nonflat_limit_close_fill"] is True
    assert sample.execution["net_reward_risk_filter"] is False
    result = single_stock_result(
        sample.bars, asdict(sample.config), sample.execution, sample.generated,
        minute_loader=_missing_historical_minutes,
    )
    orders = [order for order in result["orders"]
              if order["side"] == "BUY" and order["timestamp"].startswith("2019-02-22")]
    assert len(orders) == 1
    order = orders[0]
    assert order["status"] == "filled"
    assert order["execution_model"] == "same_day_close"
    assert order["raw_price"] == pytest.approx(6.35)
    assert order["raw_shares"] >= 100
    assert order["fill_assumption"] == "nonflat_limit_close_without_queue_verification"
    strict = single_stock_result(
        sample.bars, asdict(sample.config), dict(sample.execution, nonflat_limit_close_fill=False), sample.generated,
        minute_loader=_missing_historical_minutes,
    )
    assert not any(order["side"] == "BUY" and order["status"] == "filled"
                   and order["timestamp"].startswith("2019-02-22") for order in strict["orders"])
