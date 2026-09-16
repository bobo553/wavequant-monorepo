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
                <fieldset className="structure-option-filter" aria-label="结构信号类型">
                    <legend>结构类型</legend>
                    <div className="structure-filter-choices">
                        <label title="筛选确认出现空翻多高点的结构">
                            <input type="checkbox" name="structure-signal-type" value="bear_to_bull" />
                            <span>空翻多</span>
                        </label>
                        <label title="筛选空翻多后已确认回档低点的结构">
                            <input type="checkbox" name="structure-signal-type" value="bear_bull_alternation" />
                            <span>空多交替</span>
                        </label>
                        <label title="筛选收盘价突破空翻多高点的转多信号">
                            <input type="checkbox" name="structure-signal-type" value="bullish_turn" defaultChecked />
                            <span>转多信号</span>
                        </label>
                    </div>
                </fieldset>
                <fieldset className="structure-option-filter" aria-label="结构趋势级别">
                    <legend>趋势级别</legend>
                    <div className="structure-filter-choices">
                        {[
                            ["0", "全部", "同时查询一级、二级和三级趋势"],
                            ["1", "Ⅰ 一级", "仅查询一级趋势中的结构"],
                            ["2", "Ⅱ 二级", "仅查询二级趋势中的结构"],
                            ["3", "Ⅲ 三级", "仅查询三级趋势中的结构"],
                        ].map(([value, label, tip]) => (
                            <label key={value} title={tip}>
                                <input
                                    type="radio"
                                    name="structure-trend-level"
                                    value={value}
                                    defaultChecked={value === "2"}
                                />
                                <span>{label}</span>
                            </label>
                        ))}
                    </div>
                </fieldset>
                <fieldset className="structure-option-filter" aria-label="结构确认窗口">
                    <legend>确认窗口</legend>
                    <div className="structure-filter-choices">
                        {[
                            ["1", "当天", "仅查询回放当天确认的结构"],
                            ["5", "近5根", "查询最近 5 根 K 线内确认的结构"],
                            ["20", "近20根", "查询最近 20 根 K 线内确认的结构"],
                        ].map(([value, label, tip]) => (
                            <label key={value} title={tip}>
                                <input
                                    type="radio"
                                    name="structure-scan-lookback"
                                    value={value}
                                    defaultChecked={value === "20"}
                                />
                                <span>{label}</span>
                            </label>
                        ))}
                    </div>
                </fieldset>
                <div className="scan-actions">
                    <button id="structure-scan-start" disabled>
                        读取预计算结果
                    </button>
                </div>
                <div className="structure-watchlist-toolbar" aria-label="结构结果自选操作">
                    <span>
                        收藏到 <strong id="structure-watchlist-target">我的自选</strong>
                    </span>
                    <button id="structure-watchlist-add-all" type="button" disabled>
                        当前结果全部加入
                    </button>
                </div>
                <p id="structure-scan-status" role="status">
                    服务器在行情或算法变化后自动重建；这里按确认可用日直接读取完成快照。
                </p>
                <div id="structure-signal-list" />
                <p className="scan-note">
                    名称含 * 的股票会被排除。结构出现 ≠
                    买入信号。结果卡片右上角星标用于收藏或移除；点击结果可回到对应股票与回放截面，核验末跌高、确认链和图上标识。
                </p>
            </section>
            <p id="selected-stock-summary" className="selected-stock-summary" role="status">
                选择股票查看对应行情，切换保留回放日期。
            </p>
        </aside>
    );
}
