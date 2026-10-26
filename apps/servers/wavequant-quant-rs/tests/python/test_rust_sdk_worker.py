"""Rust 数据边界的协议回归，不访问行情网络或执行策略。"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

_worker_path = Path(__file__).resolve().parents[2] / "scripts" / "rust_sdk_worker.py"
_spec = importlib.util.spec_from_file_location("wavequant_rust_sdk_worker", _worker_path)
assert _spec is not None and _spec.loader is not None
_worker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_worker)
dispatch = _worker.dispatch
records = _worker.records


def test_unknown_operation_cannot_invoke_sdk_attribute() -> None:
    class NoCalls:
        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"unexpected SDK call: {name}")

    with pytest.raises(ValueError, match="unsupported SDK operation"):
        dispatch(NoCalls(), {"operation": "__dict__"})


@pytest.mark.parametrize("symbol", ["sz002724", "sz.002724;exit()", "bj.920001", "sz.002724/../../"])
def test_sina_sdk_rejects_invalid_or_unsupported_symbol_before_call(symbol: str) -> None:
    with pytest.raises(ValueError, match="requires a Shanghai or Shenzhen symbol"):
        dispatch(object(), {"operation": "daily", "symbol": symbol, "asof": "2026-10-23"})


def test_oversized_frame_is_rejected_before_serializing() -> None:
    class TooManyRows:
        index = range(20001)

        def to_json(self, **kwargs: object) -> str:
            raise AssertionError("oversized frame must not be serialized")

    with pytest.raises(ValueError, match="row limit"):
        records(TooManyRows())


def test_index_uses_its_sdk_endpoint_without_stock_adjustment() -> None:
    import pandas as pd

    calls: list[dict[str, object]] = []

    class IndexSdk:
        def stock_zh_index_daily_em(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return pd.DataFrame([{"date": "2026-10-23", "open": 3000.0, "close": 3050.0,
                                  "high": 3100.0, "low": 2900.0, "volume": 123, "amount": 123456.0}])

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"index must not call stock SDK endpoint: {name}")

    result = dispatch(IndexSdk(), {"operation": "index_daily", "symbol": "bj.899050", "asof": "2026-10-26"})
    assert calls == [{"symbol": "bj899050", "start_date": "19900101", "end_date": "20261026"}]
    assert isinstance(result, list) and isinstance(result[0], dict)
    assert result[0]["close"] == 3050.0
    assert result[0]["volume"] == 12300


def test_sina_index_preserves_share_volume_and_missing_amount_and_cutoff() -> None:
    import pandas as pd

    calls: list[dict[str, object]] = []

    class IndexSdk:
        def stock_zh_index_daily(self, **kwargs: object) -> object:
            calls.append(kwargs)
            return pd.DataFrame([
                {"date": "2026-10-23", "open": 3000.0, "close": 3050.0,
                 "high": 3100.0, "low": 2900.0, "volume": 12300},
                {"date": "2026-10-27", "open": 3000.0, "close": 3050.0,
                 "high": 3100.0, "low": 2900.0, "volume": 12400},
            ])

        def __getattr__(self, name: str) -> object:
            raise AssertionError(f"Sina index must not call other SDK endpoints: {name}")

    result = dispatch(IndexSdk(), {"operation": "index_daily", "symbol": "sh.000001", "asof": "2026-10-26"})
    assert calls == [{"symbol": "sh000001"}]
    assert isinstance(result, list) and len(result) == 1 and isinstance(result[0], dict)
    assert result[0]["volume"] == 12300
    assert "amount" not in result[0]


@pytest.mark.parametrize("symbol", ["000001", "sh000001", "sz.000001", "sh.000001/../"])
def test_index_rejects_non_index_identity_before_sdk_call(symbol: str) -> None:
    with pytest.raises(ValueError, match="invalid index identity"):
        dispatch(object(), {"operation": "index_daily", "symbol": symbol, "asof": "2026-10-26"})


def test_index_proxy_scope_preserves_system_proxy_for_other_hosts_and_restores_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "all_proxy", "no_proxy")
    for name in names:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_worker, "getproxies", lambda: {
        "http": "http://127.0.0.1:58309", "https": "http://127.0.0.1:58309",
    })
    with pytest.raises(ValueError, match="upstream"):
        with _worker.index_proxy_environment():
            assert os.environ["NO_PROXY"] == "push2his.eastmoney.com"
            assert os.environ["HTTPS_PROXY"] == "http://127.0.0.1:58309"
            raise ValueError("upstream")
    assert all(name not in os.environ for name in names)
    with _worker.index_proxy_environment("finance.sina.com.cn"):
        assert os.environ["NO_PROXY"] == "finance.sina.com.cn"
    assert all(name not in os.environ for name in names)
    monkeypatch.setenv("NO_PROXY", "explicit-user-rule")
    with _worker.index_proxy_environment():
        assert os.environ["NO_PROXY"] == "explicit-user-rule"
