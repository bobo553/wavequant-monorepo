"""Rust 数据边界的协议回归，不访问行情网络或执行策略。"""

from __future__ import annotations

import pytest

import importlib.util
from pathlib import Path

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

    result = dispatch(IndexSdk(), {"operation": "index_daily", "symbol": "sh.000001", "asof": "2026-10-26"})
    assert calls == [{"symbol": "sh000001", "start_date": "19900101", "end_date": "20261026"}]
    assert isinstance(result, list) and isinstance(result[0], dict)
    assert result[0]["close"] == 3050.0
    assert result[0]["volume"] == 12300


@pytest.mark.parametrize("symbol", ["000001", "sh000001", "sz.000001", "sh.000001/../"])
def test_index_rejects_non_index_identity_before_sdk_call(symbol: str) -> None:
    with pytest.raises(ValueError, match="invalid index identity"):
        dispatch(object(), {"operation": "index_daily", "symbol": symbol, "asof": "2026-10-26"})
