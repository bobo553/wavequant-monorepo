const embedScript = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";

// 嵌入版只接受 TradingView 自有行情；不要把本地复权数据误当成外部交易所数据。
export function tradingViewSymbol(localSymbol) {
    const match = /^(sh|sz)\.(\d{6})$/.exec(localSymbol || "");
    if (!match) return null;
    return `${match[1] === "sh" ? "SSE" : "SZSE"}:${match[2]}`;
}

export function tradingViewChartUrl(symbol) {
    return `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}`;
}

export function tradingViewWidgetConfig(symbol, light = false) {
    return {
        autosize: true,
        symbol,
        interval: "D",
        timezone: "Asia/Shanghai",
        theme: light ? "light" : "dark",
        style: "1",
        locale: "zh_CN",
        withdateranges: true,
        hide_side_toolbar: false,
        hide_top_toolbar: false,
        allow_symbol_change: true,
        save_image: true,
        calendar: false,
        details: false,
        hide_legend: false,
        hide_volume: false,
        support_host: "https://www.tradingview.com",
    };
}

/** 按需挂载官方嵌入脚本；切回本地图时保留 iframe，避免丢失尚未导出的画线。 */
export class TradingViewWidget {
    constructor({ container, status, symbolLabel, syncButton, retryButton, openLink }) {
        this.container = container;
        this.status = status;
        this.symbolLabel = symbolLabel;
        this.syncButton = syncButton;
        this.retryButton = retryButton;
        this.openLink = openLink;
        this.localSymbol = null;
        this.widgetSymbol = null;
        this.active = false;
        this.loadSequence = 0;
        this.observer = null;
        this.timeout = null;
        syncButton.addEventListener("click", () => this.mount(this.localSymbol));
        retryButton.addEventListener("click", () => this.mount(this.widgetSymbol || this.localSymbol));
    }

    setLocalSymbol(localSymbol) {
        this.localSymbol = tradingViewSymbol(localSymbol);
        this.updateContext();
        if (this.active && !this.widgetSymbol && this.localSymbol) this.mount(this.localSymbol);
    }

    setActive(active) {
        this.active = active;
        if (active && !this.widgetSymbol && this.localSymbol) this.mount(this.localSymbol);
        this.updateContext();
    }

    updateContext() {
        this.symbolLabel.textContent = this.widgetSymbol || this.localSymbol || "暂无支持的沪深股票";
        this.syncButton.hidden = !this.widgetSymbol || !this.localSymbol || this.widgetSymbol === this.localSymbol;
        this.openLink.hidden = !this.widgetSymbol;
        if (this.widgetSymbol) this.openLink.href = tradingViewChartUrl(this.widgetSymbol);
        if (this.active && !this.localSymbol) {
            this.showStatus(
                this.widgetSymbol
                    ? "当前本地股票暂不支持 TradingView 嵌入；外部图仍停留在上一只股票。"
                    : "当前股票暂不支持 TradingView 嵌入；原研究图仍可使用。",
                true,
            );
            this.retryButton.hidden = true;
        } else if (this.active && /^当前(?:本地)?股票/.test(this.status.textContent)) {
            this.status.hidden = Boolean(this.container.querySelector("iframe"));
            if (!this.status.hidden) this.showStatus("正在加载 TradingView 绘图工具…");
        }
    }

    showStatus(message, failed = false) {
        this.status.textContent = message;
        this.status.hidden = false;
        this.status.dataset.error = String(failed);
        this.retryButton.hidden = !failed || !this.widgetSymbol;
        this.container.setAttribute("aria-busy", String(!failed && message === "正在加载 TradingView 绘图工具…"));
    }

    stopWatching() {
        this.observer?.disconnect();
        this.observer = null;
        window.clearTimeout(this.timeout);
        this.timeout = null;
    }

    mount(symbol) {
        if (!symbol) return;
        const sequence = ++this.loadSequence;
        this.stopWatching();
        this.widgetSymbol = symbol;
        this.container.replaceChildren();
        this.updateContext();
        this.showStatus("正在加载 TradingView 绘图工具…");

        const widget = document.createElement("div");
        widget.className = "tradingview-widget-container__widget";
        widget.style.height = "100%";
        widget.style.width = "100%";
        const script = document.createElement("script");
        script.type = "text/javascript";
        script.src = embedScript;
        script.async = true;
        script.textContent = JSON.stringify(tradingViewWidgetConfig(symbol, document.documentElement.classList.contains("light")));
        script.onerror = () => {
            if (sequence === this.loadSequence) {
                this.stopWatching();
                this.showStatus("TradingView 未能加载，请检查网络后重试，或在 TradingView 官网打开。", true);
            }
        };
        this.observer = new MutationObserver(() => {
            if (sequence !== this.loadSequence || !this.container.querySelector("iframe")) return;
            this.stopWatching();
            this.status.hidden = true;
            this.container.setAttribute("aria-busy", "false");
            this.updateContext();
        });
        this.observer.observe(this.container, { childList: true, subtree: true });
        this.timeout = window.setTimeout(() => {
            if (sequence !== this.loadSequence || this.container.querySelector("iframe")) return;
            this.stopWatching();
            this.showStatus("TradingView 加载超时，请重试或在 TradingView 官网打开。", true);
        }, 15000);
        this.container.append(widget, script);
    }
}
