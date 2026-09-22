import { reasonText } from "./annotations.js";
import { retryBacktest } from "./backtest-retry.js";
import { parseBacktestSizing } from "./backtest-sizing.js";
import {
    blockedNodeMeta,
    blockedTradeNodes,
    formatBlockedTradeCopy,
    groupBlockedTradeNodes,
} from "./blocked-trade-nodes.js";
import { BuyPoints } from "./buy-points.js";
import { candleCopyText, previousCandleClose } from "./candle-details.js";
import { loadMarketTimeframeSnapshot, loadStockCatalog } from "./catalog-cache.js";
import { PerformanceCharts, PriceChart } from "./charts.js";
import { formatFilledTradeCopy } from "./filled-trade-copy.js";
import { label, names, num, pct, symbolName } from "./labels.js";
import { RatioComparison, ratioPlans } from "./ratio-comparison.js";
import { parseResearchLink, resolveResearchLink } from "./research-link.js";
import { StockList } from "./stock-list.js";
import { StructureSignals } from "./structure-signals.js";
import { closedPositionLabel, openPositionForMarker, openPositionProfit, positionProfit } from "./trade-position.js";
import { appendTradeEvidence } from "./trade-review.js";
import { TradingViewWidget } from "./tradingview-widget.js";
import { Watchlists } from "./watchlists.js";

const $ = (id) => document.getElementById(id);
const currentBacktestSizing = () => parseBacktestSizing($("backtest-capital").value, $("backtest-buy-ratio").value);
const chartPreferenceKey = "wavequant.research.chart.v1";
const timeframes = {
    "1d": { label: "日线", tag: "日 K" },
    "1w": { label: "周线", tag: "周 K" },
    "1mo": { label: "月线", tag: "月 K" },
    "3mo": { label: "季线", tag: "季 K" },
    "1y": { label: "年线", tag: "年 K" },
};
let chartPreferences = {};
try {
    const saved = JSON.parse(localStorage.getItem(chartPreferenceKey) || "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) chartPreferences = saved;
} catch {
    // 损坏或不可用的本地偏好不应阻断研究工作台启动。
}
if (typeof chartPreferences.showTrendPrices === "boolean")
    $("show-trend-prices").checked = chartPreferences.showTrendPrices;
if (typeof chartPreferences.showTertiaryRetracement === "boolean")
    $("show-tertiary-retracement").checked = chartPreferences.showTertiaryRetracement;
const timeframeTabs = [...$("timeframe-select").querySelectorAll("[role=tab][data-timeframe]")];
const selectedTimeframe = () => $("timeframe-select").dataset.value || "1d";
function setTimeframe(timeframe, { focus = false } = {}) {
    const value = Object.hasOwn(timeframes, timeframe) ? timeframe : "1d";
    $("timeframe-select").dataset.value = value;
    timeframeTabs.forEach((tab) => {
        const selected = tab.dataset.timeframe === value;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
        if (selected && focus) tab.focus();
    });
    $("timeframe-tag").textContent = timeframes[value].tag;
}
function setTimeframeDisabled(disabled) {
    timeframeTabs.forEach((tab) => (tab.disabled = disabled));
    $("timeframe-select").title = disabled ? "封存样本与策略回测保持日线口径" : "";
}
setTimeframe(Object.hasOwn(timeframes, chartPreferences.timeframe) ? chartPreferences.timeframe : "1d");

const chartLayerToggles = [...document.querySelectorAll("[data-chart-layer-toggle]")];
function updateLayerToggleCount() {
    const active = chartLayerToggles.filter((control) => control.checked).length;
    $("layer-toggle-count").textContent = `${active}/${chartLayerToggles.length}`;
}
chartLayerToggles.forEach((control) => control.addEventListener("change", updateLayerToggleCount));
updateLayerToggleCount();

const chartPopoverControllers = [];
function createChartPopoverController(triggerId, panelId) {
    const trigger = $(triggerId);
    const panel = $(panelId);
    let pinned = false;
    let hideTimer = 0;
    let restoringFocus = false;

    const isOpen = () => panel.matches(":popover-open");
    const position = () => {
        if (!isOpen()) return;
        const triggerBox = trigger.getBoundingClientRect();
        const panelBox = panel.getBoundingClientRect();
        const left = Math.max(12, Math.min(triggerBox.right - panelBox.width, window.innerWidth - panelBox.width - 12));
        const top = Math.max(12, Math.min(triggerBox.bottom + 8, window.innerHeight - panelBox.height - 12));
        panel.style.left = `${left}px`;
        panel.style.top = `${top}px`;
    };
    const close = (restoreFocus = false) => {
        window.clearTimeout(hideTimer);
        pinned = false;
        if (isOpen()) panel.hidePopover();
        trigger.setAttribute("aria-expanded", "false");
        if (restoreFocus) {
            restoringFocus = true;
            trigger.focus();
            restoringFocus = false;
        }
    };
    const open = (pin = false) => {
        window.clearTimeout(hideTimer);
        chartPopoverControllers.forEach((controller) => {
            if (controller.panel !== panel) controller.close();
        });
        pinned = pinned || pin;
        if (!isOpen()) panel.showPopover();
        trigger.setAttribute("aria-expanded", "true");
        requestAnimationFrame(position);
    };
    const scheduleClose = () => {
        window.clearTimeout(hideTimer);
        hideTimer = window.setTimeout(() => {
            const keepsFocus = trigger.matches(":focus") || panel.contains(document.activeElement);
            if (!pinned && !keepsFocus) close();
        }, 140);
    };

    trigger.addEventListener("mouseenter", () => open());
    trigger.addEventListener("mouseleave", scheduleClose);
    trigger.addEventListener("focus", () => {
        if (!restoringFocus) open();
    });
    trigger.addEventListener("blur", scheduleClose);
    trigger.addEventListener("click", () => {
        if (pinned && isOpen()) close();
        else open(true);
    });
    panel.addEventListener("mouseenter", () => window.clearTimeout(hideTimer));
    panel.addEventListener("mouseleave", scheduleClose);
    panel.addEventListener("focusin", () => window.clearTimeout(hideTimer));
    panel.addEventListener("focusout", scheduleClose);
    panel.addEventListener("toggle", () => {
        if (!isOpen()) {
            pinned = false;
            trigger.setAttribute("aria-expanded", "false");
        }
    });

    const controller = { close, isOpen, panel, position, trigger };
    chartPopoverControllers.push(controller);
    return controller;
}

createChartPopoverController("chart-layers-trigger", "chart-layers-popover");
createChartPopoverController("chart-guide-trigger", "chart-guide-popover");
document.addEventListener("pointerdown", (event) => {
    chartPopoverControllers.forEach((controller) => {
        if (!controller.trigger.contains(event.target) && !controller.panel.contains(event.target)) controller.close();
    });
});
document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const active = chartPopoverControllers.find((controller) => controller.isOpen());
    if (active) {
        event.preventDefault();
        active.close(true);
    }
});
window.addEventListener("resize", () => chartPopoverControllers.forEach((controller) => controller.position()));
document.addEventListener("scroll", () => chartPopoverControllers.forEach((controller) => controller.position()), true);

