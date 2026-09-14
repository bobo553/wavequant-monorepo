import type { JSX } from "react";

const chartLayers = [
    ["show-volume", "成交量（股）", true],
    ["show-markers", "买卖信号", true],
    ["show-fills", "B / S 成交", true],
    ["show-rules", "规则标识", true],
    ["show-diagnostics", "筛选 / 中断", false],
    ["show-theory", "折线 / N 字", true],
    ["show-trend-prices", "一级点位价格", true],
    ["show-last-fall-high", "各级末跌高", true],
    ["show-bear-to-bull-highs", "各级空翻多高点", true],
    ["show-bear-bull-alternation-lows", "各级空多交替低点", true],
    ["show-post-alternation-bull-highs", "各级交替后多头段高点", true],
    ["show-bullish-turn-signals", "各级转多信号", true],
    ["show-levels", "选中点位线", true],
] as const;

const chartTimeframes = [
    ["1d", "日线"],
    ["1w", "周线"],
    ["1mo", "月线"],
    ["3mo", "季线"],
    ["1y", "年线"],
] as const;

/** Lightweight Charts 容器及所有原有图层、趋势和历史回放控制。 */
export function ResearchChart(): JSX.Element {
    return (
        <article className="panel chart-card">
            <div className="card-header">
                <div className="symbol-title">
                    <select id="symbol-select" aria-label="股票" />
                    <label className="timeframe-control">
                        <span>周期</span>
                        <select id="timeframe-select" aria-label="K线周期" defaultValue="1d">
                            {chartTimeframes.map(([value, label]) => (
                                <option key={value} value={value}>
                                    {label}
                                </option>
                            ))}
                        </select>
                    </label>
                    <span id="timeframe-tag" className="tag">
                        日 K
                    </span>
                    <span id="partial-timeframe" className="tag timeframe-partial" hidden>
                        周期未收完
                    </span>
                    <span id="price-basis" className="muted tag">
                        因果复权
                    </span>
                </div>
                <button id="focus-fill" className="subtle-button">
                    定位最近成交 ↗
                </button>
            </div>
            <div className="chart-legend">
                {chartLayers.map(([id, label, checked]) => (
                    <label key={id}>
                        <input id={id} type="checkbox" defaultChecked={checked} /> {label}
                    </label>
                ))}
                <span id="theory-status">等待标注</span>
            </div>
            <div className="marker-key">
                <label>
                    折线口径
                    <select id="drawing-mode" aria-label="折线口径" defaultValue="lecture">
                        <option value="lecture">讲义完整绘图</option>
                        <option value="strategy">策略已确认拐点</option>
                    </select>
                </label>
                <span style={{ color: "#ffd36d" }}>黄虚线：普通高低 · 无端点圆圈</span>
                <span className="key-last-fall-high">Ⅰ/Ⅱ/Ⅲ 末跌高：来源 H → 对应图窗最低 L</span>
                <span className="key-bear-to-bull-high">Ⅰ/Ⅱ/Ⅲ 空翻多高点：Python 确认 H</span>
                <span className="key-bear-bull-alternation-low">Ⅰ/Ⅱ/Ⅲ 空多交替低点：Python 确认 L</span>
                <span className="key-post-alternation-bull-high">Ⅰ/Ⅱ/Ⅲ 交替后多头段高点：Python 确认 H</span>
                <span className="key-bullish-turn-signal">Ⅰ/Ⅱ/Ⅲ 转多信号：交替后收盘突破空翻多高点</span>
                <label style={{ color: "#50dfd2" }}>
                    <input id="show-teaching" type="checkbox" defaultChecked /> 子母段青色强调（不拆线）
                </label>
                <span className="key-rule">■ 规则确认</span>
                <span className="key-signal">● 买入信号</span>
                <span className="key-exit">● 退出信号 ≠ 卖出</span>
                <span className="key-buy">↑ B 买入成交价</span>
                <span className="key-sell">↓ S 卖出成交价</span>
            </div>
            <p id="drawing-status" className="drawing-status">
                讲义绘图保留同棒顺序；未给出的母子规则不补线。
            </p>
            <TrendControl
                id="show-trend"
                summaryId="trend-summary"
                className="trend-controls"
                label="一级趋势线"
                description="浅蓝实线按价格方向严格高低交替 · 跨小拐点 · 路径间以真实已确认极值正式衔接并进入二级计算 · 未确认尾端不延伸"
                waiting="等待一级趋势线结构…"
            />
            <TrendControl
                id="show-secondary-trend"
                summaryId="secondary-trend-summary"
                className="trend-controls secondary-controls"
                label="二级趋势线"
                description="紫色实线首尾衔接 · 基于一级关键位转换，或突破旧二级末跌高后由场景回撤与非正式二级反转确认升级 · 同类端点经一级反向极值连接 · 衔接仅用于显示"
                waiting="等待二级趋势线结构…"
            />
            <TrendControl
                id="show-tertiary-trend"
                summaryId="tertiary-trend-summary"
                className="trend-controls tertiary-controls"
                label="三级趋势线"
                description="橙色实线为正式 Ⅲ·L ↔ Ⅲ·H；橙色虚线串联已确认的二级内部转折，并保留待决尾部全部二级点直到最新点，不升级为正式三级点"
                waiting="等待三级趋势线结构…"
            />
            <div id="ohlc" className="ohlc" aria-live="off">
                鼠标移动至 K 线查看价格
            </div>
            <div id="price-chart" className="price-chart" aria-label="TradingView K线与成交量图" />
            <div className="replay">
                <div className="replay-title">
                    <span>历史回放</span>
                    <strong id="asof-label">—</strong>
                    <span id="replay-mode" className="tag">
                        最新截面
                    </span>
                </div>
                <div className="replay-controls">
                    <button id="previous" aria-label="前一根K线">
                        ‹
                    </button>
                    <input id="replay-slider" type="range" min="0" max="0" defaultValue="0" aria-label="回放日期" />
                    <button id="next" aria-label="后一根K线">
                        ›
                    </button>
                    <button id="latest">到最新</button>
                </div>
                <p>按收盘截面回放 · 只呈现当时已确认的信息 · 不模拟盘中高低先后</p>
            </div>
        </article>
    );
}

interface ITrendControlProps {
    className: string;
    description: string;
    id: string;
    label: string;
    summaryId: string;
    waiting: string;
}

function TrendControl({ className, description, id, label, summaryId, waiting }: ITrendControlProps): JSX.Element {
    return (
        <>
            <div className={className}>
                <label>
                    <input id={id} type="checkbox" defaultChecked /> {label}
                </label>
                <small>{description}</small>
            </div>
            <p id={summaryId} className="trend-summary">
                {waiting}
            </p>
        </>
    );
}
