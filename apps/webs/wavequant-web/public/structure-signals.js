import { num, pct, symbolName } from "./labels.js";
import { setWatchlistStarIcon } from "./watchlists.js";

const signalNames = {
    any: "空翻多 / 空多交替 / 转多",
    bear_to_bull: "空翻多高点",
    bear_bull_alternation: "空多交替低点",
    bullish_turn: "转多信号",
};
const signalOrder = ["bear_to_bull", "bear_bull_alternation", "bullish_turn"];
const levelNames = { 1: "Ⅰ 一级", 2: "Ⅱ 二级", 3: "Ⅲ 三级" };
const marketOrder = ["shanghai", "shenzhen", "chinext", "star", "beijing"];
const marketNames = {
    shanghai: "上证",
    shenzhen: "深证",
    chinext: "创业板",
    star: "科创板",
    beijing: "北京",
};

/** 将任意勾选顺序规整为服务端缓存使用的稳定市场键。 */
export const normalizeStructureMarkets = (markets) =>
    marketOrder.filter((market) => markets.includes(market)).join(",");

/** 将结构多选按稳定顺序编码，用于查询上下文和客户端过滤。 */
export const normalizeStructureSignalTypes = (types) => signalOrder.filter((signalType) => types.includes(signalType));

function structureMarket(symbol) {
    const normalized = typeof symbol === "string" ? symbol.toLowerCase() : "";
    const code = normalized.split(".")[1] || "";
    if (normalized.startsWith("bj.")) return "beijing";
    if (normalized.startsWith("sh.")) return code.startsWith("688") || code.startsWith("689") ? "star" : "shanghai";
    if (normalized.startsWith("sz.")) return code.startsWith("300") || code.startsWith("301") ? "chinext" : "shenzhen";
    return null;
}

/** 使用页面启动时已加载的目录解释预计算进度，不触发新的行情或信号请求。 */
export function structureUniverseCoverage(stocks, markets) {
    const selected = new Set((markets || "").split(",").filter(Boolean));
    const catalog = Array.isArray(stocks)
        ? stocks.filter(
              (stock) =>
                  structureMarket(stock?.symbol) && (!stock.catalog_source || stock.catalog_source === "akshare"),
          )
        : [];
    const selectedStocks = catalog.filter((stock) => {
        const name = typeof stock.name === "string" ? stock.name : "";
        return selected.has(structureMarket(stock.symbol)) && !name.includes("*") && !name.includes("＊");
    }).length;
    return { catalogStocks: catalog.length, selectedStocks };
}

/** 在重建期间明确区分“正在服务的完整快照”和“目标代际进度”。 */
export function structureCoverageStatus(response, catalogStocks, selectedStocks) {
    const params = response.params;
    const snapshot = response.snapshot;
    const publishedStocks = response.coverage?.published_stocks ?? response.processed;
    const buildingStocks = response.coverage?.building_stocks ?? publishedStocks;
    const expectedStocks = response.coverage?.expected_stocks ?? catalogStocks;
    if (response.status === "rebuilding") {
        return snapshot.is_fallback
            ? `AkShare 后台更新中：当前展示 ${snapshot.asof} 完整快照；目标 ${params.asof} 已发布 ${buildingStocks} / ${expectedStocks} 只；当前筛选市场 ${selectedStocks} 只`
            : `AkShare 首次重建中：目标 ${params.asof} 已发布 ${buildingStocks} / ${expectedStocks} 只；当前展示已完成部分`;
    }
    return catalogStocks
        ? `AkShare ${publishedStocks >= expectedStocks ? "全市场快照已就绪" : "后台重建中"}：已发布 ${publishedStocks} / ${expectedStocks} 只；当前筛选市场 ${selectedStocks} 只`
        : `AkShare 市场快照已发布 ${publishedStocks} 只`;
}

/** 结构筛选始终读取服务器发布的市场级读模型，与当前图表股票无关。 */
export const structureScanContextKey = (params) =>
    JSON.stringify([
        params.run,
        params.variant,
        params.source,
        params.asof,
        params.lookback,
        params.signal_type,
        params.trend_level,
        params.markets,
    ]);