const state = {
    catalog: null,
    view: null,
    theory: null,
    page: "workspace",
    sequence: 0,
    loading: false,
    error: false,
    controller: null,
    pendingFocus: null,
    pendingStructureAnnotation: null,
    tradeNodeFilter: "all",
    tradeNodeView: "filled",
};
state.tdxSessions = {};
state.akshareSessions = {};
const isTdx = () => $("result-scope").value === "tdx";
const isAkShare = () => ["akshare", "akshare-backtest"].includes($("result-scope").value);
const isTdxBacktest = () => ["tdx-backtest", "akshare-backtest"].includes($("result-scope").value);
const isLocal = () => isTdx() || $("result-scope").value === "tdx-backtest";
const isMarketBrowse = () => isTdx() || $("result-scope").value === "akshare";
const activeTimeframe = () => (isMarketBrowse() ? selectedTimeframe() : "1d");
const sessionCacheKey = (symbol) => `${symbol}:${activeTimeframe()}`;
function universe() {
    return isAkShare() ? state.akshare?.stocks || [] : isLocal() ? state.tdx?.stocks || [] : currentRun().symbols;
}
const titles = { workspace: "K 线复盘", performance: "策略绩效", orders: "订单与信号", health: "系统状态" };
const requestedPage = new URLSearchParams(window.location.search).get("page");
function cell(text, cls = "") {
    const td = document.createElement("td");
    td.textContent = text;
    td.className = cls;
    return td;
}
function row(values) {
    const tr = document.createElement("tr");
    values.forEach((v) => tr.append(typeof v === "object" ? v : cell(v)));
    return tr;
}
function actionCell(text, fn) {
    const td = document.createElement("td"),
        button = document.createElement("button");
    button.textContent = text;
    button.addEventListener("click", fn);
    td.append(button);
    return td;
}
function option(select, value, text) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = text;
    select.append(opt);
}
function select() {
    return {
        run: $("run-select").value,
        variant:
            $("variant-select").value === "lecture_v3" && $("second-pullback-select").value === "half"
                ? "lecture_v3_c50"
                : $("variant-select").value,
        scenario: $("scenario-select").value,
        symbol: $("symbol-select").value,
        asof: state.noSessionBefore || sessions()[Number($("replay-slider").value)],
        timeframe: activeTimeframe(),
    };
}
function currentRun() {
    return state.catalog.runs.find((r) => r.id === $("run-select").value);
}
function sessions() {
    const symbol = $("symbol-select").value;
    return isAkShare()
        ? state.akshareSessions[sessionCacheKey(symbol)] ||
              [universe().find((s) => s.symbol === symbol)?.last].filter(Boolean)
        : isLocal()
          ? state.tdxSessions[sessionCacheKey(symbol)] ||
            [universe().find((s) => s.symbol === symbol)?.last].filter(Boolean)
          : currentRun()?.symbols.find((s) => s.symbol === symbol)?.sessions || [];
}
async function api(path, params = {}, signal, method = "GET") {
    const timeoutMs = ["/api/tdx-backtest", "/api/akshare-backtest"].includes(path)
        ? 300000
        : path === "/api/stock-summary"
          ? 180000
          : 45000;
    const timeout = AbortSignal.timeout(timeoutMs);
    try {
        const response = await fetch(path + (method === "GET" ? "?" + new URLSearchParams(params) : ""), {
            method,
            ...(method === "POST"
                ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) }
                : {}),
            signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        });
        const text = await response.text();
        let body = {};
        if (text) {
            try {
                body = JSON.parse(text);
            } catch {
                if (!response.ok) {
                    throw new Error(`服务暂时不可用（HTTP ${response.status} ${response.statusText || "错误"}）`);
                }
                throw new Error("服务返回格式异常，请稍后重试");
            }
        }
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
    } catch (e) {
        if (e.name === "TimeoutError") {
            throw new Error(
                ["/api/tdx-backtest", "/api/akshare-backtest"].includes(path)
                    ? "首次回测计算超时，后台可能仍在生成缓存，请稍后重试"
                    : "请求超时，请稍后刷新重试",
            );
        }
        throw e;
    }
}
function showPage(page) {
    state.page = page;
    $("page-title").textContent = titles[page];
    document.querySelectorAll(".page").forEach((el) => (el.hidden = state.error || el.id !== `page-${page}`));
    $("stock-results").hidden = state.error || page !== "workspace" || $("result-scope").value !== "stock";
    document.querySelectorAll("[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    if (page === "performance") requestAnimationFrame(() => performance.resize());
    if (page === "health") loadHealth();
}
let currentCandle = null;
let candleCopyFeedbackTimer;
function describeBar(b) {
    if (!b) return;
    if (currentCandle !== b) {
        window.clearTimeout(candleCopyFeedbackTimer);
        $("copy-candle").textContent = "复制 K 线";
        $("copy-candle").removeAttribute("data-copy-state");
        $("candle-copy-feedback").textContent = "";
    }
    currentCandle = b;
    $("ohlc-text").textContent =
        `${b.time}　开 ${num(b.open)}　高 ${num(b.high)}　低 ${num(b.low)}　收 ${num(b.close)}　量 ${num(b.volume, 0)} 股　原始收盘 ${num(b.raw_close)} 元`;
    $("copy-candle").disabled = false;
    $("copy-candle").setAttribute("aria-label", `复制 ${b.time} K 线数据`);
}
const chart = new PriceChart(
    $("price-chart"),
    describeBar,
    showAnnotationDetails,
    renderVisibleAnnotations,
    (bar, button) => copyHoveredCandle(bar, button),
);
const tradingViewWidget = new TradingViewWidget({
    container: $("tradingview-widget"),
    status: $("tradingview-status"),
    symbolLabel: $("tradingview-symbol"),
    syncButton: $("tradingview-sync"),
    retryButton: $("tradingview-retry"),
    openLink: $("tradingview-open"),
});
const chartViewTabs = [$("chart-local-tab"), $("chart-tradingview-tab")];
function setChartView(view, focus = false) {
    const tradingView = view === "tradingview";
    $("tradingview-panel").hidden = !tradingView;
    $("chart-local-tab").setAttribute("aria-selected", String(!tradingView));
    $("chart-tradingview-tab").setAttribute("aria-selected", String(tradingView));
    chartViewTabs.forEach((tab) => (tab.tabIndex = tab.getAttribute("aria-selected") === "true" ? 0 : -1));
    $("price-chart").closest(".chart-card").dataset.chartView = tradingView ? "tradingview" : "local";
    chartPopoverControllers.forEach((controller) => controller.close());
    tradingViewWidget.setActive(tradingView);
    if (focus) (tradingView ? $("chart-tradingview-tab") : $("chart-local-tab")).focus();
}
chartViewTabs.forEach((tab) => {
    tab.addEventListener("click", () => setChartView(tab === $("chart-tradingview-tab") ? "tradingview" : "local"));
    tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        setChartView(
            event.key === "Home"
                ? "local"
                : event.key === "End"
                  ? "tradingview"
                  : tab === $("chart-local-tab")
                    ? "tradingview"
                    : "local",
            true,
        );
    });
});
chart.setTrendPriceLabelsVisible($("show-trend-prices").checked);
const performance = new PerformanceCharts(["equity-chart", "drawdown-chart", "exposure-chart"].map($));
const stockList = new StockList({
    list: $("stock-list"),
    count: $("stock-count"),
    onSelect: (symbol) => chooseSymbol(symbol, true),
});
const watchlists = new Watchlists({
    onSelect: (symbol) => chooseSymbol(symbol, true),
});
const buyPoints = new BuyPoints({
    api,
    getContext: () => {
        const p = select();
        return {
            run: p.run,
            variant: p.variant,
            scenario: p.scenario,
            source: isAkShare() ? "akshare" : isLocal() ? "tdx" : "snapshot",
            ...(isAkShare() ? { symbol: p.symbol } : {}),
            asof: p.asof,
            start: isLocal() || isAkShare() ? $("backtest-start").value : currentRun().start,
        };
    },
    onSelect: async (match, p) => {
        $("result-scope").value = p.source === "akshare" ? "akshare" : p.source === "tdx" ? "tdx-backtest" : "stock";
        setTimeframe("1d");
        fillSymbols();
        $("symbol-select").value = match.symbol;
        preserveCutoff(p.asof);
        state.pendingFocus = {
            time: match.signal_date,
            description: `买点复核 · ${match.regime} · ${match.signal_date}`,
        };
        showPage("workspace");
        await loadView();
        if (state.error || state.view?.symbol !== match.symbol) return;
        if (p.source === "akshare") {
            detail(
                "AkShare 当前股票买点信号",
                `原始不复权在线日线仅用于信号研究；${match.signal_date} · ${match.regime} · 参考 ${num(match.raw_reference_price)} 元 · 相对量 ${num(match.rvol)} · 未模拟成交。`,
            );
            return;
        }
        if (match.run_id !== state.view.run_id) {
            detail("扫描记录已过期", "行情或策略版本已变化；当前图为新结果，请重新扫描后再复核。");
            return;
        }
        const marker = state.view.markers.find(
            (m) => m.kind === "signal" && m.side === "LONG" && m.time === match.signal_date,
        );
        if (marker) {
            $("show-markers").checked = true;
            chart.setAnnotationOptions(annotationOptions());
            chart.selectAnnotation(marker.id);
            chart.flashSelectedAnnotation(marker.id);
            const note = document.createElement("p");
            note.textContent = `买点筛选证据：盘态 ${match.regime}，相对量 ${num(match.rvol)}，回档比例 ${pct(match.retracement)}，收盘参考盈亏比 ${num(match.gross_reward_risk)}。模拟成交尚需执行风控。`;
            $("selection-info").append(note);
            const chain = match.evidence.find((e) => e.event === "long_transition_evidence");
            if (chain) {
                const line = document.createElement("p");
                const d = (i) => state.view.bars[i]?.time || "—";
                line.textContent = chain.buy_point_type
                    ? `${chain.trend_level} 级 · ${chain.priority === 2 ? "第二类（重点）" : "第一类"}：翻多 ${d(chain.flip_index)} → 交替 ${d(chain.alternation_index)}${chain.priority === 2 ? ` → 再破翻多高 ${d(chain.maturity_index)} → 浅回撤 ${d(chain.pullback_index)}` : ""} → 攻击 ${d(chain.attack)}`
                    : `翻多 ${d(chain.flip_index)} → 交替 ${d(chain.alternation_index)} → 多头确认 ${d(chain.bullish_index)} → 攻击 ${d(chain.attack)}`;
                $("selection-info").append(line);
            }
        }
    },
});
const structureSignals = new StructureSignals({
    api,
    watchlists,
    getUniverse: () => state.akshare?.stocks || [],
    getContext: () => {
        const params = select();
        return {
            run: params.run,
            variant: params.variant,
            source: isAkShare() ? "akshare" : isLocal() ? "tdx" : "snapshot",
            asof: params.asof,
        };
    },
    onSelect: async (match, params) => {
        const structurePresentation = {
            bear_to_bull: {
                label: "空翻多高点",
                checkbox: "show-bear-to-bull-highs",
                prefix: "bear-to-bull-high",
            },
            bear_bull_alternation: {
                label: "空多交替低点",
                checkbox: "show-bear-bull-alternation-lows",
                prefix: "bear-bull-alternation-low",
            },
            bullish_turn: {
                label: "转多信号",
                checkbox: "show-bullish-turn-signals",
                prefix: "bullish-turn-signal",
            },
        }[match.signal_type];
        if (!structurePresentation) return;
        $("result-scope").value = params.source === "akshare" ? "akshare" : params.source === "tdx" ? "tdx" : "stock";
        setTimeframe("1d");
        fillSymbols();
        $("symbol-select").value = match.symbol;
        preserveCutoff(params.asof);
        state.pendingFocus = {
            time: match.event_date,
            description: `${match.trend_level} 级 ${structurePresentation.label} · 发生 ${match.event_date} · ${match.available_at} 确认`,
        };
        state.pendingStructureAnnotation = {
            symbol: match.symbol,
            asof: params.asof,
            checkbox: structurePresentation.checkbox,
            annotationId: `${structurePresentation.prefix}:${match.trend_level}:${match.id}`,
        };
        showPage("workspace");
        await loadView();
    },
});

function activeStockBrowserTab() {
    return $("stock-picker-toggle").getAttribute("aria-expanded") === "true"
        ? "all"
        : $("trade-nodes-tab").getAttribute("aria-pressed") === "true"
          ? "trade"
          : $("buy-points-tab").getAttribute("aria-pressed") === "true"
            ? "buy"
            : $("structure-signals-tab").getAttribute("aria-pressed") === "true"
              ? "structure"
              : "all";
}
function activateStockBrowserTab(tab) {
    if (tab === "trade" && $("trade-nodes-tab").hidden) tab = "all";
    $("stock-picker-panel").hidden = tab !== "all";
    $("stock-picker-toggle").setAttribute("aria-expanded", String(tab === "all"));
    $("trade-nodes-panel").hidden = tab !== "trade";
    $("buy-points-panel").hidden = tab !== "buy";
    $("structure-signals-panel").hidden = tab !== "structure";
    $("selected-stock-summary").hidden = tab === "trade";
    $("trade-nodes-tab").setAttribute("aria-pressed", String(tab === "trade"));
    $("buy-points-tab").setAttribute("aria-pressed", String(tab === "buy"));
    $("structure-signals-tab").setAttribute("aria-pressed", String(tab === "structure"));
}
function activateHeaderStockSearch() {
    showPage("workspace");
    const url = new URL(window.location.href);
    url.searchParams.set("page", "workspace");
    window.history.replaceState(null, "", url);
    window.dispatchEvent(new Event("wavequant:research-page-change"));
    activateStockBrowserTab("all");
}
function focusStockSearch() {
    activateHeaderStockSearch();
    requestAnimationFrame(() => {
        $("header-stock-search").focus();
        $("header-stock-search").select();
    });
}
window.addEventListener("wavequant:focus-stock-search", focusStockSearch);
window.addEventListener("wavequant:activate-stock-search", activateHeaderStockSearch);
window.addEventListener("wavequant:update-stock-search", (event) => {
    activateHeaderStockSearch();
    stockList.setQuery(typeof event.detail?.query === "string" ? event.detail.query : "");
});
window.addEventListener("wavequant:submit-stock-search", () => stockList.selectFirst());
function syncStockBrowserMode(showTrades, preferTrades = false) {
    const previous = activeStockBrowserTab();
    const wasTrades = !$("trade-nodes-tab").hidden;
    $("trade-nodes-tab").hidden = !showTrades;
    activateStockBrowserTab(
        showTrades && (preferTrades || !wasTrades || previous === "all")
            ? "trade"
            : !showTrades && previous === "trade"
              ? "all"
              : previous,
    );
}
$("stock-picker-toggle").addEventListener("click", () => {
    const fallback = $("trade-nodes-tab").hidden ? "buy" : "trade";
    activateStockBrowserTab(activeStockBrowserTab() === "all" ? fallback : "all");
});
for (const [id, tab] of [
    ["trade-nodes-tab", "trade"],
    ["buy-points-tab", "buy"],
    ["structure-signals-tab", "structure"],
])
    $(id).addEventListener("click", () => activateStockBrowserTab(tab));
