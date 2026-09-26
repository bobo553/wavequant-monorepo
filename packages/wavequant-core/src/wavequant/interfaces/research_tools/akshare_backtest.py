"""Single-source online research with honest unavailable-minute results."""

from dataclasses import replace
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from wavequant.domain.models.config import StrategyConfig
from wavequant.domain.models.a_share_security import a_share_security_spec
from wavequant.domain.strategies.integrated_strategy import SystemStrategy, SystemResult, generate_system_signals
from wavequant.domain.models.model import Bar
from wavequant.interfaces.charts.akshare_browser import AkShareBrowser
from wavequant.infrastructure.market_data.akshare_history import AkShareMinuteSource, MinuteCoverageError, sina_factors
from wavequant.infrastructure.market_data.data import opening_permissions
from wavequant.infrastructure.persistence.artifact_cache import ArtifactCache
from wavequant.application.analytics.trade_evidence import result_markers
from .stock_backtest import single_stock_result
from .tdx_backtest import TdxBacktester, decode_research, encode_research


class AkShareBacktester:
    def __init__(self, browser: AkShareBrowser, cache: str | Path):
        self.browser = browser
        self.artifacts = ArtifactCache(cache)  # type: ignore[no-untyped-call]  # Legacy cache boundary.
        self.engine = TdxBacktester._engine_hashes()  # Match the loaded strategy code.
        self.artifacts.clear_backtests_for_engine(self.engine)  # type: ignore[no-untyped-call]  # Legacy cache boundary.

    def _verify_engine(self) -> None:
        if TdxBacktester._engine_hashes() != self.engine:  # Shared package fingerprint.
            raise ValueError('策略代码已变更，请重启图表服务后再运行，避免新版本标识对应旧引擎')

    def run(
        self, symbol: str, start: str, asof: str, strategy: dict[str, Any], execution: dict[str, Any],
        *, progress: Callable[[int, str], None] | None = None,
    ) -> tuple[list[Bar], SystemResult, dict[str, Any]]:
        if date.fromisoformat(start) > date.fromisoformat(asof):
            raise ValueError("回测起始日期不能晚于结束日期")
        self._verify_engine()
        if not self.browser.pinned_history:
            raise ValueError("回测必须固定 AKShare 的同一上游")
        if progress is not None: progress(5, '读取行情')
        raw, _ = self.browser.bars(symbol, asof)
        factors = sina_factors(self.browser.provider, symbol)
        if progress is not None: progress(20, '整理日线')
        # Skip the first twenty listed sessions as in the local execution model.
        if len(raw) < 22:
            raise ValueError("历史日线不足 22 个交易日")
        first = max(start, raw[20].timestamp.date().isoformat())
        bars: list[Bar] = []
        cursor = 0
        current: float | None = None
        base: float | None = None
        previous: float | None = None
        for bar in raw:
            day = bar.timestamp.date().isoformat()
            old_factor = current
            while cursor < len(factors) and factors[cursor][0] <= day:
                current = factors[cursor][1]
                cursor += 1
            if current is None:
                raise ValueError("AKShare / 新浪缺少历史复权起始因子")
            if day < first:
                previous = bar.close
                continue
            if base is None:
                base = current
            factor = current / base
            reference = previous * (old_factor or current) / current if previous is not None else bar.open
            buyable, sellable = opening_permissions(
                dict(date=day, isST="0", tradestatus="1", preclose=str(reference), open=str(bar.open)), symbol
            )
            close_buyable, _ = opening_permissions(
                dict(date=day, isST="0", tradestatus="1" if bar.volume > 0 else "0",
                     preclose=str(reference), open=str(bar.close)), symbol
            )
            bars.append(
                replace(
                    bar,
                    **{key: getattr(bar, key) * factor for key in ("open", "high", "low", "close")},
                    buyable=bool(buyable),
                    close_buyable=bool(close_buyable),
                    nonflat_close_buyable=bool(bar.volume > 0 and bar.high > bar.low and opening_permissions(
                        dict(date=day, isST="0", tradestatus="1", preclose=str(reference), open=str(bar.low)), symbol)[0]),
                    sellable=bool(sellable),
                    adjustment_factor=factor,
                )
            )
            previous = bar.close
        if not bars:
            raise ValueError("所选区间没有同源日线")
        if progress is not None: progress(30, '生成信号')
        config = SystemStrategy(**strategy)
        config.validate()  # type: ignore[no-untyped-call]  # Legacy strategy boundary.
        execution = TdxBacktester._execution_for_security(  # type: ignore[no-untyped-call]  # Shared execution rules.
            execution, a_share_security_spec(symbol, date.fromisoformat(asof))
        )
        minute = AkShareMinuteSource(self.browser.provider, self.artifacts, symbol)
        source: dict[str, Any] = dict(
            provider="akshare",
            upstream="sina",
            daily_endpoint="stock_zh_a_daily",
            factor_endpoint="stock_zh_a_daily:hfq-factor",
            provider_version=self.browser.provider.version,
            price_basis="causal_adjusted_equivalent",
            volume_unit="shares",
        )
        payload = [
            [bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume, bar.adjustment_factor]
            for bar in bars
        ]
        source["daily_sha256"] = hashlib.sha256(json.dumps(payload).encode()).hexdigest()
        source["engine"] = self.engine
        inputs = dict(
            symbol=symbol, start=start, asof=asof, source=dict(source), strategy=strategy, execution=execution
        )
        identity = self.artifacts.key("akshare-backtest", inputs)  # type: ignore[no-untyped-call]  # Content address.
        signal_inputs = {name: value for name, value in inputs.items() if name != "execution"}
        with self.artifacts.lock(identity):  # type: ignore[no-untyped-call]  # Per-input single flight.
            cached = self.artifacts.get("akshare-backtest", inputs)  # type: ignore[no-untyped-call]  # Legacy cache boundary.
            signal_key = self.artifacts.key("akshare-signals", signal_inputs)  # type: ignore[no-untyped-call]
            with self.artifacts.lock(signal_key):  # type: ignore[no-untyped-call]  # Share work across sizing plans.
                research = self.artifacts.get("akshare-signals", signal_inputs)  # type: ignore[no-untyped-call]
                if research is None:
                    generated = (generate_system_signals(bars, config)
                                 if progress is None else generate_system_signals(
                                     bars, config,
                                     progress=lambda percent: progress(35+percent*25//100, '生成信号')))
                    research = encode_research(bars, generated)
                    self._verify_engine()
                    self.artifacts.put("akshare-signals", signal_inputs, research)  # type: ignore[no-untyped-call]
                else:
                    bars, generated = decode_research(research)
                    if progress is not None: progress(60, '读取信号缓存')
            if cached is not None:
                self._verify_engine()
                if progress is not None: progress(95, '读取缓存')
                return bars, generated, cached["view"]
            coverage = None
            try:
                if progress is not None: progress(65, '模拟成交')
                result = single_stock_result(  # type: ignore[no-untyped-call]  # Existing simulation boundary.
                    bars,
                    strategy,
                    execution,
                    generated,
                    minute_loader=minute.get if execution["staged_exit_intraday"] or execution.get("consolidation_entry_intraday") else None,
                    **({'progress': lambda percent: progress(65+percent*25//100, '模拟成交')}
                       if progress is not None else {}),
                )
            except MinuteCoverageError as exc:
                # Discard partial simulation, but preserve the valid daily theory.
                coverage = dict(exc.coverage, message=str(exc))
                result = dict(
                    metrics=None,
                    equity=[],
                    orders=[],
                    trades=[],
                    signals=[],
                    audit=generated.audit,
                    backtest=dict(
                        start=first,
                        end=asof,
                        execution=execution,
                        strategy=strategy,
                        initial_capital=StrategyConfig(**execution).initial_capital,
                        counts=generated.counts,
                    ),
                )
            source["minute"] = minute.provenance()
            if progress is not None: progress(90, '整理结果')
            from wavequant.interfaces.charts.visualization import metrics_at

            _, curve = metrics_at(result["equity"], result["trades"], result["backtest"]["initial_capital"])  # type: ignore[no-untyped-call]  # Shared chart normalization.
            result["backtest"].update(
                status="data_unavailable" if coverage else "complete",
                coverage=coverage,
                source=source,
                requested_start=start,
                price_basis="causal_adjusted_equivalent",
                limitations=[
                    "日线、五分钟线和复权因子统一为 AKShare / 新浪；不跨源补齐。",
                    "周、月等较大周期由同源日线聚合；启用日线回退时，缺少当日分钟按当日日线收盘撮合，并记录回退日期。",
                    "按普通非 ST 股票研究假设执行，跳过最早 20 个交易日；无完整历史 ST 状态。",
                    "模拟账户采用复权等价份额；并非券商成交回报。",
                ],
            )
            view = dict(
                run_id="akshare-" + identity[:24],
                symbol=symbol,
                asof=bars[-1].timestamp.date().isoformat(),
                result_scope="akshare" if coverage else "stock",
                data_source="akshare",
                resolved_source="akshare",
                upstream="sina",
                provider_version=self.browser.provider.version,
                price_basis="causal_adjusted_equivalent",
                sessions=[bar.timestamp.date().isoformat() for bar in bars],
                bars=[
                    dict(
                        time=bar.timestamp.date().isoformat(),
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        factor=bar.adjustment_factor,
                        raw_close=bar.close / bar.adjustment_factor,
                    )
                    for bar in bars
                ],
                markers=[] if coverage else result_markers(result),  # type: ignore[no-untyped-call]  # Existing chart adapter.
                signals=result["signals"],
                orders=result["orders"],
                trades=result["trades"],
                audit=result["audit"],
                metrics=result["metrics"],
                curve=curve,
                backtest=result["backtest"],
                evidence=coverage["message"] if coverage else "AKShare / 新浪同源独立回测；已有模拟结果不代表策略有效",
            )
            if coverage is None:
                self._verify_engine()
                self.artifacts.put(  # type: ignore[no-untyped-call]  # Disposable; valid result survives cache failure.
                    "akshare-backtest", inputs, dict(view=view)
                )
            else:
                self._verify_engine()
            if progress is not None: progress(95, '整理结果')
            return bars, generated, view
