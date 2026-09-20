import type { JSX } from "react";

const metrics = [
    ["metric-return", "个股净收益", "资金曲线含费用、未平仓估值"],
    ["metric-dd", "最大回撤", "从初始资金与历史峰值计算"],
    ["metric-trades", "卖出成交", "含减仓；按已实现份额统计"],
    ["metric-exposure", "平均仓位", "市值 / 账户净值的日均值"],
] as const;

/** 随结果口径和回放日期更新的四项核心研究指标。 */
export function ResearchMetrics(): JSX.Element {
    return (
        <>
            <section className="metric-grid" aria-label="组合指标">
                {metrics.map(([id, label, description], index) => (
                    <article className="metric panel" key={id}>
                        <span>
                            {index === 0 ? <b id="metric-scope-label">{label}</b> : label}{" "}
                            <small id={index === 2 ? "trade-scope-label" : undefined}>
                                {index === 2 ? "当前股票" : "截至回放日期"}
                            </small>
                        </span>
                        <strong id={id}>—</strong>
                        <p>{description}</p>
                    </article>
                ))}
            </section>
            <p className="comparison">
                同比 / 环比 N/A：独立历史实验。通过成本场景比较相同日期区间，不将不同实验当作连续运营指标。
            </p>
        </>
    );
}
