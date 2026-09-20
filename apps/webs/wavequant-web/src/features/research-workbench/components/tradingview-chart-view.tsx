import type { JSX } from "react";

/** 独立外部绘图视图；原有研究图表与回测控件始终留在同一张卡片中。 */
export function TradingViewChartView(): JSX.Element {
    return (
        <>
            <div id="chart-view-tabs" className="chart-view-tabs" role="tablist" aria-label="K 线图表视图">
                <button
                    id="chart-local-tab"
                    type="button"
                    role="tab"
                    aria-controls="price-chart"
                    aria-selected="true"
                    tabIndex={0}
                >
                    本地研究图
                </button>
                <button
                    id="chart-tradingview-tab"
                    type="button"
                    role="tab"
                    aria-controls="tradingview-panel"
                    aria-selected="false"
                    tabIndex={-1}
                >
                    TradingView 画图
                </button>
            </div>
            <section
                id="tradingview-panel"
                className="tradingview-panel"
                role="tabpanel"
                aria-labelledby="chart-tradingview-tab"
                hidden
            >
                <div className="tradingview-context">
                    <div>
                        <strong id="tradingview-symbol">当前股票</strong>
                        <p>
                            TradingView 独立行情与绘图工具；不含本地因果复权、策略线、B/S 成交或历史回放。
                            沪深股票在该嵌入图中为交易所日终口径。
                        </p>
                    </div>
                    <div className="tradingview-actions">
                        <button id="tradingview-sync" type="button" hidden>
                            同步当前股票
                        </button>
                        <button id="tradingview-retry" type="button" hidden>
                            重试加载
                        </button>
                        <a
                            id="tradingview-open"
                            href="https://www.tradingview.com/"
                            target="_blank"
                            rel="noopener noreferrer"
                            hidden
                        >
                            在 TradingView 打开 ↗
                        </a>
                    </div>
                </div>
                <p id="tradingview-status" className="tradingview-status" role="status" aria-live="polite" hidden />
                <div
                    id="tradingview-widget"
                    className="tradingview-widget-container"
                    aria-label="TradingView 高级绘图图表"
                />
                <p className="tradingview-disclaimer">
                    画线只存在于 TradingView
                    图表中，不会修改本地研究数据。切换本地股票后需手动同步；重新加载外部图表可能丢失未保存的画线。
                </p>
            </section>
        </>
    );
}
