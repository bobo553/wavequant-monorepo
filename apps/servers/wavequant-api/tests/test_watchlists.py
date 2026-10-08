"""Real SQLite and loopback HTTP coverage, without external market requests."""

from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
import json
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from wavequant_api.backtest_jobs import BacktestJob, BacktestJobs
from wavequant_api.server import make_server
from wavequant_api.watchlists import DEFAULT_GROUP, WatchlistConflict, WatchlistStore, snapshot, settings
from wavequant_api.watchlist_refresh import WatchlistRefresh


def saved(symbols: tuple[str, ...] = ("sz.000678",)) -> dict[str, object]:
    return {"schemaVersion": 1, "groups": [DEFAULT_GROUP, {"id": "focus", "name": "观察", "position": 1}],
            "memberships": [{"groupId": "focus", "symbol": symbol, "name": symbol, "position": index}
                            for index, symbol in enumerate(symbols)]}


def configured(enabled: bool = True) -> dict[str, object]:
    return {"enabled": enabled, "context": {"run": "example", "variant": "lecture_v3", "scenario": "base",
            "source": "akshare", "start": "2018-01-01", "volume_filter": "false",
            "net_reward_risk_filter": "false", "shallow_base_breakout_enabled": "true",
            "initial_capital": "100000", "max_position_weight": "1"}}


def test_restart_order_settings_and_conflicting_saves(tmp_path: Path) -> None:
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    doc = store.save(0, saved(("sz.000678", "sh.600001")))
    store.save(doc["revision"], configured(False), field="settings")
    restored = WatchlistStore(store.path).load()
    assert restored["settings"] == configured(False)
    assert restored["snapshot"] == snapshot(saved(("sz.000678", "sh.600001")))
    with pytest.raises(WatchlistConflict):
        store.save(0, saved(()))
    assert store.load() == restored


def test_concurrent_revision_allows_only_one_writer(tmp_path: Path) -> None:
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    def write(symbol: str) -> bool:
        try:
            store.save(0, saved((symbol,)))
            return True
        except WatchlistConflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(write, ("sz.000678", "sh.600001"))) == [False, True]
    assert store.load()["revision"] == 1


def test_import_is_atomic_idempotent_and_preserves_server_order(tmp_path: Path) -> None:
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    store.save(0, saved(("sh.600001",)))
    first = store.import_legacy("browser-0001", saved(("sz.000678", "sh.600001")))
    assert store.import_legacy("browser-0001", saved(())) == first
    assert first["snapshot"] == snapshot(saved(("sh.600001", "sz.000678")))
    with pytest.raises(ValueError):
        store.import_legacy("browser-0002", {**saved(), "memberships": [{"groupId": "focus", "symbol": "../../etc"}]})
    assert store.load() == first


def test_migration_keeps_free_category_ids_and_remaps_name_collisions(tmp_path: Path) -> None:
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    first = store.import_legacy("browser-0001", saved())
    assert first["snapshot"]["groups"][1]["id"] == "focus"
    other = saved(("sh.600001",)); other["groups"][1]["name"] = "第二分类"
    second = store.import_legacy("browser-0002", other)
    new_group = second["snapshot"]["groups"][2]
    assert new_group["id"] != "focus" and new_group["name"] == "第二分类"
    assert second["snapshot"]["memberships"][1]["groupId"] == new_group["id"]


@pytest.mark.parametrize("value", [None, {}, {**saved(), "groups": []},
                                  {**saved(), "memberships": [{"groupId": "missing", "symbol": "sz.000678"}]},
                                  {**saved(), "memberships": [{"groupId": "focus", "symbol": "sz.000678"}] * 1001}])
def test_invalid_snapshot_cannot_destroy_existing_data(tmp_path: Path, value: object) -> None:
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    old = store.save(0, saved())
    with pytest.raises(ValueError):
        store.save(1, value)
    assert store.load() == old


