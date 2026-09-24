"""Read-only local chart data adapter. Never executes strategies as orders.

Only registered, sealed operations runs are exposed. Historical chart queries
filter bars, fills and closed trades as of the selected Shanghai daily session.
Theory is computed from that prefix, not by hiding future candles afterwards.
"""

from dataclasses import asdict
from datetime import date, datetime
from functools import lru_cache
import csv
import hashlib
import json
from pathlib import Path
from threading import Lock
from zoneinfo import ZoneInfo

from wavequant.infrastructure.market_data.data import fingerprint
from wavequant.infrastructure.persistence.event_store import EventStore
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, generate_system_signals, pivot_history
from wavequant.infrastructure.persistence.operational_store import run_states, alert_states
from wavequant.application.governance.operations import verify_run
from wavequant.domain.market_structure.n_shape import project_n_targets, ValueDomain
from wavequant.domain.market_structure.price_action import Direction
from wavequant.infrastructure.filesystem.project_paths import project_path
from wavequant.domain.market_structure.lecture_drawing import lecture_drawing
from wavequant.domain.market_structure.lecture_trend import reversal_trends
from wavequant.domain.market_structure.secondary_trend import secondary_trends
from wavequant.domain.market_structure.tertiary_trend import tertiary_trends
from wavequant.domain.market_structure.abc_candidate import AbcAnchor, tertiary_abc_observations
from wavequant.interfaces.research_tools.stock_backtest import single_stock_result
from wavequant.application.analytics.trade_evidence import result_markers
from wavequant.domain.strategies.strategy_profiles import research_profile, PROFILE_ID, hierarchical_profile, HIERARCHICAL_PROFILE_ID
from wavequant.domain.strategies.strategy_profiles import whole_wave_profile, WAVE_PROFILES
from wavequant.domain.strategies import integrated_strategy as integrated_strategy_module


def _strategy_source_path() -> Path:
    """Return the loaded strategy implementation path, independent of layout.

    Reports fingerprint the executable strategy source as part of their audit
    trail. Deriving that file with ``Path(__file__).with_name(...)`` silently
    breaks whenever the presentation adapter moves to another feature package.
    The imported module is the authoritative location in both editable and
    wheel installations.
    """

    source = integrated_strategy_module.__file__
    if source is None:
        raise RuntimeError("integrated strategy module has no source file")
    return Path(source)


VARIANTS = {
    "strict_full": "旧严格折线版",
    "proxy_full": "日线代理版",
    "lecture_v1": "讲义因果版 V1",
    "lecture_v2": "分级双买点 V2",
}
VARIANTS.update(
    {
        "lecture_v3": "整段双买点 V3 · 二/三级交替 / 第二类收盘 ≤1/3",
        "lecture_v3_c50": "整段双买点 V3 · 二/三级交替 / 第二类收盘 <1/2",
        "lecture_v3_d67_c33": "整段双买点 V3 · 深 >2/3 / 收盘 ≤1/3",
        "lecture_v3_d50_c50": "整段双买点 V3 · 深 >1/2 / 收盘 ≤1/2",
        "lecture_v3_d67_c50": "整段双买点 V3 · 深 >2/3 / 收盘 ≤1/2",
        "lecture_v3_close_d50_c50": "整段双买点 V3 · 第一类收盘 >1/2 / 第二类收盘 <1/2",
    }
)
SCENARIOS = {"base": "原费用", "cost_2x": "2 倍成本", "cost_3x": "3 倍成本", "capacity_half": "半容量"}


def day(value):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is not None:
        stamp = stamp.astimezone(ZoneInfo("Asia/Shanghai"))
    return stamp.date().isoformat()


def json_file(path):
    return json.loads(path.read_text(encoding="utf-8"))


def confirmed_polyline_segments(bars, snapshots, epochs, blocked):
    """Preserve the last known shape of each separate structural episode."""
    latest = {}
    for i in range(len(bars)):
        if i in blocked:
            continue
        latest[epochs[i]] = (i, snapshots[i])
    segments = []
    for epoch, (known, points) in latest.items():
        values = [
            dict(
                time=day(bars[p.point.index].timestamp.isoformat()),
                value=p.point.price,
                kind=p.point.kind.value,
                confirmed_at=day(bars[p.confirmed_index].timestamp.isoformat()),
            )
            for p in points
            if epoch <= p.point.index <= known and p.confirmed_index <= known
        ]
        if values:
            segments.append(dict(id=f"epoch-{epoch}", points=values))
    return segments


