import { reasonText } from "./annotations.js";
import { BuyPoints } from "./buy-points.js";
import { PerformanceCharts, PriceChart } from "./charts.js";
import { label, names, num, pct, symbolName } from "./labels.js";
import { RatioComparison, ratioPlans } from "./ratio-comparison.js";
import { StockList } from "./stock-list.js";
import { appendTradeEvidence } from "./trade-review.js";

const $ = (id) => document.getElementById(id);
const chartPreferenceKey = "wavequant.research.chart.v1";
let chartPreferences = {};
try {
    const saved = JSON.parse(localStorage.getItem(chartPreferenceKey) || "null");
    if (saved && typeof saved === "object" && !Array.isArray(saved)) chartPreferences = saved;
} catch {
    // 损坏或不可用的本地偏好不应阻断研究工作台启动。
}
if (typeof chartPreferences.showTrendPrices === "boolean")
    $("show-trend-prices").checked = chartPreferences.showTrendPrices;
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
};
state.tdxSessions = {};
const isTdx = () => $("result-scope").value === "tdx";
const isTdxBacktest = () => $("result-scope").value === "tdx-backtest";
const isLocal = () => isTdx() || isTdxBacktest();
function universe() {
    return isLocal() ? state.tdx?.stocks || [] : currentRun().symbols;
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
        variant: $("variant-select").value,
        scenario: $("scenario-select").value,
        symbol: $("symbol-select").value,
        asof: state.noSessionBefore || sessions()[Number($("replay-slider").value)],
    };
}
function currentRun() {
    return state.catalog.runs.find((r) => r.id === $("run-select").value);
}
function sessions() {
    const symbol = $("symbol-select").value;
    return isLocal()
        ? state.tdxSessions[symbol] || [universe().find((s) => s.symbol === symbol)?.last].filter(Boolean)
        : currentRun()?.symbols.find((s) => s.symbol === symbol)?.sessions || [];
}
async function api(path, params = {}, signal, method = "GET") {
    const timeout = AbortSignal.timeout(path === "/api/stock-summary" ? 180000 : 45000);
    try {
        const response = await fetch(path + (method === "GET" ? "?" + new URLSearchParams(params) : ""), {
            method,
            ...(method === "POST"
                ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(params) }
                : {}),
            signal: signal ? AbortSignal.any([signal, timeout]) : timeout,
        });
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        return body;
    } catch (e) {
        if (e.name === "TimeoutError") throw new Error("请求超时，请稍后刷新重试");
        throw e;
    }
}
function showPage(page) {
    state.page = page;
    $("page-title").textContent = titles[page];
    document
        .querySelectorAll(".page")
        .forEach((el) => (el.hidden = state.loading || state.error || el.id !== `page-${page}`));
    $("stock-results").hidden =
        state.loading || state.error || page !== "workspace" || $("result-scope").value !== "stock";
    document.querySelectorAll("[data-page]").forEach((b) => b.classList.toggle("active", b.dataset.page === page));
    if (page === "performance") requestAnimationFrame(() => performance.resize());
    if (page === "health") loadHealth();
}
function describeBar(b) {
    if (!b) return;
    $("ohlc").textContent =
        `${b.time}　开 ${num(b.open)}　高 ${num(b.high)}　低 ${num(b.low)}　收 ${num(b.close)}　量 ${num(b.volume, 0)} 股　原始收盘 ${num(b.raw_close)} 元`;
}
const chart = new PriceChart($("price-chart"), describeBar, showAnnotationDetails, renderVisibleAnnotations);
chart.setTrendPriceLabelsVisible($("show-trend-prices").checked);
const performance = new PerformanceCharts(["equity-chart", "drawdown-chart", "exposure-chart"].map($));
const stockList = new StockList({
    list: $("stock-list"),
    search: $("stock-search"),
    count: $("stock-count"),
    clear: $("stock-search-clear"),
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
            source: isLocal() ? "tdx" : "snapshot",
            asof: p.asof,
            start: isLocal() ? $("backtest-start").value : currentRun().start,
        };
    },
    onSelect: async (match, p) => {
        $("result-scope").value = p.source === "tdx" ? "tdx-backtest" : "stock";
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
            const note = document.createElement("p");
            note.textContent = `买点筛选证据：盘态 ${match.regime}，相对量 ${num(match.rvol)}，回档比例 ${pct(match.retracement)}，收盘参考盈亏比 ${num(match.gross_reward_risk)}。次开盘成交尚需执行风控。`;
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
function fillSymbols() {
    const previous = $("symbol-select").value,
        stocks = universe(),
        available = stocks.filter((s) => s.has_data !== false);
    $("symbol-select").replaceChildren();
    available.forEach((s) => option($("symbol-select"), s.symbol, symbolName(s.symbol)));
    $("symbol-select").value = available.some((s) => s.symbol === previous)
        ? previous
        : available.some((s) => s.symbol === "sh.600519")
          ? "sh.600519"
          : available[0]?.symbol || "";
    resetSlider();
    stockList.setStocks(stocks, $("symbol-select").value);
    $("stock-source-notice").textContent = isLocal()
        ? `${state.tdx.with_daily} 只有本地日线 · 沪深北 A 股 · 只读`
        : "当前封存样本 · 非全市场";
    for (const id of ["run-select", "variant-select", "scenario-select"]) $(id).disabled = isTdx();
}
function syncProfileScope() {
    const portfolio = $("result-scope").value === "portfolio";
    const research = ["lecture_v1", "lecture_v2", ...ratioPlans.map((p) => p[0])];
    for (const v of research) $("variant-select").querySelector(`[value="${v}"]`).disabled = portfolio;
    if (portfolio && research.includes($("variant-select").value)) $("variant-select").value = "strict_full";
}
function chooseSymbol(symbol, workspace = false) {
    if (!universe().some((s) => s.symbol === symbol && s.has_data !== false)) return;
    const cutoff = state.requestedAsOf || state.view?.asof || currentRun().end;
    $("symbol-select").value = symbol;
    state.pendingFocus = null;
    preserveCutoff(cutoff);
    if (workspace) showPage("workspace");
    loadView();
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
    state.noSessionBefore = isLocal() && !state.tdxSessions[$("symbol-select").value] ? asof : i < 0 ? asof : null;
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
    if (state.view.result_scope === "tdx") {
        document.querySelector(".metric-grid").hidden = true;
        $("evidence").textContent = state.view.evidence;
        $("backtest-details").hidden = false;
        $("backtest-details").textContent =
            "当前为通达信全股票行情浏览，未运行个股回测。要查看已有回测，请切换“个股独立回测 · 封存样本”。";
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
    $("metric-scope-label").textContent = stock ? "个股净收益" : "组合净收益";
    $("trade-scope-label").textContent = stock ? "当前股票" : "全组合";
    $("curve-scope-label").textContent = stock ? "个股独立净值" : "原封存组合净值";
    $("orders-scope-label").textContent = stock ? "当前股票 / 截至回放日期" : "全组合 / 截至回放日期";
    document.querySelector(".metric-grid").setAttribute("aria-label", stock ? "个股回测指标" : "组合指标");
    $("backtest-details").hidden = !stock;
    if (stock) {
        const d = bt.diagnostics,
            reasons = Object.entries(d.rejection_reasons)
                .map(([key, n]) => `${reasonText(key)} × ${n}`)
                .join("；");
        $("backtest-details").textContent =
            `${symbolName(state.view.symbol)} · 独立回测 ${bt.start} — ${bt.end}｜初始资金 ${num(bt.initial_capital, 0)} 元，单股仓位上限 ${pct(bt.execution.max_position_weight)}。年化 ${pct(m.annualized_return)} · 胜率 ${m.win_rate === null ? "—（无平仓）" : pct(m.win_rate)} · Sharpe ${num(m.sharpe)} · 费用 ${num(m.fees)} 元。买入成交 ${d.entry_fills} · 已平仓 ${d.closed_trades} · 未平仓 ${d.open_positions} · 期末未执行信号 ${m.unexecuted_end_signals}。${d.entry_fills ? "成交样本不等于策略有效。" : `未产生成交：入场信号 ${bt.counts.long_signals || 0}，委托尝试 ${d.entry_attempts}；可开启“筛选 / 中断”查看未通过条件。`}${reasons ? `拒单原因：${reasons}。` : ""}${bt.open_positions.map((p) => `未平仓 ${num(p.quantity)} 等价份额，浮动盈亏 ${num(p.unrealized_pnl)} 元。`).join("")}`;
        if (state.view.strategy_profile?.id === "lecture_v1") {
            const detail = document.createElement("details"),
                summary = document.createElement("summary"),
                body = document.createElement("p");
            summary.textContent = "当前策略：讲义因果版 V1（查看生效规则）";
            body.textContent =
                "讲义折线收盘确认 → 收盘突破冻结末跌高 → 回档 < 2/3 完成交替 → 高低点抬高确认多头 → 新正 N → 轧空 / 强轧空或守住轧空低后的恢复。攻击棒相对量 ≥ 1.2、N 回档 < 2/3、最近未达目标收盘盈亏比 ≥ 1.5，次开盘含费再检验 ≥ 1.5。倒 N、末升低收盘跌破、未定义结构、止损 / 目标 / 持仓期限触发退出。仅做多；洗盘是辅助标签；一二三级实线不额外充当三个入场门槛。日线面板未接入次级周期扭转确认。完整参数与规则随回测 JSON 导出。";
            detail.append(summary, body);
            $("backtest-details").append(detail);
        }
        if (state.view.strategy_profile?.id === "lecture_v2") {
            const detail = document.createElement("details"),
                summary = document.createElement("summary"),
                body = document.createElement("p");
            summary.textContent = "当前策略：分级双买点 V2（第二类优先复核）";
            body.textContent =
                "一级及以上（1 / 2 / 3 级任一级）：第一类为翻空为多、确认更高回档低点完成空多交替 → 新正 N → 轧空 / 强轧空，不设 1/3 或 2/3 回撤过滤，但不允许跌破结构防守。第二类为交替已知后，后续收盘再次超过冻结的翻多高点 → 新上涨段回撤 < 1/3 → 新正 N → 轧空 / 强轧空。同一级成熟后不回退第一类。保留量比 ≥ 1.2、收盘和次开盘含费盈亏比 ≥ 1.5。只有收盘可知证据可用，B / S 仍为次开盘模拟成交。原画线与旧图形注释不改，V2 买点以成交 / 信号证据为准。";
            detail.append(summary, body);
            $("backtest-details").append(detail);
        }
        if (bt.strategy.buy_point_definition === "whole_flip_wave_v3") {
            const p = document.createElement("p");
            p.textContent = `整段双买点 V3：L0 为翻多上涨起始的整段最低点，H0 为翻多高点。第一类 (H0−交替低点)/(H0−L0) > ${pct(bt.strategy.first_pullback_threshold)}，不破 L0，再等正 N 轧空。第二类交替后收盘再破 H0，取已知阶段最高 H1，再回撤；(H1−回撤期间最低收盘)/(H1−L0) ≤ ${pct(bt.strategy.mature_shallow_ratio)}，再等正 N 轧空。第一类在 N 攻击时冻结类别；第二类优先。量能和执行风控仍有效。`;
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
        rules: $("show-rules").checked,
        diagnostics: $("show-diagnostics").checked,
        levels: $("show-levels").checked,
        trendKeys: $("show-last-fall-high").checked,
        bullFlipHighs: $("show-bear-to-bull-highs").checked,
    };
}
function showAnnotationDetails(items) {
    const item = items[0];
    state.selectedAnnotationId = item.id;
    detail(`${item.title} · ${item.time}`, item.description);
    const panel = $("selection-info");
    panel.dataset.annotationId = item.id;
    appendTradeEvidence(panel, item);
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
    price.textContent = `${item.kind === "fill" ? "实际成交价" : "当时参考价"}：${num(item.price)}（复权等价）`;
    panel.append(price);
    for (const level of item.levels) {
        const p = document.createElement("p");
        p.textContent = `${level.name}：${num(level.price)}`;
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
        time.textContent = `${item.time} · ${item.kind === "fill" ? "成交" : item.kind === "signal" ? "信号" : "规则可知"}`;
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
                actionCell(m.side === "BUY" ? "查看入场条件" : "查看退出原因", () => selectFill(m)),
            ]),
        );
    $("fills-empty").hidden = markers.length > 0;
    for (const t of [...v.trades].reverse())
        $("trades-body").append(
            row([
                symbolName(t.symbol),
                t.entry_time.slice(0, 10),
                t.exit_time.slice(0, 10),
                t.bars_held,
                cell(num(t.pnl), Number(t.pnl) > 0 ? "positive" : "negative"),
                num(t.fees),
                actionCell("复盘 ↗", () => locate(t.symbol, t.entry_time.slice(0, 10), `交易复核：${t.entry_reason}`)),
            ]),
        );
    $("trades-empty").hidden = v.trades.length > 0;
    $("trade-count").textContent = `${v.trades.length} 笔已平仓`;
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
async function loadTheory(request, sequence) {
    if (!$("show-theory").checked && !$("show-rules").checked) {
        $("theory-status").textContent = "规则与折线已关闭";
        return;
    }
    $("theory-status").textContent = "按历史截面计算…";
    try {
        const data = isTdxBacktest()
            ? state.view.theory
            : await api(
                  isTdx() ? "/api/tdx-theory" : "/api/theory",
                  isTdx()
                      ? { symbol: request.symbol, asof: request.asof }
                      : { run: request.run, variant: request.variant, symbol: request.symbol, asof: request.asof },
              );
        if (sequence !== state.sequence) return;
        state.theory = data;
        chart.setTheory(data, $("show-theory").checked);
        $("drawing-status").textContent =
            `讲义绘图：${data.lecture_drawing?.teaching_paths?.length || 0} 组子母三点、${data.lecture_drawing?.inside_connections?.length || 0} 处母子缩头／缩脚衔接；${data.lecture_drawing?.issues.length || 0} 处十字星／初始方向待确认。${data.strategy_pivot_mode === "lecture_causal" ? "新版从同一递推器提取收盘确认点；绘图连接不直接等于交易信号。" : "显示结构与所选旧策略／行情浏览独立。"}母子顺序是讲义约定，不代表已知真实日内路径。`;
        $("theory-status").textContent = data.interrupted ? "当前结构未解" : "已确认结构";
        if (!state.pendingFocus && !state.selectedAnnotationId)
            detail(
                data.interrupted ? "严格结构中断" : "点击标识查看规则",
                data.interrupted
                    ? "缺少次级路径，不把未解折线标成已确认形态。可开启“筛选 / 中断”查看具体日期。"
                    : "规则方块标在可知日期；信号圆点不等于成交。B / S 箭头标在实际成交价。点击标识可显示颈线、防守位与目标投影。",
            );
    } catch (error) {
        if (sequence !== state.sequence) return;
        $("theory-status").textContent = "标注不可用";
        detail("理论标注加载失败", error.message);
        renderEvents();
    }
}
async function loadView() {
    syncProfileScope();
    const sequence = ++state.sequence;
    state.controller?.abort();
    state.controller = new AbortController();
    state.summaryController?.abort();
    $("stock-results-body").replaceChildren();
    state.loading = true;
    state.error = false;
    state.theory = null;
    state.selectedAnnotationId = null;
    $("download-backtest").disabled = true;
    $("run-stock-backtest").disabled = true;
    $("error").hidden = true;
    $("loading").hidden = false;
    $("loading").textContent = isTdxBacktest()
        ? "正在校验除权数据、运行策略并生成成交账本…"
        : isTdx()
          ? "读取通达信本地日线…"
          : "读取已封存行情与交易记录…";
    $("price-chart").setAttribute("aria-busy", "true");
    document.querySelector(".metric-grid").hidden = true;
    showPage(state.page);
    syncDate();
    const request = select();
    state.requestedAsOf = request.asof;
    buyPoints.contextChanged();
    ratioComparison.contextChanged();
    for (const field of ["run-select", "variant-select", "scenario-select"])
        $(field).disabled = isTdx() && $("buy-points-panel").hidden;
    stockList.setSelected(request.symbol);
    $("selected-stock-summary").textContent = `${symbolName(request.symbol)} · 正在读取 ${request.asof} 截面…`;
    try {
        const data = await api(
            isTdxBacktest()
                ? "/api/tdx-backtest"
                : isTdx()
                  ? "/api/tdx-view"
                  : $("result-scope").value === "stock"
                    ? "/api/stock-view"
                    : "/api/view",
            isTdxBacktest()
                ? { ...request, start: $("backtest-start").value }
                : isTdx()
                  ? { symbol: request.symbol, asof: request.asof }
                  : request,
            state.controller.signal,
        );
        if (sequence !== state.sequence) return;
        state.view = data;
        state.loading = false;
        if (data.result_scope === "tdx" || data.data_source === "tdx") {
            state.tdxSessions[data.symbol] = data.sessions;
            preserveCutoff(data.asof);
        }
        document.querySelector(".metric-grid").hidden = false;
        showPage(state.page);
        renderMetrics();
        renderTables();
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
        $("download-backtest").disabled = !data.backtest;
        $("run-stock-backtest").disabled = !state.tdx?.with_daily;
        $("price-basis").textContent = data.result_scope === "tdx" ? "原始不复权" : "因果复权";
        chart.setData(data, {
            volume: $("show-volume").checked,
            markers: $("show-markers").checked,
            annotations: annotationOptions(),
        });
        describeBar(data.bars.at(-1));
        performance.update(data.curve);
        const last = data.bars.at(-1);
        $("selected-stock-summary").textContent =
            `${symbolName(data.symbol)} · ${data.asof} 截面｜原始收盘 ${num(last.raw_close)} 元 · 成交量 ${num(last.volume, 0)} 股。${data.result_scope === "tdx" ? "通达信本地日线，未回测。" : data.result_scope === "stock" ? "收益、仓位、成交账本均为该股独立回测。" : "上方收益／仓位指标及交易账本仍为全组合。"}`;
        $("price-chart").dataset.symbol = data.symbol;
        $("data-range").textContent = isLocal()
            ? `通达信 · ${universe().length} 只 · 最新 ${state.tdx.latest}`
            : `${currentRun().start} — ${currentRun().end} · ${currentRun().symbols.length} 只`;
        $("loading").hidden = true;
        $("price-chart").setAttribute("aria-busy", "false");
        if (state.pendingFocus) {
            chart.focus(state.pendingFocus.time);
            detail("已定位", state.pendingFocus.description);
        } else
            detail(
                "按所选日期复核",
                `${symbolName(data.symbol)} · ${data.asof}。青色圆点为信号，红色向上箭头为买入成交，绿色向下箭头为卖出成交。`,
            );
        renderEvents();
        loadTheory(request, sequence);
        loadStockSummary(request, sequence);
    } catch (error) {
        if (error.name === "AbortError" || sequence !== state.sequence) return;
        state.loading = false;
        state.error = true;
        $("loading").hidden = true;
        $("error").hidden = false;
        $("error").textContent = `加载失败：${error.message}。旧图已隐藏，请检查结果文件后重试。`;
        $("selected-stock-summary").textContent =
            `${symbolName(request.symbol)} · 加载失败，未展示旧股票数据；可重新选择或刷新。`;
        $("price-chart").setAttribute("aria-busy", "false");
        document.querySelectorAll(".page").forEach((p) => (p.hidden = true));
        document.querySelector(".metric-grid").hidden = true;
        $("stock-results").hidden = true;
        $("run-stock-backtest").disabled = !state.tdx?.with_daily;
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
function selectFill(marker) {
    showPage("workspace");
    $("show-fills").checked = true;
    chart.setAnnotationOptions(annotationOptions());
    chart.selectAnnotation(marker.id);
    $("selection-info").scrollIntoView({ block: "nearest" });
}
function exportBacktest() {
    if (!state.view || state.loading || state.error) return;
    const blob = new Blob([JSON.stringify(state.view, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest-${state.view.symbol}-${state.view.variant}-${state.view.asof}.json`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}
$("download-backtest").addEventListener("click", exportBacktest);
const ratioComparison = new RatioComparison({
    api,
    getContext: () => {
        const { variant, ...p } = select();
        return { ...p, start: $("backtest-start").value, local: isLocal() };
    },
    onSelect: (variant) => {
        $("variant-select").value = variant;
        $("result-scope").value = "tdx-backtest";
        loadView();
    },
});
$("backtest-start").addEventListener("change", () => {
    buyPoints.contextChanged();
    ratioComparison.contextChanged();
});
for (const id of ["buy-points-tab", "all-stocks-tab"])
    $(id).addEventListener("click", () => {
        for (const field of ["run-select", "variant-select", "scenario-select"])
            $(field).disabled = isTdx() && $("buy-points-panel").hidden;
    });
$("run-stock-backtest").addEventListener("click", () => {
    const cutoff = state.requestedAsOf || state.view?.asof || state.tdx.latest;
    $("result-scope").value = "tdx-backtest";
    state.pendingFocus = null;
    fillSymbols();
    preserveCutoff(cutoff);
    loadView();
});
$("fills-only").addEventListener("click", () => {
    $("show-fills").checked = true;
    $("show-markers").checked = false;
    $("show-rules").checked = false;
    chart.setAnnotationOptions(annotationOptions());
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
for (const id of ["variant-select", "scenario-select"])
    $(id).addEventListener("change", () => {
        state.pendingFocus = null;
        loadView();
    });
$("result-scope").addEventListener("change", () => {
    state.pendingFocus = null;
    fillSymbols();
    loadView();
});
$("symbol-select").addEventListener("change", () => chooseSymbol($("symbol-select").value));
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
$("show-tertiary-trend").addEventListener("change", (e) => chart.setTertiaryTrendVisible(e.target.checked));
for (const id of [
    "show-fills",
    "show-rules",
    "show-diagnostics",
    "show-levels",
    "show-last-fall-high",
    "show-bear-to-bull-highs",
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
        state.catalog = await api("/api/catalog");
        state.tdx = await api("/api/tdx-catalog").catch((e) => ({ available: false, stocks: [], error: e.message }));
        for (const s of state.tdx.stocks) if (s.name) names[s.symbol] = s.name;
        const tdxOption = $("result-scope").querySelector('[value="tdx"]');
        tdxOption.disabled = !state.tdx.with_daily;
        if (!state.tdx.with_daily) {
            $("result-scope").value = "stock";
            tdxOption.textContent = "通达信目录不可用";
        }
        $("run-select").replaceChildren();
        for (const r of state.catalog.runs) option($("run-select"), r.id, r.id.replace("acceptance_", ""));
        fillSymbols();
        await loadView();
        if (requestedPage && Object.hasOwn(titles, requestedPage)) showPage(requestedPage);
    } catch (e) {
        stockList.setStocks([], "");
        $("stock-count").textContent = "不可用";
        $("selected-stock-summary").textContent = "股票列表读取失败，请刷新重试。";
        $("loading").hidden = true;
        $("error").hidden = false;
        $("error").textContent = "初始化失败：" + e.message;
    }
}
start();
