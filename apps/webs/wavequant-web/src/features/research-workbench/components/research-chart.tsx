import type { JSX } from "react";

interface IChartLayer {
    checked: boolean;
    description: string;
    id: string;
    label: string;
    tone: "base" | "cyan" | "green" | "orange" | "purple" | "blue";
}

interface ITrendLayer extends IChartLayer {
    summaryId: string;
    waiting: string;
}

const chartLayers: readonly IChartLayer[] = [
    {
        id: "show-volume",
        label: "成交量（股）",
        checked: true,
        description: "在价格图下方显示按股计量的成交量柱，颜色跟随当根 K 线涨跌。",
        tone: "base",
    },
    {
        id: "show-markers",
        label: "买卖信号",
        checked: true,
        description: "显示策略或结构产生的买入、退出信号；信号用于研究，不等同于实际成交。",
        tone: "cyan",
    },
    {
        id: "show-fills",
        label: "B / S 成交",
        checked: true,
        description: "显示回测账本中的实际模拟成交价；B 为买入，S 为卖出。",
        tone: "orange",
    },
    {
        id: "show-rules",
        label: "规则标识",
        checked: true,
        description: "显示规则在当时已经可知的确认点，避免把事后信息倒填到历史 K 线。",
        tone: "purple",
    },
    {
        id: "show-diagnostics",
        label: "筛选 / 中断",
        checked: false,
        description: "显示候选信号被筛除、等待确认或计算中断的位置，主要用于诊断规则。",
        tone: "base",
    },
    {
        id: "show-theory",
        label: "折线 / N 字",
        checked: true,
        description: "显示服务器按当前周期计算的讲义折线、N 字结构和结构确认信息。",
        tone: "blue",
    },
    {
        id: "show-trend-prices",
        label: "一级点位价格",
        checked: true,
        description: "在一级趋势端点旁显示紧凑价格标签；只影响显示，不改变趋势计算。",
        tone: "blue",
    },
    {
        id: "show-last-fall-high",
        label: "各级末跌高",
        checked: true,
        description: "显示各级下跌结构的最后关键高点，并用虚线跟踪其后是否被收盘严格突破。",
        tone: "blue",
    },
    {
        id: "show-bear-to-bull-highs",
        label: "各级空翻多高点",
        checked: true,
        description: "显示 Python 已确认的空头转多头关键高点，Ⅰ/Ⅱ/Ⅲ 分别对应一至三级趋势。",
        tone: "purple",
    },
    {
        id: "show-bear-bull-alternation-lows",
        label: "各级空多交替低点",
        checked: true,
        description: "显示空翻多之后经回档比例与因果确认成立的交替低点。",
        tone: "green",
    },
    {
        id: "show-post-alternation-bull-highs",
        label: "各级交替后多头段高点",
        checked: true,
        description: "显示空多交替低点之后第一段已确认上涨趋势的终点高点。",
        tone: "blue",
    },
    {
        id: "show-bullish-turn-signals",
        label: "各级转多信号",
        checked: true,
        description: "显示交替确认后首次收盘严格突破对应空翻多高点的 K 线和证据虚线。",
        tone: "green",
    },
    {
        id: "show-levels",
        label: "选中点位线",
        checked: true,
        description: "点击图表标识或证据记录后，显示所选价格水平的辅助定位线。",
        tone: "base",
    },
];

const trendLayers: readonly ITrendLayer[] = [
    {
        id: "show-trend",
        label: "一级趋势线",
        checked: true,
        description: "浅蓝实线按价格方向严格高低交替；跨路径只用真实已确认极值衔接，未确认尾端不延伸。",
        tone: "blue",
        summaryId: "trend-summary",
        waiting: "等待一级趋势线结构…",
    },
    {
        id: "show-secondary-trend",
        label: "二级趋势线",
        checked: true,
        description: "紫色实线基于一级关键位转换；紫色虚线仅展示已确认一级内部转折和待决尾部，不升级正式点。",
        tone: "purple",
        summaryId: "secondary-trend-summary",
        waiting: "等待二级趋势线结构…",
    },
    {
        id: "show-tertiary-trend",
        label: "三级趋势线",
        checked: true,
        description: "橙色实线连接正式三级点；橙色虚线保留已确认二级内部转折和待决尾部，不进入策略或回测。",
        tone: "orange",
        summaryId: "tertiary-trend-summary",
        waiting: "等待三级趋势线结构…",
    },
];

const chartTimeframes = [
    ["1d", "日"],
    ["1w", "周"],
    ["1mo", "月"],
    ["3mo", "季"],
    ["1y", "年"],
] as const;