function fillSymbols() {
    setTimeframeDisabled(!isMarketBrowse());
    if (!isMarketBrowse()) setTimeframe("1d");
    $("timeframe-tag").textContent = timeframes[activeTimeframe()].tag;
    $("partial-timeframe").hidden = true;
    const previous = $("symbol-select").value,
        stocks = universe(),
        available = stocks.filter((s) => s.has_data !== false);
    const catalog = isAkShare() ? state.akshare : isLocal() ? state.tdx : null;
    const cacheLabel =
        catalog?.catalog_cache === "validated"
            ? "IndexedDB 缓存已校验"
            : catalog?.catalog_cache === "updated"
              ? "IndexedDB 缓存已更新"
              : catalog?.catalog_cache === "offline"
                ? "服务器暂不可用，使用 IndexedDB 缓存"
                : "";
    $("symbol-select").value = available.some((s) => s.symbol === previous)
        ? previous
        : available.some((s) => s.symbol === "sh.600519")
          ? "sh.600519"
          : available[0]?.symbol || "";
    syncSymbolCopy();
    tradingViewWidget.setLocalSymbol($("symbol-select").value);
    resetSlider();
    stockList.setStocks(stocks, $("symbol-select").value);
    watchlists.setUniverse(stocks, $("symbol-select").value);
    $("stock-source-notice").textContent = isAkShare()
        ? `${state.akshare.with_daily} 只在线目录 · 沪深北 A 股 · 买点与结构仅读服务器预计算结果${cacheLabel ? ` · ${cacheLabel}` : ""}`
        : isLocal()
          ? `${state.tdx.with_daily} 只有本地日线 · 沪深北 A 股 · 只读${cacheLabel ? ` · ${cacheLabel}` : ""}`
          : "当前封存样本 · 非全市场";
    for (const id of ["buy-points-tab", "structure-signals-tab"]) $(id).disabled = false;
    $("scan-start").textContent = isAkShare() ? "查询当前股票买点" : "查询买点结果";
    $("structure-scan-start").textContent = isAkShare() ? "查询全市场结构" : "读取预计算结果";
    if (isAkShare()) {
        $("scan-status").textContent = "使用当前股票的 AkShare 原始不复权日线计算策略信号，不模拟成交。";
        $("structure-scan-status").textContent =
            "读取服务器提前计算的 AkShare 覆盖股票列表；切换当前股票不会改变筛选范围。";
    }
}
let symbolCopyFeedbackTimer;
function syncSymbolCopy() {
    const symbol = $("symbol-select").value;
    const text = symbol ? symbolName(symbol) : "";
    $("symbol-copy-text").textContent = text || "暂无股票";
    $("stock-picker-current").textContent = text || "暂无股票";
    $("symbol-copy").disabled = !text;
    $("symbol-copy").setAttribute("aria-label", text ? `复制 ${text}` : "暂无可复制股票");
    $("symbol-copy").title = text ? `点击复制 ${text}` : "暂无可复制股票";
    $("symbol-copy").querySelector(".symbol-copy-icon").textContent = "⧉";
    $("symbol-copy").removeAttribute("data-copy-state");
    $("symbol-copy-feedback").textContent = "";
    window.clearTimeout(symbolCopyFeedbackTimer);
}
function fallbackCopyText(text, focusTarget) {
    const field = document.createElement("textarea");
    field.value = text;
    field.style.position = "fixed";
    field.style.opacity = "0";
    document.body.append(field);
    field.select();
    try {
        return document.execCommand("copy");
    } catch {
        return false;
    } finally {
        field.remove();
        focusTarget.focus();
    }
}
async function writeClipboardText(text, focusTarget) {
    let copied = false;
    try {
        if (navigator.clipboard?.writeText) {
            await navigator.clipboard.writeText(text);
            copied = true;
        }
    } catch {
        // 浏览器剪贴板权限不可用时继续尝试兼容复制。
    }
    if (!copied) copied = fallbackCopyText(text, focusTarget);
    return copied;
}
function showCandleCopyFeedback(bar, button, copied) {
    const topButton = $("copy-candle");
    for (const target of new Set([topButton, button])) {
        target.textContent = copied ? "已复制" : "复制失败";
        target.dataset.copyState = copied ? "success" : "error";
    }
    $("candle-copy-feedback").textContent = copied ? `已复制 ${bar.time} K 线数据` : "复制失败，请检查剪贴板权限";
    window.clearTimeout(candleCopyFeedbackTimer);
    candleCopyFeedbackTimer = window.setTimeout(() => {
        if (bar !== currentCandle) return;
        topButton.textContent = "复制 K 线";
        topButton.removeAttribute("data-copy-state");
        if (button !== topButton && button.isConnected) {
            button.textContent = button.dataset.copyLabel || "复制";
            button.removeAttribute("data-copy-state");
        }
        $("candle-copy-feedback").textContent = "";
    }, 2500);
}
function currentCandleCopyText(bar) {
    const symbol = state.view?.symbol;
    return candleCopyText(
        bar,
        symbol ? `${symbolName(symbol)}（${symbol}）` : "",
        previousCandleClose(state.view?.bars, bar),
    );
}
async function copyHoveredCandle(bar, button = $("copy-candle")) {
    if (!bar) return;
    const copied = await writeClipboardText(currentCandleCopyText(bar), button);
    if (bar === currentCandle) showCandleCopyFeedback(bar, button, copied);
}
$("copy-candle").addEventListener("click", () => copyHoveredCandle(currentCandle));
document.addEventListener("copy", (event) => {
    // 仅接管悬停 K 线时的原生复制；输入框、选中文字和其他页面仍使用浏览器默认行为。
    if (
        !currentCandle ||
        state.page !== "workspace" ||
        state.loading ||
        state.error ||
        chart.tooltip.hidden ||
        !chart.container.matches(":hover") ||
        !event.clipboardData ||
        (event.target instanceof Element &&
            event.target.closest("input, textarea, select, [contenteditable], [role=textbox]")) ||
        window.getSelection()?.toString()
    )
        return;
    event.clipboardData.setData("text/plain", currentCandleCopyText(currentCandle));
    event.preventDefault();
    showCandleCopyFeedback(currentCandle, $("copy-candle"), true);
});
async function copyCurrentSymbol() {
    const text = $("symbol-copy-text").textContent.trim();
    if (!text || $("symbol-copy").disabled) return;
    const copied = await writeClipboardText(text, $("symbol-copy"));
    if (text !== $("symbol-copy-text").textContent.trim()) return;
    $("symbol-copy").dataset.copyState = copied ? "success" : "error";
    $("symbol-copy").querySelector(".symbol-copy-icon").textContent = copied ? "✓" : "!";
    $("symbol-copy-feedback").textContent = copied ? `已复制：${text}` : "复制失败，请稍后重试";
    window.clearTimeout(symbolCopyFeedbackTimer);
    symbolCopyFeedbackTimer = window.setTimeout(() => {
        $("symbol-copy").removeAttribute("data-copy-state");
        $("symbol-copy").querySelector(".symbol-copy-icon").textContent = "⧉";
        $("symbol-copy-feedback").textContent = "";
    }, 2500);
}
$("symbol-copy").addEventListener("click", copyCurrentSymbol);
function syncProfileScope() {
    const portfolio = $("result-scope").value === "portfolio";
    const research = ["lecture_v1", "lecture_v2", ...ratioPlans.map((p) => p[0])];
    for (const v of research) {
        const opt = $("variant-select").querySelector(`[value="${v}"]`);
        if (opt) opt.disabled = portfolio;
    }
    if (portfolio && research.includes($("variant-select").value)) $("variant-select").value = "strict_full";
    $("second-pullback-field").hidden = $("variant-select").value !== "lecture_v3";
    $("second-pullback-select").disabled = portfolio || $("variant-select").value !== "lecture_v3";
}
function canBacktestSymbol(symbol) {
    return Boolean(
        state.tdx?.with_daily && state.tdx.stocks.some((stock) => stock.symbol === symbol && stock.has_data !== false),
    );
}
function chooseSymbol(symbol, workspace = false, autoBacktest = true) {
    if (!universe().some((s) => s.symbol === symbol && s.has_data !== false)) return;
    const cutoff = state.requestedAsOf || state.view?.asof || currentRun().end;
    $("symbol-select").value = symbol;
    state.pendingFocus = null;
    const canBacktest = canBacktestSymbol(symbol);
    // AkShare 选股保持用户正在浏览的数据口径；回测由明确按钮切到通达信。
    const runBacktest = autoBacktest && canBacktest && !isAkShare();
    if (runBacktest) {
        $("result-scope").value = "tdx-backtest";
        fillSymbols();
    } else {
        syncSymbolCopy();
        watchlists.setSelected(symbol);
    }
    preserveCutoff(cutoff);
    if (workspace) showPage("workspace");
    $("stock-picker-feedback").hidden = !autoBacktest || Boolean(canBacktest);
    $("stock-picker-feedback").textContent =
        !autoBacktest || canBacktest ? "" : `${symbolName(symbol)} 暂无通达信本地日线，保留当前行情查看，未运行回测。`;
    loadView({ preferTrades: runBacktest });
}
function resetSlider() {
    state.noSessionBefore = null;
    const days = sessions();
    $("replay-slider").max = days.length - 1;
    $("replay-slider").value = days.length - 1;
    syncDate();
}
function preserveCutoff(asof) {
    const days = sessions();
    $("replay-slider").max = days.length - 1;
    const i = days.findLastIndex((d) => d <= asof);
    const sessionsLoaded = isAkShare()
        ? state.akshareSessions[sessionCacheKey($("symbol-select").value)]
        : state.tdxSessions[sessionCacheKey($("symbol-select").value)];
    state.noSessionBefore = isMarketBrowse() && !sessionsLoaded ? asof : i < 0 ? asof : null;
    $("replay-slider").value = Math.max(0, i);
    syncDate();
    return i >= 0;
}
function syncDate() {
    const days = sessions(),
        i = Number($("replay-slider").value);
    $("asof-label").textContent = state.noSessionBefore || days[i] || "—";
    $("replay-mode").textContent = state.noSessionBefore ? "尚无行情" : i === days.length - 1 ? "最新截面" : "历史截面";
    $("previous").disabled = i === 0;
    $("next").disabled = i === days.length - 1;
}
function setMetric(id, value, type) {
    const el = $(id);
    el.textContent = type === "count" ? num(value, 0) : pct(value);
    el.classList.remove("positive", "negative");
    if (type === "return" && value !== 0) el.classList.add(value > 0 ? "positive" : "negative");
}
function renderMetrics() {
    if (state.view.backtest?.status === "data_unavailable") {
        document.querySelector(".metric-grid").hidden = true;
        $("evidence").textContent = state.view.evidence;
        $("backtest-details").hidden = false;
        $("backtest-details").textContent = state.view.evidence;
        $("curve-scope-label").textContent = "分钟历史不足，未生成回测净值";
        $("orders-scope-label").textContent = "未执行完整回测，无成交账本";
        return;
    }
    if (["tdx", "akshare"].includes(state.view.result_scope)) {
        document.querySelector(".metric-grid").hidden = true;
        $("evidence").textContent = state.view.evidence;
        $("backtest-details").hidden = false;
        $("backtest-details").textContent =
            `${state.view.result_scope === "akshare" ? "当前为 AkShare 在线行情浏览" : "当前为通达信全股票行情浏览"}，未运行个股回测。要查看已有回测，请切换“个股独立回测 · 封存样本”。`;
        $("curve-scope-label").textContent = "当前仅行情，无回测净值";
        $("orders-scope-label").textContent = "当前仅行情，无委托";
        return;
    }
    const m = state.view.metrics;
    setMetric("metric-return", m.total_return, "return");
    setMetric("metric-dd", m.max_drawdown);
    setMetric("metric-trades", m.trades, "count");
    setMetric("metric-exposure", m.average_exposure);
    $("evidence").textContent =
        `${label(state.catalog.variants[state.view.variant])} · ${state.view.evidence}。以下仅展示 ${state.view.asof} 收盘前的已知数据，行情口径为因果复权等价价格。`;
    const stock = state.view.result_scope === "stock",
        bt = state.view.backtest;
    if (stock) {
        const unrealized = Number.isFinite(m.unrealized_pnl)
            ? m.unrealized_pnl
            : (bt.open_positions || []).reduce((sum, position) => sum + (position.unrealized_pnl || 0), 0);
        const total = Number.isFinite(m.total_pnl) ? m.total_pnl : m.final_equity - bt.initial_capital;
        const realized = Number.isFinite(m.realized_pnl) ? m.realized_pnl : total - unrealized;
        $("metric-return-note").textContent =
            `已实现 ${num(realized)} 元 + 未实现 ${num(unrealized)} 元 = 期末盈亏 ${num(total)} 元；收益率按期末净值计算，未卖出持仓只计入一次。`;
    } else {
        $("metric-return-note").textContent = "资金曲线含费用、未平仓估值";
    }
    $("metric-scope-label").textContent = stock ? "个股净收益" : "组合净收益";
    $("trade-scope-label").textContent = stock ? "当前股票" : "全组合";
    $("curve-scope-label").textContent = stock ? "个股独立净值" : "原封存组合净值";
    $("orders-scope-label").textContent = stock ? "当前股票 / 截至回放日期" : "全组合 / 截至回放日期";
    document.querySelector(".metric-grid").setAttribute("aria-label", stock ? "个股回测指标" : "组合指标");
    $("backtest-details").hidden = !stock;
    if (stock) {
        const netRiskEnabled = bt.execution.net_reward_risk_filter !== false;
        const d = bt.diagnostics,
            reasons = Object.entries(d.rejection_reasons)
                .map(([key, n]) => `${reasonText(key)} × ${n}`)
                .join("；");
        $("backtest-details").textContent =
            `${symbolName(state.view.symbol)} · 独立回测 ${bt.start} — ${bt.end}｜量能过滤${bt.strategy.volume_filter ? `开启（攻击日量比 ≥ ${num(bt.strategy.minimum_rvol)}）` : "关闭"}；成交价含费净盈亏比过滤${netRiskEnabled ? "开启" : "关闭"}；初始资金 ${num(bt.initial_capital, 0)} 元，单股仓位上限 ${pct(bt.execution.max_position_weight)}。年化 ${pct(m.annualized_return)} · 胜率 ${m.win_rate === null ? "—（无平仓）" : pct(m.win_rate)} · Sharpe ${num(m.sharpe)} · 费用 ${num(m.fees)} 元。买入成交 ${d.entry_fills} · 已平仓 ${d.closed_trades} · 未平仓 ${d.open_positions} · 期末未执行信号 ${m.unexecuted_end_signals}。${d.entry_fills ? "成交样本不等于策略有效。" : `未产生成交：入场信号 ${bt.counts.long_signals || 0}，委托尝试 ${d.entry_attempts}；可开启“筛选 / 中断”查看未通过条件。`}${reasons ? `拒单原因：${reasons}。` : ""}${bt.open_positions.map((p) => `未平仓 ${num(p.quantity)} 等价份额，累计已实现 ${num(p.realized_pnl)} 元，剩余浮动盈亏 ${num(p.unrealized_pnl)} 元，整笔当前盈亏 ${num(p.total_pnl)} 元（${pct(p.net_return)}，含未实现部分）。`).join("")}`;
        if (bt.execution.missing_minute_daily_fallback) {
            const p = document.createElement("p");
            const days = bt.minute_fallbacks || [];
            p.textContent = `撮合方式：优先同源五分钟线；缺少当日分钟时按当日日线收盘价模拟成交（含滑点与费用）。本次 ${days.length} 个交易日采用日线判断${days.length ? `：${days.map((item) => item.date).join("、")}` : ""}；是否成交仍由策略和风控决定。`;
            $("backtest-details").append(p);
        }
        if (state.view.strategy_profile?.id === "lecture_v1") {
            const detail = document.createElement("details"),
                summary = document.createElement("summary"),
                body = document.createElement("p");
            summary.textContent = "当前策略：讲义因果版 V1（查看生效规则）";
            body.textContent = `讲义折线收盘确认 → 收盘突破冻结末跌高 → 回档 < 2/3 完成交替 → 高低点抬高确认多头 → 新正 N → 轧空 / 强轧空或守住轧空低后的恢复。${bt.strategy.volume_filter ? "攻击棒相对量 ≥ 1.2、" : "量能过滤已关闭、"}N 回档 < 2/3、最近未达目标收盘盈亏比 ≥ 1.5。${netRiskEnabled ? "成交价含费净盈亏比须 ≥ 1.5。" : "成交价含费净盈亏比过滤已关闭。"}倒 N、末升低收盘跌破、未定义结构、止损 / 目标 / 持仓期限触发退出。仅做多；洗盘是辅助标签；一二三级实线不额外充当三个入场门槛。日线面板未接入次级周期扭转确认。完整参数与规则随回测 JSON 导出。`;
            detail.append(summary, body);
            $("backtest-details").append(detail);
        }
        if (state.view.strategy_profile?.id === "lecture_v2") {
            const detail = document.createElement("details"),
                summary = document.createElement("summary"),
                body = document.createElement("p");
            summary.textContent = "当前策略：分级双买点 V2（第二类优先复核）";
            body.textContent = `第一类仅二级或三级：翻空为多、确认更高回档低点完成空多交替 → 新正 N → 轧空 / 强轧空，不设 1/3 或 2/3 回撤过滤，但不允许跌破结构防守。第二类为交替已知后，后续收盘再次超过冻结的翻多高点 → 新上涨段回撤 < 1/3 → 新正 N → 轧空 / 强轧空。同一级成熟后不回退第一类。${bt.strategy.volume_filter ? "保留量比 ≥ 1.2、" : "量能过滤已关闭；保留"}收盘盈亏比 ≥ 1.5。${netRiskEnabled ? "成交价含费净盈亏比须 ≥ 1.5。" : "成交价含费净盈亏比过滤已关闭。"}只有收盘可知证据可用，B / S 仍为次开盘模拟成交。原画线与旧图形注释不改，V2 买点以成交 / 信号证据为准。`;
            detail.append(summary, body);
            $("backtest-details").append(detail);
        }
        if (bt.strategy.buy_point_definition === "whole_flip_wave_v3") {
            const p = document.createElement("p");
            const firstCounter =
                bt.strategy.first_pullback_basis === "minimum_close" ? "H0 至交替低点期间的最低收盘价" : "交替低点";
            const secondOperator = bt.strategy.mature_shallow_inclusive === false ? "<" : "≤";
            p.textContent = `整段双买点 V3：L0 为翻多上涨起始的整段最低点，H0 为翻多高点。第一类仅二级或三级空多交替后，等待新正 N 的轧空或强轧空，不破 L0。${bt.strategy.first_pullback_threshold == null ? "不附加深回撤门槛。" : `当前对照方案另要求 (H0−${firstCounter})/(H0−L0) > ${pct(bt.strategy.first_pullback_threshold)}。`}第二类交替后收盘再破 H0，取已知阶段最高 H1，再回撤；(H1−回撤期间最低收盘)/(H1−L0) ${secondOperator} ${pct(bt.strategy.mature_shallow_ratio)}，再等正 N 轧空。第一类按已知交替与正 N 证据判定；同一 N 可在轧空当日共同确认交替。第二类优先。放量强反转须收盘突破本次 N 全部先前高点、守住 N 起点，且仍通过全局入场资格。${bt.strategy.volume_filter ? "另启用相对量能过滤；执行风控仍有效。" : "本次关闭相对量能过滤；各轧空路径自身的量价条件及执行风控仍有效。"}`;
            if (bt.execution.consolidation_entry_intraday)
                p.textContent +=
                    " 守住旧正 N 虚拟低点整理后，跳空放量形成新正 N，按已完成五分钟线确认、下一段开盘模拟买入；其他日线买点当日收盘模拟执行。缺少完整分钟时记录日线回退，价格限制仍须通过。";
            $("backtest-details").append(p);
        }
    }
}
async function loadStockSummary(request, sequence) {
    if ($("result-scope").value !== "stock") return;
    $("backtest-summary-status").textContent = "逐股回测中…";
    $("stock-results-body").replaceChildren();
    state.summaryController = new AbortController();
    try {
        const data = await api(
            "/api/stock-summary",
            { run: request.run, variant: request.variant, asof: request.asof, scenario: request.scenario },
            state.summaryController.signal,
        );
        if (sequence !== state.sequence) return;
        for (const r of data.results) {
            const m = r.metrics;
            $("stock-results-body").append(
                row([
                    symbolName(r.symbol),
                    r.asof,
                    m ? pct(m.total_return) : "无历史",
                    m ? pct(m.max_drawdown) : "—",
                    m ? num(m.entry_fills, 0) : "—",
                    m ? num(m.trades, 0) : "—",
                    m?.win_rate == null ? "—" : pct(m.win_rate),
                    m ? num(m.fees) : "—",
                    actionCell("查看", () => chooseSymbol(r.symbol, true)),
                ]),
            );
        }
        $("backtest-summary-status").textContent =
            `${data.results.filter((r) => r.status === "completed").length} 只完成 · ${data.asof}`;
    } catch (e) {
        if (sequence !== state.sequence || e.name === "AbortError") return;
        $("backtest-summary-status").textContent = "汇总失败：" + e.message;
    }
}
function detail(title, value) {
    $("selection-info").replaceChildren();
    const b = document.createElement("b");
    b.textContent = title;
    const p = document.createElement("p");
    p.textContent = value;
    $("selection-info").append(b, p);
}
function annotationOptions() {
    return {
        signals: $("show-markers").checked,
        fills: $("show-fills").checked,
        candidateRejections: $("show-entry-rejections").checked,
        rules: $("show-rules").checked,
        diagnostics: $("show-diagnostics").checked,
        levels: $("show-levels").checked,
        trendKeys: $("show-last-fall-high").checked,
        bullFlipHighs: $("show-bear-to-bull-highs").checked,
        bullAlternationLows: $("show-bear-bull-alternation-lows").checked,
        postAlternationBullHighs: $("show-post-alternation-bull-highs").checked,
        bullishTurnSignals: $("show-bullish-turn-signals").checked,
        tertiaryRetracement: $("show-tertiary-retracement").checked,
        tertiaryAbc: $("show-tertiary-abc").checked,
    };
}
function showAnnotationDetails(items) {
    if (!$("tradingview-panel").hidden) setChartView("local");
    const item = items[0];
    state.selectedAnnotationId = item.id;
    highlightTradeNode(
        item.kind === "fill" || item.kind === "candidate" || (item.kind === "order" && item.status === "cancelled")
            ? item.id
            : null,
    );
    detail(`${item.title} · ${item.time}`, item.description);
    const panel = $("selection-info");
    panel.dataset.annotationId = item.id;
    appendTradeEvidence(panel, item, openPositionForMarker(state.view, item));
    const source = document.createElement("p");
    source.className = "annotation-source";
    source.textContent = `${item.sourceLabel}。${item.sourceTime && item.sourceTime !== item.time ? `原结构日期 ${item.sourceTime}，到 ${item.time} 才可知。` : ""}`;
    panel.append(source);
    if (item.reason) {
        const reason = document.createElement("p");
        reason.textContent = reasonText(item.reason);
        panel.append(reason);
    }
    const price = document.createElement("p");
    price.textContent = `${item.kind === "fill" ? "实际成交价" : item.category === "entry-rejections" ? "当时收盘参考价（未下单）" : item.category === "risk-rejections" ? "拟买价（未成交）" : "当时参考价"}：${num(item.price)}（复权等价）`;
    panel.append(price);
    for (const rejection of item.executionRiskRejections || []) {
        const execution = document.createElement("p");
        execution.textContent = `执行风控 · 拟买价 ${num(rejection.price)} 元（未成交）：${rejection.description}`;
        panel.append(execution);
    }
    for (const level of item.levels) {
        const p = document.createElement("p");
        p.textContent = `${level.name}：${num(level.price)}${level.available_at && level.available_at > item.time ? `（${level.available_at} 起可知）` : ""}`;
        panel.append(p);
    }
    if (item.quantity !== undefined) {
        const p = document.createElement("p");
        p.textContent = `等价份额 ${num(item.quantity)} · 费用 ${num(item.fee)} 元`;
        panel.append(p);
    }
    const note = document.createElement("small");
    note.textContent =
        "点位线从事件可知日开始；原始失效位与测幅不是当前止损指令，也不保证成交。超出价格轴的点位仍在此列出。";
    panel.append(note);
    for (const other of items.slice(1)) {
        const button = document.createElement("button");
        button.textContent = `同日 · ${other.title}`;
        button.addEventListener("click", () => chart.selectAnnotation(other.id, false));
        panel.append(button);
    }
    const raw = document.createElement("details"),
        summary = document.createElement("summary"),
        pre = document.createElement("pre");
    summary.textContent = "原始判定字段";
    pre.textContent = JSON.stringify(item.raw, null, 2);
    raw.append(summary, pre);
    panel.append(raw);
}
function renderVisibleAnnotations(items, range) {
    const t = range.trend;
    $("trend-summary").textContent = t
        ? `一级趋势线 · 视窗：${t.windowTrend} · 最新局部：${t.trend}（至 ${t.latestKnown}）｜视窗末跌高 ${num(t.lastFallHigh?.value)} · 末升低 ${num(t.lastRiseLow?.value)} · 点击一级转折点查看依据`
        : "一级趋势线：当前无已确认波段，或图层已关闭；未确认尾端不计入。";
    const s = range.secondaryTrend;
    const secondaryDeveloping = range.secondaryDeveloping,
        secondaryDevelopingStart = secondaryDeveloping?.points[0],
        secondaryDevelopingEnd = secondaryDeveloping?.points.at(-1),
        secondaryDevelopmentText = secondaryDeveloping
            ? `｜发展路径 ${secondaryDeveloping.points.length} 点：${secondaryDevelopingStart.time} ${secondaryDevelopingStart.label} ${num(secondaryDevelopingStart.value)} → 当前${secondaryDeveloping.wave_direction === "up" ? "上涨" : "下跌"}候选 ${secondaryDevelopingEnd.time} ${secondaryDevelopingEnd.label} ${num(secondaryDevelopingEnd.value)}`
            : "";
    $("secondary-trend-summary").textContent = s
        ? `二级趋势线 · 视窗：${s.windowTrend} · 最新局部：${s.trend}（至 ${s.latestKnown}）｜二级末跌高 ${num(s.lastFallHigh?.value)} · 末升低 ${num(s.lastRiseLow?.value)}${secondaryDevelopmentText} · 点击二级转折点查看一级突破依据`
        : secondaryDeveloping
          ? `二级趋势线 · 发展路径 ${secondaryDeveloping.points.length} 点：${secondaryDevelopingStart.time} ${secondaryDevelopingStart.label} ${num(secondaryDevelopingStart.value)} → 当前${secondaryDeveloping.wave_direction === "up" ? "上涨" : "下跌"}候选 ${secondaryDevelopingEnd.time} ${secondaryDevelopingEnd.label} ${num(secondaryDevelopingEnd.value)}｜紫色虚线点均来自已确认一级结构，不升级为正式二级反转`
          : "二级趋势线：当前无已确认波段，或图层已关闭；等待一级末跌高／末升低被突破。";
    const u = range.tertiaryTrend;
    const developing = range.tertiaryDeveloping,
        developingStart = developing?.points[0],
        developingEnd = developing?.points.at(-1);
    $("tertiary-trend-summary").textContent = u
        ? `三级趋势线 · 视窗：${u.windowTrend} · 最新局部：${u.trend}（至 ${u.latestKnown}）｜三级末跌高 ${num(u.lastFallHigh?.value)} · 末升低 ${num(u.lastRiseLow?.value)} · 点击三级转折点查看二级突破依据`
        : developing
          ? `三级趋势线 · 完整发展路径 ${developing.points.length} 点：${developingStart.time} ${developingStart.label} ${num(developingStart.value)} → 当前${developing.wave_direction === "up" ? "上涨" : "下跌"}候选 ${developingEnd.time} ${developingEnd.label} ${num(developingEnd.value)}｜橙色虚线点均来自已确认二级结构，不升级为正式三级反转`
          : "三级趋势线：当前无已确认波段，或图层已关闭；等待二级末跌高／末升低被突破。";
    $("annotation-count").textContent = `当前图窗 ${range.from} — ${range.to} · ${items.length} 项`;
    $("events").replaceChildren();
    if (!items.length) {
        const p = document.createElement("p");
        p.className = "empty";
        p.textContent = "当前图窗／筛选无标识。可平移图表，或开启“筛选 / 中断”查看未通过规则。";
        $("events").append(p);
        return;
    }
    for (const item of [...items]
        .sort((a, b) => b.time.localeCompare(a.time) || b.priority - a.priority)
        .slice(0, 100)) {
        const button = document.createElement("button");
        button.className = "event-row";
        button.dataset.annotationId = item.id;
        button.dataset.kind = item.kind;
        const time = document.createElement("time");
        time.textContent = `${item.time} · ${item.category === "entry-rejections" ? "候选未通过" : item.category === "risk-rejections" ? "风控拒单" : item.kind === "fill" ? "成交" : item.kind === "signal" ? "信号" : "规则可知"}`;
        const title = document.createElement("strong");
        title.textContent = item.title;
        const price = document.createElement("small");
        price.textContent = `${num(item.price)} · ${reasonText(item.reason) || item.sourceLabel}`;
        button.append(time, title, price);
        button.addEventListener("click", () => chart.selectAnnotation(item.id));
        $("events").append(button);
    }
    if (items.length > 100) {
        const p = document.createElement("p");
        p.className = "empty";
        p.textContent = "列表仅列最近 100 项；放大图表或点击图上标识查看更早事件。";
        $("events").append(p);
    }
}
function highlightTradeNode(markerId) {
    for (const button of $("trade-nodes-panel").querySelectorAll("button[data-trade-marker-id]")) {
        const selected = Boolean(
            markerId &&
            (button.dataset.tradeMarkerId === markerId || button.dataset.tradeMarkerIds?.split(" ").includes(markerId)),
        );
        button.setAttribute("aria-current", String(selected));
        if (
            selected &&
            !$("trade-nodes-panel").hidden &&
            !button.closest(".trade-nodes-view-panel").hidden &&
            !button.closest("details:not([open])")
        )
            button.scrollIntoView({ block: "nearest" });
    }
}
function applyTradeNodeView() {
    for (const view of ["filled", "blocked"]) {
        const active = state.tradeNodeView === view;
        const tab = $(`trade-nodes-${view}-tab`);
        tab.setAttribute("aria-selected", String(active));
        tab.tabIndex = active ? 0 : -1;
        $(`trade-nodes-${view}-panel`).hidden = !active;
    }
    highlightTradeNode(state.selectedAnnotationId);
}
for (const view of ["filled", "blocked"]) {
    const tab = $(`trade-nodes-${view}-tab`);
    tab.addEventListener("click", () => {
        state.tradeNodeView = view;
        applyTradeNodeView();
    });
    tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        state.tradeNodeView =
            event.key === "Home"
                ? "filled"
                : event.key === "End"
                  ? "blocked"
                  : view === "filled"
                    ? "blocked"
                    : "filled";
        applyTradeNodeView();
        $(`trade-nodes-${state.tradeNodeView}-tab`).focus();
    });
}
function applyTradeNodeFilter() {
    const filter = state.tradeNodeFilter;
    for (const button of document.querySelectorAll("[data-trade-node-filter]"))
        button.setAttribute("aria-pressed", String(button.dataset.tradeNodeFilter === filter));
    const nodes = [...$("trade-nodes-list").children];
    for (const node of nodes) node.hidden = filter !== "all" && node.dataset.side !== filter;
    const visible = nodes.some((node) => !node.hidden);
    $("trade-nodes-empty").hidden = visible;
    $("trade-nodes-empty").textContent = nodes.length
        ? `当前没有${filter === "BUY" ? "买入" : "卖出"}成交节点，可切换“全部”查看。`
        : "当前截面没有模拟成交；图上的信号和候选标识不代表已买卖。";
}
let blockedCopyFeedbackTimer;
let lastBlockedCopyButton;
function showBlockedCopyFeedback(button, copied, scope) {
    window.clearTimeout(blockedCopyFeedbackTimer);
    if (lastBlockedCopyButton && lastBlockedCopyButton !== button) {
        lastBlockedCopyButton.textContent = lastBlockedCopyButton.dataset.copyLabel;
        lastBlockedCopyButton.removeAttribute("data-copy-state");
    }
    lastBlockedCopyButton = button;
    button.dataset.copyState = copied ? "success" : "error";
    button.textContent = copied ? "已复制" : "复制失败";
    $("trade-nodes-copy-feedback").textContent = copied
        ? `已复制${scope}的完整拦截信息`
        : "复制失败，请检查浏览器剪贴板权限后重试";
    blockedCopyFeedbackTimer = window.setTimeout(() => {
        button.textContent = button.dataset.copyLabel;
        button.removeAttribute("data-copy-state");
        $("trade-nodes-copy-feedback").textContent = "";
        lastBlockedCopyButton = null;
    }, 2500);
}
async function copyBlockedTradeGroups(groups, button, scope) {
    const view = state.view;
    if (!view || !groups.length) return;
    const text = formatBlockedTradeCopy(view, groups, label(state.catalog.variants[view.variant]));
    const copied = await writeClipboardText(text, button);
    if (state.view === view) showBlockedCopyFeedback(button, copied, scope);
}
function renderBlockedTradeDate(group, view) {
    const primary = group.primary;
    const candidate = primary.blockedStage === "screening";
    const item = document.createElement("li");
    item.className = "trade-node-item";
    item.dataset.date = group.time;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "trade-node-button";
    button.dataset.tradeMarkerId = primary.id;
    button.dataset.tradeMarkerIds = group.nodes.map((node) => node.id).join(" ");
    button.dataset.blockedStage = primary.blockedStage;
    button.setAttribute("aria-current", "false");
    button.addEventListener("click", () => selectBlockedNode(primary));
    const badge = document.createElement("span");
    badge.className = `trade-node-badge ${candidate ? "trade-node-candidate" : "trade-node-blocked"}`;
    badge.textContent = candidate ? "筛" : "拦";
    const content = document.createElement("span");
    content.className = "trade-node-content";
    const heading = document.createElement("span");
    heading.className = "trade-node-heading";
    const date = document.createElement("time");
    date.dateTime = group.time;
    date.textContent = group.time;
    const count = document.createElement("strong");
    count.textContent =
        group.nodes.length > 1
            ? `${group.nodes.length} 次`
            : Number.isFinite(primary.price)
              ? `${candidate ? "参考价" : "拟价"} ${num(primary.price)} 元`
              : "未成交";
    heading.append(date, count);
    content.append(heading);
    const reasons = new Map();
    for (const node of group.nodes) {
        const key = `${node.blockedStage}:${node.reason}`;
        if (!reasons.has(key)) reasons.set(key, { node, count: 0 });
        reasons.get(key).count += 1;
    }
    for (const { node, count: reasonCount } of reasons.values()) {
        const reason = document.createElement("span");
        reason.className = "trade-node-reason";
        reason.textContent = `${node.blockedStage === "screening" ? "筛 · 未下单" : "拦 · 未成交"} · ${reasonText(node.reason)}${reasonCount > 1 ? ` ×${reasonCount}` : ""}`;
        content.append(reason);
    }
    const details = document.createElement("span");
    details.className = "trade-node-details";
    details.textContent =
        group.nodes.length === 1
            ? blockedNodeMeta(primary, view)
            : `优先定位${candidate ? "筛选候选" : "取消委托"} · ${candidate ? "收盘参考价" : "拟价"} ${num(primary.price)} 元 · ${blockedNodeMeta(primary, view)}；展开可逐条复核`;
    content.append(details);
    button.setAttribute(
        "aria-label",
        `定位 ${group.time} 的 ${group.nodes.length} 次拦截：${[...reasons.values()].map(({ node }) => reasonText(node.reason)).join("；")}`,
    );
    button.append(badge, content);
    const row = document.createElement("div");
    row.className = "trade-node-row";
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "trade-node-copy";
    copy.dataset.copyLabel = "复制";
    copy.textContent = "复制";
    copy.title = `复制 ${group.time} 的完整拦截信息`;
    copy.setAttribute("aria-label", copy.title);
    copy.addEventListener("click", () => void copyBlockedTradeGroups([group], copy, `${group.time}`));
    row.append(button, copy);
    item.append(row);
    if (group.nodes.length > 1) {
        const disclosure = document.createElement("details");
        disclosure.className = "trade-node-disclosure";
        const summary = document.createElement("summary");
        summary.textContent = `查看该日 ${group.nodes.length} 条原始记录`;
        disclosure.append(summary);
        for (const node of group.nodes) {
            const detailButton = document.createElement("button");
            detailButton.type = "button";
            detailButton.className = "trade-node-detail-button";
            detailButton.dataset.tradeMarkerId = node.id;
            detailButton.dataset.blockedStage = node.blockedStage;
            detailButton.setAttribute("aria-current", "false");
            detailButton.textContent = `${node.blockedStage === "screening" ? "筛 · 未下单" : "拦 · 未成交"} · ${reasonText(node.reason)} · ${num(node.price)} 元 · ${blockedNodeMeta(node, view)}`;
            detailButton.addEventListener("click", () => selectBlockedNode(node));
            disclosure.append(detailButton);
        }
        item.append(disclosure);
    }
    return item;
}
function entryPositionWeight(marker) {
    if (marker.side !== "BUY") return null;
    if (Number.isFinite(marker.entry_position_weight)) return marker.entry_position_weight;
    // Older saved backtests have the fill inputs but not the derived weight.
    const positionValue = Number.isFinite(marker.entry_position_value)
        ? marker.entry_position_value
        : Number.isFinite(marker.price) && Number.isFinite(marker.quantity)
          ? marker.price * marker.quantity
          : NaN;
    const equity = Number.isFinite(marker.account_equity_after_fill)
        ? marker.account_equity_after_fill
        : Number.isFinite(marker.equity_at_open) && Number.isFinite(marker.fee)
          ? marker.equity_at_open - marker.fee
          : NaN;
    return Number.isFinite(positionValue) && Number.isFinite(equity) && equity > 0 ? positionValue / equity : null;
}
function entryPositionLabel(marker) {
    const weight = entryPositionWeight(marker);
    return weight === null ? "—" : pct(weight);
}
function renderTradeNodes() {
    const view = state.view;
    const fills = view.markers.filter((marker) => marker.kind === "fill");
    const blocked = blockedTradeNodes(view);
    const blockedDates = groupBlockedTradeNodes(blocked);
    const sellIds = new Set(
        fills
            .filter((marker) => marker.side === "SELL" && marker.position_closed !== false)
            .map((marker) => marker.trade_id),
    );
    const closedEntryDates = new Set(
        view.trades.filter((trade) => trade.position_closed !== false).map((trade) => trade.entry_time.slice(0, 10)),
    );
    const tradesByExitTime = new Map(view.trades.map((trade) => [trade.exit_time, trade]));
    $("trade-nodes-context").textContent =
        `${symbolName(view.symbol)} · ${view.backtest.start} 至 ${view.asof} · ${label(state.catalog.variants[view.variant])}`;
    $("trade-nodes-buy-count").textContent = num(view.metrics.entry_fills, 0);
    $("trade-nodes-closed-count").textContent = num(view.metrics.trades, 0);
    $("trade-nodes-open-count").textContent = num(view.metrics.open_positions, 0);
    $("trade-nodes-filled-count").textContent = String(fills.length);
    $("trade-nodes-blocked-count").textContent = String(blockedDates.length);
    $("trade-nodes-blocked-summary").textContent =
        `按交易日汇总：${blockedDates.length} 日 · ${blocked.length} 次拦截。`;
    const copyAll = $("trade-nodes-copy-all");
    copyAll.disabled = blockedDates.length === 0;
    copyAll.textContent = copyAll.dataset.copyLabel;
    copyAll.removeAttribute("data-copy-state");
    $("trade-nodes-copy-feedback").textContent = "";
    window.clearTimeout(blockedCopyFeedbackTimer);
    lastBlockedCopyButton = null;
    $("trade-nodes-filled-copy-feedback").textContent = "";
    $("trade-nodes-list").replaceChildren();
    $("trade-nodes-blocked-list").replaceChildren();
    for (const marker of [...fills].sort((a, b) => b.timestamp.localeCompare(a.timestamp))) {
        const buy = marker.side === "BUY";
        const open = buy && (marker.trade_id ? !sellIds.has(marker.trade_id) : !closedEntryDates.has(marker.time));
        const trade = buy ? null : tradesByExitTime.get(marker.timestamp);
        const item = document.createElement("li");
        item.className = "trade-node-item";
        item.dataset.side = marker.side;
        const button = document.createElement("button");
        button.type = "button";
        button.className = "trade-node-button";
        button.dataset.tradeMarkerId = marker.id;
        button.setAttribute("aria-current", "false");
        button.setAttribute(
            "aria-label",
            `定位 ${marker.time} ${buy ? "买入" : "卖出"}成交，等价价 ${num(marker.price)} 元`,
        );
        button.addEventListener("click", () => selectFill(marker, true));
        const badge = document.createElement("span");
        badge.className = `trade-node-badge ${buy ? "trade-node-buy" : "trade-node-sell"}`;
        badge.textContent = buy ? "B" : "S";
        const content = document.createElement("span");
        content.className = "trade-node-content";
        const heading = document.createElement("span");
        heading.className = "trade-node-heading";
        const date = document.createElement("time");
        date.dateTime = marker.time;
        date.textContent = marker.time;
        const price = document.createElement("strong");
        price.textContent = `${num(marker.price)} 元`;
        heading.append(date, price);
        const reason = document.createElement("span");
        reason.className = "trade-node-reason";
        reason.textContent = `${buy ? "买入" : marker.position_closed === false ? "减仓" : "卖出"} · ${reasonText(marker.reason)}${open ? " · 尚未平仓" : ""}`;
        const details = document.createElement("span");
        details.className = "trade-node-details";
        details.textContent = `决定 ${marker.decision_timestamp || marker.signal_time || "—"}${marker.execution_model === "intraday_5m_next_open" ? ` · 成交 ${marker.timestamp}` : ""} · 原价 ${num(marker.raw_price)} 元 · ${num(marker.quantity)} 等价份额 · 费用 ${num(marker.fee)} 元`;
        content.append(heading, reason, details);
        if (!buy) {
            const proportion = document.createElement("span");
            proportion.className = "trade-node-details";
            proportion.textContent = closedPositionLabel(marker);
            content.append(proportion);
        }
        if (marker.position_closed === false) {
            const remaining = document.createElement("span");
            remaining.className = "trade-node-details";
            remaining.textContent = `剩余 ${num(marker.remaining_quantity)} 等价份额`;
            content.append(remaining);
        }
        if (buy) {
            const position = document.createElement("span");
            position.className = "trade-node-position";
            position.textContent = `买入后仓位 ${entryPositionLabel(marker)}`;
            position.title = "该笔买入成交额（不含费用）÷ 成交后账户权益；不是全回测期间的日均仓位";
            content.append(position);
        }
        const profit = positionProfit(marker, trade, open ? openPositionForMarker(view, marker) : null);
        if (profit) {
            const pnl = document.createElement("span");
            pnl.className = `trade-node-pnl ${profit.pnl >= 0 ? "positive" : "negative"}`;
            pnl.textContent = profit.text;
            content.append(pnl);
        }
        button.append(badge, content);
        const row = document.createElement("div");
        row.className = "trade-node-row";
        const copy = document.createElement("button");
        copy.type = "button";
        copy.className = "trade-node-copy";
        copy.textContent = "复制信息";
        copy.setAttribute("aria-label", `复制 ${marker.time} ${buy ? "买入" : "卖出"}成交信息`);
        copy.addEventListener("click", async () => {
            const text = formatFilledTradeCopy(
                view,
                marker,
                label(state.catalog.variants[view.variant]),
                entryPositionLabel(marker),
                trade,
            );
            const copied = await writeClipboardText(text, copy);
            if (state.view !== view || !copy.isConnected) return;
            copy.textContent = copied ? "已复制" : "重试复制";
            copy.dataset.copyState = copied ? "success" : "error";
            $("trade-nodes-filled-copy-feedback").textContent = copied
                ? `已复制 ${marker.time} ${buy ? "买入" : "卖出"}成交与 K 线信息`
                : "复制失败，请检查剪贴板权限后重试";
        });
        row.append(button, copy);
        item.append(row);
        $("trade-nodes-list").append(item);
    }
    for (const group of blockedDates) $("trade-nodes-blocked-list").append(renderBlockedTradeDate(group, view));
    $("trade-nodes-blocked-empty").hidden = blocked.length > 0;
    $("trade-nodes-blocked-empty").textContent = "本次回测没有被拦截的候选或委托。";
    $("trade-nodes-panel").setAttribute("aria-busy", "false");
    applyTradeNodeView();
    applyTradeNodeFilter();
    highlightTradeNode(state.selectedAnnotationId);
}
$("trade-nodes-copy-all").addEventListener("click", () => {
    if (!state.view) return;
    const groups = groupBlockedTradeNodes(blockedTradeNodes(state.view));
    void copyBlockedTradeGroups(groups, $("trade-nodes-copy-all"), "全部日期");
});
for (const button of document.querySelectorAll("[data-trade-node-filter]"))
    button.addEventListener("click", () => {
        state.tradeNodeFilter = button.dataset.tradeNodeFilter;
        applyTradeNodeFilter();
    });
