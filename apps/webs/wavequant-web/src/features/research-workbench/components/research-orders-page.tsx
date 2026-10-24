"use client";
import type { JSX } from "react";
import { ResearchTable } from "./research-ledgers";
import { useResearchSection } from "../runtime/use-research-section";

export function OrdersPage(): JSX.Element {
    const active = useResearchSection("orders");
    return (
        <section id="page-orders" className="page" hidden={!active}>
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