/** 先显示最新确认，再按级别和证券代码提供确定顺序。 */
export function sortedStructureMatches(rows) {
    return [...rows].sort(
        (left, right) =>
            right.available_at.localeCompare(left.available_at) ||
            right.trend_level - left.trend_level ||
            right.event_date.localeCompare(left.event_date) ||
            left.symbol.localeCompare(right.symbol) ||
            left.id.localeCompare(right.id),
    );
}

/** 查询后台已发布的结构读模型；浏览器请求绝不触发逐股计算。 */
export class StructureSignals {
    constructor({ api, getContext, getUniverse = () => [], onSelect, watchlists = null }) {
        Object.assign(this, { api, getContext, getUniverse, onSelect, watchlists });
        this.$ = (id) => document.getElementById(id);
        this.generation = 0;
        this.$("structure-scan-start").addEventListener("click", () => this.start());
        this.$("structure-watchlist-add-all").addEventListener("click", () => void this.addAllToWatchlist());
        window.addEventListener("wavequant:watchlists-changed", () => this.syncWatchlistActions());
        for (const input of document.querySelectorAll(
            'input[name="structure-signal-type"], input[name="structure-trend-level"], input[name="structure-scan-lookback"]',
        ))
            input.addEventListener("change", () => this.contextChanged());
        for (const checkbox of document.querySelectorAll('input[name="structure-market"]'))
            checkbox.addEventListener("change", () => this.contextChanged());
    }

    params() {
        const signalTypes = normalizeStructureSignalTypes(
            [...document.querySelectorAll('input[name="structure-signal-type"]:checked')].map(
                (checkbox) => checkbox.value,
            ),
        );
        return {
            ...this.getContext(),
            signal_type: signalTypes.join(","),
            trend_level: Number(document.querySelector('input[name="structure-trend-level"]:checked')?.value),
            lookback: Number(document.querySelector('input[name="structure-scan-lookback"]:checked')?.value),
            markets: normalizeStructureMarkets(
                [...document.querySelectorAll('input[name="structure-market"]:checked')].map(
                    (checkbox) => checkbox.value,
                ),
            ),
        };
    }

    contextChanged() {
        if (this.key && this.key !== structureScanContextKey(this.params())) {
            this.generation++;
            this.controller?.abort();
            this.response = null;
            this.key = null;
            this.$("structure-signal-list").replaceChildren();
            this.$("structure-scan-status").textContent = "日期、结构类型、级别或数据范围已变化，请重新读取快照。";
            this.$("structure-scan-start").disabled = false;
            this.syncWatchlistActions();
        }
        if (!this.response) this.$("structure-scan-start").disabled = false;
    }

    async start() {
        const generation = ++this.generation;
        this.controller?.abort();
        this.response = null;
        this.syncWatchlistActions();
        const params = this.params();
        if (!params.signal_type) {
            this.$("structure-scan-status").textContent = "请至少选择一种结构类型。";
            this.$("structure-scan-start").disabled = false;
            return;
        }
        if (!params.markets) {
            this.$("structure-scan-status").textContent = "请至少选择一个查询市场。";
            this.$("structure-scan-start").disabled = false;
            return;
        }
        this.key = structureScanContextKey(params);
        this.$("structure-scan-start").disabled = true;
        this.$("structure-scan-status").textContent = "正在读取服务器预计算快照…";
        this.$("structure-signal-list").replaceChildren();
        this.controller = new AbortController();
        try {
            const selectedSignalTypes = params.signal_type.split(",");
            const requestParams = {
                ...params,
                signal_type: selectedSignalTypes.length === 1 ? selectedSignalTypes[0] : "any",
            };
            const response = await this.api("/api/structure-signals", requestParams, this.controller.signal);
            if (generation !== this.generation) return;
            response.results = response.results.filter((row) => selectedSignalTypes.includes(row.signal_type));
            response.matched_stocks = new Set(response.results.map((row) => row.symbol)).size;
            response.params.signal_type = params.signal_type;
            this.response = response;
            this.render();
        } catch (error) {
            if (generation !== this.generation || error.name === "AbortError") return;
            this.$("structure-scan-status").textContent = "结构快照不可用：" + error.message;
            this.$("structure-scan-start").disabled = false;
            this.syncWatchlistActions();
        }
    }