function renderTables() {
    const v = state.view;
    $("trades-body").replaceChildren();
    $("orders-body").replaceChildren();
    $("signals-body").replaceChildren();
    $("fills-body").replaceChildren();
    const markers = v.markers.filter((m) => m.kind === "fill");
    for (const m of [...markers].reverse())
        $("fills-body").append(
            row([
                m.signal_time || "—",
                m.time,
                m.side === "BUY" ? "B 买入" : "S 卖出",
                num(m.price, 4),
                num(m.raw_price, 4),
                num(m.quantity),
                num(m.fee),
                entryPositionLabel(m),
                actionCell(m.side === "BUY" ? "查看入场条件" : "查看退出原因", () => selectFill(m)),
            ]),
        );
    $("fills-empty").hidden = markers.length > 0;
    $("fills-only").disabled = markers.length === 0;
    for (const t of [...v.trades].reverse())
        $("trades-body").append(
            row([
                symbolName(t.symbol),
                t.entry_time.slice(0, 10),
                t.exit_time.slice(0, 10),
                t.bars_held,
                cell(num(t.pnl), Number(t.pnl) > 0 ? "positive" : "negative"),
                cell(pct(t.net_return), Number(t.pnl) > 0 ? "positive" : "negative"),
                num(t.fees),
                actionCell("复盘 ↗", () => locate(t.symbol, t.entry_time.slice(0, 10), `交易复核：${t.entry_reason}`)),
            ]),
        );
    $("trades-empty").hidden = v.trades.length > 0;
    $("trade-count").textContent = `${v.trades.length} 笔已平仓`;
    const openSummary = $("open-position-summary");
    openSummary.replaceChildren();
    const openPositions = v.result_scope === "stock" ? v.backtest?.open_positions || [] : [];
    openSummary.hidden = openPositions.length === 0;
    for (const position of openPositions) {
        const profit = openPositionProfit(position);
        if (!profit) continue;
        const paragraph = document.createElement("p");
        paragraph.textContent = `${symbolName(position.symbol)} · 期末未平仓估值：${profit.text}`;
        paragraph.className = profit.pnl >= 0 ? "positive" : "negative";
        openSummary.append(paragraph);
    }
    for (const o of [...v.orders].reverse())
        $("orders-body").append(
            row([
                o.timestamp.slice(0, 10),
                symbolName(o.symbol),
                label(o.side),
                label(o.status),
                num(o.price),
                num(o.quantity),
                o.reason,
                actionCell("定位", () =>
                    locate(o.symbol, o.timestamp.slice(0, 10), `${label(o.side)} · ${label(o.status)}：${o.reason}`),
                ),
            ]),
        );
    $("orders-empty").hidden = v.orders.length > 0;
    for (const s of [...v.signals].reverse().slice(0, 100))
        $("signals-body").append(
            row([s.time, label(s.side), num(s.reference_price), num(s.invalidation_price), label(s.regime), s.reason]),
        );
    const fills = v.orders.filter((o) => o.status === "filled");
    $("focus-fill").disabled = fills.length === 0;
}
function renderEvents() {
    chart.refreshMarkers();
}
async function loadTheory(request, sequence, preloaded = null) {
    if (preloaded) state.theory = preloaded;
    if (!$("show-theory").checked && !$("show-rules").checked) {
        $("theory-status").textContent = "规则与折线已关闭";
        return;
    }
    $("theory-status").textContent = "按历史截面计算…";
    try {
        const data =
            preloaded ||
            (isTdxBacktest()
                ? state.view.theory
                : await api(
                      isAkShare() ? "/api/akshare-theory" : isTdx() ? "/api/tdx-theory" : "/api/theory",
                      isMarketBrowse()
                          ? { symbol: request.symbol, asof: request.asof, timeframe: request.timeframe }
                          : { run: request.run, variant: request.variant, symbol: request.symbol, asof: request.asof },
                  ));
        if (sequence !== state.sequence) return;
        state.theory = data;
        chart.setTheory(data, $("show-theory").checked);
        const pending = state.pendingStructureAnnotation;
        if (pending && pending.symbol === request.symbol && pending.asof === request.asof) {
            $(pending.checkbox).checked = true;
            chart.setAnnotationOptions(annotationOptions());
            chart.selectAnnotation(pending.annotationId);
            chart.flashSelectedAnnotation(pending.annotationId);
            state.pendingStructureAnnotation = null;
        }
        $("drawing-status").textContent =
            `讲义绘图：${data.lecture_drawing?.teaching_paths?.length || 0} 组子母三点、${data.lecture_drawing?.inside_connections?.length || 0} 处母子缩头／缩脚衔接；${data.lecture_drawing?.issues.length || 0} 处十字星／初始方向待确认。${data.strategy_pivot_mode === "lecture_causal" ? "新版从同一递推器提取收盘确认点；绘图连接不直接等于交易信号。" : "显示结构与所选旧策略／行情浏览独立。"}母子顺序是讲义约定，不代表已知真实日内路径。`;
        $("theory-status").textContent = data.interrupted ? "当前结构未解" : "已确认结构";
        if (!state.pendingFocus && !state.selectedAnnotationId)
            detail(
                data.interrupted ? "严格结构中断" : "点击标识查看规则",
                data.interrupted
                    ? "缺少次级路径，不把未解折线标成已确认形态。可开启“筛选 / 中断”查看具体日期。"
                    : `${symbolName(request.symbol)} · ${data.asof}。规则圆点标在可知日期；信号圆点不等于成交。B / S 字母锚定实际成交价，点击可查看价格、颈线、防守位与目标投影。`,
            );
    } catch (error) {
        if (sequence !== state.sequence) return;
        $("theory-status").textContent = "标注不可用";
        detail("理论标注加载失败", error.message);
        renderEvents();
    }
}
async function loadView({ focusLatestFill = false, preferTrades = focusLatestFill } = {}) {
    syncSymbolCopy();
    syncProfileScope();
    const hasRenderedView = Boolean(state.view);
    const sequence = ++state.sequence;
    state.controller?.abort();
    state.controller = new AbortController();
    state.summaryController?.abort();
    $("stock-results-body").replaceChildren();
    state.loading = true;
    state.error = false;
    state.theory = null;
    state.selectedAnnotationId = null;
    if (!$("trade-nodes-tab").hidden) {
        $("trade-nodes-panel").setAttribute("aria-busy", "true");
        $("trade-nodes-list").replaceChildren();
        $("trade-nodes-empty").hidden = false;
        $("trade-nodes-empty").textContent = "正在读取当前股票的交易节点…";
    }
    $("download-backtest").disabled = true;
    $("run-stock-backtest").disabled = true;
    $("error").hidden = true;
    $("loading").hidden = false;
    $("loading").textContent = isTdxBacktest()
        ? "正在校验除权数据、运行策略并生成成交账本…"
        : isAkShare()
          ? `正在读取 AkShare ${timeframes[activeTimeframe()].label}预计算快照…`
          : isTdx()
            ? `正在读取通达信${timeframes[activeTimeframe()].label}预计算快照…`
            : "读取已封存行情与交易记录…";
    $("chart-loading-overlay").hidden = false;
    $("price-chart").setAttribute("aria-busy", "true");
    if (!hasRenderedView) document.querySelector(".metric-grid").hidden = true;
    showPage(state.page);
    syncDate();
    const request = select();
    if (focusLatestFill) $("show-fills").checked = true;
    state.requestedAsOf = request.asof;
    buyPoints.contextChanged();
    structureSignals.contextChanged();
    ratioComparison.contextChanged();
    stockList.setSelected(request.symbol);
    watchlists.setSelected(request.symbol);
    $("selected-stock-summary").textContent = `${symbolName(request.symbol)} · 正在读取 ${request.asof} 截面…`;
    const requestSignal = state.controller.signal;
    try {
        let timeframeBundle = null;
        const viewParams = {
            run: request.run,
            variant: request.variant,
            scenario: request.scenario,
            symbol: request.symbol,
            asof: request.asof,
        };
        const backtestParams = {
            ...viewParams,
            start: $("backtest-start").value,
            volume_filter: String($("backtest-volume-filter").checked),
            net_reward_risk_filter: String($("backtest-net-reward-risk-filter").checked),
            ...(isTdxBacktest() ? currentBacktestSizing() : {}),
        };
        const data = isMarketBrowse()
            ? (timeframeBundle = await loadMarketTimeframeSnapshot(
                  isAkShare() ? "akshare" : "tdx",
                  request.symbol,
                  request.asof,
                  request.timeframe,
              )).view
            : isTdxBacktest()
              ? await retryBacktest(
                    () =>
                        api(isAkShare() ? "/api/akshare-backtest" : "/api/tdx-backtest", backtestParams, requestSignal),
                    {
                        signal: requestSignal,
                        onRetry: (attempt, total) => {
                            if (sequence === state.sequence)
                                $("loading").textContent =
                                    `图表服务暂不可用，等待自动恢复后重试当前股票回测（${attempt}/${total}）…`;
                        },
                    },
                )
              : await api(
                    $("result-scope").value === "stock" ? "/api/stock-view" : "/api/view",
                    viewParams,
                    requestSignal,
                );
        if (sequence !== state.sequence) return;
        state.view = data;
        state.loading = false;
        const showTrades = data.result_scope === "stock" && Boolean(data.backtest);
        if (showTrades && (preferTrades || $("trade-nodes-tab").hidden)) {
            state.tradeNodeFilter = "all";
            state.tradeNodeView = "filled";
        }
        syncStockBrowserMode(showTrades, preferTrades);
        if (data.result_scope === "tdx" || data.data_source === "tdx") {
            state.tdxSessions[`${data.symbol}:${data.timeframe || "1d"}`] = data.sessions;
            preserveCutoff(data.asof);
        }
        if (data.result_scope === "akshare" || data.data_source === "akshare") {
            state.akshareSessions[`${data.symbol}:${data.timeframe || "1d"}`] = data.sessions;
            preserveCutoff(data.asof);
        }
        $("timeframe-tag").textContent = timeframes[data.timeframe || "1d"].tag;
        $("partial-timeframe").hidden = !data.is_partial_last_bar;
        document.querySelector(".metric-grid").hidden = false;
        showPage(state.page);
        renderMetrics();
        renderTables();
        if (showTrades) renderTradeNodes();
        if (data.backtest?.screening) {
            const p = document.createElement("p");
            p.textContent =
                "策略候选未通过：" +
                (Object.entries(data.backtest.screening.rejection_reasons)
                    .map(([r, n]) => `${reasonText(r)} × ${n}`)
                    .join("；") || "无候选拒绝记录") +
                `。严格结构中断 ${data.backtest.screening.events.strict_structure_interrupted || 0} 次。`;
            $("backtest-details").append(p);
        }
        if (data.backtest?.limitations) {
            const p = document.createElement("p");
            p.textContent = data.backtest.limitations.join(" ");
            $("backtest-details").append(p);
        }
        $("download-backtest").disabled = !data.backtest || data.backtest.status === "data_unavailable";
        // Keep the chosen source for daily prices, minute execution and factors.
        $("run-stock-backtest").disabled = !isAkShare() && !canBacktestSymbol(data.symbol);
        $("run-stock-backtest").title =
            !isAkShare() && !canBacktestSymbol(data.symbol)
                ? "当前股票缺少通达信本地日线，暂不可回测"
                : isAkShare()
                  ? "日线、分钟线和复权因子统一使用 AKShare / 新浪"
                  : "";
        $("price-basis").textContent = data.price_basis === "raw_unadjusted" ? "原始不复权" : "因果复权";
        chart.setData(data, {
            volume: $("show-volume").checked,
            markers: $("show-markers").checked,
            annotations: annotationOptions(),
        });
        describeBar(data.bars.at(-1));
        performance.update(data.curve);
        const last = data.bars.at(-1);
        const providerNames = { akshare: "AkShare", tdx: "通达信" };
        const sourceSummary = data.source_fallback
            ? `首选 ${providerNames[data.data_source] || data.data_source}，已回退到 ${providerNames[data.resolved_source] || data.resolved_source}。`
            : data.supplemented_bars
              ? `${providerNames[data.data_source] || data.data_source} 为主，${data.providers
                    .slice(1)
                    .map((source) => providerNames[source] || source)
                    .join("、")}补齐 ${data.supplemented_bars} 个缺失交易日。`
              : data.result_scope === "akshare"
                ? `AkShare 在线日线（v${data.provider_version}），未回测。`
                : data.result_scope === "tdx"
                  ? "通达信本地日线，未回测。"
                  : data.result_scope === "stock"
                    ? "收益、仓位、成交账本均为该股独立回测。"
                    : "上方收益／仓位指标及交易账本仍为全组合。";
        const cacheSummary = timeframeBundle
            ? timeframeBundle.browser_cache === "validated"
                ? " 浏览器周期缓存已校验。"
                : timeframeBundle.browser_cache === "offline"
                  ? " 服务器暂不可用，使用浏览器周期缓存。"
                  : " 浏览器周期缓存已同步。"
            : "";
        $("selected-stock-summary").textContent =
            `${symbolName(data.symbol)} · ${data.asof} ${data.timeframe_label || "日线"}截面｜原始收盘 ${num(last.raw_close)} 元 · 周期成交量 ${num(last.volume, 0)} 股。${sourceSummary}${cacheSummary}${data.is_partial_last_bar ? " 当前最后一根周期 K 线尚未收完。" : ""}`;
        $("price-chart").dataset.symbol = data.symbol;
        tradingViewWidget.setLocalSymbol(data.symbol);
        $("data-range").textContent = isAkShare()
            ? `AkShare · ${universe().length} 只 · ${data.timeframe_label || "日线"} · 当前个股最新 ${data.sessions.at(-1)}`
            : isLocal()
              ? `通达信 · ${universe().length} 只 · ${data.timeframe_label || "日线"} · 最新 ${state.tdx.latest}`
              : `${currentRun().start} — ${currentRun().end} · ${currentRun().symbols.length} 只`;
        $("loading").hidden = true;
        $("chart-loading-overlay").hidden = true;
        $("price-chart").setAttribute("aria-busy", "false");
        if (state.pendingFocus) {
            setChartView("local");
            chart.focus(state.pendingFocus.time);
            detail("已定位", state.pendingFocus.description);
        } else if (data.backtest?.status === "data_unavailable") {
            detail("同源分钟历史不足", data.evidence);
        } else if (focusLatestFill) {
            setChartView("local");
            const latestFill = data.markers.filter((marker) => marker.kind === "fill").at(-1);
            if (latestFill) {
                chart.selectAnnotation(latestFill.id);
                requestAnimationFrame(() => $("price-chart").scrollIntoView({ block: "center", behavior: "instant" }));
            } else {
                const blockedCount = blockedTradeNodes(data).length;
                detail(
                    "本次回测没有模拟成交",
                    `图上的圆点是结构或策略信号，不是买卖成交；只有成交账本中的真实模拟买卖才显示 B / S。${blockedCount ? `右侧“被拦截”有 ${blockedCount} 个未通过候选或未成交委托，可定位 K 线查看原因。` : ""}`,
                );
            }
        } else
            detail(
                "按所选日期复核",
                `${symbolName(data.symbol)} · ${data.asof}。青色圆点为信号；红色 B、绿色 S 字母分别表示实际模拟买入和卖出。`,
            );
        renderEvents();
        loadTheory(request, sequence, timeframeBundle?.theory || null);
        loadStockSummary(request, sequence);
    } catch (error) {
        if (error.name === "AbortError" || sequence !== state.sequence) return;
        state.loading = false;
        state.error = true;
        $("loading").hidden = true;
        $("chart-loading-overlay").hidden = true;
        $("error").hidden = false;
        $("error").textContent = `加载失败：${error.message}。旧图已隐藏，请检查结果文件后重试。`;
        $("selected-stock-summary").textContent =
            `${symbolName(request.symbol)} · 加载失败，未展示旧股票数据；可重新选择或刷新。`;
        $("price-chart").setAttribute("aria-busy", "false");
        document.querySelectorAll(".page").forEach((p) => (p.hidden = true));
        document.querySelector(".metric-grid").hidden = true;
        $("stock-results").hidden = true;
        $("run-stock-backtest").disabled = !canBacktestSymbol(request.symbol);
    }
}
async function locate(symbol, time, description) {
    state.pendingFocus = { time, description };
    const cutoff = state.view.asof;
    if ($("symbol-select").value !== symbol) {
        $("symbol-select").value = symbol;
        preserveCutoff(cutoff);
    }
    showPage("workspace");
    await loadView();
}
function selectFill(marker, scrollToChart = false) {
    showPage("workspace");
    setChartView("local");
    $("show-fills").checked = true;
    chart.setAnnotationOptions(annotationOptions());
    chart.selectAnnotation(marker.id);
    chart.flashSelectedAnnotation(marker.id, "execution");
    (scrollToChart ? $("price-chart") : $("selection-info")).scrollIntoView({
        block: scrollToChart ? "center" : "nearest",
    });
}
function selectBlockedNode(marker) {
    showPage("workspace");
    setChartView("local");
    chart.selectAnnotation(marker.id);
    chart.flashSelectedAnnotation(marker.id, marker.blockedStage);
    $("price-chart").scrollIntoView({ block: "center" });
}
function exportBacktest() {
    if (!state.view || state.loading || state.error) return;
    const blob = new Blob([JSON.stringify(state.view, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest-${state.view.symbol}-${state.view.variant}-${state.view.asof}${state.view.result_scope === "stock" && state.view.data_source === "tdx" ? `-volume-${state.view.backtest.strategy.volume_filter ? "on" : "off"}` : ""}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download-backtest").addEventListener("click", exportBacktest);
const ratioComparison = new RatioComparison({
    api,
    getContext: () => {
        const { variant, ...p } = select();
        let sizing = null;
        try {
            sizing = currentBacktestSizing();
        } catch {
            // Invalid form values disable comparison until corrected.
        }
        return {
            ...p,
            ...sizing,
            start: $("backtest-start").value,
            volume_filter: $("backtest-volume-filter").checked,
            net_reward_risk_filter: $("backtest-net-reward-risk-filter").checked,
            local: (isLocal() || isAkShare()) && sizing !== null,
            source: isAkShare() ? "akshare" : "tdx",
        };
    },
    onSelect: (variant) => {
        $("variant-select").value = variant === "lecture_v3_c50" ? "lecture_v3" : variant;
        if (variant === "lecture_v3" || variant === "lecture_v3_c50")
            $("second-pullback-select").value = variant === "lecture_v3_c50" ? "half" : "third";
        $("result-scope").value = isAkShare() ? "akshare-backtest" : "tdx-backtest";
        loadView();
    },
});
$("backtest-start").addEventListener("change", () => {
    buyPoints.contextChanged();
    structureSignals.contextChanged();
    ratioComparison.contextChanged();
});
for (const id of ["backtest-capital", "backtest-buy-ratio"]) {
    $(id).addEventListener("change", () => {
        ratioComparison.contextChanged();
        if (isTdxBacktest()) loadView();
    });
}
$("backtest-volume-filter").addEventListener("change", () => {
    ratioComparison.contextChanged();
    if (isTdxBacktest()) loadView();
});
$("backtest-net-reward-risk-filter").addEventListener("change", () => {
    ratioComparison.contextChanged();
    if (isTdxBacktest()) loadView();
});
$("run-stock-backtest").addEventListener("click", () => {
    const cutoff = state.requestedAsOf || state.view?.asof || state.tdx.latest;
    $("result-scope").value = isAkShare() ? "akshare-backtest" : "tdx-backtest";
    state.pendingFocus = null;
    fillSymbols();
    preserveCutoff(cutoff);
    loadView({ focusLatestFill: true });
});
$("fills-only").addEventListener("click", () => {
    $("show-fills").checked = true;
    $("show-markers").checked = false;
    $("show-entry-rejections").checked = false;
    $("show-rules").checked = false;
    updateLayerToggleCount();
    chart.setAnnotationOptions(annotationOptions());
    const marker = state.view?.markers.filter((m) => m.kind === "fill").at(-1);
    if (marker) {
        chart.selectAnnotation(marker.id);
        requestAnimationFrame(() => $("price-chart").scrollIntoView({ block: "center", behavior: "instant" }));
    }
});
async function loadHealth() {
    if (!state.catalog) return;
    const rid = $("run-select").value;
    try {
        const h = await api("/api/health", { run: rid });
        if (rid !== $("run-select").value) return;
        $("health-summary").textContent =
            `运行 ${h.status} · ${h.data.rows.toLocaleString()} 条日线 · 备份恢复演练${h.recovery_drill_passed ? "通过" : "未通过"}。实盘关闭，数据与研究证据分开验收。`;
        $("health-body").replaceChildren();
        for (const r of h.readiness) {
            const status = cell("");
            const badge = document.createElement("span");
            badge.className = "badge" + (["PASS", "CURRENT", "EXECUTED"].includes(r.status) ? " good" : "");
            badge.textContent = label(r.status);
            status.append(badge);
            $("health-body").append(row([label(r.code), status, r.detail]));
        }
        $("alerts").replaceChildren();
        for (const a of h.alerts.filter((a) => a.active)) {
            const div = document.createElement("div");
            div.className = "alert-row";
            div.textContent = `${a.acknowledged ? "已阅读" : "待处理"} · ${label(a.code)}`;
            const small = document.createElement("small");
            small.textContent = a.message;
            div.append(small);
            $("alerts").append(div);
        }
        if (!$("alerts").children.length) $("alerts").textContent = "当前无活动告警。";
    } catch (e) {
        $("health-summary").textContent = "运行状态读取失败：" + e.message;
    }
}
document.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-page]");
    if (button?.dataset.page) showPage(button.dataset.page);
});
$("run-select").addEventListener("change", () => {
    state.pendingFocus = null;
    fillSymbols();
    loadView();
});
for (const id of ["variant-select", "scenario-select", "second-pullback-select"])
    $(id).addEventListener("change", () => {
        state.pendingFocus = null;
        loadView();
    });
$("result-scope").addEventListener("change", () => {
    state.pendingFocus = null;
    fillSymbols();
    loadView();
});
$("symbol-select").addEventListener("change", () => chooseSymbol($("symbol-select").value, false, false));
function activateTimeframe(timeframe, focus = false) {
    if (!isMarketBrowse() || timeframe === activeTimeframe()) return;
    const cutoff = state.requestedAsOf || state.view?.asof || currentRun().end;
    setTimeframe(timeframe, { focus });
    chartPreferences = { ...chartPreferences, timeframe };
    try {
        localStorage.setItem(chartPreferenceKey, JSON.stringify(chartPreferences));
    } catch {
        // 当前会话仍可切换周期；持久化不可用不影响行情请求。
    }
    $("timeframe-tag").textContent = timeframes[activeTimeframe()].tag;
    state.noSessionBefore = cutoff;
    state.pendingFocus = null;
    loadView();
}
$("timeframe-select").addEventListener("click", (event) => {
    const tab = event.target.closest?.("[role=tab][data-timeframe]");
    if (tab && !tab.disabled) activateTimeframe(tab.dataset.timeframe);
});
$("timeframe-select").addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const enabled = timeframeTabs.filter((tab) => !tab.disabled);
    if (!enabled.length) return;
    event.preventDefault();
    const current = enabled.findIndex((tab) => tab.dataset.timeframe === activeTimeframe());
    const index =
        event.key === "Home"
            ? 0
            : event.key === "End"
              ? enabled.length - 1
              : (current + (event.key === "ArrowRight" ? 1 : -1) + enabled.length) % enabled.length;
    activateTimeframe(enabled[index].dataset.timeframe, true);
});
$("replay-slider").addEventListener("input", () => {
    $("asof-label").textContent = `待加载 ${sessions()[Number($("replay-slider").value)]}`;
});
$("replay-slider").addEventListener("change", () => {
    state.noSessionBefore = null;
    state.pendingFocus = null;
    loadView();
});
function step(delta) {
    state.noSessionBefore = null;
    const slider = $("replay-slider");
    slider.value = Math.min(Number(slider.max), Math.max(0, Number(slider.value) + delta));
    state.pendingFocus = null;
    loadView();
}
$("previous").addEventListener("click", () => step(-1));
$("next").addEventListener("click", () => step(1));
$("latest").addEventListener("click", () => {
    resetSlider();
    state.pendingFocus = null;
    loadView();
});
$("reload").addEventListener("click", () => loadView());
$("show-volume").addEventListener("change", (e) => chart.setVolume(e.target.checked));
$("show-markers").addEventListener("change", (e) => chart.setMarkers(e.target.checked));
$("show-theory").addEventListener("change", (e) => {
    if (!e.target.checked) {
        chart.clearTheory();
        chart.refreshMarkers();
    } else if (state.theory) chart.setTheory(state.theory);
    else loadTheory(select(), state.sequence);
});
for (const id of ["drawing-mode", "show-teaching"])
    $(id).addEventListener("change", () => chart.setDrawingMode($("drawing-mode").value, $("show-teaching").checked));
