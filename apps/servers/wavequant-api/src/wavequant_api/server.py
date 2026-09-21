"""Loopback HTTP adapter for the WaveQuant dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import logging
import math
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from wavequant.infrastructure.market_data.akshare import AkShareUnavailable
from wavequant.interfaces.charts.visualization import ChartRepository

from .application import (
    BuySignalSnapshotService,
    BuySignalSnapshotUnavailable,
    MarketTimeframeService,
    StructureSnapshotService,
    StructureSnapshotUnavailable,
)
from .infrastructure import Infrastructure, InfrastructureSettings


APPS_ROOT = Path(__file__).resolve().parents[4]
WEB_WORKSPACE_ROOT = APPS_ROOT / "webs" / "wavequant-web"
DEFAULT_WEB_ROOT = WEB_WORKSPACE_ROOT / "out"


def backtest_positive_number(query: dict[str, list[str]], name: str, default: float, maximum: float) -> float:
    """Parse one bounded backtest input before it reaches the research engine."""
    raw = query.get(name, [str(default)])[0]
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f"{name} must be in (0, {maximum}]")
    return value


def normalize_loopback_web_url(value: str | None) -> str | None:
    """Validate the optional development UI target used by the API root.

    The API is deliberately loopback-only. Accepting an arbitrary redirect
    target here would turn a trusted local URL into an open redirect, so the
    target must be a plain HTTP origin on the same loopback host family.
    """
    if value is None:
        return None
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Web URL must contain a valid port") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Web URL must be a loopback HTTP origin with an explicit port")
    return f"http://{parsed.hostname}:{port}/"


def make_server(
    repository,
    *,
    host="127.0.0.1",
    port=8765,
    web_root=None,
    infrastructure=None,
    serve_static=True,
    allowed_origins=(),
    web_url=None,
):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("dashboard binds to loopback only")
    static_routes = {}
    if serve_static:
        assets = Path(web_root).resolve() if web_root else DEFAULT_WEB_ROOT
        if not (assets / "index.html").is_file():
            raise ValueError("WaveQuant Web build missing: run pnpm --filter wavequant-web build")
        static_routes = {
            "/" + path.relative_to(assets).as_posix(): path for path in assets.rglob("*") if path.is_file()
        }
        static_routes["/"] = assets / "index.html"
        static_routes["/research"] = assets / "research.html"
        market_page = assets / "market.html"
        if market_page.is_file():
            static_routes["/market"] = market_page
    proxy_origins = set(allowed_origins)
    web_redirect = normalize_loopback_web_url(web_url)
    structure_snapshots = StructureSnapshotService(repository, infrastructure) if infrastructure is not None else None
    buy_snapshots = BuySignalSnapshotService(repository, infrastructure) if infrastructure is not None else None
    market_data = getattr(repository, "market_data", None)
    snapshot_database = getattr(infrastructure, "database", None) if infrastructure is not None else None
    snapshot_cache = getattr(infrastructure, "cache", None) if infrastructure is not None else None
    market_timeframes = (
        MarketTimeframeService(market_data, snapshots=snapshot_database, cache=snapshot_cache)
        if market_data is not None
        else None
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(
            self,
            status,
            body,
            content_type="application/json; charset=utf-8",
            *,
            cache_control="no-store",
            headers=None,
        ):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache_control)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def send_versioned_catalog(self, catalog):
            body = json.dumps(
                catalog,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            etag = f'"{hashlib.sha256(body).hexdigest()}"'
            headers = {"ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                self.send(304, b"", cache_control="private, no-cache", headers=headers)
                return
            self.send(200, body, cache_control="private, no-cache", headers=headers)

        def send_versioned_payload(self, payload, version):
            """Serve a materialized read model with conditional revalidation."""

            body = json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()
            etag = f'"{version}"'
            headers = {"ETag": etag}
            if self.headers.get("If-None-Match") == etag:
                self.send(304, b"", cache_control="private, no-cache", headers=headers)
                return
            self.send(200, body, cache_control="private, no-cache", headers=headers)

        def redirect(self, location):
            """Send a non-cacheable development redirect without a response body."""
            self.send_response(307)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()

        def do_GET(self):
            host_header = self.headers.get("Host", "")
            valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host_header not in valid:
                self.send(403, {"error": "loopback Host required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in valid} | proxy_origins:
                self.send(403, {"error": "cross-origin access denied"})
                return
            url = urlsplit(self.path)
            if url.path == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
                return
            try:
                request_path = unquote(url.path)
                if request_path in static_routes:
                    path = static_routes[request_path]
                    mime = (
                        "text/plain"
                        if path.name in {"LICENSE", "NOTICE"}
                        else {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
                            path.suffix.lower(), mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                        )
                    )
                    self.send(200, path.read_bytes(), mime + "; charset=utf-8")
                    return
                if request_path == "/" and web_redirect is not None:
                    self.redirect(web_redirect)
                    return
                if url.path == "/api/catalog":
                    catalog = (
                        infrastructure.cached_json("api:catalog:v1", repository.catalog)
                        if infrastructure is not None
                        else repository.catalog()
                    )
                    self.send(200, catalog)
                    return
                if url.path == "/api/infrastructure/health":
                    if url.query:
                        raise ValueError("infrastructure health does not accept query arguments")
                    self.send(
                        200,
                        infrastructure.health()
                        if infrastructure is not None
                        else {
                            "status": "disabled",
                            "database": {"configured": False, "healthy": False},
                            "redis": {"configured": False, "healthy": False},
                        },
                    )
                    return
                if url.path == "/api/tdx-catalog":
                    self.send_versioned_catalog(
                        repository.market_data.catalog("tdx")
                        if repository.tdx
                        else dict(available=False, stocks=[], with_daily=0, notice="未配置通达信目录"),
                    )
                    return
                if url.path == "/api/akshare-catalog":
                    if url.query:
                        raise ValueError("AkShare catalog does not accept query arguments")
                    if repository.akshare is None:
                        self.send_versioned_catalog(
                            dict(available=False, stocks=[], with_daily=0, notice="AkShare 数据源已禁用")
                        )
                    else:
                        self.send_versioned_catalog(repository.market_data.catalog("akshare"))
                    return
                q = parse_qs(url.query, strict_parsing=True, keep_blank_values=True)
                if any(len(v) != 1 for v in q.values()):
                    raise ValueError("duplicate query arguments")
                if url.path == "/api/market-timeframe":
                    if set(q) != {"source", "symbol", "asof", "timeframe"}:
                        raise ValueError("invalid market timeframe arguments")
                    source = q["source"][0]
                    if source not in {"tdx", "akshare"}:
                        raise ValueError("source must be tdx or akshare")
                    if source == "tdx" and repository.tdx is None:
                        raise ValueError("通达信目录未配置")
                    if source == "akshare" and repository.akshare is None:
                        raise AkShareUnavailable("AkShare 数据源已禁用")
                    if market_timeframes is None:
                        raise ValueError("行情仓库未配置")
                    bundle = market_timeframes.bundle(
                        source,
                        q["symbol"][0],
                        q["asof"][0],
                        q["timeframe"][0],
                    )
                    self.send_versioned_payload(bundle, bundle["snapshot_id"])
                    return
                if url.path == "/api/structure-signals":
                    expected = {"run", "variant", "source", "asof", "lookback", "signal_type", "trend_level"}
                    optional = {"symbol", "markets"}
                    if not expected.issubset(q) or not set(q).issubset(expected | optional):
                        raise ValueError("invalid precomputed structure query")
                    params = {key: q[key][0] for key in set(q)}
                    params["lookback"] = int(params["lookback"])
                    params["trend_level"] = int(params["trend_level"])
                    if "symbol" in q and params["source"] != "akshare":
                        raise ValueError("symbol is only accepted for legacy AkShare clients")
                    if structure_snapshots is None:
                        raise StructureSnapshotUnavailable("结构读模型未配置")
                    self.send(200, structure_snapshots.query(params))
                    return
                if url.path == "/api/buy-signals":
                    expected = {"run", "variant", "scenario", "source", "asof", "start", "lookback"}
                    online_expected = expected | {"symbol"}
                    if set(q) not in (expected, online_expected):
                        raise ValueError("invalid precomputed buy signal query")
                    params = {key: q[key][0] for key in set(q)}
                    params["lookback"] = int(params["lookback"])
                    if (params["source"] == "akshare") != (set(q) == online_expected):
                        raise ValueError("AkShare buy query requires one symbol")
                    if buy_snapshots is None:
                        raise BuySignalSnapshotUnavailable("买点读模型未配置")
                    self.send(200, buy_snapshots.query(params))
                    return
                if url.path in ("/api/tdx-view", "/api/tdx-theory"):
                    if set(q) not in ({"symbol", "asof"}, {"symbol", "asof", "timeframe"}):
                        raise ValueError("invalid TDX arguments")
                    if repository.tdx is None:
                        raise ValueError("通达信目录未配置")
                    if market_timeframes is None:
                        raise ValueError("行情仓库未配置")
                    method = market_timeframes.view if url.path == "/api/tdx-view" else market_timeframes.theory
                    self.send(200, method("tdx", q["symbol"][0], q["asof"][0], q.get("timeframe", ["1d"])[0]))
                    return
                if url.path in ("/api/akshare-view", "/api/akshare-theory"):
                    if set(q) not in ({"symbol", "asof"}, {"symbol", "asof", "timeframe"}):
                        raise ValueError("invalid AkShare arguments")
                    if repository.akshare is None:
                        raise AkShareUnavailable("AkShare 数据源已禁用")
                    if market_timeframes is None:
                        raise ValueError("行情仓库未配置")
                    method = market_timeframes.view if url.path == "/api/akshare-view" else market_timeframes.theory
                    self.send(200, method("akshare", q["symbol"][0], q["asof"][0], q.get("timeframe", ["1d"])[0]))
                    return
                if url.path == "/api/health":
                    if set(q) != {"run"}:
                        raise ValueError("run argument required")
                    self.send(200, repository.health(q["run"][0]))
                    return
                if url.path == "/api/stock-summary":
                    if set(q) != {"run", "variant", "asof", "scenario"}:
                        raise ValueError("invalid summary arguments")
                    self.send(200, repository.stock_summary(*(q[k][0] for k in ("run", "variant", "asof", "scenario"))))
                    return
                if url.path in ("/api/tdx-backtest", "/api/akshare-backtest"):
                    required = {"run", "variant", "symbol", "asof", "scenario", "start"}
                    optional = {"volume_filter", "net_reward_risk_filter", "initial_capital", "max_position_weight"}
                    if not required <= set(q) or set(q) - required - optional:
                        raise ValueError("invalid TDX backtest arguments")
                    filter_values = q.get("volume_filter", ["true"])
                    if len(filter_values) != 1:
                        raise ValueError("volume_filter must be provided once")
                    volume_filter = filter_values[0]
                    if volume_filter not in ("true", "false"):
                        raise ValueError("volume_filter must be true or false")
                    risk_values = q.get("net_reward_risk_filter", ["false"])
                    if len(risk_values) != 1 or risk_values[0] not in ("true", "false"):
                        raise ValueError("net_reward_risk_filter must be true or false and provided once")
                    initial_capital = backtest_positive_number(q, "initial_capital", 100_000, 1_000_000_000)
                    max_position_weight = backtest_positive_number(q, "max_position_weight", 1.0, 1.0)
                    self.send(
                        200,
                        (
                            repository.akshare_backtest
                            if url.path == "/api/akshare-backtest"
                            else repository.tdx_backtest
                        )(
                            *(q[k][0] for k in ("run", "variant", "symbol", "asof", "scenario", "start")),
                            volume_filter=volume_filter == "true",
                            net_reward_risk_filter=risk_values[0] == "true",
                            initial_capital=initial_capital,
                            max_position_weight=max_position_weight,
                        ),
                    )
                    return
                if url.path not in ("/api/view", "/api/stock-view", "/api/theory"):
                    self.send(404, {"error": "not found"})
                    return
                expected = {"run", "variant", "symbol", "asof"} | ({"scenario"} if url.path != "/api/theory" else set())
                if set(q) != expected:
                    raise ValueError("invalid query arguments")
                args = [q[k][0] for k in ("run", "variant", "symbol", "asof")]
                result = (
                    repository.view(*args, q["scenario"][0])
                    if url.path == "/api/view"
                    else repository.stock_view(*args, q["scenario"][0])
                    if url.path == "/api/stock-view"
                    else repository.theory(*args)
                )
                self.send(200, result)
            except (BuySignalSnapshotUnavailable, StructureSnapshotUnavailable, AkShareUnavailable, LookupError) as exc:
                self.send(503, {"error": str(exc)})
            except (ValueError, KeyError, FileNotFoundError) as exc:
                self.send(400, {"error": str(exc) if isinstance(exc, ValueError) else "required result unavailable"})
            except Exception:
                logging.exception("chart service request failed")
                self.send(500, {"error": "chart service error; check server logs"})

        def do_POST(self):
            self.send(405, {"error": "read-only server: signal calculations run only in background workers"})

        def reject_mutation(self):
            self.send(405, {"error": "read-only server: mutations disabled"})

        do_PUT = reject_mutation
        do_DELETE = reject_mutation
        do_PATCH = reject_mutation

    return ThreadingHTTPServer((host, port), Handler)


def serve_dashboard(
    root: str | Path,
    port: int = 8765,
    tdx_root: str | Path | None = None,
    web_root: str | Path | None = None,
    infrastructure: Infrastructure | None = None,
    serve_static: bool = True,
    allowed_origins: tuple[str, ...] = (),
    web_url: str | None = None,
    akshare_enabled: bool = True,
    akshare_timeout: float = 30.0,
) -> None:
    repository = ChartRepository(
        root,
        tdx_root=tdx_root,
        akshare_enabled=akshare_enabled,
        akshare_timeout=akshare_timeout,
        artifact_cache_scope="api",
    )
    services = infrastructure or Infrastructure.from_settings(InfrastructureSettings.from_env())
    server = make_server(
        repository,
        port=port,
        web_root=web_root,
        infrastructure=services,
        serve_static=serve_static,
        allowed_origins=allowed_origins,
        web_url=web_url,
    )
    print(f"WaveQuant read-only dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        services.close()
