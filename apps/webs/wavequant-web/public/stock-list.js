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
    constructor({ list, search, count, clear, onSelect }) {
        Object.assign(this, { list, search, count, clear, onSelect });
        this.stocks = [];
        this.selected = "";
        this.limit = 100;
        search.addEventListener("input", () => {
            this.limit = 100;
            this.render();
        });
        clear.addEventListener("click", () => {
            search.value = "";
            this.render();
            search.focus();
        });
        search.addEventListener("keydown", (e) => {
            if (e.isComposing) return;
            if (e.key === "Escape") {
                search.value = "";
                this.render();
            }
            if (e.key === "Enter" && this.matches?.length) {
                const first = this.matches.find((s) => s.has_data !== false);
                if (first) {
                    e.preventDefault();
                    this.onSelect(first.symbol);
                }
            }
        });
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
    render() {
        const focused = this.list.contains(document.activeElement) ? document.activeElement.dataset.symbol : null;
        this.matches = filterStocks(this.stocks, this.search.value);
        this.list.replaceChildren();
        this.count.textContent = `${this.matches.length} / ${this.stocks.length} 只`;
        this.clear.disabled = !this.search.value;
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
