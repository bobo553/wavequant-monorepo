import { reasonText } from "./annotations.js";
import {
    adoptServerBacktestHistory,
    expiredBacktestSnapshot,
    formatBacktestElapsed,
    historicalBacktestStatuses,
    runningBacktestStatuses,
    selectedBacktestAction,
    selectedBacktestButton,
} from "./backtest-job-status.js";
import { retryBacktest, waitForBacktestJob } from "./backtest-retry.js";
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
import { chartNavigationKeyPosition } from "./chart-navigation.js";
import { PerformanceCharts, PriceChart } from "./charts.js";
import { reuseCompletedBacktest } from "./completed-backtest-result.js";
import { formatFilledTradeCopy } from "./filled-trade-copy.js";
import { label, names, num, pct, symbolName } from "./labels.js";
import { RatioComparison, ratioPlans } from "./ratio-comparison.js";
import { parseResearchLink, resolveResearchLink } from "./research-link.js";
import { StockBacktestTasks } from "./stock-backtest-tasks.js";
import { StockList } from "./stock-list.js";
import { StructureSignals } from "./structure-signals.js";
import { closedPositionLabel, openPositionForMarker, openPositionProfit, positionProfit } from "./trade-position.js";
import { numberedTradeReasons } from "./trade-reasons.js";
import { appendTradeEvidence } from "./trade-review.js";
import { TradingViewWidget } from "./tradingview-widget.js";
import { IdleWatchlistBacktests, backtestArgumentsKey, watchlistBacktestRequest } from "./watchlist-backtest-queue.js";
import { Watchlists } from "./watchlists.js";

