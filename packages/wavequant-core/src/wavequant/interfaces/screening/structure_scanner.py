"""Cancellable searches over confirmed market-structure landmarks.

The scanner deliberately consumes the same Core landmarks rendered by the
research chart.  It never reconstructs a transition from raw bars, and it
matches a recent window by ``available_at`` so historical replay cannot reveal
an event before the complete structural evidence was knowable.
"""

from collections import Counter
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
from threading import Condition, Event, Lock, Thread
from time import perf_counter
from uuid import uuid4

LANDMARK_FIELDS = {
    "bear_to_bull": "bear_to_bull_highs",
    "bear_bull_alternation": "bear_bull_alternation_lows",
}
LEVEL_FIELDS = {
    1: "reversal_trends",
    2: "secondary_trends",
    3: "tertiary_trends",
}


def structure_matches(
    theory,
    sessions,
    *,
    symbol,
    name,
    asof,
    lookback,
    signal_type,
    trend_level,
):
    """Return landmarks confirmed inside the requested trading-session window.

    ``time`` is the date of the structural high or low.  It is intentionally
    not used for screening because the point may only become confirmed several
    sessions later.  ``available_at`` is the causal search date and therefore
    owns both inclusion and result ordering.
    """
    if signal_type not in {"any", *LANDMARK_FIELDS}:
        raise ValueError("invalid structure signal type")
    if type(trend_level) is not int or trend_level not in (0, 1, 2, 3):
        raise ValueError("trend level must be 0, 1, 2 or 3")
    if type(lookback) is not int or lookback not in (1, 5, 20):
        raise ValueError("lookback must be 1, 5 or 20")
    if not isinstance(sessions, list) or not all(isinstance(day, str) for day in sessions):
        raise ValueError("invalid structure sessions")

    eligible_sessions = [day for day in sessions if day <= asof]
    if not eligible_sessions or eligible_sessions[-1] != asof:
        return [], "stale"
    window = set(eligible_sessions[-lookback:])
    levels = (trend_level,) if trend_level else (1, 2, 3)
    signal_types = tuple(LANDMARK_FIELDS) if signal_type == "any" else (signal_type,)
    results = []
    for level in levels:
        group = theory.get(LEVEL_FIELDS[level], {})
        for matched_type in signal_types:
            landmarks = group.get(LANDMARK_FIELDS[matched_type], []) if isinstance(group, dict) else []
            for landmark in landmarks:
                if not isinstance(landmark, dict) or landmark.get("available_at") not in window:
                    continue
                # These fields are part of the published landmark contract.  An
                # incomplete cache entry is not a valid search hit.
                if not all(field in landmark for field in ("id", "time", "kind", "value", "available_at")):
                    continue
                results.append(
                    dict(
                        id=landmark["id"],
                        symbol=symbol,
                        name=name,
                        signal_type=matched_type,
                        trend_level=level,
                        event_date=landmark["time"],
                        available_at=landmark["available_at"],
                        # ``1`` means confirmed on the requested session.  A
                        # persisted 20-session snapshot can therefore answer
                        # the 1/5/20-session views without recomputing theory.
                        session_age=len(eligible_sessions) - eligible_sessions.index(landmark["available_at"]),
                        label=landmark.get("label"),
                        kind=landmark["kind"],
                        value=landmark["value"],
                        price_basis=theory.get("price_basis"),
                        evidence=deepcopy(landmark),
                    )
                )
    results.sort(key=lambda row: (row["available_at"], row["event_date"], row["trend_level"], row["id"]), reverse=True)
    return results, None