def metrics_at(equity, trades, capital):
    peak = capital
    drawdown = 0
    points = []
    exposure = 0
    for row in equity:
        value = float(row["equity"])
        peak = max(peak, value)
        dd = value / peak - 1
        drawdown = min(drawdown, dd)
        exposure += float(row["exposure"])
        points.append(
            dict(
                time=day(row["timestamp"]),
                value=value / capital,
                drawdown=dd * 100,
                cash=float(row["cash"]),
                exposure=float(row["exposure"]) * 100,
            )
        )
    return dict(
        total_return=points[-1]["value"] - 1 if points else 0,
        max_drawdown=drawdown,
        trades=len(trades),
        average_exposure=exposure / len(points) if points else 0,
        equity=float(equity[-1]["equity"]) if equity else capital,
        win_rate=sum(float(t["pnl"]) > 0 for t in trades) / len(trades) if trades else None,
    ), points


class ChartRepository:
    def __init__(
        self,
        root,
        tdx_root=None,
        *,
        akshare_enabled=True,
        akshare_timeout=30.0,
        artifact_cache_scope=None,
    ):
        from wavequant.interfaces.charts.akshare_browser import AkShareBrowser
        from wavequant.interfaces.charts.market_data_repository import (
            AkShareMarketDataAdapter,
            MarketDataRepository,
            TdxMarketDataAdapter,
        )
        from wavequant.interfaces.charts.tdx_browser import TdxBrowser

        shared_cache_root = project_path("data", "cache")
        cache_root = shared_cache_root
        if artifact_cache_scope is not None:
            if (
                not isinstance(artifact_cache_scope, str)
                or not artifact_cache_scope
                or len(artifact_cache_scope) > 48
                or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in artifact_cache_scope)
            ):
                raise ValueError("invalid artifact cache scope")
            cache_root /= artifact_cache_scope
        self.tdx = TdxBrowser(tdx_root, cache=cache_root) if tdx_root else None
        self.akshare = AkShareBrowser(timeout=akshare_timeout, pinned_history=True) if akshare_enabled else None
        adapters = []
        if self.akshare is not None:
            adapters.append(AkShareMarketDataAdapter(self.akshare))
        if self.tdx is not None:
            adapters.append(TdxMarketDataAdapter(self.tdx))
        self.market_data = MarketDataRepository(
            adapters,
            default_source="akshare",
            # Keep the online source as the requested contract, but preserve a
            # usable chart when AkShare is temporarily unavailable. Local TDX
            # remains isolated and never triggers an unexpected network read.
            fallback_order={"akshare": ("tdx",), "tdx": ()},
        )
        self.root = Path(root).resolve()
        self.runs = {}
        self.cache = {}
        self.theory_lock = Lock()
        self.stock_lock = Lock()
        from wavequant.interfaces.research_tools.tdx_backtest import TdxBacktester

        self.tdx_backtester = (
            TdxBacktester(self.tdx, cache_root, actions_cache=shared_cache_root) if self.tdx else None
        )
        from wavequant.interfaces.research_tools.akshare_backtest import AkShareBacktester
        self.akshare_backtester = AkShareBacktester(self.akshare, cache_root / 'akshare') if self.akshare else None
        self.refresh()
        from wavequant.interfaces.screening.buy_scanner import BuyScanner
        from wavequant.interfaces.screening.structure_scanner import StructureScanner

        self.buy_scanner = BuyScanner(self)
        self.structure_scanner = StructureScanner(self)

    def refresh(self):
        path = self.root / "operations.sqlite"
        if not path.is_file():
            raise ValueError("existing operations journal required")
        store = EventStore(path, readonly=True)
        try:
            registered = run_states(store)
        finally:
            store.close()
        runs = {}
        for rid, state in registered.items():
            path = (self.root / "runs" / rid).resolve()
            if not path.is_relative_to(self.root / "runs") or state["status"] != "COMPLETED":
                continue
            if not (path / "research/report.json").is_file():
                continue
            verify_run(path, state["artifacts_sha256"])
            runs[rid] = dict(path=path, state=state, report=json_file(path / "report.json"))
        if not runs:
            raise ValueError("no sealed run with research data; run system-run first")
        self.runs = runs

    def _run(self, rid):
        if rid not in self.runs:
            raise ValueError("unknown registered run")
        return self.runs[rid]

    def _json(self, rid, relative):
        return json.loads(self._bytes(rid, relative))

    def _bytes(self, rid, relative):
        """Check bytes against immutable manifest before placing them in cache."""
        row = self._run(rid)
        path = row["path"]
        key = (rid, relative)
        if key not in self.cache:
            artifact_path = path / "artifacts.json"
            if fingerprint(artifact_path) != row["state"]["artifacts_sha256"]:
                raise ValueError("artifact manifest changed")
            manifest = json_file(artifact_path)
            raw = (path / relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest.get(relative):
                raise ValueError("sealed input changed")
            self.cache[key] = raw
        return self.cache[key]

    def _csv(self, rid, relative):
        import io

        return list(csv.DictReader(io.StringIO(self._bytes(rid, relative).decode("utf-8-sig"))))

    @lru_cache(maxsize=8)
    def bars(self, rid):
        self._bytes(rid, "snapshot/daily.csv")
        # Parse precisely the verified bytes, not a second mutable file read.
        import io
        from wavequant.domain.models.model import Bar
        from wavequant.infrastructure.market_data.io import _validate_bar, _parse_bool

        grouped = {}
        for row in csv.DictReader(io.StringIO(self._bytes(rid, "snapshot/daily.csv").decode("utf-8-sig"))):
            b = Bar(
                datetime.fromisoformat(row["timestamp"]),
                row["symbol"],
                *(float(row[k]) for k in ("open", "high", "low", "close", "volume")),
                _parse_bool(row.get("buyable")),
                _parse_bool(row.get("sellable")),
                float(row.get("adjustment_factor") or 1),
            )
            _validate_bar(b, 0)
            grouped.setdefault(b.symbol, []).append(b)
        for values in grouped.values():
            values.sort(key=lambda b: b.timestamp)
        return grouped

    def catalog(self):
        result = []
        for rid, r in sorted(self.runs.items(), key=lambda item: item[1]["state"]["finished_at"], reverse=True):
            grouped = self.bars(rid)
            result.append(
                dict(
                    id=rid,
                    completed_at=r["state"]["finished_at"],
                    symbols=[
                        dict(symbol=s, sessions=[day(b.timestamp.isoformat()) for b in bs])
                        for s, bs in sorted(grouped.items())
                    ],
                    start=r["report"]["data"]["start"],
                    end=r["report"]["data"]["end"],
                    rows=r["report"]["data"]["rows"],
                )
            )
        return dict(
            runs=result,
            variants=VARIANTS,
            scenarios=SCENARIOS,
            sdk="TradingView Lightweight Charts 5.2.1",
            mode="read_only_offline_research",
            price_basis="causal_adjusted_equivalent",
            notice="历史诊断，不是实时行情；严格版与日线代理不得混合。",
        )

    def selection(self, rid, variant, symbol, asof, scenario="base"):
        self._run(rid)
        if variant not in VARIANTS or scenario not in SCENARIOS:
            raise ValueError("unknown strategy or scenario")
        grouped = self.bars(rid)
        if symbol not in grouped:
            raise ValueError("unknown symbol")
        if date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("use YYYY-MM-DD date")
        if asof > day(grouped[symbol][-1].timestamp.isoformat()):
            raise ValueError("as-of exceeds last available session")
        bars = [b for b in grouped[symbol] if day(b.timestamp.isoformat()) <= asof]
        if not bars:
            raise ValueError("no bars available as of selected date")
        return bars

    def strategy_config(self, rid, variant):
        variants = self._json(rid, "research/report.json")["variants"]
        if variant in WAVE_PROFILES:
            return whole_wave_profile(variants["strict_full"], variant)
        if variant in (PROFILE_ID, HIERARCHICAL_PROFILE_ID):
            # Only execution scenarios inherit the selected frozen experiment.
            return (hierarchical_profile if variant == HIERARCHICAL_PROFILE_ID else research_profile)(
                variants["strict_full"]
            )
        return variants[variant]

    def view(self, rid, variant, symbol, asof, scenario="base"):
        if variant in (PROFILE_ID, HIERARCHICAL_PROFILE_ID, *WAVE_PROFILES):
            raise ValueError("讲义新版未存在于旧封存组合，请使用个股回测或通达信回测")
        bars = self.selection(rid, variant, symbol, asof, scenario)
        prefix = f"research/{variant}/{scenario}"
        equity = [r for r in self._csv(rid, prefix + "/equity.csv") if day(r["timestamp"]) <= asof]
        closed = [r for r in self._csv(rid, prefix + "/trades.csv") if day(r["exit_time"]) <= asof]
        orders = [r for r in self._csv(rid, prefix + "/orders.csv") if day(r["timestamp"]) <= asof]
        signals = [r for r in self._csv(rid, f"research/{variant}/signals.csv") if day(r["timestamp"]) <= asof]
        research = self._json(rid, "research/report.json")
        capital = research["variants"][variant]["scenarios"][scenario]["execution"]["initial_capital"]
        metrics, curve = metrics_at(equity, closed, float(capital))
        selected_signals = [dict(r, time=day(r["timestamp"])) for r in signals if r["symbol"] == symbol]
        selected_orders = [dict(r, time=day(r["timestamp"])) for r in orders if r["symbol"] == symbol]

        # Do not send an open trade's future exit timestamp/price/P&L at all.
        def number(value):
            return float(value) if value not in (None, "") else None

        markers = [
            dict(
                id=f"order-{index}",
                time=r["time"],
                kind="fill" if r["status"] == "filled" else "order",
                side=r["side"],
                status=r["status"],
                price=number(r.get("price")),
                reason=r["reason"],
                quantity=number(r.get("quantity")),
                fee=number(r.get("fee")),
                stop=number(r.get("stop_price")),
                target=number(r.get("target_price")),
                signal_time=day(r["signal_timestamp"]) if r.get("signal_timestamp") else None,
                source="sealed_orders",
            )
            for index, r in enumerate(selected_orders)
        ]
        markers += [
            dict(
                id=f"signal-{index}",
                time=r["time"],
                kind="signal",
                side=r["side"],
                price=number(r["reference_price"]),
                reason=r["reason"],
                regime=r.get("regime"),
                stop=number(r.get("invalidation_price")) if r["side"] == "LONG" else None,
                target=number(r.get("target_price")) if r["side"] == "LONG" else None,
                rvol=number(r.get("rvol")),
                source="sealed_signals",
            )
            for index, r in enumerate(selected_signals)
            if r["side"] in ("LONG", "EXIT")
        ]
        return dict(
            run_id=rid,
            variant=variant,
            scenario=scenario,
            symbol=symbol,
            asof=asof,
            price_basis="causal_adjusted_equivalent",
            bars=[
                dict(
                    time=day(b.timestamp.isoformat()),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    factor=b.adjustment_factor,
                    raw_close=b.close / b.adjustment_factor,
                )
                for b in bars
            ],
            markers=sorted(markers, key=lambda r: r["time"]),
            signals=selected_signals,
            orders=orders,
            trades=closed,
            metrics=metrics,
            curve=curve,
            evidence="无平仓交易证据" if not closed else "交易样本有限，尚未建立策略优势",
            comparison="同比 / 环比 N/A：独立历史实验；切换成本场景进行同区间比较。",
        )

    @lru_cache(maxsize=32)
    def _stock_signals(self, rid, variant, symbol, asof):
        bars = self.selection(rid, variant, symbol, asof)
        strategy = self.strategy_config(rid, variant)["strategy"]
        return generate_system_signals(bars, SystemStrategy(**strategy))

    def stock_view(self, rid, variant, symbol, asof, scenario="base"):
        self.selection(rid, variant, symbol, asof, scenario)
        with self.stock_lock:
            return self._stock_view(rid, variant, symbol, asof, scenario)

    @lru_cache(maxsize=64)
    def _stock_view(self, rid, variant, symbol, asof, scenario):
        bars = self.selection(rid, variant, symbol, asof, scenario)
        config = self.strategy_config(rid, variant)
        result = single_stock_result(
            bars,
            config["strategy"],
            config["scenarios"][scenario]["execution"],
            self._stock_signals(rid, variant, symbol, asof),
        )
        _, curve = metrics_at(result["equity"], result["trades"], result["backtest"]["initial_capital"])
        markers = result_markers(result)
        result["backtest"].update(
            source_run=rid, data_sha256=hashlib.sha256(self._bytes(rid, "snapshot/daily.csv")).hexdigest()
        )
        return dict(
            run_id=rid,
            variant=variant,
            scenario=scenario,
            symbol=symbol,
            asof=asof,
            result_scope="stock",
            price_basis="causal_adjusted_equivalent",
            strategy_profile=dict(
                id=variant, version=config.get("profile_version", variant), definition=config.get("definition", {})
            ),
            bars=[
                dict(
                    time=day(b.timestamp.isoformat()),
                    open=b.open,
                    high=b.high,
                    low=b.low,
                    close=b.close,
                    volume=b.volume,
                    factor=b.adjustment_factor,
                    raw_close=b.close / b.adjustment_factor,
                )
                for b in bars
            ],
            markers=sorted(markers, key=lambda r: r["time"]),
            signals=result["signals"],
            orders=result["orders"],
            trades=result["trades"],
            metrics=result["metrics"],
            curve=curve,
            backtest=result["backtest"],
            audit=result["audit"],
            evidence="个股独立回测：" + ("无平仓交易证据" if not result["trades"] else "已有成交样本，不代表策略有效"),
            comparison="相同资金与风控的独立账户，不是共享组合结果的拆分。",
        )

    def akshare_signal_view(self, rid, variant, symbol, asof, scenario="base", start="1990-01-01"):
        """Generate signal evidence for one online symbol without simulating fills."""
        self._run(rid)
        if variant not in VARIANTS or scenario not in SCENARIOS:
            raise ValueError("unknown strategy or scenario")
        if self.akshare is None:
            raise ValueError("AkShare 数据源未配置")
        if date.fromisoformat(start).isoformat() != start or date.fromisoformat(asof).isoformat() != asof:
            raise ValueError("use YYYY-MM-DD date")
        if start > asof:
            raise ValueError("回测起点不能晚于回放日期")
        selection = self.market_data.window("akshare", symbol, asof)
        bars = [bar for bar in selection.bars if bar.timestamp.date().isoformat() >= start]
        if not bars:
            raise ValueError("所选起点后没有 AkShare 日线")
        config = self.strategy_config(rid, variant)
        generated = generate_system_signals(bars, SystemStrategy(**config["strategy"]))

        def serial(value):
            return json.loads(
                json.dumps(value, default=lambda item: item.isoformat() if isinstance(item, datetime) else str(item))
            )

        signals = [
            dict(serial(asdict(signal)), time=signal.timestamp.date().isoformat()) for signal in generated.signals
        ]
        audit = serial(generated.audit)
        for event in audit:
            if not event.get("buy_point_type"):
                continue
            for key in (
                "flip_index",
                "alternation_index",
                "maturity_index",
                "attack",
                "pullback_index",
                "flip_high_index",
                "alternation_low_index",
                "impulse_origin_index",
                "impulse_high_index",
                "origin_index",
                "peak_index",
                "minimum_close_index",
                "eligibility_frozen_at",
            ):
                index = event.get(key)
                if isinstance(index, int) and 0 <= index <= event["bar_index"]:
                    event[key + "_date"] = bars[index].timestamp.date().isoformat()
        return dict(
            run_id=rid,
            variant=variant,
            scenario=scenario,
            symbol=symbol,
            asof=bars[-1].timestamp.date().isoformat(),
            result_scope="akshare_signal_only",
            data_source=selection.requested_source,
            resolved_source=selection.resolved_source,
            providers=list(selection.providers),
            supplemented_bars=selection.supplemented_bars,
            source_fallback=selection.resolved_source != selection.requested_source,
            source_warning=selection.primary_error,
            price_basis="raw_unadjusted",
            bars=[
                dict(
                    time=bar.timestamp.date().isoformat(),
                    open=bar.open,
                    high=bar.high,
                    low=bar.low,
                    close=bar.close,
                    volume=bar.volume,
                    factor=1,
                    raw_close=bar.close,
                )
                for bar in bars
            ],
            signals=signals,
            orders=[],
            audit=audit,
            backtest={"source": "akshare_online_raw_signal_only"},
        )

    @lru_cache(maxsize=12)
    def stock_summary(self, rid, variant, asof, scenario="base"):
        self._run(rid)
        if variant not in VARIANTS or scenario not in SCENARIOS:
            raise ValueError("unknown strategy or scenario")
        if date.fromisoformat(asof).isoformat() != asof or asof > self.runs[rid]["report"]["data"]["end"]:
            raise ValueError("invalid summary cutoff")
        rows = []
        for symbol, bars in sorted(self.bars(rid).items()):
            end = min(asof, day(bars[-1].timestamp.isoformat()))
            if end < day(bars[0].timestamp.isoformat()):
                rows.append(dict(symbol=symbol, status="no_history", asof=end))
                continue
            view = self.stock_view(rid, variant, symbol, end, scenario)
            rows.append(
                dict(
                    symbol=symbol,
                    status="completed",
                    asof=end,
                    metrics=view["metrics"],
                    diagnostics=view["backtest"]["diagnostics"],
                )
            )
        return dict(
            run_id=rid,
            variant=variant,
            scenario=scenario,
            asof=asof,
            results=rows,
            account_scope="independent_single_stock",
        )

    def theory(self, rid, variant, symbol, asof):
        self.selection(rid, variant, symbol, asof)
        with self.theory_lock:
            return self._theory(rid, variant, symbol, asof)

    @lru_cache(maxsize=24)
    def _theory(self, rid, variant, symbol, asof):
        bars = self.selection(rid, variant, symbol, asof)
        config = SystemStrategy(**self.strategy_config(rid, variant)["strategy"])
        result = self._stock_signals(rid, variant, symbol, asof)
        return self.render_theory(bars, config, result, asof)

    def tdx_backtest(
        self,
        rid,
        variant,
        symbol,
        asof,
        scenario,
        start,
        *,
        volume_filter=True,
        net_reward_risk_filter=False,
        initial_capital=100_000,
        max_position_weight=1.0,
    ):
        self._run(rid)
        if variant not in VARIANTS or scenario not in SCENARIOS:
            raise ValueError("unknown strategy or scenario")
        if type(volume_filter) is not bool:
            raise ValueError("volume_filter must be a boolean")
        if type(net_reward_risk_filter) is not bool:
            raise ValueError("net_reward_risk_filter must be a boolean")
        if self.tdx_backtester is None:
            raise ValueError("通达信目录未配置")
        config = self.strategy_config(rid, variant)
        # Never mutate a sealed profile: the effective strategy is the cache key,
        # so checked and unchecked backtests remain independently reproducible.
        strategy = dict(config["strategy"], volume_filter=volume_filter)
        execution = dict(
            config["scenarios"][scenario]["execution"],
            net_reward_risk_filter=net_reward_risk_filter,
            initial_capital=initial_capital,
            max_position_weight=max_position_weight,
        )
        bars, result, view = self.tdx_backtester.run(symbol, start, asof, strategy, execution)
        cache = self.tdx_backtester.artifacts
        key = dict(
            source=view["backtest"]["source"],
            symbol=symbol,
            start=start,
            asof=asof,
            strategy=strategy,
            price_basis=view["price_basis"],
        )
        with cache.lock(cache.key("adjusted_theory", key)):
            theory = cache.get("adjusted_theory", key)
            if theory is None:
                from .chart_geometry import cached_geometry

                geometry = cached_geometry(cache, bars, view["price_basis"], view["backtest"]["source"]["engine"])
                theory = self.render_theory(bars, SystemStrategy(**strategy), result, asof, geometry=geometry)
                self.tdx_backtester._verify(dict(view["backtest"]["source"], symbol=symbol))
                cache.put("adjusted_theory", key, theory)
        theory = dict(theory, price_basis=view["price_basis"], run_id=view["run_id"])
        definition = dict(config.get("definition", {}))
        definition["net_reward_risk_filter"] = net_reward_risk_filter
        definition["reward_risk_policy"] = (
            "execution_price_net_reward_risk_gate_enabled" if net_reward_risk_filter else "execution_price_net_reward_risk_gate_disabled"
        )
        if not net_reward_risk_filter and "primary_filters" in definition:
            definition["primary_filters"] = [
                name for name in definition["primary_filters"] if name not in ("next_open_net_rr_1_5", "execution_price_net_rr_1_5")
            ]
        if not volume_filter and "primary_filters" in definition:
            definition["primary_filters"] = [
                filter_name for filter_name in definition["primary_filters"] if filter_name not in ("rvol_1_2", "volume_gt_previous")
            ]
        return dict(
            view,
            variant=variant,
            scenario=scenario,
            parameter_source_run=rid,
            theory=theory,
            strategy_profile=dict(
                id=variant, version=config.get("profile_version", variant), definition=definition
            ),
        )

    def akshare_backtest(
        self,
        rid,
        variant,
        symbol,
        asof,
        scenario,
        start,
        *,
        volume_filter=True,
        net_reward_risk_filter=False,
        initial_capital=100_000,
        max_position_weight=1.0,
    ):
        self._run(rid)
        if variant not in VARIANTS or scenario not in SCENARIOS:
            raise ValueError('unknown strategy or scenario')
        if type(volume_filter) is not bool or type(net_reward_risk_filter) is not bool:
            raise ValueError('backtest filters must be boolean')
        if self.akshare_backtester is None:
            raise ValueError('AKShare 数据源未配置')
        profile = self.strategy_config(rid, variant)
        strategy = dict(profile['strategy'], volume_filter=volume_filter)
        execution = dict(
            profile["scenarios"][scenario]["execution"],
            net_reward_risk_filter=net_reward_risk_filter,
            initial_capital=initial_capital,
            max_position_weight=max_position_weight,
        )
        bars, generated, view = self.akshare_backtester.run(symbol, start, asof, strategy, execution)
        theory = self.render_theory(bars, SystemStrategy(**strategy), generated, view['asof'])
        theory.update(price_basis=view['price_basis'], data_source='akshare', upstream='sina', run_id=view['run_id'])
        definition = dict(profile.get('definition', {}), net_reward_risk_filter=net_reward_risk_filter)
        definition['reward_risk_policy'] = (
            'execution_price_net_reward_risk_gate_enabled' if net_reward_risk_filter else 'execution_price_net_reward_risk_gate_disabled'
        )
        if 'primary_filters' in definition:
            definition['primary_filters'] = [name for name in definition['primary_filters']
                if (volume_filter or name not in ('rvol_1_2', 'volume_gt_previous')) and (net_reward_risk_filter or name not in ('next_open_net_rr_1_5', 'execution_price_net_rr_1_5'))]
        return dict(view, variant=variant, scenario=scenario, parameter_source_run=rid, theory=theory,
                    strategy_profile=dict(id=variant, version=profile.get('profile_version', variant), definition=definition))

    def render_theory(self, bars, config, result, asof, *, geometry=None):
        snapshots, epochs, limits, blocked = pivot_history(bars, config)
        segments = confirmed_polyline_segments(bars, snapshots, epochs, blocked)
        i = len(bars) - 1
        points = [
            dict(
                time=day(bars[p.point.index].timestamp.isoformat()),
                value=p.point.price,
                kind=p.point.kind.value,
                confirmed_at=day(bars[p.confirmed_index].timestamp.isoformat()),
            )
            for p in snapshots[i]
        ]
        events = []
        shapes = []
        # Join later, dated projections back to the N the user selects. Keep
        # fulfilled targets for review and label suspended targets explicitly.
        extension_levels = {}
        for row in result.audit:
            if not row['event'].startswith('wave_projection_') or row['bar_index'] > i:
                continue
            levels = extension_levels.setdefault(row['attack'], {})
            reached = row.get('reached_stage')
            if reached in levels:
                levels[reached]['status'] = '已满足'
            stage = row.get('target_stage')
            if stage not in ('five_top', 'ten_full'):
                continue
            if row.get('target') is not None:
                levels[stage] = dict(
                    stage=stage, price=row['target'], status='推演中',
                    mode='叠箱' if row['state'] == 'stacking' else '堆箱',
                    available_at=day(bars[row['bar_index']].timestamp.isoformat()),
                    projection_span=row['projection_span'], a_origin=row['a_origin'],
                    a_high=row['a_high'], b_low=row.get('b_low'),
                )
            elif stage in levels and row['state'] in ('pullback', 'invalidated'):
                levels[stage]['status'] = '回调暂停' if row['state'] == 'pullback' else '已失效'
        for r in result.audit:
            available = max(r["bar_index"], r.get("known_at", r["bar_index"]))
            if available > i:
                continue
            event = dict(
                r,
                id=f"rule-{len(events)}",
                available_at=day(bars[available].timestamp.isoformat()),
                time=day(r["timestamp"]),
                price=bars[available].close,
                levels=[],
            )
            for key, title in (
                ("key", "末跌高"),
                ("key_price", "冻结末跌高"),
                ("bottom", "冻结波段低点"),
                ("stop", "信号失效参考"),
                ("target", "测幅目标（非保证）"),
                ("wave_c_1618_target", "C 浪扩展 1.618×A（非保证）"),
                ("wave_c_2618_target", "C 浪扩展 2.618×A（非保证）"),
            ):
                if r.get(key) is not None:
                    event["levels"].append(dict(name=title, price=r[key]))
            events.append(event)
            if r["event"] == "n_completed":
                up = r["direction"] == "up"
                indices = [r["origin"], r["neckline"], r["pullback"], r["bar_index"]]
                coords = [
                    dict(
                        time=day(bars[j].timestamp.isoformat()),
                        value=(bars[j].low if (k % 2 == 0) == up else bars[j].high),
                    )
                    for k, j in enumerate(indices)
                ]
                event["shape"] = coords
                attack = bars[r["bar_index"]]
                previous = bars[r["bar_index"] - 1]
                box = max(attack.high, previous.close) if up else min(attack.low, previous.close)
                targets = project_n_targets(
                    *(p["value"] for p in coords[:3]),
                    box_anchor=box,
                    direction=Direction.UP if up else Direction.DOWN,
                    domain=ValueDomain.PRICE,
                )
                event["levels"] += [
                    dict(name="颈线（高低点）", price=coords[1]["value"]),
                    dict(name="颈线（前波收盘）", price=bars[r["neckline"]].close),
                    dict(name="轧空低" if up else "杀多高", price=r["defense"]),
                ]
                event["levels"] += [
                    dict(name=title, price=getattr(targets, key))
                    for key, title in (("equal_wave", "等浪投影"), ("one_p", "1P 投影"), ("two_t", "2T 投影"))
                    if getattr(targets, key) is not None
                ]
                for level in extension_levels.get(r['bar_index'], {}).values():
                    title = '五顶' if level['stage'] == 'five_top' else '十满'
                    event['levels'].append(dict(level, name=f"{title}（{level['mode']} · {level['status']}）"))
                shapes.append(
                    dict(
                        points=coords,
                        direction=r["direction"],
                        available_at=event["available_at"],
                        defense=r["defense"],
                        attack_time=event["time"],
                    )
                )
        # Only the last few objects are needed for legible chart overlays; all
        # dated event evidence is available in the event panel.
        if geometry is None:
            drawing = lecture_drawing(bars)
            level1 = reversal_trends(drawing, bars)
            level2 = secondary_trends(level1, bars)
            geometry = dict(
                lecture_drawing=drawing,
                reversal_trends=level1,
                secondary_trends=level2,
                tertiary_trends=tertiary_trends(level2, bars),
            )
        from ...domain.market_structure.squeeze_alternation import squeeze_landmarks
        from ...domain.market_structure.alternation_breakout import promote_alternation_segments
        geometry = dict(geometry)
        for level, name in ((2, "secondary_trends"), (3, "tertiary_trends")):
            if name not in geometry:
                continue
            additions = squeeze_landmarks(bars, result.audit, level)
            identities = {(p["confirmed_bear_low"]["index"], p["confirmed_flip_high"]["index"], p["index"])
                          for p in additions}
            ordinary = [p for p in geometry[name].get("bear_bull_alternation_lows", [])
                        if (p["confirmed_bear_low"]["index"], p["confirmed_flip_high"]["index"], p["index"])
                        not in identities]
            geometry[name] = dict(geometry[name], bear_bull_alternation_lows=[*ordinary, *additions])
            geometry[name] = promote_alternation_segments(geometry[name], bars, result.audit, level)
            if level == 2:
                # Level 3 must consume the same ordered level-2 geometry shown
                # on the chart, including newly confirmed local segments.
                geometry["tertiary_trends"] = tertiary_trends(geometry[name], bars)
        dates = {day(bar.timestamp.isoformat()): index for index, bar in enumerate(bars)}
        anchors = [
            AbcAnchor(
                high["confirmed_low"]["index"], high["index"],
                dates[high["available_at"]], high["confirmed_low"]["value"],
                high["value"], high["source_path"],
            )
            for high in geometry["tertiary_trends"].get("bear_to_bull_highs", [])
            if high["available_at"] in dates
        ]
        for observation in tertiary_abc_observations(bars, anchors, result.audit):
            index = observation["bar_index"]
            date = day(bars[index].timestamp.isoformat())
            events.append(dict(
                observation, id=(f"rule-abc-{observation['source_path']}-"
                                 f"{observation['a_high_index']}-{observation['b_low_index']}-"
                                 f"{observation['attack']}-{observation['event']}"),
                time=date, available_at=date,
                price=bars[index].close,
                levels=[dict(name=title, price=observation[key]) for key, title in (
                    ("a_origin_price", "a 起点"), ("a_high_price", "a 高点 / c 突破参考"),
                    ("b_low_price", "b 低点 / c 候选失效参考"),
                    ("two_thirds_price", "a 的 2/3 回撤价"), ("half_price", "a 的 1/2 回撤价"),
                )],
            ))
        return dict(
            asof=asof,
            points=points,
            polyline_segments=segments,
            **geometry,
            shapes=shapes[-5:],
            events=events,
            counts=result.counts,
            interrupted=i in blocked,
            computed_from="current_engine_on_selected_prefix",
            engine_sha256=fingerprint(_strategy_source_path()),
            strategy_pivot_mode=config.pivot_mode,
            note=(
                "新版使用讲义递推器的收盘确认转折，过滤同日 N 和非真实高低点；显示连接线本身不作为交易证据。"
                if config.pivot_mode == "lecture_causal"
                else "折线保留各历史结构段的已确认点；旧严格内外包未解处断开，代理版使用独立分形规则。"
            ),
        )

    def health(self, rid):
        row = self._run(rid)
        report = self._json(rid, "report.json")
        store = EventStore(self.root / "operations.sqlite", readonly=True)
        try:
            alerts = list(alert_states(store).values())
        finally:
            store.close()
        return dict(
            run_id=rid,
            status=row["state"]["status"],
            readiness=report["readiness"],
            stages=row["state"]["stages"],
            alerts=alerts,
            data=report["data"],
            recovery_drill_passed=report["recovery_drill_passed"],
            production_ready=False,
            live_orders_enabled=False,
            integrity="sealed_artifacts_verified_on_start_and_first_read",
        )