@pytest.mark.parametrize("key,value", [("initial_capital", "nan"), ("max_position_weight", "2"),
                                      ("start", "2026-99-01"), ("volume_filter", "yes"), ("source", "file")])
def test_settings_reject_invalid_parameters(key: str, value: str) -> None:
    data = configured()
    data["context"][key] = value
    with pytest.raises(ValueError):
        settings(data)


def refresh_fixture(tmp_path: Path):
    store = WatchlistStore(tmp_path / "watchlists.sqlite")
    document = saved(("sz.000678", "sh.600001"))
    document["memberships"].append({"groupId": "default", "symbol": "sz.000678", "name": "重复来源"})
    doc = store.save(0, document)
    store.save(doc["revision"], configured(), field="settings")
    jobs = BacktestJobs()
    state = {"version": "v1", "asof": "2026-09-30", "now": 100.0}
    calls = []
    def submit(path, params):
        job = BacktestJob("controlled", details={"path": path, "params": {k: v[0] for k, v in params.items()},
                                               "version": state["version"], "symbol": params["symbol"][0]})
        calls.append((path, params, job))
        return job
    def create():
        return WatchlistRefresh(store, jobs, version=lambda *_: state["version"],
                               catalog=lambda _: {"stocks": [{"symbol": symbol, "last": state["asof"]}
                                                             for symbol in ("sz.000678", "sh.600001")]},
                               submit=submit, clock=lambda: state["now"])
    return store, state, calls, create


def complete(job, *, failed=False):
    if failed:
        job.error = ValueError("provider unavailable")
    else:
        job.result = {"symbol": job.details["params"]["symbol"], "asof": job.details["params"]["asof"],
                      "result_scope": "stock", "backtest": {"status": "complete"},
                      "orders": [{"status": "filled"}], "metrics": {"total_return": 0.1, "total_pnl": 10}}
    job.done.set()


def test_all_groups_dedup_restart_engine_and_market_refresh(tmp_path: Path) -> None:
    store, state, calls, create = refresh_fixture(tmp_path)
    refresh = create()
    refresh.tick()
    assert calls[0][1]["symbol"] == ["sz.000678"]
    complete(calls[0][2]); refresh.tick()
    assert calls[1][1]["symbol"] == ["sh.600001"]
    complete(calls[1][2]); refresh.tick()
    assert refresh.state()["completed"] == 2 and len(calls) == 2
    create().tick()
    assert len(calls) == 2 and len(store.summaries()) == 2
    state["version"] = "v2"
    restarted = create(); restarted.tick()
    assert len(calls) == 3 and restarted.state()["completed"] == 0
    complete(calls[2][2]); restarted.tick(); complete(calls[3][2]); restarted.tick()
    state.update(asof="2026-10-01", now=140.0)
    restarted.tick()
    assert calls[4][1]["asof"] == ["2026-10-01"]
    assert restarted.state()["completed"] == 0


def test_pause_parameter_change_and_bounded_retry_survive_restart(tmp_path: Path) -> None:
    store, state, calls, create = refresh_fixture(tmp_path)
    refresh = create(); refresh.tick()
    complete(calls[0][2], failed=True); refresh.tick()
    # Another stock can run while the first one waits for its retry.
    complete(calls[1][2]); refresh.tick()
    assert len(calls) == 2
    for number, now in enumerate((106.0, 122.0), start=2):
        state["now"] = now
        refresh = create(); refresh.tick(); complete(calls[number][2], failed=True); refresh.tick()
    state["now"] = 1000.0
    create().tick()
    assert len(calls) == 4 and refresh.state()["failed"] == 1
    doc = store.load(); store.save(doc["revision"], configured(False), field="settings")
    store.retry(); create().tick()
    assert len(calls) == 4
    doc = store.load(); config = configured(); config["context"]["max_position_weight"] = "0.5"
    store.save(doc["revision"], config, field="settings")
    create().tick()
    assert calls[-1][1]["max_position_weight"] == ["0.5"]