/** Lightweight Charts 容器及所有原有图层、趋势和历史回放控制。 */
export function ResearchChart(): JSX.Element {
    return (
        <article className="panel chart-card">
            <div className="card-header chart-card-header">
                <div className="symbol-title">
                    <select id="symbol-select" aria-label="股票" />
                    <div id="timeframe-select" className="timeframe-tabs" role="tablist" aria-label="K线周期">
                        {chartTimeframes.map(([value, label], index) => (
                            <button
                                key={value}
                                type="button"
                                role="tab"
                                data-timeframe={value}
                                aria-selected={index === 0}
                                tabIndex={index === 0 ? 0 : -1}
                                title={`${label}线 K 线及结构画线`}
                            >
                                {label}
                            </button>
                        ))}
                    </div>
                    <span id="timeframe-tag" className="tag" hidden>
                        日 K
                    </span>
                    <span id="partial-timeframe" className="tag timeframe-partial" hidden>
                        周期未收完
                    </span>
                    <span id="price-basis" className="muted tag">
                        因果复权
                    </span>
                </div>
                <div className="chart-header-actions" aria-label="图表工具">
                    <label className="drawing-mode-control">
                        <span>折线</span>
                        <select id="drawing-mode" aria-label="折线口径" defaultValue="lecture">
                            <option value="lecture">讲义绘图</option>
                            <option value="strategy">确认拐点</option>
                        </select>
                    </label>
                    <ChartToolTrigger
                        controls="chart-layers-popover"
                        id="chart-layers-trigger"
                        label="图层"
                        meta="16/17"
                        metaId="layer-toggle-count"
                    />
                    <ChartToolTrigger controls="chart-guide-popover" id="chart-guide-trigger" label="图例" meta="?" />
                    <button id="focus-fill" className="subtle-button" aria-label="定位最近成交 ↗">
                        <span className="focus-fill-prefix">定位最近</span>成交 ↗
                    </button>
                </div>
            </div>

            <div id="chart-layers-popover" className="chart-tool-popover chart-layers-popover" popover="manual">
                <div className="chart-popover-header">
                    <div>
                        <strong>图层显示</strong>
                        <span>悬停或聚焦选项查看定义，修改立即生效</span>
                    </div>
                    <span id="theory-status" className="tool-status" aria-live="polite">
                        等待标注
                    </span>
                </div>
                <section className="layer-section" aria-labelledby="overlay-layer-heading">
                    <h3 id="overlay-layer-heading">标识与辅助</h3>
                    <div className="layer-grid">
                        {chartLayers.map((layer) => (
                            <LayerControl key={layer.id} {...layer} />
                        ))}
                        <LayerControl
                            id="show-teaching"
                            label="子母段青色强调"
                            checked
                            description="用青色强调子母段关系，但不拆分原折线，也不改变母子顺序或结构计算。"
                            tone="cyan"
                        />
                    </div>
                </section>
                <section className="layer-section trend-layer-section" aria-labelledby="trend-layer-heading">
                    <h3 id="trend-layer-heading">趋势级别</h3>
                    <div className="layer-grid trend-layer-grid">
                        {trendLayers.map((layer) => (
                            <TrendControl key={layer.id} {...layer} />
                        ))}
                    </div>
                </section>
            </div>

            <div id="chart-guide-popover" className="chart-tool-popover chart-guide-popover" popover="manual">
                <div className="chart-popover-header">
                    <div>
                        <strong>图例与口径</strong>
                        <span>颜色同时配有文字，结构标识不直接构成交易建议</span>
                    </div>
                </div>
                <div className="marker-key legend-grid">
                    <span className="key-standard">黄虚线 · 普通高低，无端点圆圈</span>
                    <span className="key-last-fall-high">Ⅰ/Ⅱ/Ⅲ 末跌高 · 来源 H → 图窗最低 L</span>
                    <span className="key-bear-to-bull-high">Ⅰ/Ⅱ/Ⅲ 空翻多高点 · Python 确认 H</span>
                    <span className="key-bear-bull-alternation-low">Ⅰ/Ⅱ/Ⅲ 空多交替低点 · Python 确认 L</span>
                    <span className="key-post-alternation-bull-high">Ⅰ/Ⅱ/Ⅲ 交替后多头段高点 · Python 确认 H</span>
                    <span className="key-bullish-turn-signal">Ⅰ/Ⅱ/Ⅲ 转多信号 · 收盘突破空翻多高点</span>
                    <span className="key-rule">■ 规则确认</span>
                    <span className="key-signal">● 买入信号</span>
                    <span className="key-exit">● 退出信号 ≠ 卖出</span>
                    <span className="key-buy">↑ B 买入成交价</span>
                    <span className="key-sell">↓ S 卖出成交价</span>
                </div>
                <p id="drawing-status" className="drawing-status">
                    讲义绘图保留同棒顺序；未给出的母子规则不补线。
                </p>
            </div>

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

interface IChartToolTriggerProps {
    controls: string;
    id: string;
    label: string;
    meta: string;
    metaId?: string;
}

function ChartToolTrigger({ controls, id, label, meta, metaId }: IChartToolTriggerProps): JSX.Element {
    return (
        <button id={id} className="chart-tool-trigger" type="button" aria-controls={controls} aria-expanded="false">
            <span>{label}</span>
            <span id={metaId} className="chart-tool-meta" aria-live={metaId ? "polite" : undefined}>
                {meta}
            </span>
        </button>
    );
}

function LayerControl({ checked, description, id, label, tone }: IChartLayer): JSX.Element {
    const helpId = `${id}-help`;
    return (
        <label className={`layer-option layer-option--${tone}`}>
            <input
                id={id}
                type="checkbox"
                defaultChecked={checked}
                aria-describedby={helpId}
                data-chart-layer-toggle="true"
            />
            <span className="layer-option-label">{label}</span>
            <span className="layer-option-info" aria-hidden="true">
                i
            </span>
            <span id={helpId} className="layer-option-tooltip" role="tooltip">
                {description}
            </span>
        </label>
    );
}

function TrendControl({ checked, description, id, label, summaryId, tone, waiting }: ITrendLayer): JSX.Element {
    const helpId = `${id}-help`;
    return (
        <label className={`layer-option trend-layer-option layer-option--${tone}`}>
            <input
                id={id}
                type="checkbox"
                defaultChecked={checked}
                aria-describedby={helpId}
                data-chart-layer-toggle="true"
            />
            <span className="layer-option-label">{label}</span>
            <span className="layer-option-info" aria-hidden="true">
                i
            </span>
            <span id={helpId} className="layer-option-tooltip trend-option-tooltip" role="tooltip">
                <span>{description}</span>
                <strong id={summaryId} className="trend-summary">
                    {waiting}
                </strong>
            </span>
        </label>
    );
}
