import type { JSX } from "react";

/** K线画布焦点、方向键提示与图窗位置滑块，保留原有运行时挂载点。 */
export function ChartViewport(): JSX.Element {
    return (
        <>
            <div id="ohlc" className="ohlc" aria-live="off">
                <span id="ohlc-text">鼠标移动至 K 线查看价格</span>
                <button id="copy-candle" type="button" aria-label="复制当前 K 线数据" disabled>
                    复制 K 线
                </button>
                <span id="candle-copy-feedback" className="candle-copy-feedback" role="status" aria-live="polite" />
            </div>
            <section id="chart-preparing-state" className="chart-preparing-state" role="status" hidden>
                <strong>正在准备当前股票的行情与回测</strong>
                <p id="chart-preparing-message">正在读取日线，行情到达后自动显示。</p>
                <p>你可以切换股票或调整设置，后台任务会继续计算。</p>
            </section>
            <div
                id="price-chart"
                className="price-chart"
                role="region"
                tabIndex={0}
                aria-label="TradingView K线与成交量图"
                aria-describedby="chart-keyboard-help"
                aria-keyshortcuts="ArrowUp ArrowDown ArrowLeft ArrowRight"
            />
            <p id="chart-keyboard-help" className="chart-keyboard-help">
                鼠标在 K 线图内或图表聚焦时：↑ 放大 · ↓ 缩小 · ← → 移动，长按连续操作
            </p>
            <div className="chart-position">
                <div className="chart-position-heading">
                    <label htmlFor="chart-position-slider">当前图窗位置</label>
                    <output id="chart-position-label" htmlFor="chart-position-slider">
                        等待行情数据
                    </output>
                </div>
                <div id="chart-position-timeline">
                    <div className="chart-position-track">
                        <div className="chart-position-rail" aria-hidden="true">
                            <div className="chart-backtest-track">
                                <span />
                            </div>
                            <div className="chart-eligibility-track" data-eligibility-segments="" />
                        </div>
                        <input
                            id="chart-position-slider"
                            type="range"
                            min="0"
                            max="0"
                            step="any"
                            defaultValue="0"
                            aria-controls="price-chart"
                            aria-describedby="chart-position-legend"
                            disabled
                        />
                    </div>
                    <div id="chart-position-legend" className="chart-position-legend">
                        <span>
                            <i className="chart-progress-key" aria-hidden="true" />
                            <span data-backtest-progress-label="">等待回测</span>
                        </span>
                        <span>
                            <i className="chart-pending-key" aria-hidden="true" />
                            未回测
                        </span>
                        <span>
                            <i className="chart-eligibility-key" aria-hidden="true" />
                            <span data-eligibility-count="">资格区间 0 段</span>
                            <button
                                id="copy-eligibility-ranges"
                                type="button"
                                disabled
                                data-copy-eligibility=""
                                data-feedback-id="eligibility-copy-feedback"
                                aria-label="复制当前股票的全部已显示资格区间"
                                title="复制资格区间及确认、结束日期"
                            >
                                复制
                            </button>
                            <span data-current-eligibility-action="" hidden>
                                <button
                                    id="copy-current-eligibility-range-timeline"
                                    type="button"
                                    data-copy-eligibility="current"
                                    data-default-label="复制当前区间"
                                    data-feedback-id="current-eligibility-timeline-copy-feedback"
                                    aria-label="只复制进度条当前位置所在的交易资格区间"
                                    hidden
                                    disabled
                                >
                                    复制当前区间
                                </button>
                                <span
                                    id="current-eligibility-timeline-copy-feedback"
                                    role="status"
                                    aria-live="polite"
                                />
                            </span>
                            <span id="eligibility-copy-feedback" role="status" aria-live="polite" />
                        </span>
                        <span>拖动定位图窗中心</span>
                    </div>
                </div>
            </div>
            <div id="chart-loading-overlay" className="chart-loading-overlay" role="status" aria-live="polite" hidden>
                <span className="chart-loading-spinner" aria-hidden="true" />
                <span>正在加载股票数据…</span>
            </div>
            <div id="stock-data-load-status" className="stock-data-load-status" role="status" aria-live="polite" hidden>
                <span id="stock-data-load-message" />
                <button id="stock-data-load-retry" type="button" hidden>
                    重试加载
                </button>
            </div>
            <div className="replay">
                <div className="replay-title">
                    <span>历史回放</span>
                    <strong id="asof-label">—</strong>
                    <span id="replay-mode" className="tag">
                        最新截面
                    </span>
                </div>
                <div id="replay-controls" className="replay-controls" hidden>
                    <button id="previous" aria-label="前一根K线">
                        ‹
                    </button>
                    <input id="replay-slider" type="hidden" min="0" max="0" defaultValue="0" />
                    <button id="next" aria-label="后一根K线">
                        ›
                    </button>
                    <button id="latest">到最新</button>
                </div>
                <p>股票回测区间固定为 2018-01-01 至最新交易日 · 图表可查看历史行情</p>
            </div>
        </>
    );
}
