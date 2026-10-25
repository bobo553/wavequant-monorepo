import { researchSymbols } from "./research-symbols.js";
import { Watchlists } from "./watchlists.js";

let runtime;

/** 侧栏与图表共享实例，避免重复绑定事件和重复读取、迁移自选数据。 */
export function getWatchlistRuntime() {
    const root = document.getElementById("watchlist-rail");
    if (!root) return null;
    if (runtime?.root === root) return runtime;
    const boot = window.wavequantWatchlistBoot;
    let watchlists;
    const select = (symbol) => {
        if (boot) boot.pendingSymbol = symbol;
        watchlists.setSelected(symbol);
        watchlists.announce("已选中股票，正在准备分析配置…");
    };
    watchlists = new Watchlists({ onSelect: select });
    const ready = watchlists.init().then(() => {
        if (document.getElementById("watchlist-rail") !== root) return;
        if (boot?.groupId && watchlists.state.groups.some((group) => group.id === boot.groupId)) {
            watchlists.selectedGroupId = boot.groupId;
        }
        if (boot) {
            watchlists.query = boot.query;
            boot.runtimeReady = true;
            boot.sidebarReady = true;
        }
        const linked = new URLSearchParams(location.search).get("symbol");
        const linkedSymbol = /^(sh\.(60|68)\d{4}|sz\.(00|30)\d{4}|bj\.(43|82|83|87|88|92)\d{4})$/.test(linked || "") ? linked : "";
        const stocks = researchSymbols(watchlists.state.memberships, linkedSymbol);
        watchlists.setUniverse(stocks, watchlists.initialSymbol(stocks, boot?.pendingSymbol || linkedSymbol));
        boot?.release();
        performance.mark?.("wavequant:watchlist-data-ready");
    });
    runtime = { root, watchlists, ready };
    return runtime;
}

getWatchlistRuntime();
