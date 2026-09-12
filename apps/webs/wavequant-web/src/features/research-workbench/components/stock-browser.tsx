import type { JSX } from "react";

/** 股票目录、中文搜索与异步买点扫描入口。 */
export function StockBrowser(): JSX.Element {
    return (
        <aside className="panel stock-browser" aria-label="股票列表">
            <div className="stock-tabs" aria-label="股票列表类型">
                <button id="all-stocks-tab" aria-pressed="true">
                    全部股票
                </button>
                <button id="buy-points-tab" aria-pressed="false">
                    符合买点
                </button>
            </div>
            <div className="stock-browser-header">
                <div>
                    <h2>
                        股票列表 <small id="stock-count">读取中…</small>
                    </h2>
                    <p id="stock-source-notice">通达信本地沪深北 A 股目录</p>
                </div>
                <div className="stock-search-controls" id="stock-search-controls">
                    <label htmlFor="stock-search" className="sr-only">
                        搜索股票
                    </label>
                    <input
                        id="stock-search"
                        type="search"
                        placeholder="中文名 / 代码 / SH、SZ、BJ"
                        autoComplete="off"
                        aria-controls="stock-list"
                    />
                    <button id="stock-search-clear" aria-label="清空搜索" type="button" disabled>
                        清空
                    </button>
                </div>
            </div>
            <div id="stock-list" className="stock-list">
                <p className="stock-empty">正在读取股票列表…</p>
            </div>
            <section id="buy-points-panel" hidden aria-label="符合买点的股票">
                <label>
                    信号窗口
                    <select id="scan-lookback" aria-label="买点信号窗口" defaultValue="1">
                        <option value="1">回放当天 · 新信号</option>
                        <option value="5">近 5 根 · 历史信号</option>
                        <option value="20">近 20 根 · 历史信号</option>
                    </select>
                </label>
                <div className="scan-actions">
                    <button id="scan-start" disabled>
                        扫描买点
                    </button>
                    <button id="scan-cancel" disabled>
                        取消扫描
                    </button>
                </div>
                <p id="scan-status" role="status">
                    沿用上方策略、成本和回放日期，点击开始扫描。
                </p>
                <div id="buy-points-list" />
                <p className="scan-note">
                    买入信号 ≠ 可以成交。次开盘仍需检查跳空、价格限制、仓位和费用后盈亏比。严格版不自动切换为代理版。
                </p>
            </section>
            <p id="selected-stock-summary" className="selected-stock-summary" role="status">
                选择股票查看对应行情，切换保留回放日期。
            </p>
        </aside>
    );
}