    async addAllToWatchlist() {
        if (!this.watchlists || !this.response) return;
        const unique = new Map();
        for (const result of this.response.results)
            if (!unique.has(result.symbol)) unique.set(result.symbol, { symbol: result.symbol, name: result.name || "" });
        await this.watchlists.addStocks([...unique.values()]);
        this.syncWatchlistActions();
    }

    syncWatchlistActions() {
        const target = this.$("structure-watchlist-target");
        const addAll = this.$("structure-watchlist-add-all");
        if (!target || !addAll) return;
        const ready = Boolean(this.watchlists?.available);
        const group = this.watchlists?.selectedGroup;
        target.textContent = group?.name || "自选股不可用";
        const symbols = [...new Set((this.response?.results || []).map((result) => result.symbol))];
        const allAdded = symbols.length > 0 && symbols.every((symbol) => this.watchlists.has(symbol));
        addAll.disabled = !ready || !symbols.length || allAdded;
        addAll.textContent = allAdded ? "当前结果已全部加入" : `当前结果全部加入${symbols.length ? `（${symbols.length}）` : ""}`;
        for (const button of document.querySelectorAll(".structure-watchlist-add")) {
            const added = ready && this.watchlists.has(button.dataset.symbol);
            const stockName = button.dataset.name || button.dataset.symbol;
            button.disabled = !ready;
            button.setAttribute("aria-pressed", String(added));
            setWatchlistStarIcon(button, added);
            button.setAttribute(
                "aria-label",
                added
                    ? `从“${group?.name || "我的自选"}”移除 ${stockName}`
                    : `将 ${stockName} 加入“${group?.name || "我的自选"}”`,
            );
            button.title = added
                ? `已收藏到“${group?.name || "我的自选"}”，点击移除`
                : `收藏到“${group?.name || "我的自选"}”`;
        }
    }

