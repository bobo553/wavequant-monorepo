"""Loopback HTTP adapter for the WaveQuant dashboard."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from wavequant.visualization import ChartRepository

from .infrastructure import Infrastructure, InfrastructureSettings


APPS_ROOT = Path(__file__).resolve().parents[4]
WEB_WORKSPACE_ROOT = APPS_ROOT / "webs" / "wavequant-web"
DEFAULT_WEB_ROOT = WEB_WORKSPACE_ROOT / "src"


def make_server(repository, *, host="127.0.0.1", port=8765, web_root=None, infrastructure=None):
    if host not in ("127.0.0.1", "localhost"):
        raise ValueError("dashboard binds to loopback only")
    assets = Path(web_root).resolve() if web_root else DEFAULT_WEB_ROOT
    sdk_candidates = (
        (assets / "node_modules/lightweight-charts",)
        if web_root
        else (WEB_WORKSPACE_ROOT / "node_modules/lightweight-charts",)
    )
    sdk = next(
        (
            candidate
            for candidate in sdk_candidates
            if (candidate / "dist/lightweight-charts.standalone.production.js").is_file()
        ),
        sdk_candidates[0],
    )
    allowed = {
        "/": assets / "index.html",
        "/app.js": assets / "app.js",
        "/charts.js": assets / "charts.js",
        "/styles.css": assets / "styles.css",
        "/labels.js": assets / "labels.js",
        "/annotations.js": assets / "annotations.js",
        "/lecture-overlay.js": assets / "lecture-overlay.js",
        "/stock-list.js": assets / "stock-list.js",
        "/trade-review.js": assets / "trade-review.js",
        "/buy-points.js": assets / "buy-points.js",
        "/ratio-comparison.js": assets / "ratio-comparison.js",
        "/vendor/lightweight-charts.js": sdk / "dist/lightweight-charts.standalone.production.js",
        "/vendor/NOTICE": (
            assets / "THIRD_PARTY_NOTICE.txt" if web_root else WEB_WORKSPACE_ROOT / "THIRD_PARTY_NOTICE.txt"
        ),
        "/vendor/LICENSE": sdk / "LICENSE",
    }
    if not allowed["/vendor/lightweight-charts.js"].is_file():
        raise ValueError("TradingView SDK missing: run pnpm install at the monorepo root")

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
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            )
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def do_GET(self):
            host_header = self.headers.get("Host", "")
            valid = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            if host_header not in valid:
                self.send(403, {"error": "loopback Host required"})
                return
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://{h}" for h in valid}:
                self.send(403, {"error": "cross-origin access denied"})
                return
            url = urlsplit(self.path)
            if url.path == "/favicon.ico":
                self.send(204, b"", "image/x-icon")
                return
            try:
                if url.path in allowed:
                    path = allowed[url.path]
                    mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
                        path.suffix, "text/plain"
                    )
                    self.send(200, path.read_bytes(), mime + "; charset=utf-8")
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
            if self.headers.get("Host") not in valid or self.headers.get("Origin") not in {
                None,
                *("http://" + h for h in valid),
            }:
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
) -> None:
    repository = ChartRepository(root, tdx_root=tdx_root)
    services = infrastructure or Infrastructure.from_settings(InfrastructureSettings.from_env())
    server = make_server(repository, port=port, web_root=web_root, infrastructure=services)
    print(f"WaveQuant read-only dashboard: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        services.close()
