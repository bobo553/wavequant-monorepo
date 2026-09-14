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
                <button id="structure-signals-tab" aria-pressed="false">
                    结构信号
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
                        查询买点结果
                    </button>
                </div>
                <p id="scan-status" role="status">
                    沿用上方策略、成本和回放日期，查询服务器已经发布的信号快照。
                </p>
                <div id="buy-points-list" />
                <p className="scan-note">
                    买入信号 ≠ 可以成交。次开盘仍需检查跳空、价格限制、仓位和费用后盈亏比。严格版不自动切换为代理版。
                </p>
            </section>
            <section id="structure-signals-panel" hidden aria-label="转多与空多交替结构搜索">
                <fieldset id="structure-market-filter" className="structure-market-filter">
                    <legend>查询市场</legend>
                    <label>
                        <input type="checkbox" name="structure-market" value="shanghai" defaultChecked /> 上证
                    </label>
                    <label>
                        <input type="checkbox" name="structure-market" value="shenzhen" defaultChecked /> 深证
                    </label>
                    <label>
                        <input type="checkbox" name="structure-market" value="chinext" defaultChecked /> 创业板
                    </label>
                    <label>
                        <input type="checkbox" name="structure-market" value="star" /> 科创板
                    </label>
                    <label>
                        <input type="checkbox" name="structure-market" value="beijing" /> 北京
                    </label>
                </fieldset>
                <label>
                    结构类型
                    <select id="structure-signal-type" aria-label="结构信号类型" defaultValue="any">
                        <option value="any">任一：空翻多、空多交替或转多</option>
                        <option value="bear_to_bull">出现空翻多高点</option>
                        <option value="bear_bull_alternation">出现空多交替低点</option>
                        <option value="bullish_turn">转多信号：突破空翻多高点</option>
                    </select>
                </label>
                <label>
                    趋势级别
                    <select id="structure-trend-level" aria-label="结构趋势级别" defaultValue="0">
                        <option value="0">全部级别</option>
                        <option value="1">Ⅰ 一级趋势</option>
                        <option value="2">Ⅱ 二级趋势</option>
                        <option value="3">Ⅲ 三级趋势</option>
                    </select>
                </label>
                <label>
                    确认窗口
                    <select id="structure-scan-lookback" aria-label="结构确认窗口" defaultValue="1">
                        <option value="1">回放当天确认</option>
                        <option value="5">近 5 根确认</option>
                        <option value="20">近 20 根确认</option>
                    </select>
                </label>
                <div className="scan-actions">
                    <button id="structure-scan-start" disabled>
                        读取预计算结果
                    </button>
                </div>
                <p id="structure-scan-status" role="status">
                    服务器在行情或算法变化后自动重建；这里按确认可用日直接读取完成快照。
                </p>
                <div id="structure-signal-list" />
                <p className="scan-note">
                    名称含 * 的股票会被排除。结构出现 ≠
                    买入信号。点击结果可回到对应股票与回放截面，核验末跌高、确认链和图上标识。
                </p>
            </section>
            <p id="selected-stock-summary" className="selected-stock-summary" role="status">
                选择股票查看对应行情，切换保留回放日期。
            </p>
        </aside>
    );
}