class StructureScanner:
    """Run bounded, read-only landmark searches without blocking HTTP polls."""

    def __init__(self, repository):
        self.repository = repository
        self.lock = Lock()
        self.changed = Condition(self.lock)
        self.jobs = {}
        self.stops = {}
        from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester

        self.engine_hashes = TdxBacktester._engine_hashes
        self.engine = self.engine_hashes()

    def algorithm_version(self):
        """Return a stable digest for every Python file affecting Core theory."""
        if self.engine_hashes() != self.engine:
            raise RuntimeError("结构算法文件已变化，请由进程管理器重启 Worker 后自动重建")
        encoded = json.dumps(self.engine, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def current_akshare(self, params):
        """Filter confirmed landmarks for one cached/on-demand AkShare symbol."""
        expected = {"run", "variant", "source", "symbol", "asof", "lookback", "signal_type", "trend_level"}
        if set(params) != expected or params.get("source") != "akshare":
            raise ValueError("invalid AkShare structure query")
        request = dict(params)
        self.repository._run(request["run"])
        self.repository.strategy_config(request["run"], request["variant"])
        if type(request["lookback"]) is not int or request["lookback"] not in (1, 5, 20):
            raise ValueError("lookback must be 1, 5 or 20")
        if type(request["trend_level"]) is not int or request["trend_level"] not in (0, 1, 2, 3):
            raise ValueError("trend level must be 0, 1, 2 or 3")
        if request["signal_type"] not in {"any", *LANDMARK_FIELDS}:
            raise ValueError("invalid structure signal type")
        if not isinstance(request["asof"], str) or date.fromisoformat(request["asof"]).isoformat() != request["asof"]:
            raise ValueError("invalid date")
        if self.repository.akshare is None:
            raise ValueError("AkShare 数据源未配置")
        stock = next(
            (
                item
                for item in self.repository.market_data.catalog("akshare")["stocks"]
                if item["symbol"] == request["symbol"]
            ),
            None,
        )
        if stock is None:
            raise ValueError("AkShare 股票不在可用目录")
        selection = self.repository.market_data.window("akshare", request["symbol"], request["asof"])
        bars = list(selection.bars)
        sessions = [bar.timestamp.date().isoformat() for bar in bars]
        theory = self.repository.market_data.theory("akshare", request["symbol"], request["asof"])
        results, stale = structure_matches(
            theory,
            sessions,
            symbol=request["symbol"],
            name=stock.get("name"),
            asof=request["asof"],
            lookback=request["lookback"],
            signal_type=request["signal_type"],
            trend_level=request["trend_level"],
        )
        encoded = json.dumps(
            [[bar.timestamp.date().isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume] for bar in bars],
            separators=(",", ":"),
        ).encode()
        return dict(
            status="ready",
            params=request,
            total=1,
            processed=1,
            failed=0,
            stale=int(bool(stale)),
            skipped=0,
            skip_reasons={},
            results=results,
            errors=[],
            matched_stocks=int(bool(results)),
            snapshot=dict(
                source=selection.requested_source,
                resolved_source=selection.resolved_source,
                providers=list(selection.providers),
                supplemented_bars=selection.supplemented_bars,
                computed_at=datetime.now().astimezone().isoformat(),
                data_version=theory.get("data_version", hashlib.sha256(encoded).hexdigest()),
                algorithm_version=self.algorithm_version(),
            ),
            notice=(
                "AkShare 当前股票在线原始不复权结构分析；未进行全市场抓取或交易。"
                if selection.resolved_source == selection.requested_source and not selection.supplemented_bars
                else "当前股票按统一行情仓库分析；部分或全部日线由备用源补齐，未进行交易。"
            ),
        )

    def latest_tdx_session(self):
        """Use the modal latest date so one stray file cannot move the market cutoff."""
        if self.repository.tdx is None:
            raise ValueError("通达信目录未配置")
        counts = Counter(
            stock.get("last")
            for stock in self.repository.tdx.catalog()["stocks"]
            if stock.get("has_data") and isinstance(stock.get("last"), str) and stock.get("last")
        )
        if not counts:
            raise ValueError("通达信目录没有可用日线")
        return max(counts, key=lambda day: (counts[day], day))

    def describe_tdx(self, asof):
        """Freeze the eligible universe and derive its inexpensive source fingerprint."""
        if self.repository.tdx is None:
            raise ValueError("通达信目录未配置")
        skipped = Counter()
        stocks = []
        versions = {}
        digest = hashlib.sha256()
        for stock in self.repository.tdx.catalog()["stocks"]:
            reason = (
                "no_daily" if not stock.get("has_data") else "stale_daily" if stock.get("last", "") < asof else None
            )
            if reason:
                skipped[reason] += 1
                continue
            path = self.repository.tdx._path(stock["symbol"])
            stat = path.stat()
            version = (stat.st_mtime_ns, stat.st_size)
            versions[stock["symbol"]] = version
            stocks.append(dict(symbol=stock["symbol"], name=stock.get("name")))
            digest.update(
                json.dumps(
                    [stock["symbol"], stock.get("name"), stock.get("last"), *version],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
        return dict(
            stocks=stocks,
            versions=versions,
            skipped=skipped,
            data_version=digest.hexdigest(),
        )

    def start(self, params):
        expected = {"run", "variant", "source", "asof", "lookback", "signal_type", "trend_level"}
        if set(params) != expected:
            raise ValueError("invalid structure screening arguments")
        if self.engine_hashes() != self.engine:
            raise ValueError("画线代码已变更，请重启服务后再搜索")

        request = dict(params)
        repository = self.repository
        repository._run(request["run"])
        repository.strategy_config(request["run"], request["variant"])
        if request["source"] not in ("tdx", "snapshot"):
            raise ValueError("invalid structure screening source")
        if request["signal_type"] not in {"any", *LANDMARK_FIELDS}:
            raise ValueError("invalid structure signal type")
        if type(request["trend_level"]) is not int or request["trend_level"] not in (0, 1, 2, 3):
            raise ValueError("trend level must be 0, 1, 2 or 3")
        if type(request["lookback"]) is not int or request["lookback"] not in (1, 5, 20):
            raise ValueError("lookback must be 1, 5 or 20")
        if not isinstance(request["asof"], str) or date.fromisoformat(request["asof"]).isoformat() != request["asof"]:
            raise ValueError("invalid date")

        skipped = Counter()
        stocks = []
        versions = {}
        data_version = None
        if request["source"] == "tdx":
            source = self.describe_tdx(request["asof"])
            skipped = source["skipped"]
            stocks = source["stocks"]
            versions = source["versions"]
            data_version = source["data_version"]
        else:
            run_end = repository.runs[request["run"]]["report"]["data"]["end"]
            if request["asof"] > run_end:
                raise ValueError("回放日超出封存样本")
            stocks = [dict(symbol=symbol, name=None) for symbol in sorted(repository.bars(request["run"]))]

        with self.lock:
            for job in self.jobs.values():
                if job["status"] in ("running", "cancelling"):
                    if job["params"] == request and job["status"] == "running":
                        return deepcopy(job)
                    raise ValueError("已有结构搜索在运行，请先取消或等待完成")
            while len(self.jobs) >= 4:
                oldest = next(iter(self.jobs))
                self.jobs.pop(oldest)
                self.stops.pop(oldest, None)
            identifier = uuid4().hex
            stop = Event()
            job = dict(
                id=identifier,
                params=request,
                status="running",
                revision=0,
                total=len(stocks),
                processed=0,
                failed=0,
                stale=0,
                skipped=sum(skipped.values()),
                skip_reasons=dict(skipped),
                results=[],
                errors=[],
                current=None,
                created_at=datetime.now().astimezone().isoformat(),
                error=None,
                performance=dict(elapsed_seconds=0),
                algorithm_version=self.algorithm_version(),
                data_version=data_version,
                notice="只搜索已确认结构地标；发生日不等于确认可用日，结果不构成买卖建议。",
            )
            self.jobs[identifier] = job
            self.stops[identifier] = stop
            Thread(target=self._work, args=(identifier, stocks, versions), daemon=True).start()
            return deepcopy(job)

    def _work(self, identifier, stocks, versions):
        repository = self.repository
        params = self.jobs[identifier]["params"]
        stop = self.stops[identifier]
        started = perf_counter()
        try:
            for stock in stocks:
                if stop.is_set():
                    break
                if self.engine_hashes() != self.engine:
                    raise RuntimeError("搜索期间画线代码已变化，结果作废；请重启服务")
                symbol = stock["symbol"]
                with self.lock:
                    self.jobs[identifier]["current"] = symbol
                    self._publish(identifier)
                try:
                    if params["source"] == "tdx":
                        path = repository.tdx._path(symbol)
                        stat = path.stat()
                        if (stat.st_mtime_ns, stat.st_size) != versions[symbol]:
                            raise RuntimeError("搜索期间日线已更新，请重新搜索")
                        bars = repository.tdx.bars(symbol, params["asof"])
                        theory = repository.tdx.theory(symbol, params["asof"])
                    else:
                        source_bars = repository.bars(params["run"])[symbol]
                        bars = [bar for bar in source_bars if bar.timestamp.date().isoformat() <= params["asof"]]
                        theory = repository.theory(params["run"], params["variant"], symbol, params["asof"])
                    sessions = [bar.timestamp.date().isoformat() for bar in bars]
                    matches, stale = structure_matches(
                        theory,
                        sessions,
                        symbol=symbol,
                        name=stock["name"],
                        asof=params["asof"],
                        lookback=params["lookback"],
                        signal_type=params["signal_type"],
                        trend_level=params["trend_level"],
                    )
                    with self.lock:
                        self.jobs[identifier]["results"].extend(matches)
                        if stale:
                            self.jobs[identifier]["stale"] += 1
                except (ValueError, FileNotFoundError) as exc:
                    with self.lock:
                        self.jobs[identifier]["failed"] += 1
                        if len(self.jobs[identifier]["errors"]) < 30:
                            self.jobs[identifier]["errors"].append(dict(symbol=symbol, error=str(exc)))
                with self.lock:
                    self.jobs[identifier]["processed"] += 1
                    self.jobs[identifier]["performance"]["elapsed_seconds"] = perf_counter() - started
                    self._publish(identifier)

            if params["source"] == "tdx":
                for symbol, version in versions.items():
                    stat = repository.tdx._path(symbol).stat()
                    if (stat.st_mtime_ns, stat.st_size) != version:
                        raise RuntimeError("行情文件发生变化，结果作废")
            with self.lock:
                self.jobs[identifier].update(status="cancelled" if stop.is_set() else "completed", current=None)
                self._publish(identifier)
        except Exception as exc:
            with self.lock:
                self.jobs[identifier].update(status="failed", error=str(exc), results=[], current=None)
                self._publish(identifier)

    def _publish(self, identifier):
        """Publish one immutable revision while the caller owns ``self.lock``."""
        self.jobs[identifier]["revision"] += 1
        self.changed.notify_all()

    def get(self, identifier, after=None, *, timeout=20):
        with self.changed:
            if identifier not in self.jobs:
                raise ValueError("结构搜索任务不存在或服务已重启")
            if after is not None:
                if type(after) is not int or not 0 <= after <= self.jobs[identifier]["revision"]:
                    raise ValueError("invalid structure scan revision")
                if not 0 <= timeout <= 20:
                    raise ValueError("invalid structure scan wait timeout")
                self.changed.wait_for(
                    lambda: (
                        identifier not in self.jobs
                        or self.jobs[identifier]["revision"] > after
                        or self.jobs[identifier]["status"] not in ("running", "cancelling")
                    ),
                    timeout=timeout,
                )
                if identifier not in self.jobs:
                    raise ValueError("结构搜索任务不存在或已清理")
            return deepcopy(self.jobs[identifier])

    def cancel(self, identifier):
        with self.lock:
            if identifier not in self.jobs:
                raise ValueError("结构搜索任务不存在")
            if self.jobs[identifier]["status"] == "running":
                self.stops[identifier].set()
                self.jobs[identifier]["status"] = "cancelling"
                self._publish(identifier)
            return deepcopy(self.jobs[identifier])