$("show-trend").addEventListener("change", (e) => chart.setTrendVisible(e.target.checked));
$("show-trend-prices").addEventListener("change", (e) => {
    chart.setTrendPriceLabelsVisible(e.target.checked);
    chartPreferences = { ...chartPreferences, showTrendPrices: e.target.checked };
    try {
        localStorage.setItem(chartPreferenceKey, JSON.stringify(chartPreferences));
    } catch {
        // 隐私模式或存储配额异常时，当前会话内的开关仍然有效。
    }
});
$("show-secondary-trend").addEventListener("change", (e) => chart.setSecondaryTrendVisible(e.target.checked));
$("show-tertiary-retracement").addEventListener("change", (e) => {
    chart.setAnnotationOptions(annotationOptions());
    chartPreferences = { ...chartPreferences, showTertiaryRetracement: e.target.checked };
    try {
        localStorage.setItem(chartPreferenceKey, JSON.stringify(chartPreferences));
    } catch {
        // 存储不可用时仍保留本次会话的显示选择。
    }
});
$("show-tertiary-trend").addEventListener("change", (e) => chart.setTertiaryTrendVisible(e.target.checked));
for (const id of [
    "show-fills",
    "show-entry-rejections",
    "show-rules",
    "show-diagnostics",
    "show-levels",
    "show-last-fall-high",
    "show-bear-to-bull-highs",
    "show-bear-bull-alternation-lows",
    "show-post-alternation-bull-highs",
    "show-bullish-turn-signals",
    "show-tertiary-abc",
])
    $(id).addEventListener("change", () => {
        chart.setAnnotationOptions(annotationOptions());
        if ($("show-rules").checked && !state.theory && !state.loading && !state.error)
            loadTheory(select(), state.sequence);
    });
