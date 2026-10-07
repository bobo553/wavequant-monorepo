import type { JSX } from "react";

import { IconChevronLeft, IconChevronRight, IconMinus, IconPlus } from "@tabler/icons-react";

/** 本地图表缩放、平移及成交播放工具，保持既有控制元素 ID。 */
export function ChartViewNavigation(): JSX.Element {
    return (
        <>
            <div className="chart-navigation" role="group" aria-label="本地图表视图控制">
                <div className="chart-navigation-buttons">
                    <button
                        id="chart-pan-left"
                        type="button"
                        aria-label="视图左移"
                        aria-controls="price-chart"
                        title="向较早 K 线移动，长按连续移动；图表聚焦后按 ←"
                        disabled
                    >
                        <IconChevronLeft size={17} stroke={1.8} aria-hidden="true" />
                    </button>
                    <button
                        id="chart-pan-right"
                        type="button"
                        aria-label="视图右移"
                        aria-controls="price-chart"
                        title="向较晚 K 线移动，长按连续移动；图表聚焦后按 →"
                        disabled
                    >
                        <IconChevronRight size={17} stroke={1.8} aria-hidden="true" />
                    </button>
                    <button
                        id="chart-zoom-in"
                        type="button"
                        aria-label="放大视图"
                        aria-controls="price-chart"
                        title="显示更少 K 线，长按连续放大；图表聚焦后按 ↑"
                        disabled
                    >
                        <IconPlus size={17} stroke={1.8} aria-hidden="true" />
                    </button>
                    <button
                        id="chart-zoom-out"
                        type="button"
                        aria-label="缩小视图"
                        aria-controls="price-chart"
                        title="显示更多 K 线，长按连续缩小；图表聚焦后按 ↓"
                        disabled
                    >
                        <IconMinus size={17} stroke={1.8} aria-hidden="true" />
                    </button>
                </div>
            </div>
            <div className="trade-playback-navigation">
                <div
                    className="chart-navigation-buttons trade-playback-controls"
                    role="group"
                    aria-label="回测买卖点播放"
                    aria-describedby="trade-playback-current"
                >
                    <button
                        id="trade-playback-previous"
                        type="button"
                        aria-label="上一笔成交"
                        aria-controls="price-chart"
                        disabled
                    >
                        上一笔
                    </button>
                    <button
                        id="trade-playback-toggle"
                        type="button"
                        aria-label="播放买卖点"
                        aria-controls="price-chart"
                        aria-pressed="false"
                        disabled
                    >
                        播放
                    </button>
                    <button
                        id="trade-playback-next"
                        type="button"
                        aria-label="下一笔成交"
                        aria-controls="price-chart"
                        disabled
                    >
                        下一笔
                    </button>
                    <label htmlFor="trade-playback-speed">速度</label>
                    <select id="trade-playback-speed" defaultValue="1600" disabled>
                        <option value="3200">0.5×</option>
                        <option value="1600">1×</option>
                        <option value="800">2×</option>
                    </select>
                    <output id="trade-playback-progress" aria-label="成交播放进度">
                        0 / 0
                    </output>
                    <span id="trade-playback-current" className="sr-only">
                        运行当前股票回测后可按成交顺序播放 B/S 点位。
                    </span>
                    <span id="trade-playback-announcement" className="sr-only" role="status" aria-live="polite" />
                </div>
            </div>
        </>
    );
}