const $ = (id) => document.getElementById(id);
let serverBacktestSnapshot = { active: 0, max_active: 4, jobs: [] };
let serverBacktestSnapshotSeenAt = Date.now();
let backtestToastTimer;
function showBacktestToast(message) {
    let toast = $("backtest-toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "backtest-toast";
        toast.setAttribute("role", "alert");
        document.body.append(toast);
    }
    toast.textContent = message;
    toast.hidden = false;
    clearTimeout(backtestToastTimer);
    backtestToastTimer = setTimeout(() => {
        toast.hidden = true;
    }, 5_000);
}
function showBacktestRejection(error) {
    if (Array.isArray(error.jobs)) {
        serverBacktestSnapshot = { active: error.active, max_active: error.max_active, jobs: error.jobs };
        serverBacktestSnapshotSeenAt = Date.now();
        renderServerBacktestStatuses();
        watchlistBacktests.emit();
    }
    if (error.code === "BACKTEST_CAPACITY")
        showBacktestToast(
            `并行回测已满（${error.active ?? "?"}/${error.max_active ?? "?"}），请等待已有回测完成后重试`,
        );
    else if (error.code === "BACKTEST_SYMBOL_RUNNING") showBacktestToast("该股票已有回测正在进行，请等待完成");
}
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
    tdx: { available: false, stocks: [], with_daily: 0 },
    akshare: { available: false, stocks: [], with_daily: 0 },
    view: null,
    theory: null,
    page: "workspace",
    sequence: 0,
    loading: false,
    error: false,
    controller: null,
    pendingFocus: null,
    pendingStructureAnnotation: null,
    activeBacktestTask: null,
    symbolNavigation: 0,
    tradeNodeFilter: "all",
    tradeNodeView: "filled",
};
state.tdxSessions = {};
state.akshareSessions = {};
const catalogRequests = new Map();
const loadedCatalogs = new Set();
const sourceForScope = (scope) => (scope.startsWith("akshare") ? "akshare" : scope.startsWith("tdx") ? "tdx" : null);
function catalogHasSymbol(catalog, symbol) {
    return Boolean(
        catalog?.with_daily && catalog.stocks.some((stock) => stock.symbol === symbol && stock.has_data !== false),
    );
}
function syncSourceOptions() {
    const sources = [
        ["akshare", "AkShare · 在线 A 股行情", "AkShare · 同源股票回测"],
        ["tdx", "通达信 · 全部 A 股行情", "通达信 · 当前股票回测"],
    ];
    for (const [source, browseLabel, backtestLabel] of sources) {
        const suffix = loadedCatalogs.has(source) ? "" : "（选择后加载目录）";
        $("result-scope").querySelector(`[value="${source}"]`).textContent = browseLabel + suffix;
        $("result-scope").querySelector(`[value="${source}-backtest"]`).textContent = backtestLabel + suffix;
    }
}
function ensureSourceCatalog(source) {
    if (loadedCatalogs.has(source)) return Promise.resolve(state[source]);
    if (catalogRequests.has(source)) return catalogRequests.get(source);
    const request = loadStockCatalog(source, `/api/${source}-catalog`)
        .then((catalog) => {
            state[source] = catalog;
            for (const stock of catalog.stocks || []) if (stock.name) names[stock.symbol] = stock.name;
            if (catalog.with_daily) loadedCatalogs.add(source);
            syncSourceOptions();
            return catalog;
        })
        .finally(() => catalogRequests.delete(source));
    catalogRequests.set(source, request);
    return request;
}
const isTdx = () => $("result-scope").value === "tdx";
const isAkShare = () => ["akshare", "akshare-backtest"].includes($("result-scope").value);
const isTdxBacktest = () => ["tdx-backtest", "akshare-backtest"].includes($("result-scope").value);
const isLocal = () => isTdx() || $("result-scope").value === "tdx-backtest";
const isMarketBrowse = () => isTdx() || $("result-scope").value === "akshare";
let lastStockBacktestStatuses = "";
function renderRunStockBacktestButton() {
    const button = $("run-stock-backtest");
    const symbol = $("symbol-select").value;
    const currentTask = state.activeBacktestTask?.symbol === symbol ? state.activeBacktestTask : null;
    const status =
        currentTask?.status === "completed" && currentTask.result === state.view
            ? "completed"
            : watchlists.backtestStatuses[symbol] || currentTask?.status || "pending";
    const showingCompletedResult =
        isTdxBacktest() && !state.error && currentTask?.status === "completed" && currentTask.result === state.view;
    const action = selectedBacktestButton(status, showingCompletedResult);
    button.dataset.status = status;
    button.textContent = action.label;
    button.disabled = state.loading || !canBacktestSymbol(symbol) || status === "running" || status === "unknown";
    button.title = !canBacktestSymbol(symbol)
        ? "当前股票缺少所选数据源的日线，暂不可回测"
        : status === "running"
          ? "该股票回测正在运行，完成后可重新回测"
          : status === "unknown"
            ? "服务器任务状态待确认，暂不重复提交"
            : isAkShare()
              ? "日线、分钟线和复权因子统一使用 AKShare / 新浪"
              : "";
}
function renderStockBacktestStatus() {
    const current =
        state.activeBacktestTask?.symbol === $("symbol-select").value && isTdxBacktest()
            ? state.activeBacktestTask
            : null;
    const others = [...stockBacktestTasks.tasks.values()].filter(
        (task) => task.status === "running" && task !== current,
    ).length;
    const status = $("stock-backtest-status");
    status.hidden = !current && !others;
    status.dataset.status = current?.status || "background";
    const name = current ? symbolName(current.symbol) : "";
    const currentServerJob = serverBacktestSnapshot.jobs.find((job) => job.symbol === current?.symbol);
    const elapsed = formatBacktestElapsed(currentServerJob?.elapsed_seconds);
    const serverStatus = serverBacktestSnapshot.unavailable
        ? "服务器任务状态暂不可确认。"
        : elapsed
          ? `${elapsed}。`
          : "";
    const priorResult =
        current?.status === "running" && state.view?.symbol === current.symbol && state.view.result_scope === "stock"
            ? "图中成交标记为上次结果，完成后更新。"
            : "";
    $("stock-backtest-status-text").textContent =
        current?.status === "running"
            ? `${name} · ${current.message} ${serverStatus}可继续查看或切换股票。${priorResult}`
            : current?.status === "completed"
              ? `${name} · 回测已完成，结果已显示在当前股票。`
              : current?.status === "failed"
                ? `${name} · 回测失败：${current.error?.message || "未知错误"}。可修改设置后重试。`
                : "";
    $("stock-backtest-other").textContent = others ? `另有 ${others} 只股票仍在后台回测。` : "";
    const statuses = {};
    for (const task of stockBacktestTasks.tasks.values()) statuses[task.symbol] = task.status;
    const merged = runningBacktestStatuses(statuses, serverBacktestSnapshot.jobs, {
        unavailable: serverBacktestSnapshot.unavailable,
    });
    const serialized = JSON.stringify([
        merged,
        serverBacktestSnapshot.jobs.map((job) => [job.symbol, job.elapsed_seconds]),
    ]);
    if (serialized !== lastStockBacktestStatuses) {
        lastStockBacktestStatuses = serialized;
        stockList.setBacktestStatuses(merged, serverBacktestSnapshot.jobs);
    }
    renderRunStockBacktestButton();
}
const stockBacktestTasks = new StockBacktestTasks({
    run: (task, progress) => {
        const request = () => api(task.path, task.params);
        return retryBacktest(request, {
            onTimeout: () =>
                waitForBacktestJob(() => api("/api/backtest-job", { job: task.params.backtest_job }), request, {
                    maxWaitMs: 24 * 60 * 60 * 1000,
                    onPending: () => progress("服务器仍在计算，正在查询任务状态…"),
                    onRestart: () => progress("服务恢复后正在继续查询回测…"),
                    onRetry: (attempt, total) => progress(`状态查询暂不可用，重试中（${attempt}/${total}）…`),
                }),
            onRetry: (attempt, total) => progress(`回测服务暂不可用，重试中（${attempt}/${total}）…`),
        }).catch((error) => {
            if (error.code !== "BACKTEST_SYMBOL_RUNNING" || !error.same_request || !error.running_job) throw error;
            task.params.backtest_job = error.running_job;
            progress("相同参数的回测已在运行，正在同步任务状态…");
            return waitForBacktestJob(() => api("/api/backtest-job", { job: error.running_job }), request, {
                maxMissingResubmits: 0,
                maxWaitMs: 24 * 60 * 60 * 1000,
                onPending: () => progress("服务器仍在计算，正在查询任务状态…"),
            });
        });
    },
    onChange: (task) => {
        if (task.status === "completed") syncCompletedStockBacktests();
        renderServerBacktestStatuses();
    },
});
const activeTimeframe = () => (isMarketBrowse() ? selectedTimeframe() : "1d");
const sessionCacheKey = (symbol) => `${symbol}:${activeTimeframe()}`;
function universe() {
    return isAkShare() ? state.akshare?.stocks || [] : isLocal() ? state.tdx?.stocks || [] : currentRun().symbols;
}
const titles = {
    workspace: "K 线复盘",
    performance: "策略绩效",
    topology: "策略拓扑",
    orders: "订单与信号",
    health: "系统状态",
};
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
        if (!response.ok) {
            const error = new Error(body.error || `HTTP ${response.status}`);
            error.httpStatus = response.status;
            error.code = body.code;
            if (["BACKTEST_CAPACITY", "BACKTEST_SYMBOL_RUNNING"].includes(error.code)) {
                Object.assign(error, {
                    active: body.active,
                    max_active: body.max_active,
                    jobs: body.jobs,
                    running_job: body.running_job,
                    same_request: body.same_request,
                });
                showBacktestRejection(error);
            }
            throw error;
        }
        return body;
    } catch (e) {
        if (e.name === "TimeoutError") {
            const error = new Error(
                ["/api/tdx-backtest", "/api/akshare-backtest"].includes(path)
                    ? "回测计算仍在后台进行"
                    : path === "/api/backtest-job"
                      ? "回测任务状态查询超时"
                      : "请求超时，请稍后刷新重试",
            );
            if (["/api/tdx-backtest", "/api/akshare-backtest"].includes(path)) error.code = "BACKTEST_TIMEOUT";
            if (path === "/api/backtest-job") error.code = "BACKTEST_POLL_TIMEOUT";
            throw error;
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
if (requestedPage === "topology") showPage("topology");
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
function renderChartViewport(state, bars) {
    $("chart-pan-left").disabled = !state?.canPanLeft;
    $("chart-pan-right").disabled = !state?.canPanRight;
    $("chart-zoom-in").disabled = !state?.canZoomIn;
    $("chart-zoom-out").disabled = !state?.canZoomOut;
    const slider = $("chart-position-slider");
    slider.disabled = !state || state.maxStart < 0.01;
    slider.max = String(state?.maxStart ?? 0);
    slider.value = String(state?.from ?? 0);
    if (!state) {
        $("chart-position-label").textContent = "等待行情数据";
        slider.removeAttribute("aria-valuetext");
        return;
    }
    const first = bars[state.firstIndex]?.time ?? "—";
    const last = bars[state.lastIndex]?.time ?? "—";
    const progress = state.maxStart ? Math.round((state.from / state.maxStart) * 100) : 100;
    $("chart-position-label").textContent = `${first} — ${last} · ${progress}%`;
    slider.setAttribute("aria-valuetext", `${first} 至 ${last}，全程 ${progress}%`);
}
const chart = new PriceChart(
    $("price-chart"),
    describeBar,
    showAnnotationDetails,
    renderVisibleAnnotations,
    (bar, button) => copyHoveredCandle(bar, button),
    renderChartViewport,
);
$("chart-pan-left").addEventListener("click", () => chart.pan(-1));
$("chart-pan-right").addEventListener("click", () => chart.pan(1));
$("chart-zoom-in").addEventListener("click", () => chart.zoom("in"));
$("chart-zoom-out").addEventListener("click", () => chart.zoom("out"));
$("chart-position-slider").addEventListener("input", (event) => chart.seek(Number(event.currentTarget.value)));
$("chart-position-slider").addEventListener("keydown", (event) => {
    const state = chart.navigationState();
    if (!state) return;
    const position = chartNavigationKeyPosition(state, event.key);
    if (position === null) return;
    event.preventDefault();
    chart.seek(position);
});
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
    const v3 = $("variant-select").value.startsWith("lecture_v3");
    $("shallow-base-breakout-field").hidden = !v3;
    $("backtest-shallow-base-breakout").disabled = !v3;
}
function canBacktestSymbol(symbol) {
    const source = sourceForScope($("result-scope").value);
    return source ? catalogHasSymbol(state[source], symbol) : false;
}
function chooseSymbol(symbol, workspace = false, autoBacktest = true) {
    if (!universe().some((s) => s.symbol === symbol && s.has_data !== false)) return;
    const navigation = ++state.symbolNavigation;
    const previousScope = $("result-scope").value;
    const source = sourceForScope(previousScope);
    const completedMember =
        source && watchlistBacktests.hasCompleted(symbol, source)
            ? watchlistBacktests.members.find((member) => member.symbol === symbol)
            : null;
    const cutoff = completedMember?.asof || state.requestedAsOf || state.view?.asof || currentRun().end;
    $("symbol-select").value = symbol;
    state.pendingFocus = null;
    const canBacktest = canBacktestSymbol(symbol);
    const historical =
        watchlistBacktests.statuses.get(symbol) === "historical" ||
        watchlists.backtestStatuses[symbol] === "historical";
    const {
        run: runBacktest,
        force: refreshBacktest,
        direct,
    } = selectedBacktestAction({
        historical,
        canBacktest,
        autoBacktest,
        completed: Boolean(completedMember),
    });
    const resumeBacktest =
        !historical &&
        Boolean(source) &&
        (Boolean(completedMember) ||
            [...stockBacktestTasks.tasks.values()].some(
                (task) => task.symbol === symbol && task.path === `/api/${source}-backtest`,
            ));
    if (runBacktest) {
        $("result-scope").value = source;
        fillSymbols();
    } else if (previousScope.endsWith("-backtest") && source) {
        $("result-scope").value = source;
        fillSymbols();
    } else {
        syncSymbolCopy();
        watchlists.setSelected(symbol);
    }
    preserveCutoff(cutoff);
    if (workspace) showPage("workspace");
    watchlistBacktests.start();
    $("stock-picker-feedback").hidden = (!autoBacktest && !historical) || Boolean(canBacktest) || !source;
    $("stock-picker-feedback").textContent =
        (!autoBacktest && !historical) || !source
            ? ""
            : historical
              ? `${symbolName(symbol)} 的旧回测结果已过期，且暂无日线可更新。`
              : canBacktest
                ? ""
                : `${symbolName(symbol)} 暂无${source === "akshare" ? "AkShare" : "通达信"}日线，保留当前行情查看，未运行回测。`;
    if (!runBacktest && !resumeBacktest) {
        loadView();
        return;
    }
    const showBacktest = () => {
        if (navigation !== state.symbolNavigation || $("symbol-select").value !== symbol) return;
        $("result-scope").value = `${source}-backtest`;
        fillSymbols();
        preserveCutoff(cutoff);
        void loadView({ preferTrades: true, forceBacktest: refreshBacktest });
    };
    if (direct) {
        showBacktest();
        return;
    }
    // Show this stock's ordinary chart first; its strategy calculation then runs beside it.
    void loadView().then(() => {
        if ($("result-scope").value === source) showBacktest();
    });
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
            `${symbolName(state.view.symbol)} · 独立回测 ${bt.start} — ${bt.end}｜量能过滤${bt.strategy.volume_filter ? (bt.strategy.buy_point_definition === "whole_flip_wave_v3" ? "开启（确认时量 ＞ 昨日全天量；C 浪跳空突破可独立触发）" : `开启（攻击日量比 ≥ ${num(bt.strategy.minimum_rvol)}）`) : "关闭"}；${bt.strategy.buy_point_definition === "whole_flip_wave_v3" ? `浅回撤横盘突破${bt.strategy.shallow_base_breakout_enabled ? "开启" : "关闭"}；` : ""}成交价含费净盈亏比过滤${netRiskEnabled ? "开启" : "关闭"}；初始资金 ${num(bt.initial_capital, 0)} 元，单股仓位上限 ${pct(bt.execution.max_position_weight)}。年化 ${pct(m.annualized_return)} · 胜率 ${m.win_rate === null ? "—（无平仓）" : pct(m.win_rate)} · Sharpe ${num(m.sharpe)} · 费用 ${num(m.fees)} 元。买入成交 ${d.entry_fills} · 已平仓 ${d.closed_trades} · 未平仓 ${d.open_positions} · 期末未执行信号 ${m.unexecuted_end_signals}。${d.entry_fills ? "成交样本不等于策略有效。" : `未产生成交：入场信号 ${bt.counts.long_signals || 0}，委托尝试 ${d.entry_attempts}；可开启“筛选 / 中断”查看未通过条件。`}${reasons ? `拒单原因：${reasons}。` : ""}${bt.open_positions.map((p) => `未平仓 ${num(p.quantity)} 等价份额，累计已实现 ${num(p.realized_pnl)} 元，剩余浮动盈亏 ${num(p.unrealized_pnl)} 元，整笔当前盈亏 ${num(p.total_pnl)} 元（${pct(p.net_return)}，含未实现部分）。`).join("")}`;
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
            p.textContent += bt.strategy.shallow_base_breakout_enabled
                ? " 独立买点：0.618 至不足 2/3 的来源级回撤低点先列待选；守低整理 40–120 根、近 40 根区间波幅不超过 18%，大阳线收盘突破整个整理区间且量至少为前 20 日均量的 2 倍，最近已确认高点的毛盈亏比至少 1.5 才触发。不把待选低点提前标成正式交替。"
                : " 浅回撤横盘突破独立买点已关闭。";
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
    if (item.reason && item.side !== "BUY" && item.side !== "SELL") {
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
    if (Number.isFinite(marker.position_weight_after_fill)) return marker.position_weight_after_fill;
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
        reason.textContent = `${buy ? (marker.add_on ? "加仓成交" : "买入成交") : marker.position_closed === false ? "减仓成交" : "卖出成交"}${open ? " · 尚未平仓" : ""}`;
        const details = document.createElement("span");
        details.className = "trade-node-details";
        details.textContent = `决定 ${marker.decision_timestamp || marker.signal_time || "—"}${marker.execution_model === "intraday_5m_next_open" ? ` · 成交 ${marker.timestamp}` : ""} · 原价 ${num(marker.raw_price)} 元 · ${num(marker.quantity)} 等价份额 · 费用 ${num(marker.fee)} 元`;
        content.append(heading, reason, details);
        const reasons = document.createElement("span");
        reasons.className = "trade-node-reasons";
        reasons.textContent = `${buy ? "买入" : "卖出"}原因：\n${numberedTradeReasons(marker).join("\n")}`;
        content.append(reasons);
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
            position.title = Number.isFinite(marker.position_weight_after_fill)
                ? "该股票成交后总持仓市值 ÷ 成交后账户权益；不是全回测期间的日均仓位"
                : "该笔买入成交额（不含费用）÷ 成交后账户权益；旧回测未记录加仓后总仓位";
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
        if (
            !state.pendingFocus &&
            !state.selectedAnnotationId &&
            !(state.view?.backtest && !state.view.orders.some((order) => order.status === "filled"))
        )
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
async function loadView({ focusLatestFill = false, preferTrades = focusLatestFill, forceBacktest = false } = {}) {
    state.lastResolvedScope = $("result-scope").value;
    syncSymbolCopy();
    syncProfileScope();
    const backtestMode = isTdxBacktest();
    const hasRenderedView = Boolean(state.view && state.view.symbol === $("symbol-select").value);
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
    $("loading").hidden = backtestMode;
    $("loading").textContent = isTdxBacktest()
        ? "正在校验除权数据、运行策略并生成成交账本…"
        : isAkShare()
          ? `正在读取 AkShare ${timeframes[activeTimeframe()].label}预计算快照…`
          : isTdx()
            ? `正在读取通达信${timeframes[activeTimeframe()].label}预计算快照…`
            : "读取已封存行情与交易记录…";
    $("chart-loading-overlay").hidden = backtestMode;
    $("price-chart").setAttribute("aria-busy", String(!backtestMode));
    if (!hasRenderedView) document.querySelector(".metric-grid").hidden = true;
    showPage(state.page);
    syncDate();
    const request = select();
    const updateBacktestProgress = (message, running = true) => {
        if (sequence !== state.sequence) return;
        const summary = $("selected-stock-summary");
        const description = `${symbolName(request.symbol)} · ${message}${running ? " 切换股票后仍会继续计算。" : ""}`;
        summary.dataset.backtestStatus = running ? "running" : "loading";
        if (summary.textContent !== description) summary.textContent = description;
    };
    if (focusLatestFill) $("show-fills").checked = true;
    state.requestedAsOf = request.asof;
    buyPoints.contextChanged();
    structureSignals.contextChanged();
    ratioComparison.contextChanged();
    stockList.setSelected(request.symbol);
    watchlists.setSelected(request.symbol);
    if (backtestMode) {
        const completed = watchlistBacktests.hasCompleted(request.symbol, sourceForScope($("result-scope").value));
        updateBacktestProgress(
            completed ? "正在读取已完成的回测结果…" : "当前股票回测运行中，正在生成成交账本…",
            !completed,
        );
    } else {
        state.activeBacktestTask = null;
        renderStockBacktestStatus();
        delete $("selected-stock-summary").dataset.backtestStatus;
        $("selected-stock-summary").textContent = `${symbolName(request.symbol)} · 正在读取 ${request.asof} 截面…`;
    }
    const requestSignal = state.controller.signal;
    let completedResultRequest = false;
    let reusedCompletedResult = false;
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
            shallow_base_breakout_enabled: String($("backtest-shallow-base-breakout").checked),
            ...(isTdxBacktest() ? currentBacktestSizing() : {}),
        };
        const backtestPath = isAkShare() ? "/api/akshare-backtest" : "/api/tdx-backtest";
        if (backtestMode && !watchlistBacktests.strategyVersion) {
            await watchlistBacktests.ensureVersion();
            if (sequence !== state.sequence) return;
        }
        const version = watchlistBacktests.strategyVersion || "";
        if (
            backtestMode &&
            !forceBacktest &&
            !stockBacktestTasks.find(backtestPath, backtestParams, version) &&
            !watchlistBacktests.hasCompleted(request.symbol, sourceForScope($("result-scope").value))
        ) {
            await refreshServerBacktestStatuses();
            if (sequence !== state.sequence) return;
        }
        const completedJobId =
            !forceBacktest && watchlistBacktests.hasCompleted(request.symbol, sourceForScope($("result-scope").value))
                ? watchlistBacktests.matchingJobId(backtestPath, backtestParams)
                : null;
        reusedCompletedResult = Boolean(completedJobId);
        const cachedTask = completedJobId && stockBacktestTasks.find(backtestPath, backtestParams, version);
        completedResultRequest = Boolean(completedJobId && !["running", "completed"].includes(cachedTask?.status));
        const task = !backtestMode
            ? null
            : completedJobId
              ? await reuseCompletedBacktest({
                    tasks: stockBacktestTasks,
                    path: backtestPath,
                    params: backtestParams,
                    version,
                    jobId: completedJobId,
                    fetchJob: (job) =>
                        retryBacktest(() => api("/api/backtest-job", { job }, requestSignal), {
                            signal: requestSignal,
                            delays: [500, 1_000, 2_000],
                        }),
                })
              : stockBacktestTasks.start(backtestPath, backtestParams, {
                    version,
                    force: forceBacktest,
                    jobId: forceBacktest
                        ? undefined
                        : watchlistBacktests.matchingJobId(backtestPath, backtestParams) || undefined,
                });
        if (task) {
            state.activeBacktestTask = task;
            updateBacktestProgress(
                task.status === "running" ? task.message : "正在读取已完成的回测结果…",
                task.status === "running",
            );
            renderStockBacktestStatus();
        }
        const data = isMarketBrowse()
            ? (timeframeBundle = await loadMarketTimeframeSnapshot(
                  isAkShare() ? "akshare" : "tdx",
                  request.symbol,
                  request.asof,
                  request.timeframe,
              )).view
            : task
              ? await task.promise
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
        renderRunStockBacktestButton();
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
        if (backtestMode) $("selected-stock-summary").dataset.backtestStatus = "completed";
        else delete $("selected-stock-summary").dataset.backtestStatus;
        $("selected-stock-summary").textContent =
            `${backtestMode ? "当前股票回测已完成 · " : ""}${symbolName(data.symbol)} · ${data.asof} ${data.timeframe_label || "日线"}截面｜原始收盘 ${num(last.raw_close)} 元 · 周期成交量 ${num(last.volume, 0)} 股。${sourceSummary}${cacheSummary}${data.is_partial_last_bar ? " 当前最后一根周期 K 线尚未收完。" : ""}`;
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
        } else if (backtestMode && !(data.orders || []).some((order) => order.status === "filled")) {
            detail("本次回测没有模拟成交", "回测计算已完成，但成交账本没有买卖记录，因此图上没有 B / S 成交标记。");
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
        if (
            backtestMode &&
            completedResultRequest &&
            (error.httpStatus === 404 || error.code === "BACKTEST_COMPLETED_RESULT_UNAVAILABLE")
        ) {
            watchlistBacktests.markResultUnavailable(request.symbol);
            state.loading = false;
            state.activeBacktestTask = null;
            $("result-scope").value = sourceForScope($("result-scope").value);
            fillSymbols();
            preserveCutoff(request.asof);
            $("loading").hidden = true;
            $("chart-loading-overlay").hidden = true;
            $("price-chart").setAttribute("aria-busy", "false");
            $("selected-stock-summary").textContent =
                `${symbolName(request.symbol)} · 旧回测结果已过期，可手动重新运行。`;
            delete $("selected-stock-summary").dataset.backtestStatus;
            $("stock-picker-feedback").hidden = false;
            $("stock-picker-feedback").textContent = "旧回测结果已过期；点击回测按钮可重新计算。";
            renderRunStockBacktestButton();
            void loadView();
            return;
        }
        state.loading = false;
        state.error = true;
        $("loading").hidden = true;
        $("chart-loading-overlay").hidden = true;
        $("error").hidden = false;
        const errorHint = backtestMode
            ? reusedCompletedResult
                ? "已完成的任务仍可重试读取，无需重新计算。"
                : "当前股票视图仍可操作，请调整回测设置或重新运行。"
            : "旧图已隐藏，请检查结果文件后重试。";
        const errorMessage = `加载失败：${error.message}。${errorHint}`;
        $("error").replaceChildren(document.createTextNode(errorMessage));
        const copyError = document.createElement("button");
        copyError.type = "button";
        copyError.className = "backtest-error-copy-button";
        copyError.textContent = "复制";
        copyError.setAttribute("aria-label", "复制加载失败信息");
        copyError.addEventListener("click", async () => {
            const copied = await writeClipboardText(errorMessage, copyError);
            copyError.textContent = copied ? "已复制" : "复制失败，请重试";
            copyError.setAttribute("aria-label", copied ? "加载失败信息已复制" : "复制加载失败信息失败，请重试");
        });
        $("error").append(copyError);
        if (backtestMode) {
            const retry = document.createElement("button");
            retry.type = "button";
            retry.className = "backtest-retry-button";
            retry.textContent = reusedCompletedResult ? "重试读取已完成回测" : "重新运行当前股票回测";
            retry.addEventListener("click", () => loadView({ forceBacktest: !reusedCompletedResult }));
            $("error").append(retry);
        }
        if (backtestMode) $("selected-stock-summary").dataset.backtestStatus = "failed";
        else delete $("selected-stock-summary").dataset.backtestStatus;
        const failureSummary = backtestMode
            ? reusedCompletedResult
                ? "可重试读取该任务。"
                : "可调整设置或重新运行。"
            : "未展示旧股票数据，可重新选择或刷新。";
        $("selected-stock-summary").textContent =
            `${symbolName(request.symbol)} · ${backtestMode ? (reusedCompletedResult ? "回测结果加载失败" : "回测失败") : "加载失败"}：${error.message}；${failureSummary}`;
        $("price-chart").setAttribute("aria-busy", "false");
        if (!backtestMode || !hasRenderedView) {
            document.querySelectorAll(".page").forEach((p) => (p.hidden = true));
            document.querySelector(".metric-grid").hidden = true;
            $("stock-results").hidden = true;
        }
        renderRunStockBacktestButton();
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
            shallow_base_breakout_enabled: $("backtest-shallow-base-breakout").checked,
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
const autoBacktestPreferenceKey = "wavequant.watchlists.auto-backtest.v1";
const sharedBacktestCompletionPrefix = "wavequant.watchlists.backtest-done.v2:";
const sharedBacktestCompletionTtlMs = 60_000;
let lastSharedBacktestPrune = 0;
let lastWorkbenchInteraction = Date.now();
for (const eventName of ["pointerdown", "keydown", "input", "wheel"]) {
    document.addEventListener(eventName, () => (lastWorkbenchInteraction = Date.now()), { passive: true });
}
const watchlistBacktests = new IdleWatchlistBacktests({
    serverJobs: () => serverBacktestSnapshot.jobs,
    hasCapacity: () =>
        !serverBacktestSnapshot.unavailable && serverBacktestSnapshot.active < serverBacktestSnapshot.max_active,
    onEngineChanged: () => {
        try {
            localStorage.setItem(autoBacktestPreferenceKey, "false");
        } catch {
            // 当前页面的自动队列仍会暂停。
        }
    },
    snapshot: () => {
        if (!state.catalog || !watchlists.available) return null;
        const source = sourceForScope($("result-scope").value);
        if (!source) return null;
        const request = select();
        const sourceCatalog = state[source];
        const sourceUniverse = sourceCatalog?.stocks;
        if (!sourceUniverse?.length || !sourceCatalog.latest || !request.run || !request.variant) return null;
        const start = $("backtest-start").value;
        if (!start || start > sourceCatalog.latest) return null;
        let sizing;
        try {
            sizing = currentBacktestSizing();
        } catch {
            return null;
        }
        const context = {
            run: request.run,
            variant: request.variant,
            scenario: request.scenario,
            source,
            group: watchlists.selectedGroup.id,
            cutoff: sourceCatalog.latest,
            start,
            volume_filter: String($("backtest-volume-filter").checked),
            net_reward_risk_filter: String($("backtest-net-reward-risk-filter").checked),
            shallow_base_breakout_enabled: String($("backtest-shallow-base-breakout").checked),
            ...sizing,
        };
        const members = watchlists.orderedAvailableMembers(sourceUniverse).map((member) => ({
            symbol: member.symbol,
            name: member.stock.name || member.name || member.symbol,
            asof: member.stock.last || sourceCatalog.latest,
        }));
        return { context, members };
    },
    version: ({ context }) => api("/api/backtest-version", { run: context.run, variant: context.variant }),
    run: async (member, { context }, active) => {
        const { path, params: requestParams } = watchlistBacktestRequest(member, context);
        const params = { ...requestParams, backtest_job: crypto.randomUUID() };
        const catalogEtag = state[context.source]?.catalog_etag || "";
        const completionKey =
            sharedBacktestCompletionPrefix +
            JSON.stringify([watchlistBacktests.strategyVersion, catalogEtag, backtestArgumentsKey(path, params)]);
        const signal = active.controller.signal;
        const execute = async () => {
            active.queued = false;
            watchlistBacktests.emit();
            try {
                const now = Date.now();
                if (now - lastSharedBacktestPrune >= sharedBacktestCompletionTtlMs) {
                    for (let index = localStorage.length - 1; index >= 0; index--) {
                        const key = localStorage.key(index);
                        if (
                            key?.startsWith(sharedBacktestCompletionPrefix) &&
                            now - Number(localStorage.getItem(key)?.split(":")[0]) > sharedBacktestCompletionTtlMs
                        )
                            localStorage.removeItem(key);
                    }
                    lastSharedBacktestPrune = now;
                }
                const [completedAt, sharedJobId] = (localStorage.getItem(completionKey) || "").split(":");
                if (sharedJobId && now - Number(completedAt) <= sharedBacktestCompletionTtlMs) {
                    const shared = await api("/api/backtest-job", { job: sharedJobId }, signal);
                    if (
                        shared.status === "completed" &&
                        shared.result?.symbol === member.symbol &&
                        shared.result.result_scope === "stock" &&
                        shared.result.backtest
                    ) {
                        active.job = { path, params: { ...params, backtest_job: sharedJobId } };
                        return shared.result;
                    }
                }
            } catch {
                // 共享任务不可读取时重新计算，不能只凭完成标记声称图表已有结果。
            }
            active.job = { path, params };
            watchlistBacktests.emit();
            const request = () => api(path, params, signal);
            let result;
            try {
                result = await retryBacktest(request, {
                    signal,
                    onTimeout: () =>
                        waitForBacktestJob(
                            () => api("/api/backtest-job", { job: params.backtest_job }, signal),
                            request,
                            { signal },
                        ),
                });
            } catch (error) {
                if (error.code !== "BACKTEST_SYMBOL_RUNNING" || !error.same_request || !error.running_job) throw error;
                params.backtest_job = error.running_job;
                active.job = { path, params };
                result = await waitForBacktestJob(
                    () => api("/api/backtest-job", { job: error.running_job }, signal),
                    request,
                    { signal, maxMissingResubmits: 0 },
                );
            }
            if (
                result?.symbol === member.symbol &&
                result?.result_scope === "stock" &&
                result?.backtest &&
                result.backtest.status !== "data_unavailable"
            ) {
                try {
                    localStorage.setItem(completionKey, `${Date.now()}:${params.backtest_job}`);
                } catch {
                    // 进度仍保留在当前页面内存中。
                }
            }
            return result;
        };
        if (!navigator.locks?.request) return execute();
        active.queued = true;
        watchlistBacktests.emit();
        return navigator.locks.request("wavequant-watchlist-auto-backtest", { signal }, execute);
    },
    isIdle: () =>
        document.visibilityState === "visible" &&
        !state.loading &&
        !ratioComparison.running &&
        Date.now() - lastWorkbenchInteraction >= 5_000,
    onCompleted: (result, active) => {
        const job = active.job;
        const source = watchlistBacktests.context?.source;
        if (
            !job ||
            !source ||
            state.loading ||
            state.view?.symbol !== result.symbol ||
            state.view.asof !== result.asof ||
            $("symbol-select").value !== result.symbol ||
            $("result-scope").value !== source
        )
            return;
        stockBacktestTasks.adoptCompleted(job.path, job.params, result, {
            version: watchlistBacktests.strategyVersion,
            jobId: job.params.backtest_job,
        });
        $("result-scope").value = `${source}-backtest`;
        fillSymbols();
        preserveCutoff(result.asof);
        void loadView({ preferTrades: true });
    },
    onChange: ({
        enabled,
        ready,
        version,
        source,
        checking,
        idle,
        active,
        queued,
        draining,
        statuses,
        fillCounts,
        returns,
        failures,
        total,
        completed,
        failed,
        retrying,
        capacityFull,
        error,
    }) => {
        if (syncServerBacktestHistory()) return;
        if (syncCompletedStockBacktests()) return;
        const toggle = $("watchlist-auto-backtest-toggle");
        toggle.setAttribute("aria-pressed", String(enabled));
        toggle.textContent = enabled ? "暂停" : "继续";
        $("watchlist-backtest-retry").disabled = !failed;
        const progress = $("watchlist-backtest-progress");
        progress.max = Math.max(1, total);
        progress.value = completed + failed;
        const status = $("watchlist-backtest-status");
        const activeIndex = active
            ? watchlistBacktests.members.findIndex((member) => member.symbol === active.symbol) + 1
            : 0;
        const sourceLabel = source === "akshare" ? "AkShare" : "通达信";
        const activeServerJob = serverBacktestSnapshot.jobs.find((job) => job.symbol === active?.symbol);
        const activeElapsed = formatBacktestElapsed(activeServerJob?.elapsed_seconds);
        status.textContent = !ready
            ? "等待可用行情和回测参数…"
            : draining
              ? "设置已更新，等待先前回测结束后按新设置继续…"
              : queued
                ? `其他页面正在自动回测，${active.name}（${active.symbol}）已排队…`
                : active
                  ? `${sourceLabel} ${activeIndex}/${total}：正在回测 ${active.name}（${active.symbol}）${activeElapsed ? `，${activeElapsed}` : ""}${serverBacktestSnapshot.unavailable ? "，服务器任务状态暂不可确认" : ""}；已完成 ${completed}，失败 ${failed}${enabled ? "" : "。完成当前股票后暂停"}`
                  : !enabled
                    ? `已暂停 · 已完成 ${completed}/${total}，失败 ${failed}`
                    : !total
                      ? "当前分类没有可回测的股票"
                      : error
                        ? `策略版本读取失败：${error}；稍后自动重试`
                        : version && completed + failed === total
                          ? retrying
                              ? `本轮已完成 ${completed}/${total}，失败 ${failed} 只待自动重试`
                              : failed
                                ? `本轮已结束：完成 ${completed}/${total}，失败 ${failed} 只；可点击重试失败`
                                : `自动回测已完成 ${completed}/${total}；等待策略更新`
                          : serverBacktestSnapshot.unavailable
                            ? "服务器任务状态暂不可确认，等待连接恢复…"
                            : capacityFull
                              ? `并行回测已满（${serverBacktestSnapshot.active}/${serverBacktestSnapshot.max_active}），等待空位…`
                              : !idle
                                ? `等待页面空闲 · 已完成 ${completed}/${total}，失败 ${failed}`
                                : checking || !version
                                  ? "正在核对策略版本…"
                                  : `准备按列表顺序回测 · 已完成 ${completed}/${total}，失败 ${failed}`;
        renderServerBacktestStatuses({ statuses, ready, failures, fillCounts, returns });
    },
});
function syncServerBacktestHistory() {
    return adoptServerBacktestHistory(watchlistBacktests, serverBacktestSnapshot.recent, stockBacktestTasks);
}
function syncCompletedStockBacktests() {
    watchlistBacktests.sync(watchlistBacktests.snapshot());
    if (!watchlistBacktests.strategyVersion) return false;
    for (const task of stockBacktestTasks.tasks.values()) {
        if (task.status !== "completed" || watchlistBacktests.statuses.get(task.symbol) === "completed") continue;
        if (watchlistBacktests.adoptCompleted(task.path, task.params, task.result, task.version)) return true;
    }
    return false;
}
function renderServerBacktestStatuses(queueState = watchlistBacktests.state()) {
    const { statuses, ready, failures, fillCounts, returns } = queueState;
    const visibleStatuses = runningBacktestStatuses(
        historicalBacktestStatuses(statuses, serverBacktestSnapshot.recent),
        serverBacktestSnapshot.jobs,
        { unavailable: serverBacktestSnapshot.unavailable },
    );
    const visibleFailures = { ...failures };
    if (watchlistBacktests.context && watchlistBacktests.strategyVersion) {
        for (const member of watchlistBacktests.members) {
            const expected = watchlistBacktestRequest(member, watchlistBacktests.context);
            const task = stockBacktestTasks.find(expected.path, expected.params, watchlistBacktests.strategyVersion);
            if (task?.status === "running")
                visibleStatuses[member.symbol] = serverBacktestSnapshot.unavailable ? "unknown" : "running";
            else if (task?.status === "failed") {
                visibleStatuses[member.symbol] = "failed";
                visibleFailures[member.symbol] = task.error?.message || "回测失败";
            }
        }
    }
    watchlists.setBacktestStatuses(
        visibleStatuses,
        ready ? new Set(watchlistBacktests.members.map((member) => member.symbol)) : null,
        visibleFailures,
        fillCounts,
        returns,
        serverBacktestSnapshot.jobs,
    );
    renderStockBacktestStatus();
}
let serverBacktestStatusRequest = null;
async function refreshServerBacktestStatuses() {
    if (serverBacktestStatusRequest) return serverBacktestStatusRequest;
    serverBacktestStatusRequest = api("/api/backtest-jobs")
        .then((snapshot) => {
            if (!Array.isArray(snapshot.jobs)) throw new Error("回测任务状态格式异常");
            serverBacktestSnapshot = snapshot;
            serverBacktestSnapshotSeenAt = Date.now();
            syncServerBacktestHistory();
            renderServerBacktestStatuses();
            watchlistBacktests.emit();
        })
        .catch(() => {
            const expired = expiredBacktestSnapshot(serverBacktestSnapshot, serverBacktestSnapshotSeenAt);
            if (expired === serverBacktestSnapshot || serverBacktestSnapshot.unavailable) return;
            serverBacktestSnapshot = expired;
            renderServerBacktestStatuses();
            watchlistBacktests.emit();
        })
        .finally(() => {
            serverBacktestStatusRequest = null;
        });
    return serverBacktestStatusRequest;
}
setInterval(() => {
    if (document.visibilityState === "visible") void refreshServerBacktestStatuses();
}, 3_000);
void refreshServerBacktestStatuses();
try {
    localStorage.setItem(autoBacktestPreferenceKey, "false");
    watchlistBacktests.setEnabled(false);
} catch {
    watchlistBacktests.setEnabled(false);
}
$("watchlist-auto-backtest-toggle").addEventListener("click", () => {
    watchlistBacktests.setEnabled(!watchlistBacktests.enabled);
    try {
        localStorage.setItem(autoBacktestPreferenceKey, String(watchlistBacktests.enabled));
    } catch {
        // 当前会话内的暂停状态仍然有效。
    }
});
window.addEventListener("storage", (event) => {
    if (event.key === autoBacktestPreferenceKey) watchlistBacktests.setEnabled(event.newValue !== "false");
});
$("watchlist-backtest-retry").addEventListener("click", () => watchlistBacktests.retryFailed());
document.addEventListener("visibilitychange", () => {
    lastWorkbenchInteraction = Date.now();
    if (document.visibilityState === "visible") void refreshServerBacktestStatuses();
    void watchlistBacktests.tick();
});
window.addEventListener("wavequant:watchlists-changed", () => void watchlistBacktests.tick());
$("backtest-start").addEventListener("change", () => {
    buyPoints.contextChanged();
    structureSignals.contextChanged();
    ratioComparison.contextChanged();
    if (isTdxBacktest()) loadView();
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
$("backtest-shallow-base-breakout").addEventListener("change", () => {
    ratioComparison.contextChanged();
    buyPoints.contextChanged();
    if (isTdxBacktest()) loadView();
});
$("run-stock-backtest").addEventListener("click", () => {
    const source = sourceForScope($("result-scope").value);
    if (!source || !canBacktestSymbol($("symbol-select").value)) return;
    state.symbolNavigation++;
    const cutoff = state.requestedAsOf || state.view?.asof || state[source].latest;
    $("result-scope").value = `${source}-backtest`;
    state.pendingFocus = null;
    fillSymbols();
    preserveCutoff(cutoff);
    const status = $("run-stock-backtest").dataset.status;
    const showingCompletedResult =
        isTdxBacktest() &&
        !state.error &&
        state.activeBacktestTask?.status === "completed" &&
        state.activeBacktestTask.result === state.view;
    const action = selectedBacktestButton(status, showingCompletedResult);
    loadView({ focusLatestFill: true, forceBacktest: action.force });
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
$("result-scope").addEventListener("change", async () => {
    const targetScope = $("result-scope").value;
    const source = sourceForScope(targetScope);
    if (source && !loadedCatalogs.has(source)) {
        $("result-scope").value = state.lastResolvedScope || "stock";
        $("result-scope").disabled = true;
        $("stock-source-notice").textContent = `正在读取${source === "tdx" ? "通达信" : "AkShare"}股票目录…`;
        try {
            const catalog = await ensureSourceCatalog(source);
            if (!catalog.with_daily) {
                $("stock-source-notice").textContent =
                    `${source === "tdx" ? "通达信" : "AkShare"}股票目录暂无可用行情，请稍后重新选择。`;
                return;
            }
        } catch (error) {
            $("stock-source-notice").textContent = `股票目录加载失败：${error.message}；请重新选择重试。`;
            return;
        } finally {
            $("result-scope").disabled = false;
        }
        $("result-scope").value = targetScope;
    }
    state.lastResolvedScope = targetScope;
    state.pendingFocus = null;
    fillSymbols();
    lastWorkbenchInteraction = Date.now();
    void watchlistBacktests.tick();
    loadView();
    watchlistBacktests.start();
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
        const initialSource = requestedStock?.source || "akshare";
        $("result-scope").value = initialSource;
        $("result-scope").disabled = true;
        syncSourceOptions();
        [state.catalog] = await Promise.all([
            api("/api/catalog"),
            ensureSourceCatalog(initialSource).catch((error) => {
                state[initialSource] = { available: false, stocks: [], with_daily: 0, error: error.message };
            }),
        ]);
        await watchlists.init();
        $("result-scope").disabled = false;
        syncSourceOptions();
        state.lastResolvedScope = initialSource;
        $("run-select").replaceChildren();
        for (const r of state.catalog.runs) option($("run-select"), r.id, r.id.replace("acceptance_", ""));
        let sourceError = !state[initialSource].with_daily
            ? `${initialSource === "akshare" ? "AkShare" : "通达信"}目录暂无可用行情，请重新选择或稍后重试。`
            : "";
        let linkedStock = null;
        if (!sourceError && requestedStock) {
            try {
                linkedStock = resolveResearchLink(requestedStock, { [initialSource]: state[initialSource] });
            } catch (error) {
                sourceError = error.message;
            }
        }
        const firstWatchlistSymbol = watchlists.firstAvailableSymbol(universe());
        $("symbol-select").value = linkedStock?.symbol || firstWatchlistSymbol;
        if (linkedStock) setTimeframe("1d");
        fillSymbols();
        if (sourceError) {
            $("loading").hidden = true;
            $("error").hidden = false;
            $("error").textContent = sourceError;
            $("stock-source-notice").textContent = sourceError;
            $("selected-stock-summary").textContent = sourceError;
            if (requestedPage && Object.hasOwn(titles, requestedPage)) showPage(requestedPage);
            return;
        }
        if (linkedStock?.asof) preserveCutoff(linkedStock.asof);
        await loadView();
        if (linkedStock) {
            const notices = [];
            if (linkedStock.asof && state.view?.symbol === linkedStock.symbol && state.view.asof !== linkedStock.asof)
                notices.push(`请求日期 ${linkedStock.asof}，实际可用行情截至 ${state.view.asof}。`);
            $("stock-picker-feedback").hidden = notices.length === 0;
            $("stock-picker-feedback").textContent = notices.join(" ");
        }
        if (requestedPage && Object.hasOwn(titles, requestedPage)) showPage(requestedPage);
        watchlistBacktests.start();
        if (
            !linkedStock &&
            firstWatchlistSymbol &&
            $("symbol-select").value === firstWatchlistSymbol &&
            (state.view?.symbol === firstWatchlistSymbol || state.error)
        ) {
            const cutoff = state.requestedAsOf || state.view?.asof || state[initialSource].latest;
            $("result-scope").value = `${initialSource}-backtest`;
            fillSymbols();
            preserveCutoff(cutoff);
            void loadView({ preferTrades: true });
        }
    } catch (e) {
        $("result-scope").disabled = false;
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
