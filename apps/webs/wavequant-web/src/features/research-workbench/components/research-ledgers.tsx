import type { JSX } from "react";

const fillHeaders = [
    "决定日期",
    "成交日期",
    "B / S",
    "图表等价价",
    "原始模拟价 / 元",
    "等价份额",
    "费用 / 元",
    "买入后仓位",
    "入场条件 / 退出原因",
];
const tradeHeaders = ["证券", "买入日期", "卖出日期", "持有 K 数", "净盈亏 / 元", "交易费用 / 元", "复盘"];

/** B/S 成交账本、交易复核和全部股票独立回测结果。 */
export function ResearchLedgers(): JSX.Element {
    return (
        <>
            <article className="panel ledger-card">
                <div className="card-header">
                    <h2>
                        B / S 成交账本 <small>点击查看当时条件，含尚未平仓的买入</small>
                    </h2>
                    <button id="fills-only">仅看并定位成交</button>
                </div>
                <ResearchTable headers={fillHeaders} bodyId="fills-body" />
                <p id="fills-empty" className="empty">
                    没有模拟成交。信号圆点不会被转换成 B / S。
                </p>
            </article>
            <article className="panel ledger-card">
                <div className="card-header">
                    <h2>
                        成交复核 <small>点击记录定位 K 线</small>
                    </h2>
                    <span id="trade-count" className="tag">
                        —
                    </span>
                </div>
                <ResearchTable headers={tradeHeaders} bodyId="trades-body" />
                <p id="trades-empty" className="empty" hidden>
                    该截面没有已平仓交易。没有交易证据，不代表风险为零。
                </p>
            </article>
        </>
    );
}

export function StockBacktestResults(): JSX.Element {
    return (
        <article className="panel stock-results" id="stock-results" hidden>
            <div className="card-header">
                <h2>全部股票 · 独立回测结果</h2>
                <span id="backtest-summary-status" className="tag">
                    等待回测
                </span>
            </div>
            <ResearchTable
                headers={["股票", "截至日期", "净收益", "最大回撤", "买入成交", "已平仓", "胜率", "费用 / 元", "查看"]}
                bodyId="stock-results-body"
            />
            <p className="note-card">
                每只股票使用独立账户、相同资金及原风控参数；不可把这些收益直接相加当作组合收益。无交易不代表策略有效。
            </p>
        </article>
    );
}

export function ResearchTable({ headers, bodyId }: { bodyId: string; headers: readonly string[] }): JSX.Element {
    return (
        <div className="table-scroll">
            <table>
                <thead>
                    <tr>
                        {headers.map((header) => (
                            <th key={header}>{header}</th>
                        ))}
                    </tr>
                </thead>
                <tbody id={bodyId} />
            </table>
        </div>
    );
}
