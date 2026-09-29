import { formatBacktestElapsed } from "./backtest-job-status.js";
import { names } from "./labels.js";

export function stockInfo(stock) {
    const symbol = stock.symbol,
        parts = symbol.split(".");
    return {
        ...stock,
        name: stock.name || names[symbol] || "名称待补充",
        code: parts.at(-1),
        exchange: parts.length > 1 ? parts[0].toUpperCase() : "",
    };
}
export function filterStocks(stocks, query = "") {
    const tokens = query.normalize("NFKC").trim().toLowerCase().split(/\s+/).filter(Boolean);
    return stocks
        .map(stockInfo)
        .filter((s) => tokens.every((t) => `${s.symbol} ${s.exchange}${s.code} ${s.name}`.toLowerCase().includes(t)));
}

export function stockCoverageText(stock) {
    if (stock.has_data === false) {
        return stock.status === "invalid_daily" ? "本地日线异常" : "尚未下载日线";
    }
    if (stock.status === "available_on_demand" || stock.bar_count == null) {
        return "按需读取 · 最新交易日以返回为准";
    }
    return `${stock.bar_count ?? stock.sessions?.length ?? 0} 根日线 · 至 ${stock.last || stock.sessions?.at(-1) || "—"}`;
}

export class StockList {
    constructor({ list, count, onSelect }) {
        Object.assign(this, { list, count, onSelect });
        this.stocks = [];
        this.selected = "";
        this.query = "";
        this.limit = 100;
        this.backtestStatuses = {};
        this.backtestJobs = [];
    }
    setStocks(stocks, selected) {
        this.stocks = stocks;
        this.selected = selected;
        this.limit = 100;
        this.render();
    }
    setSelected(symbol) {
        this.selected = symbol;
        this.render();
    }
    setBacktestStatuses(statuses, jobs = []) {
        this.backtestStatuses = statuses;
        this.backtestJobs = jobs;
        const stocksBySymbol = new Map(this.matches?.map((stock) => [stock.symbol, stock]));
        for (const button of this.list.querySelectorAll(".stock-item")) {
            const stock = stocksBySymbol.get(button.dataset.symbol);
            if (stock) this.renderBacktestStatus(button, stock);
        }
    }
    setQuery(query) {
        this.query = query;
        this.limit = 100;
        this.render();
    }
    selectFirst() {
        const first = this.matches?.find((stock) => stock.has_data !== false);
        if (!first) return false;
        this.onSelect(first.symbol);
        return true;
    }
    renderBacktestStatus(button, stock) {
        const baseLabel = `${stock.name} ${stock.code} ${stock.exchange}`;
        const status = this.backtestStatuses[stock.symbol];
        let badge = button.querySelector(".stock-backtest-badge");
        let duration = button.querySelector(".stock-backtest-elapsed");
        if (!status) {
            badge?.remove();
            duration?.remove();
            if (button.getAttribute("aria-label") !== baseLabel) button.setAttribute("aria-label", baseLabel);
            return;
        }
        if (!badge) {
            badge = document.createElement("span");
            badge.className = "stock-backtest-badge";
            button.append(badge);
        }
        const badgeText =
            status === "running"
                ? "回测中"
                : status === "completed"
                  ? "已回测"
                  : status === "unknown"
                    ? "状态待确认"
                    : "回测失败";
        if (badge.dataset.status !== status) badge.dataset.status = status;
        if (badge.textContent !== badgeText) badge.textContent = badgeText;
        const runningJob =
            status === "running"
                ? this.backtestJobs.find((job) => job.symbol === stock.symbol && job.status === "running")
                : null;
        const elapsed = formatBacktestElapsed(runningJob?.elapsed_seconds);
        if (elapsed) {
            if (!duration) {
                duration = document.createElement("small");
                duration.className = "stock-backtest-elapsed";
                button.append(duration);
            }
            if (duration.textContent !== elapsed) duration.textContent = elapsed;
        } else duration?.remove();
        const label = `${baseLabel} ${badgeText}${elapsed ? ` ${elapsed}` : ""}`;
        if (button.getAttribute("aria-label") !== label) button.setAttribute("aria-label", label);
    }
    render() {
        const focused = this.list.contains(document.activeElement) ? document.activeElement.dataset.symbol : null;
        this.matches = filterStocks(this.stocks, this.query);
        this.list.replaceChildren();
        this.count.textContent = `${this.matches.length} / ${this.stocks.length} 只`;
        if (!this.matches.length) {
            const p = document.createElement("p");
            p.className = "stock-empty";
            p.textContent = this.stocks.length ? "没有匹配的股票，请换用中文名或代码搜索。" : "当前快照没有可用股票。";
            this.list.append(p);
            return;
        }
        const shown = this.matches.slice(0, this.limit);
        const selected = this.matches.find((s) => s.symbol === this.selected);
        if (selected && !shown.includes(selected)) shown.unshift(selected);
        for (const s of shown) {
            const b = document.createElement("button");
            b.type = "button";
            b.className = "stock-item";
            b.dataset.symbol = s.symbol;
            b.setAttribute("aria-pressed", String(s.symbol === this.selected));
            b.setAttribute("aria-label", `${s.name} ${s.code} ${s.exchange}`);
            b.disabled = s.has_data === false;
            const name = document.createElement("strong");
            name.textContent = s.name;
            const code = document.createElement("span");
            code.textContent = `${s.code} · ${s.exchange}`;
            const coverage = document.createElement("small");
            coverage.textContent = stockCoverageText(s);
            b.append(name, code, coverage);
            this.renderBacktestStatus(b, s);
            b.addEventListener("click", () => this.onSelect(s.symbol));
            this.list.append(b);
            if (focused === s.symbol) b.focus({ preventScroll: true });
        }
        if (this.matches.length > this.limit) {
            const more = document.createElement("button");
            more.type = "button";
            more.className = "stock-more";
            more.textContent = `加载更多（已列 ${shown.length} / ${this.matches.length}）`;
            more.addEventListener("click", () => {
                this.limit += 100;
                this.render();
            });
            this.list.append(more);
        }
    }
}