def test_background_waits_for_existing_foreground_capacity(tmp_path: Path) -> None:
    store, state, calls, create = refresh_fixture(tmp_path)
    refresh = create()
    refresh.jobs.snapshot = lambda: {"active": 1, "recent": []}
    refresh.tick()
    assert not calls and refresh.state()["status"] == "waiting"


def test_mismatched_or_unavailable_result_is_never_recorded_as_current(tmp_path: Path) -> None:
    store, state, calls, create = refresh_fixture(tmp_path)
    refresh = create(); refresh.tick()
    complete(calls[0][2]); calls[0][2].result["symbol"] = "sh.600000"
    refresh.tick()
    complete(calls[1][2]); calls[1][2].result["backtest"]["status"] = "data_unavailable"
    refresh.tick()
    assert not store.summaries() and refresh.state()["completed"] == 0


def test_http_persistence_origin_body_conflict_and_read_only_other_routes(tmp_path: Path) -> None:
    repository = SimpleNamespace(root=tmp_path, backtest_engine={}, backtest_version=lambda *_: {"version": "v1"})
    server = make_server(repository, port=0, serve_static=False, allowed_origins=("http://127.0.0.1:3003",))
    thread = Thread(target=server.serve_forever); thread.start()
    def request(method, body=None, headers=None, path="/api/watchlists"):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request(method, path, json.dumps(body) if body else None,
                               {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()
    try:
        assert request("GET")[1]["revision"] == 0
        assert request("POST", {"action": "save", "revision": 0, "snapshot": saved()}, {"Origin": "https://evil.test"})[0] == 403
        assert request("POST", {"action": "save", "revision": 0, "snapshot": saved()}, {"Content-Type": "text/plain"})[0] == 400
        status, document = request("POST", {"action": "save", "revision": 0, "snapshot": saved()}, {"Origin": "http://127.0.0.1:3003"})
        assert status == 200 and document["revision"] == 1
        assert request("POST", {"action": "save", "revision": 0, "snapshot": saved(())})[0] == 409
        assert request("GET")[1] == document
        assert request("POST", {}, path="/api/akshare-backtest")[0] == 405
        assert request("PUT", {})[0] == 405
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_real_http_settings_start_background_jobs_after_the_client_disconnects(tmp_path: Path) -> None:
    all_started = Event()
    calls = []
    def backtest(*args, **kwargs):
        calls.append((args, kwargs))
        kwargs["progress"](80, "模拟成交")
        if len(calls) == 2:
            all_started.set()
        return {"symbol": args[2], "asof": args[3], "result_scope": "stock", "backtest": {"status": "complete"},
                "orders": [], "metrics": {"total_return": 0, "total_pnl": 0}}
    repository = SimpleNamespace(root=tmp_path, backtest_engine={}, backtest_version=lambda *_: {"version": "v1"},
        market_data=SimpleNamespace(catalog=lambda _: {"stocks": [{"symbol": symbol, "last": "2026-09-30"}
                                                                   for symbol in ("sz.000678", "sh.600001")]}),
        akshare_backtest=backtest)
    server = make_server(repository, port=0, serve_static=False)
    thread = Thread(target=server.serve_forever); thread.start()
    def save(body):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        try:
            connection.request("POST", "/api/watchlists", json.dumps(body), {"Content-Type": "application/json"})
            response = connection.getresponse()
            assert response.status == 200
            return json.loads(response.read())
        finally:
            connection.close()
    try:
        document = save({"action": "save", "revision": 0, "snapshot": saved(("sz.000678", "sh.600001"))})
        save({"action": "settings", "revision": document["revision"], "settings": configured()})
        # No browser task or HTTP backtest call follows the accepted setting.
        assert all_started.wait(8)
        assert [call[0][2] for call in calls] == ["sz.000678", "sh.600001"]
        assert calls[0][1]["volume_filter"] is False
        assert calls[0][1]["initial_capital"] == 100000
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
