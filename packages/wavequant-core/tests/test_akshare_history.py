from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from wavequant.infrastructure.market_data.akshare_history import AkShareMinuteSource, MinuteCoverageError, sina_factors
from wavequant.infrastructure.market_data.akshare import AkShareUnavailable
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.interfaces.charts.akshare_browser import AkShareBrowser
from wavequant.interfaces.research_tools.akshare_backtest import AkShareBacktester
from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.strategies.integrated_strategy import SystemResult
from wavequant.domain.models.model import Bar


class Frame:
    def __init__(self, records):
        self.records = records
        self.columns = list(records[0]) if records else []

    def to_dict(self, _):
        return self.records


def day():
    return Bar(datetime(2025, 5, 15), "sz.300154", 9.99, 10.01, 9.97, 9.99, 48000)


class Provider:
    version = "test"
    timeout = 2

    def __init__(self, records):
        self.records = records
        self.calls = []

    def call(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return Frame(self.records)


def minute_records():
    clocks = [
        datetime(2025, 5, 15, hour, minute) + timedelta(minutes=5 * index)
        for hour, minute in ((9, 35), (13, 5))
        for index in range(24)
    ]
    return [dict(day=stamp.isoformat(), open=9.99, high=10.01, low=9.97, close=9.99, volume=1000) for stamp in clocks]


def test_sina_minutes_use_daily_factor_and_reject_mismatch(tmp_path):
    provider = Provider(minute_records())
    source = AkShareMinuteSource(provider, ArtifactCache(tmp_path), day().symbol)
    adjusted = replace(day(), open=19.98, high=20.02, low=19.94, close=19.98, adjustment_factor=2)
    result = source.get(adjusted)
    assert len(result) == 48
    assert result[0].close == pytest.approx(9.99)  # Execution applies the daily factor once.
    assert provider.calls == [("stock_zh_a_minute", dict(symbol="sz300154", period="5", adjust=""))]
    with pytest.raises(ValueError, match="不一致"):
        source.get(replace(adjusted, high=21))
    assert source.provenance()["upstream"] == "sina"


def test_partial_minutes_are_unavailable_and_complete_cache_is_reusable(tmp_path):
    cache = ArtifactCache(tmp_path)
    AkShareMinuteSource(Provider(minute_records()), cache, day().symbol).get(day())
    assert len(AkShareMinuteSource(Provider([]), cache, day().symbol).get(day())) == 48
    with pytest.raises(MinuteCoverageError) as caught:
        AkShareMinuteSource(Provider(minute_records()[:-1]), ArtifactCache(tmp_path / "empty"), day().symbol).get(day())
    assert caught.value.coverage["available_start"] is None


def test_pinned_daily_never_tries_another_upstream():
    provider = Provider([])
    browser = AkShareBrowser(provider, pinned_history=True)
    with pytest.raises(AkShareUnavailable):
        browser.bars("sz.300154", "2026-09-07")
    assert {name for name, _ in provider.calls} == {"stock_zh_a_daily"}


def test_invalid_factors_are_rejected():
    with pytest.raises(ValueError, match="无效"):
        sina_factors(Provider([dict(date="2020-01-01", hfq_factor=0)]), "sz.300154")


def test_missing_minutes_discards_account_but_keeps_daily_theory(tmp_path, monkeypatch):
    import wavequant.interfaces.research_tools.akshare_backtest as module

    provider = Provider([dict(date="2000-01-01", hfq_factor=1), dict(date="2030-01-01", hfq_factor=99)])
    bars = [replace(day(), timestamp=datetime(2026, 1, 1) + timedelta(days=index)) for index in range(30)]
    browser = AkShareBrowser(provider, pinned_history=True)
    monkeypatch.setattr(browser, "bars", lambda *_: (bars, bars))
    generated = SystemResult([], [], {})
    monkeypatch.setattr(module, "generate_system_signals", lambda *_: generated)
    attempts = []

    def missing(*args, **kwargs):
        attempts.append(1)
        assert kwargs["minute_loader"].__self__.provider is provider
        raise MinuteCoverageError("2026-01-23", "2026-07-24", "2026-09-18")

    monkeypatch.setattr(module, "single_stock_result", missing)
    output, signals, view = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", {}, StrategyConfig(staged_exit_intraday=True).to_dict()
    )
    assert signals is generated
    assert all(bar.adjustment_factor == 1 for bar in output)
    assert view["backtest"]["status"] == "data_unavailable"
    assert view["backtest"]["source"]["upstream"] == "sina"
    assert view["orders"] == view["trades"] == view["curve"] == []
    assert view["metrics"] is None
    assert len(view["bars"]) == 10
    again = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", {}, StrategyConfig(staged_exit_intraday=True).to_dict()
    )[2]
    assert again["backtest"]["status"] == "data_unavailable"
    assert len(attempts) == 2  # Incomplete minutes may become available later.


