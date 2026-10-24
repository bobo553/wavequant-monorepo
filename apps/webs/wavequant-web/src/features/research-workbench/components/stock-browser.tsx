import type { JSX } from "react";

/** 交易节点与预计算信号查询；选股统一由左侧自选栏承载。 */
export function StockBrowser(): JSX.Element {
    return (
        <aside className="panel stock-browser" aria-label="交易节点与结构信号">
            <p id="stock-picker-feedback" className="stock-picker-feedback" role="status" hidden />
            <div className="stock-tabs" aria-label="右侧信息类型">
                <button id="trade-nodes-tab" aria-pressed="false" hidden>
                    交易节点
                </button>
                <button id="buy-points-tab" aria-pressed="false">
                    符合买点
                </button>
                <button id="structure-signals-tab" aria-pressed="false">
                    结构信号
                </button>
            </div>
            <section id="trade-nodes-panel" className="trade-nodes-panel" aria-label="当前股票回测交易节点" hidden>
                <header className="trade-nodes-header">
                    <span className="trade-nodes-eyebrow">BACKTEST JOURNAL</span>
                    <h2>交易时间线</h2>
                    <p id="trade-nodes-context">等待当前股票回测结果…</p>
                </header>
                <div className="trade-nodes-summary" aria-label="回测成交概览">
                    <div>
                        <strong id="trade-nodes-buy-count">—</strong>
                        <span>买入成交</span>
                    </div>
                    <div>
                        <strong id="trade-nodes-closed-count">—</strong>
                        <span>卖出成交（含减仓）</span>
                    </div>
                    <div>
                        <strong id="trade-nodes-open-count">—</strong>
                        <span>未平仓</span>
                    </div>
                </div>
                <div className="trade-nodes-view-tabs" role="tablist" aria-label="交易节点状态">
                    <button
                        id="trade-nodes-filled-tab"
                        type="button"
                        role="tab"
                        aria-selected="true"
                        aria-controls="trade-nodes-filled-panel"
                        tabIndex={0}
                    >
                        已成交 <span id="trade-nodes-filled-count">0</span>
                    </button>
                    <button
                        id="trade-nodes-blocked-tab"
                        type="button"
                        role="tab"
                        aria-selected="false"
                        aria-controls="trade-nodes-blocked-panel"
                        tabIndex={-1}
                    >
                        被拦截 <span id="trade-nodes-blocked-count">0</span> 日
                    </button>
                </div>
                <div
                    id="trade-nodes-filled-panel"
                    className="trade-nodes-view-panel"
                    role="tabpanel"
                    aria-labelledby="trade-nodes-filled-tab"
                >
                    <div className="trade-nodes-filters" role="group" aria-label="筛选已成交节点">
                        <button type="button" data-trade-node-filter="all" aria-pressed="true">
                            全部
                        </button>
                        <button type="button" data-trade-node-filter="BUY" aria-pressed="false">
                            买入 B
                        </button>
                        <button type="button" data-trade-node-filter="SELL" aria-pressed="false">
                            卖出 S
                        </button>
                    </div>
                    <p id="trade-nodes-filled-copy-feedback" className="trade-nodes-copy-feedback" role="status" />
                    <ol id="trade-nodes-list" className="trade-nodes-list" aria-label="按成交日期由近到远的交易节点" />
                    <p id="trade-nodes-empty" className="trade-nodes-empty" hidden />
                    <p className="trade-nodes-note">仅列实际模拟成交；策略信号和未通过的候选不算交易。</p>
                </div>
                <div
                    id="trade-nodes-blocked-panel"
                    className="trade-nodes-view-panel"
                    role="tabpanel"
                    aria-labelledby="trade-nodes-blocked-tab"
                    hidden
                >
                    <div className="trade-nodes-copy-bar">
                        <p id="trade-nodes-blocked-summary" className="trade-nodes-group-summary">
                            按交易日汇总，等待回测结果…
                        </p>
                        <button
                            id="trade-nodes-copy-all"
                            type="button"
                            data-copy-label="复制全部"
                            aria-label="复制全部被拦截记录"
                            disabled
                        >
                            复制全部
                        </button>
                    </div>
                    <p id="trade-nodes-copy-feedback" className="trade-nodes-copy-feedback" role="status" />
                    <ol
                        id="trade-nodes-blocked-list"
                        className="trade-nodes-list"
                        aria-label="按交易日由近到远的被拦截记录，同日合并"
                    />
                    <p id="trade-nodes-blocked-empty" className="trade-nodes-empty" hidden />
                    <p className="trade-nodes-note">
                        “筛”是策略候选未通过、尚未下单；“拦”是执行层取消的委托、未成交。两者均不计入 B / S 成交。
                    </p>
                </div>
            </section>
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
            <div id="selected-stock-summary-row" className="selected-stock-summary-row">
                <p id="selected-stock-summary" className="selected-stock-summary" role="status">
                    选择股票查看对应行情，切换保留回放日期。
                </p>
                <div className="selected-stock-copy-actions">
                    <button
                        id="copy-backtest-progress"
                        className="backtest-progress-copy"
                        type="button"
                        aria-label="复制当前回测进度"
                        title="复制当前回测进度"
                        aria-live="polite"
                    >
                        复制
                    </button>
                    <button
                        id="copy-eligibility-ranges-summary"
                        type="button"
                        className="backtest-progress-copy"
                        data-copy-eligibility=""
                        data-feedback-id="eligibility-summary-copy-feedback"
                        disabled
                        aria-label="复制当前股票的全部已显示资格区间"
                        title="复制资格区间及确认、结束日期"
                    >
                        复制资格区间
                    </button>
                    <span id="eligibility-summary-copy-feedback" role="status" aria-live="polite" />
                    <div data-current-eligibility-action="" hidden>
                        <button
                            id="copy-current-eligibility-range"
                            type="button"
                            className="backtest-progress-copy"
                            data-copy-eligibility="current"
                            data-default-label="复制当前资格区间"
                            data-feedback-id="current-eligibility-copy-feedback"
                            hidden
                            disabled
                            aria-label="只复制进度条当前位置所在的资格区间"
                        >
                            复制当前资格区间
                        </button>
                        <span id="current-eligibility-copy-feedback" role="status" aria-live="polite" />
                    </div>
                </div>
            </div>
        </aside>
    );
}
