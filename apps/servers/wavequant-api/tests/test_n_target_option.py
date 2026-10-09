"""Optional N confirmation flows through loopback HTTP without live providers."""

from http.client import HTTPConnection
import json
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from wavequant_api.server import make_server


FIELD = "n_target_trend_confirmation_enabled"


@pytest.fixture
def api():
    calls = []

    def theory(*args, **kwargs):
        calls.append((args, kwargs))
        return {FIELD: kwargs[FIELD], "asof": "2026-09-25"}

    def view(source, symbol, asof):
        return {"symbol": symbol, "asof": asof, "data_version": "data-v1",
                "sessions": [asof], "bars": [{"time": asof, "open": 5.04, "high": 5.11,
                                                "low": 4.94, "close": 5.01, "volume": 100}],
                "price_basis": "raw_unadjusted"}

    repository = SimpleNamespace(
        tdx=object(), akshare=object(), theory=theory, stock_view=theory,
        tdx_backtest=theory, akshare_backtest=theory,
        backtest_version=lambda *_: {"version": "v109"},
        market_data=SimpleNamespace(view=view, theory=theory),
    )
    server = make_server(repository, port=0, serve_static=False)
    thread = Thread(target=server.serve_forever)
    thread.start()

    def request(path, *, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("GET", path, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            return response.status, json.loads(raw) if raw else None, dict(response.getheaders())
        finally:
            connection.close()

    try:
        yield request, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("path", [
    "/api/theory?run=example&variant=lecture_v1&symbol=sz.000678&asof=2026-09-25",
    "/api/stock-view?run=example&variant=lecture_v1&symbol=sz.000678&asof=2026-09-25&scenario=base",
    "/api/tdx-theory?symbol=sz.000678&asof=2026-09-25",
    "/api/akshare-theory?symbol=sz.000678&asof=2026-09-25",
    "/api/tdx-backtest?run=example&variant=lecture_v1&symbol=sz.000678&asof=2026-09-25&scenario=base&start=2018-01-01",
    "/api/akshare-backtest?run=example&variant=lecture_v1&symbol=sz.000678&asof=2026-09-25&scenario=base&start=2018-01-01",
])
def test_theory_and_backtests_default_disabled_and_accept_both_modes(api, path):
    request, calls = api
    for suffix, enabled in (("", False), (f"&{FIELD}=false", False), (f"&{FIELD}=true", True)):
        status, body, _ = request(path + suffix)
        assert status == 200
        assert body[FIELD] is enabled
        assert calls[-1][1][FIELD] is enabled
    before = len(calls)
    for value in ("yes", "TRUE", "1", "", "false&" + FIELD + "=true"):
        assert request(path + f"&{FIELD}={value}")[0] == 400
    assert len(calls) == before


def test_timeframe_snapshot_etag_and_backtest_job_signature_separate_modes(api):
    request, _ = api
    path = "/api/market-timeframe?source=tdx&symbol=sz.000678&asof=2026-09-25&timeframe=1d"
    off_status, off, off_headers = request(path)
    assert off_status == 200
    assert off[FIELD] is False
    on_status, on, on_headers = request(path + f"&{FIELD}=true", headers={"If-None-Match": off_headers["ETag"]})
    assert on_status == 200
    assert on[FIELD] is True
    assert on["snapshot_id"] != off["snapshot_id"]
    assert on_headers["ETag"] != off_headers["ETag"]
    assert request(path + f"&{FIELD}=true", headers={"If-None-Match": on_headers["ETag"]})[0] == 304
    base = "/api/tdx-backtest?run=example&variant=lecture_v2&symbol=sz.000678&asof=2026-09-25&scenario=base&start=2018-01-01"
    assert request(base + "&backtest_job=n-option-same-job")[0] == 200
    assert request(base + f"&backtest_job=n-option-same-job&{FIELD}=false")[0] == 200
    assert request(base + f"&backtest_job=n-option-same-job&{FIELD}=true")[0] == 409
    assert request(base + f"&backtest_job=n-option-new-job&{FIELD}=true")[0] == 200
    recent = request("/api/backtest-jobs")[1]["recent"]
    assert {item["params"][FIELD] for item in recent} == {"true", "false"}


@pytest.mark.parametrize("path,service", [
    ("/api/structure-signals?run=example&variant=lecture_v1&source=akshare&asof=2026-09-25&lookback=5&signal_type=any&trend_level=0", "StructureSnapshotService"),
    ("/api/buy-signals?run=example&variant=lecture_v2&scenario=base&source=akshare&symbol=sz.000678&asof=2026-09-25&start=2018-01-01&lookback=5", "BuySignalSnapshotService"),
])
def test_scan_http_context_uses_the_same_typed_option(path, service):
    repository = SimpleNamespace()
    snapshot_service = SimpleNamespace(query=lambda params: {"params": params})
    with patch(f"wavequant_api.server.{service}", return_value=snapshot_service), patch(
        "wavequant_api.server." + ("BuySignalSnapshotService" if service == "StructureSnapshotService" else "StructureSnapshotService"),
        return_value=snapshot_service,
    ):
        server = make_server(repository, port=0, serve_static=False, infrastructure=SimpleNamespace(database=None, cache=None))
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        for value, enabled in ((None, False), ("false", False), ("true", True), ("yes", None)):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            try:
                connection.request("GET", path + (f"&{FIELD}={value}" if value is not None else ""))
                response = connection.getresponse()
                body = json.loads(response.read())
                assert response.status == (400 if enabled is None else 200)
                if enabled is not None:
                    assert body["params"][FIELD] is enabled
            finally:
                connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