def test_pinned_backtest_reuses_exact_result_and_invalidates_changed_inputs(tmp_path, monkeypatch):
    import wavequant.interfaces.research_tools.akshare_backtest as module

    provider = Provider([dict(date="2000-01-01", hfq_factor=1)])
    browser = AkShareBrowser(provider, pinned_history=True)
    raw = [Bar(datetime(2026, 1, 1) + timedelta(days=index), "sz.300154", 10, 11, 9, 10, 100000) for index in range(30)]
    monkeypatch.setattr(browser, "bars", lambda *_: (raw, raw))
    calls = {"signals": 0, "account": 0}
    original_account = module.single_stock_result

    def signals(*args):
        calls["signals"] += 1
        return SystemResult([], [], {"long_signals": 0})

    def account(*args, **kwargs):
        calls["account"] += 1
        return original_account(*args, **kwargs)

    monkeypatch.setattr(module, "generate_system_signals", signals)
    monkeypatch.setattr(module, "single_stock_result", account)
    strategy = {}
    execution = StrategyConfig(staged_exit_intraday=False).to_dict()
    first_bars, first_signals, first_view = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", strategy, execution
    )
    second_bars, second_signals, second_view = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", strategy, execution
    )
    assert second_bars == first_bars
    assert second_signals == first_signals
    assert second_view == first_view
    assert calls == {"signals": 1, "account": 1}

    changed_execution = dict(execution, initial_capital=200000)
    AkShareBacktester(browser, tmp_path).run("sz.300154", "2026-01-21", "2026-01-30", strategy, changed_execution)
    assert calls == {"signals": 1, "account": 2}

    raw[-1] = Bar(datetime(2026, 1, 30), "sz.300154", 10, 12, 9, 11, 100000)
    changed = AkShareBacktester(browser, tmp_path).run("sz.300154", "2026-01-21", "2026-01-30", strategy, execution)[2]
    assert changed["run_id"] != first_view["run_id"]
    assert calls == {"signals": 2, "account": 3}

    strategy_view = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", {"volume_filter": False}, execution
    )[2]
    assert strategy_view["run_id"] != changed["run_id"]
    assert calls == {"signals": 3, "account": 4}

    provider.records.append(dict(date="2026-01-25", hfq_factor=2))
    factor_view = AkShareBacktester(browser, tmp_path).run(
        "sz.300154", "2026-01-21", "2026-01-30", strategy, execution
    )[2]
    assert factor_view["run_id"] != changed["run_id"]
    assert calls == {"signals": 4, "account": 5}

    monkeypatch.setattr(module.TdxBacktester, "_engine_hashes", staticmethod(lambda: {"engine": "next-version"}))
    code_view = AkShareBacktester(browser, tmp_path).run("sz.300154", "2026-01-21", "2026-01-30", strategy, execution)[
        2
    ]
    assert code_view["run_id"] != factor_view["run_id"]
    assert calls == {"signals": 5, "account": 6}

    without_signals = AkShareBacktester(browser, tmp_path)
    original_get = without_signals.artifacts.get
    monkeypatch.setattr(
        without_signals.artifacts,
        "get",
        lambda namespace, key: None if namespace == "akshare-signals" else original_get(namespace, key),
    )
    recovered = without_signals.run("sz.300154", "2026-01-21", "2026-01-30", strategy, execution)[2]
    assert recovered == code_view
    assert calls == {"signals": 6, "account": 6}  # Evicted signal artifact must not rerun a valid account.
