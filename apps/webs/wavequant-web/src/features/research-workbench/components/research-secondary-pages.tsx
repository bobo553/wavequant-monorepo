import type { JSX } from "react";

import { ResearchTable } from "./research-ledgers";

/** 绩效净值、回撤和仓位图表页。 */
export function PerformancePage(): JSX.Element {
    return (
        <section id="page-performance" className="page" hidden>
            <article className="panel">
                <div className="card-header">
                    <h2>
                        账户净值 <small>初始资金归一化为 1</small>
                    </h2>
                    <span id="curve-scope-label" className="tag">
                        个股独立净值
                    </span>
                </div>
                <div id="equity-chart" className="performance-chart" aria-label="账户净值图" />
            </article>
            <div className="two-col">
                <article className="panel">
                    <div className="card-header">
                        <h2>
                            回撤曲线 <small>%</small>
                        </h2>
                    </div>
                    <div id="drawdown-chart" className="small-chart" aria-label="回撤图" />
                </article>
                <article className="panel">
                    <div className="card-header">
                        <h2>
                            仓位使用 <small>%</small>
                        </h2>
                    </div>
                    <div id="exposure-chart" className="small-chart" aria-label="仓位图" />
                </article>
            </div>
            <div className="panel note-card">
                收益和回撤遵循上方“结果口径”，个股独立账户与原封存组合分开。仅使用回放日期之前的数据；没有把日线代理结果标成严格讲义策略。
            </div>
        </section>
    );
}

/** 委托成交与策略信号页。 */
export function OrdersPage(): JSX.Element {
    return (
        <section id="page-orders" className="page" hidden>
            <article className="panel">
                <div className="card-header">
                    <h2>
                        委托与成交明细 <small id="orders-scope-label">当前股票 / 截至回放日期</small>
                    </h2>
                    <button id="export-orders">导出当前明细</button>
                </div>
                <ResearchTable
                    headers={["日期", "证券", "方向", "状态", "价格 / 复权等价", "数量 / 等价份额", "原因", "定位"]}
                    bodyId="orders-body"
                />
                <p id="orders-empty" className="empty" hidden>
                    没有委托记录。请在 K 线页查看前置筛选与结构中断事件。
                </p>
            </article>
            <article className="panel">
                <div className="card-header">
                    <h2>
                        策略信号 <small>当前股票 / 最近 100 条</small>
                    </h2>
                </div>
                <ResearchTable headers={["日期", "类型", "参考价", "失效位", "盘态", "原因"]} bodyId="signals-body" />
            </article>
        </section>
    );
}

/** 运行门禁、数据健康度与本地告警页。 */
export function HealthPage(): JSX.Element {
    return (
        <section id="page-health" className="page" hidden>
            <article className="panel">
                <div className="card-header">
                    <h2>运行与数据门禁</h2>
                    <span className="pill">真实订单关闭</span>
                </div>
                <div id="health-summary" className="note-card" />
                <ResearchTable headers={["验收项", "状态", "说明"]} bodyId="health-body" />
            </article>
            <article className="panel">
                <div className="card-header">
                    <h2>
                        本地告警 <small>只读展示；确认操作请使用 CLI</small>
                    </h2>
                </div>
                <div id="alerts" className="alerts" />
            </article>
        </section>
    );
}
