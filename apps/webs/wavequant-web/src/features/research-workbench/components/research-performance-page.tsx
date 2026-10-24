"use client";
import type { JSX } from "react";
import { ResearchTable } from "./research-ledgers";
import { useResearchSection } from "../runtime/use-research-section";

export function PerformancePage(): JSX.Element {
    const active = useResearchSection("performance");
    return (
        <section id="page-performance" className="page" hidden={!active}>
            <article className="panel">
                <div className="card-header performance-card-header">
                    <h2>
                        账户净值 <small>初始资金归一化为 1</small>
                    </h2>
                    <span id="curve-scope-label" className="tag">
                        个股独立净值
                    </span>
                    <button id="performance-dd-jump" className="drawdown-range-jump" type="button" hidden>
                        最大回撤 <span id="performance-dd-period" /> · 定位买入至清仓
                    </button>
                </div>
                <div id="equity-chart" className="performance-chart" aria-label="账户净值图" />
            </article>
            <div className="two-col">
                <article className="panel">
                    <div className="card-header">
                        <h2>
                            持仓最大亏损 <small>% · 每次清仓归零</small>
                        </h2>
                    </div>
                    <div id="drawdown-chart" className="small-chart" aria-label="按持仓轮次归零的最大亏损图" />
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
            <article className="panel">
                <div className="card-header">
                    <h2>
                        各轮持仓最大亏损 <small>当前股票 · 买入至完全卖出</small>
                    </h2>
                </div>
                <ResearchTable
                    headers={["持仓轮次", "买入成交", "完全卖出", "最大亏损", "最低价时点", "定位"]}
                    bodyId="holding-cycles-body"
                />
                <p id="holding-cycles-empty" className="empty">
                    暂无本口径的持仓记录，请运行回测。
                </p>
            </article>
            <div className="panel note-card">
                每轮从持仓为零后的首次买入开始，到数量归零的完全卖出结束。清仓后本轮计算归零，再次买入独立计算；最大回撤取所有完整轮次中最深的最大亏损，未清仓不计入最终统计。减仓不重置，加仓按当时均价，不含费用。曲线显示每个交易日收盘时本轮累计最大亏损，明细保留各轮最终结果。
            </div>
        </section>
    );
}