$("focus-fill").addEventListener("click", () => {
    const marker = state.view.markers.filter((m) => m.kind === "fill").at(-1);
    if (marker) selectFill(marker);
    else {
        const o = state.view.orders.filter((o) => o.status === "filled").at(-1);
        if (o) locate(o.symbol, o.timestamp.slice(0, 10), `${label(o.side)} · ${o.reason}`);
    }
});
$("export-orders").addEventListener("click", () => {
    if (!state.view) return;
    const blob = new Blob(
        [
            JSON.stringify(
                {
                    run_id: state.view.run_id,
                    asof: state.view.asof,
                    result_scope: state.view.result_scope || "portfolio",
                    symbol: state.view.symbol,
                    price_basis: state.view.price_basis,
                    metrics: state.view.metrics,
                    backtest: state.view.backtest,
                    orders: state.view.orders,
                    trades: state.view.trades,
                },
                null,
                2,
            ),
        ],
        { type: "application/json" },
    );
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `orders-${state.view.result_scope || "portfolio"}-${state.view.symbol}-${state.view.variant}-${state.view.asof}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
});
async function start() {
    try {
        const requestedStock = parseResearchLink(window.location.search);
        const akshareOption = $("result-scope").querySelector('[value="akshare"]');
        akshareOption.disabled = true;
        akshareOption.textContent = "AkShare · 正在连接…";
        [state.catalog, state.tdx, state.akshare] = await Promise.all([
            api("/api/catalog"),
            loadStockCatalog("tdx", "/api/tdx-catalog").catch((e) => ({
                available: false,
                stocks: [],
                with_daily: 0,
                error: e.message,
            })),
            loadStockCatalog("akshare", "/api/akshare-catalog").catch((e) => ({
                available: false,
                stocks: [],
                with_daily: 0,
                error: e.message,
            })),
        ]);
        for (const s of state.tdx.stocks) if (s.name) names[s.symbol] = s.name;
        for (const s of state.akshare.stocks) if (s.name) names[s.symbol] = s.name;
        await watchlists.init();
        const tdxOption = $("result-scope").querySelector('[value="tdx"]');
        tdxOption.disabled = !state.tdx.with_daily;
        if (!state.tdx.with_daily) tdxOption.textContent = "通达信目录不可用";
        akshareOption.disabled = !state.akshare.with_daily;
        akshareOption.textContent = state.akshare.with_daily ? "AkShare · 在线 A 股行情" : "AkShare 数据源不可用";
        // AkShare 是默认实时浏览口径；上游不可用时才按本地数据、封存样本的顺序降级。
        $("result-scope").value = state.akshare.with_daily ? "akshare" : state.tdx.with_daily ? "tdx" : "stock";
        $("symbol-select").value = watchlists.firstAvailableSymbol(universe());
        const linkedStock = requestedStock
            ? resolveResearchLink(requestedStock, { akshare: state.akshare, tdx: state.tdx })
            : null;
        if (linkedStock) {
            $("result-scope").value = linkedStock.source;
            $("symbol-select").value = linkedStock.symbol;
            setTimeframe("1d");
        }
        $("run-select").replaceChildren();
        for (const r of state.catalog.runs) option($("run-select"), r.id, r.id.replace("acceptance_", ""));
        fillSymbols();
        if (linkedStock?.asof) preserveCutoff(linkedStock.asof);
        await loadView();
        if (linkedStock) {
            const notices = [];
            if (linkedStock.fallback)
                notices.push(
                    `所选股票在 ${requestedStock.source === "akshare" ? "AkShare" : "通达信"} 目录中暂无可用行情，当前使用 ${linkedStock.source === "akshare" ? "AkShare" : "通达信"} 查看同一股票。`,
                );
            if (linkedStock.asof && state.view?.symbol === linkedStock.symbol && state.view.asof !== linkedStock.asof)
                notices.push(`请求日期 ${linkedStock.asof}，实际可用行情截至 ${state.view.asof}。`);
            $("stock-picker-feedback").hidden = notices.length === 0;
            $("stock-picker-feedback").textContent = notices.join(" ");
        }
        if (requestedPage && Object.hasOwn(titles, requestedPage)) showPage(requestedPage);
    } catch (e) {
        stockList.setStocks([], "");
        watchlists.setUniverse([], "");
        $("stock-count").textContent = "不可用";
        $("selected-stock-summary").textContent = "股票列表读取失败，请刷新重试。";
        $("loading").hidden = true;
        $("error").hidden = false;
        $("error").textContent = "初始化失败：" + e.message;
    }
}
start();
