import type { JSX } from "react";

import {
    IconGripVertical,
    IconLayoutSidebarLeftCollapse,
    IconLayoutSidebarLeftExpand,
    IconStar,
    IconStarFilled,
} from "@tabler/icons-react";

/** 分类自选股独立侧栏；运行时负责 IndexedDB 数据和折叠状态。 */
export function WatchlistRail(): JSX.Element {
    return (
        <aside id="watchlist-rail" className="panel watchlist-rail" aria-label="自选股列表">
            <header className="watchlist-rail-header">
                <span className="watchlist-rail-mark" aria-hidden="true">
                    <IconStarFilled size={16} />
                </span>
                <div className="watchlist-rail-heading">
                    <strong>自选股</strong>
                    <small id="watchlist-count">0 只</small>
                </div>
                <button
                    id="watchlist-rail-toggle"
                    className="watchlist-rail-toggle"
                    type="button"
                    aria-controls="watchlist-rail-body"
                    aria-expanded="true"
                    aria-label="收起自选股列表"
                    title="收起自选股列表"
                >
                    <IconLayoutSidebarLeftCollapse
                        className="watchlist-collapse-icon"
                        size={18}
                        stroke={1.8}
                        aria-hidden="true"
                    />
                    <IconLayoutSidebarLeftExpand
                        className="watchlist-expand-icon"
                        size={18}
                        stroke={1.8}
                        aria-hidden="true"
                    />
                </button>
            </header>
            <div id="watchlist-rail-body" className="watchlist-rail-body">
                <div className="watchlist-group-toolbar">
                    <label htmlFor="watchlist-group-select">
                        分类
                        <select id="watchlist-group-select" aria-label="自选股分类" disabled />
                    </label>
                    <div className="watchlist-group-actions">
                        <button id="watchlist-group-add" type="button" disabled>
                            新建
                        </button>
                        <button id="watchlist-group-rename" type="button" disabled>
                            重命名
                        </button>
                        <button id="watchlist-group-delete" type="button" disabled>
                            删除
                        </button>
                    </div>
                </div>
                <form id="watchlist-group-form" className="watchlist-group-form" hidden>
                    <label htmlFor="watchlist-group-name">
                        <span id="watchlist-group-editor-title">新建分类</span>
                        <input id="watchlist-group-name" maxLength={24} autoComplete="off" required />
                    </label>
                    <button type="submit">保存</button>
                    <button id="watchlist-group-cancel" type="button">
                        取消
                    </button>
                </form>
                <p id="watchlist-status" role="status">
                    正在读取浏览器中的自选股…
                </p>
                <section className="watchlist-backtest-panel" aria-label="自选股自动回测">
                    <div className="watchlist-backtest-heading">
                        <strong>自动回测</strong>
                        <div className="watchlist-backtest-actions">
                            <button id="watchlist-backtest-retry" type="button" disabled>
                                重试失败
                            </button>
                            <button id="watchlist-auto-backtest-toggle" type="button" aria-pressed="true">
                                暂停
                            </button>
                        </div>
                    </div>
                    <p id="watchlist-backtest-status" role="status">
                        等待自选股列表加载…
                    </p>
                    <progress id="watchlist-backtest-progress" max={1} value={0} aria-label="自选股回测进度" />
                    <small>页面空闲后按列表顺序、各股最新交易日逐只回测；策略更新后自动重跑。</small>
                </section>
                <div id="watchlist-stock-list" className="watchlist-stock-list">
                    <p className="stock-empty">自选股加载中…</p>
                </div>
                <p className="scan-note">
                    点击股票名称切换；拖动左侧手柄或用上下方向键调整顺序。使用图表标题旁的星标加入或移除当前分类。列表右侧显示当前设置下回测的期末收益率，悬停可查看盈亏金额。
                </p>
            </div>
            <span hidden aria-hidden="true">
                <IconStar id="watchlist-star-outline-icon" size={18} stroke={1.8} />
                <IconStarFilled id="watchlist-star-filled-icon" size={18} />
                <IconGripVertical id="watchlist-drag-icon" size={16} stroke={1.8} />
            </span>
        </aside>
    );
}
