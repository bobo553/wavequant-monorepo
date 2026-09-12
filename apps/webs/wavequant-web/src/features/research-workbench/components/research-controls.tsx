import type { JSX } from "react";

/** 全局研究口径、策略、成本和现场回测控制。 */
export function ResearchControls(): JSX.Element {
    return (
        <>
            <div className="filters panel">
                <label>
                    数据 / 结果口径
                    <select id="result-scope" aria-label="结果口径" defaultValue="tdx">
                        <option value="tdx">通达信 · 全部 A 股行情</option>
                        <option value="tdx-backtest">通达信 · 当前股票回测</option>
                        <option value="stock">个股独立回测 · 封存样本</option>
                        <option value="portfolio">原封存组合</option>
                    </select>
                </label>
                <label>
                    实验快照
                    <select id="run-select" aria-label="实验快照" />
                </label>
                <label>
                    策略 / 幅度方案
                    <select id="variant-select" aria-label="策略版本" defaultValue="lecture_v3">
                        <option value="lecture_v3">V3 · 第一类 &gt;1/2 / 第二类 ≤1/3</option>
                        <option value="lecture_v3_d67_c33">V3 · 第一类 &gt;2/3 / 第二类 ≤1/3</option>
                        <option value="lecture_v3_d50_c50">V3 · 第一类 &gt;1/2 / 第二类 ≤1/2</option>
                        <option value="lecture_v3_d67_c50">V3 · 第一类 &gt;2/3 / 第二类 ≤1/2</option>
                        <option value="lecture_v2">分级双买点 V2 · 旧规则对照</option>
                        <option value="lecture_v1">讲义因果版 V1 · 研究对照</option>
                        <option value="strict_full">旧严格折线版 · 研究对照</option>
                        <option value="proxy_full">日线代理版 · 研究对照</option>
                    </select>
                </label>
                <label>
                    成本场景
                    <select id="scenario-select" aria-label="成本场景" defaultValue="base">
                        <option value="base">原费用</option>
                        <option value="cost_2x">2 倍成本</option>
                        <option value="cost_3x">3 倍成本</option>
                        <option value="capacity_half">半容量</option>
                    </select>
                </label>
                <div className="filter-context">
                    <span className="tiny-dot" />
                    <span id="data-range">读取已封存实验…</span>
                </div>
            </div>
            <div className="backtest-toolbar panel">
                <label>
                    回测起点 <input type="date" id="backtest-start" defaultValue="2018-01-01" aria-label="回测起点" />
                </label>
                <button id="run-stock-backtest">运行当前股票回测</button>
                <button id="download-backtest" disabled>
                    导出回测账本
                </button>
                <span>终点为回放日期 · 沪深主板非 ST 研究模型 · 其他板块仅行情浏览</span>
            </div>
            <div id="error" className="message error" role="alert" hidden />
            <section className="panel note-card" aria-label="幅度方案对比">
                <button id="compare-ratios" disabled>
                    对比四组幅度
                </button>
                <button id="cancel-ratios" disabled>
                    取消对比
                </button>
                <p id="ratio-status" role="status">
                    当前股票 · 相同回测区间和成本。准确率按扣费后已平仓交易胜率统计；无样本不记为 0%。
                </p>
                <div style={{ overflowX: "auto" }}>
                    <table>
                        <thead>
                            <tr>
                                {[
                                    "第一类 / 第二类",
                                    "买点数",
                                    "买入成交",
                                    "已平仓",
                                    "扣费胜率",
                                    "净收益",
                                    "最大回撤",
                                    "复盘",
                                ].map((label) => (
                                    <th key={label}>{label}</th>
                                ))}
                            </tr>
                        </thead>
                        <tbody id="ratio-results" />
                    </table>
                </div>
                <p className="muted">
                    第一类用交替低点，第二类用整段最低收盘；历史样本内对比不等于样本外有效性，也不自动推荐最高胜率方案。
                </p>
            </section>
            <div id="loading" className="loading-line" role="status">
                正在校验并读取本地结果…
            </div>
            <div id="evidence" className="evidence-banner">
                研究结果不构成交易建议，工程通过不等于策略有效。
            </div>
        </>
    );
}
