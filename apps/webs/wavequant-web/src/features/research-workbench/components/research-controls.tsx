import type { JSX } from "react";

/** 全局研究口径、策略、成本和现场回测控制。 */
export function ResearchControls(): JSX.Element {
    return (
        <>
            <div className="filters panel">
                <label>
                    数据 / 结果口径
                    <select id="result-scope" aria-label="结果口径" defaultValue="akshare">
                        <option value="akshare">AkShare · 在线 A 股行情</option>
                        <option value="akshare-backtest">AkShare · 同源股票回测</option>
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
                        <option value="lecture_v3">V3 · 二/三级交替后 N 轧空</option>
                        <option value="lecture_v3_d67_c33">V3 · 第一类 &gt;2/3 / 第二类 ≤1/3</option>
                        <option value="lecture_v3_d50_c50">V3 · 第一类 &gt;1/2 / 第二类 ≤1/2</option>
                        <option value="lecture_v3_d67_c50">V3 · 第一类 &gt;2/3 / 第二类 ≤1/2</option>
                        <option value="lecture_v3_close_d50_c50">
                            V3 · 第一类最低收盘 &gt;1/2 / 第二类最低收盘 &lt;1/2
                        </option>
                        <option value="lecture_v2">分级双买点 V2 · 旧规则对照</option>
                        <option value="lecture_v1">讲义因果版 V1 · 研究对照</option>
                        <option value="strict_full">旧严格折线版 · 研究对照</option>
                        <option value="proxy_full">日线代理版 · 研究对照</option>
                    </select>
                </label>
                <label id="second-pullback-field">
                    第二类浅回撤
                    <select id="second-pullback-select" aria-label="第二类浅回撤" defaultValue="third">
                        <option value="third">浅回撤 ≤1/3（默认）</option>
                        <option value="half">浅回撤 &lt;1/2</option>
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
                <label title="回测账户的起始资金；仅影响当前股票回测与幅度方案对比，不修改封存组合。">
                    初始资金（万）
                    <input
                        type="number"
                        id="backtest-capital"
                        defaultValue="10"
                        min="0.01"
                        max="100000"
                        step="any"
                        inputMode="decimal"
                        aria-label="回测初始资金，单位万元"
                    />
                </label>
                <label title="每笔买入的单股仓位上限，以买入时账户权益计算；实际成交还受风险预算、流动性、可用现金和整手限制。">
                    每次买入比例（%）
                    <input
                        type="number"
                        id="backtest-buy-ratio"
                        defaultValue="100"
                        min="0.01"
                        max="100"
                        step="any"
                        inputMode="decimal"
                        aria-label="每次买入比例，百分比"
                        aria-describedby="backtest-sizing-help"
                    />
                </label>
                <label
                    className="backtest-volume-filter"
                    title="V3 放量为确认时成交量严格超过前一交易日全天量；盘中使用截至确认时的累计量。C 浪跳空突破已知回调折线高点可独立触发。历史 V1/V2 对照保留原均量口径。"
                >
                    <input type="checkbox" id="backtest-volume-filter" />
                    启用量能过滤（V3：成交量 ＞ 昨日）
                </label>
                <label
                    className="backtest-volume-filter"
                    title="默认关闭。勾选后，执行价格计入滑点和费用的净盈亏比低于策略门槛时不买入；适用于当前股票回测与幅度方案对比。"
                >
                    <input type="checkbox" id="backtest-net-reward-risk-filter" />
                    启用成交价含费净盈亏比过滤
                </label>
                <label
                    id="shallow-base-breakout-field"
                    className="backtest-volume-filter"
                    title="V3 默认开启。已确认高低结构之后，0.618 至不足 2/3 的回撤先列为待选；守低横盘至少 40 根 K 线后，放量大阳线收盘突破整理区间才产生独立买点。"
                >
                    <input type="checkbox" id="backtest-shallow-base-breakout" defaultChecked />
                    启用浅回撤横盘突破买点
                </label>
                <button id="run-stock-backtest">运行当前股票回测</button>
                <div
                    id="stock-backtest-status"
                    className="stock-backtest-status"
                    role="status"
                    aria-live="polite"
                    hidden
                >
                    <span className="stock-backtest-indicator" aria-hidden="true" />
                    <span id="stock-backtest-status-text" />
                    <span id="stock-backtest-other" className="stock-backtest-other" />
                </div>
                <button id="download-backtest" disabled>
                    导出回测账本
                </button>
                <span id="backtest-sizing-help">
                    买入比例为单股仓位上限，实际成交仍受风险预算、流动性、现金和整手限制。终点为回放日期 · 沪深京普通 A
                    股按板块规则回测 · ST / 退市暂不生成成交
                </span>
            </div>
            <div id="error" className="message error" role="alert" hidden />
            <section className="panel note-card" aria-label="幅度方案对比">
                <button id="compare-ratios" disabled>
                    对比幅度方案
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
                    第一类旧方案用交替低点，新方案用交替前最低收盘；第二类均用整段最低收盘。历史样本内对比不等于样本外有效性，也不自动推荐最高胜率方案。
                </p>
            </section>
            <div className="loading-slot">
                <div id="loading" className="loading-line" role="status">
                    正在校验并读取本地结果…
                </div>
            </div>
            <div id="evidence" className="evidence-banner">
                研究结果不构成交易建议，工程通过不等于策略有效。
            </div>
        </>
    );
}
