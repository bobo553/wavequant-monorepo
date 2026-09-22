from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
import json
from threading import Thread
from types import SimpleNamespace

import pandas as pd
import pytest

from wavequant.infrastructure.market_data.akshare import AkShareUnavailable
from wavequant_api.application.limit_up_ladder import LimitUpLadderService, normalize_pool
from wavequant_api.server import make_server


def stock(code="000001", streak=1, **values):
    return {"代码": code, "名称": "测试证券", "连板数": streak, "首次封板时间": "093000", "最新价": 10.5, **values}


class Provider:
    def __init__(self):
        self.calls = []
        self.fail = False

    def call(self, name, **kwargs):
        self.calls.append((name, kwargs))
        if self.fail:
            raise AkShareUnavailable("AkShare 上游请求超时，请稍后重试")
        return pd.DataFrame([stock(streak=3 if kwargs["date"] == "20260921" else 1)])


def service(provider=None):
    return LimitUpLadderService(
        provider or Provider(), clock=lambda: datetime(2026, 9, 22, 10, tzinfo=timezone(timedelta(hours=8)))
    )


def test_mapping_preserves_price_units_nulls_codes_and_orders_by_height():
    rows = normalize_pool(
        pd.DataFrame([stock(), stock("600001", 3, **{"最新价": float("nan"), "首次封板时间": 92500, "炸板次数": 0})])
    )
    assert [row["code"] for row in rows] == ["600001", "000001"]
    assert rows[0]["first_seal"] == "09:25:00"
    assert rows[0]["price"] is None and rows[0]["open_count"] == 0
    assert rows[1]["price"] == 10.5
    json.dumps(rows, allow_nan=False)


@pytest.mark.parametrize(
    "rows", [[stock(streak=0)], [stock(streak=1.5)], [stock("bad")], [stock(), stock()], [{"名称": "缺字段"}]]
)
def test_malformed_pool_does_not_silently_drop_stocks(rows):
    with pytest.raises(AkShareUnavailable):
        normalize_pool(pd.DataFrame(rows))


def test_empty_pool_is_explicit():
    class Empty(Provider):
        def call(self, name, **kwargs):
            return pd.DataFrame()

    value = service(Empty()).snapshot("2026-09-20")
    assert value["date"] == "2026-09-20" and value["status"] == "empty" and value["stocks"] == []


def test_today_uses_shanghai_timezone_and_cache_expires():
    provider = Provider()
    now = datetime(2026, 9, 21, 17, tzinfo=timezone.utc)
    loader = LimitUpLadderService(provider, clock=lambda: now)
    assert loader.snapshot()["date"] == "2026-09-22"
    loader.snapshot()
    assert len(provider.calls) == 1
    now += timedelta(seconds=61)
    loader.snapshot()
    assert len(provider.calls) == 2


def test_date_cache_is_isolated_refreshable_and_failure_is_not_stale_success():
    provider = Provider()
    loader = service(provider)
    first = loader.snapshot("2026-09-21")
    first["stocks"].clear()
    assert loader.snapshot("2026-09-21")["stocks"][0]["streak"] == 3
    assert len(provider.calls) == 1
    assert loader.snapshot("2026-09-18")["stocks"][0]["streak"] == 1
    loader.snapshot("2026-09-21", refresh=True)
    assert len(provider.calls) == 3
    provider.fail = True
    with pytest.raises(AkShareUnavailable):
        loader.snapshot("2026-09-21", refresh=True)


@pytest.mark.parametrize("day", ["20260921", "2026-02-30", "2026-09-23", "../2026-09-21", "", "1989-01-01"])
def test_invalid_and_future_dates_rejected_before_provider(day):
    provider = Provider()
    with pytest.raises(ValueError):
        service(provider).snapshot(day)
    assert provider.calls == []


def test_http_date_validation_source_and_upstream_errors():
    provider = Provider()
    server = make_server(SimpleNamespace(), port=0, serve_static=False, limit_up_ladder=service(provider))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    try:
        connection.request("GET", "/api/limit-up-ladder?date=2026-09-21")
        response = connection.getresponse()
        data = json.loads(response.read())
        assert response.status == 200 and data["source"] == "akshare/eastmoney"
        assert data["date"] == "2026-09-21" and data["stocks"][0]["streak"] == 3
        for query in ["date=2026-09-21&date=2026-09-18", "refresh=yes", "unknown=1", "date="]:
            connection.request("GET", "/api/limit-up-ladder?" + query)
            response = connection.getresponse()
            response.read()
            assert response.status == 400
        provider.fail = True
        connection.request("GET", "/api/limit-up-ladder?date=2026-09-21&refresh=true")
        response = connection.getresponse()
        body = json.loads(response.read())
        assert response.status == 503 and "error" in body and "stocks" not in body
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