    render() {
        const response = this.response;
        const params = response.params;
        const snapshot = response.snapshot;
        const matchedStocks = response.matched_stocks ?? new Set(response.results.map((row) => row.symbol)).size;
        const calculated = snapshot.computed_at
            ? snapshot.computed_at.replace("T", " ").replace(/\.\d+(?=[+-]|Z)/, "")
            : "—";
        this.$("structure-scan-start").disabled = false;
        const online = params.source === "akshare";
        const { catalogStocks, selectedStocks } = structureUniverseCoverage(this.getUniverse(), params.markets);
        const akshareCoverage = structureCoverageStatus(response, catalogStocks, selectedStocks);
        const selectedMarkets = (params.markets || "shanghai,shenzhen,chinext")
            .split(",")
            .map((market) => marketNames[market] || market)
            .join("、");
        this.$("structure-scan-status").textContent =
            `${params.asof} · ${selectedMarkets} · ${params.signal_type
                .split(",")
                .map((type) => signalNames[type])
                .join("、")} · ${params.trend_level ? levelNames[params.trend_level] : "全部级别"}\n` +
            `${online ? akshareCoverage : "全市场快照已就绪"}，发现 ${response.results.length} 个已确认结构（${matchedStocks} 只股票）；跳过 ${response.skipped}，过期 ${response.stale}，失败 ${response.failed}。\n` +
            `已排除名称含 * 的股票 · 预计算完成 ${calculated} · 行情 ${snapshot.data_version.slice(0, 8)} · 算法 ${snapshot.algorithm_version.slice(0, 8)}`;

        const list = this.$("structure-signal-list");
        const focused = document.activeElement;
        const scrollTop = list.scrollTop;
        const existing = new Map(
            [...list.querySelectorAll(".structure-signal-card")].map((card) => [card.dataset.resultId, card]),
        );
        const rows = sortedStructureMatches(response.results);
        for (const child of [...list.children]) if (!child.classList.contains("structure-signal-card")) child.remove();
        for (const [index, result] of rows.entries()) {
            const resultId = `${result.symbol}:${result.id}`;
            let card = existing.get(resultId);
            existing.delete(resultId);
            if (!card) {
                card = document.createElement("div");
                card.className = "structure-signal-card";
                card.dataset.resultId = resultId;
                const button = document.createElement("button");
                button.type = "button";
                button.className = "structure-signal-item";
                const name = document.createElement("strong");
                name.textContent = result.name
                    ? `${result.symbol.split(".")[1]} ${result.name}`
                    : symbolName(result.symbol);
                const status = document.createElement("span");
                status.textContent = `${levelNames[result.trend_level]} · ${signalNames[result.signal_type]} · ${result.label || result.kind}`;
                const timing = document.createElement("small");
                timing.textContent = `发生 ${result.event_date} · 确认可用 ${result.available_at} · ${num(result.value)} 元`;
                const evidence = document.createElement("small");
                evidence.textContent =
                    result.signal_type === "bear_to_bull"
                        ? `严格突破末跌高 ${num(result.evidence?.broken_key?.value)} 元`
                        : result.signal_type === "bullish_turn"
                          ? `收盘 ${num(result.evidence?.previous_close)} → ${num(result.value)}，严格突破空翻多高点 ${num(result.evidence?.breakout_level)} 元`
                          : result.evidence?.confirmed_rebreak_high
                            ? `由 ${result.evidence?.confirmed_flip_high?.label || "翻多高点"} 回档 ${pct(result.evidence?.retracement_ratio)}，随后确认高点 ${num(result.evidence.confirmed_rebreak_high.value)} 元严格再突破`
                            : `由 ${result.evidence?.confirmed_flip_high?.label || "翻多高点"} 回档 ${pct(result.evidence?.retracement_ratio)} 确认`;
                button.append(name, status, timing, evidence);
                button.addEventListener("click", () => this.onSelect(result, params));
                const add = document.createElement("button");
                add.type = "button";
                add.className = "structure-watchlist-add";
                add.dataset.symbol = result.symbol;
                add.dataset.name = result.name || result.symbol;
                add.setAttribute("aria-pressed", "false");
                add.setAttribute("aria-label", `将 ${result.name || result.symbol} 加入自选股`);
                add.addEventListener("click", async () => {
                    if (this.watchlists?.has(result.symbol)) await this.watchlists.remove(result.symbol);
                    else await this.watchlists?.addStocks([{ symbol: result.symbol, name: result.name || "" }]);
                    this.syncWatchlistActions();
                });
                card.append(button, add);
            }
            if (list.children[index] !== card) list.insertBefore(card, list.children[index] || null);
        }
        for (const card of existing.values()) card.remove();
        if (focused?.isConnected && list.contains(focused) && document.activeElement !== focused)
            focused.focus({ preventScroll: true });
        list.scrollTop = scrollTop;

        if (!rows.length) {
            const empty = document.createElement("p");
            empty.className = "stock-empty";
            empty.textContent = "已完成快照中没有在确认窗口出现的匹配结构。";
            list.append(empty);
        }
        if (response.errors.length || response.skipped) this.appendDiagnostics(list, response);
        this.syncWatchlistActions();
    }

    appendDiagnostics(list, job) {
        const detail = document.createElement("details");
        const summary = document.createElement("summary");
        summary.textContent = "查看跳过 / 失败原因";
        detail.append(summary);
        const names = { no_daily: "无有效日线", stale_daily: "行情未更新到回放日" };
        for (const [reason, count] of Object.entries(job.skip_reasons || {})) {
            const line = document.createElement("p");
            line.textContent = `${names[reason] || reason}：${count}`;
            detail.append(line);
        }
        for (const error of job.errors) {
            const line = document.createElement("p");
            line.textContent = `${error.symbol}：${error.error}`;
            detail.append(line);
        }
        list.append(detail);
    }
}
