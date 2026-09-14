"""Signal worker orchestration keeps per-symbol AkShare failures isolated."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import unittest

from wavequant_api.cli import refresh_signals


class AkShareStructureWorkerTests(unittest.TestCase):
    def test_complete_catalog_continues_after_one_symbol_fails(self) -> None:
        calls: list[str] = []

        class Service:
            def refresh(self, run: str, variant: str, **kwargs: object) -> dict[str, object]:
                symbol = kwargs["symbol"]
                if not isinstance(symbol, str):
                    raise AssertionError("expected a symbol shard")
                calls.append(symbol)
                if symbol == "sh.600000":
                    raise ValueError("temporary provider failure")
                return {"status": "published", "symbol": symbol}

        repository = SimpleNamespace(
            catalog=lambda: {"runs": [{"id": "run-001"}]},
            market_data=SimpleNamespace(
                catalog=lambda source: {
                    "stocks": [{"symbol": "sh.600000"}, {"symbol": "sz.000001"}],
                    "source": source,
                }
            ),
        )
        with patch("wavequant_api.cli.StructureSnapshotService", return_value=Service()):
            results = refresh_signals(
                SimpleNamespace(),
                repository,
                run=None,
                variant="lecture_v1",
                scenario="base",
                source="akshare",
                symbols=[],
                start="2018-01-01",
                asof="2026-09-11",
                family="structure",
            )

        self.assertEqual(calls, ["sh.600000", "sz.000001"])
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[1], {"status": "published", "symbol": "sz.000001"})


if __name__ == "__main__":
    unittest.main()
