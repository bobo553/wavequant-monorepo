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
                点击图表或 Tab 聚焦后：↑ 放大 · ↓ 缩小 · ← → 移动，长按连续操作
            </p>
            <div className="chart-position">
                <div className="chart-position-heading">
                    <label htmlFor="chart-position-slider">当前图窗位置</label>
                    <output id="chart-position-label" htmlFor="chart-position-slider">
                        等待行情数据
                    </output>
                </div>
                <input
                    id="chart-position-slider"
                    type="range"
                    min="0"
                    max="0"
                    step="any"
                    defaultValue="0"
                    aria-controls="price-chart"
                    disabled
                />
            </div>
            <div id="chart-loading-overlay" className="chart-loading-overlay" role="status" aria-live="polite" hidden>
                <span className="chart-loading-spinner" aria-hidden="true" />
                <span>正在加载股票数据…</span>
            </div>
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
        </>
    );
}
