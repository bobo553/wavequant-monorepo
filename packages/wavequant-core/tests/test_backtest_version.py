import pytest

from wavequant.interfaces.charts.visualization import ChartRepository
from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester


def test_backtest_version_tracks_loaded_engine_and_selected_profile(monkeypatch):
    current_engine = {"value": {"strategy.py": "first"}}
    monkeypatch.setattr(TdxBacktester, "_engine_hashes", staticmethod(lambda: current_engine["value"]))
    repository = object.__new__(ChartRepository)
    repository.runs = {"run-a": {}, "run-b": {}}
    repository.backtest_engine = current_engine["value"]
    profile = {"profile_version": "v3", "strategy": {"threshold": 1}, "scenarios": {"base": {"fee": 1}}}
    monkeypatch.setattr(repository, "strategy_config", lambda _run, _variant: profile)

    initial = repository.backtest_version("run-a", "lecture_v3")
    assert len(initial["version"]) == 64
    assert initial["profile_version"] == "v3"
    assert repository.backtest_version("run-a", "lecture_v3") == initial
    assert repository.backtest_version("run-b", "lecture_v3")["version"] != initial["version"]
    assert repository.backtest_version("run-a", "strict_full")["version"] != initial["version"]

    profile["strategy"]["threshold"] = 2
    assert repository.backtest_version("run-a", "lecture_v3")["version"] != initial["version"]

    current_engine["value"] = {"strategy.py": "second"}
    with pytest.raises(ValueError, match="策略代码已变更"):
        repository.backtest_version("run-a", "lecture_v3")
    repository.backtest_engine = current_engine["value"]
    assert repository.backtest_version("run-a", "lecture_v3")["version"] != initial["version"]

    with pytest.raises(ValueError, match="unknown registered run"):
        repository.backtest_version("missing", "lecture_v3")
    with pytest.raises(ValueError, match="unknown strategy"):
        repository.backtest_version("run-a", "missing")
