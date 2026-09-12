"""Loopback HTTP adapter for the WaveQuant dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from wavequant.interfaces.charts.visualization import ChartRepository

from .infrastructure import Infrastructure, InfrastructureSettings


APPS_ROOT = Path(__file__).resolve().parents[4]
WEB_WORKSPACE_ROOT = APPS_ROOT / "webs" / "wavequant-web"
DEFAULT_WEB_ROOT = WEB_WORKSPACE_ROOT / "out"


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
        static_routes = {"/" + path.relative_to(assets).as_posix(): path for path in assets.rglob("*") if path.is_file()}
        static_routes["/"] = assets / "index.html"
        static_routes["/research"] = assets / "research.html"
        market_page = assets / "market.html"
        if market_page.is_file():
            static_routes["/market"] = market_page
    proxy_origins = set(allowed_origins)
    web_redirect = normalize_loopback_web_url(web_url)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

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
                    self.send(
                        200,
                        repository.tdx.catalog()
                        if repository.tdx
                        else dict(available=False, stocks=[], with_daily=0, notice="未配置通达信目录"),
                    )
                    return
                q = parse_qs(url.query, strict_parsing=True)
                if any(len(v) != 1 for v in q.values()):
                    raise ValueError("duplicate query arguments")
                if url.path == "/api/buy-scan":
                    if set(q) not in ({"id"}, {"id", "after"}):
                        raise ValueError("scan id and optional revision required")
                    after = int(q["after"][0]) if "after" in q else None
                    self.send(200, repository.buy_scanner.get(q["id"][0], after=after))
                    return
                if url.path in ("/api/tdx-view", "/api/tdx-theory"):
                    if set(q) != {"symbol", "asof"}:
                        raise ValueError("invalid TDX arguments")
                    if repository.tdx is None:
                        raise ValueError("通达信目录未配置")
                    method = repository.tdx.view if url.path == "/api/tdx-view" else repository.tdx.theory
                    self.send(200, method(q["symbol"][0], q["asof"][0]))
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
                if url.path == "/api/tdx-backtest":
                    if set(q) != {"run", "variant", "symbol", "asof", "scenario", "start"}:
                        raise ValueError("invalid TDX backtest arguments")
                    self.send(
                        200,
                        repository.tdx_backtest(
                            *(q[k][0] for k in ("run", "variant", "symbol", "asof", "scenario", "start"))
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
            except (ValueError, KeyError, FileNotFoundError) as exc:
                self.send(400, {"error": str(exc) if isinstance(exc, ValueError) else "required result unavailable"})
            except Exception:
                logging.exception("chart service request failed")
                self.send(500, {"error": "chart service error; check server logs"})

        def do_POST(self):
            if self.path not in ("/api/buy-scan", "/api/buy-scan/cancel"):
                self.send(405, {"error": "read-only server: mutations disabled"})
                return
            valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            valid_origins = {f"http://{host}" for host in valid} | proxy_origins
            if self.headers.get("Host") not in valid or self.headers.get("Origin") not in {None, *valid_origins}:
                self.send(403, {"error": "loopback same-origin required"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 4096 or self.headers.get("Content-Type") != "application/json":
                    raise ValueError("small JSON body required")
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                if self.path.endswith("/cancel"):
                    if set(body) != {"id"}:
                        raise ValueError("scan id required")
                    result = repository.buy_scanner.cancel(body["id"])
                else:
                    result = repository.buy_scanner.start(body)
                self.send(200, result)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.send(400, {"error": str(exc) if isinstance(exc, ValueError) else "invalid screening request"})

        def reject_mutation(self):
            self.send(405, {"error": "read-only data; only screening jobs supported"})

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
) -> None:
    repository = ChartRepository(root, tdx_root=tdx_root)
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
