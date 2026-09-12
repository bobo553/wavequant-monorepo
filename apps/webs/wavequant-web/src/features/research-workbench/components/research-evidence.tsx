import type { JSX } from "react";

/** 图表选中项和结构事件的可追溯证据面板。 */
export function ResearchEvidence(): JSX.Element {
    return (
        <aside className="panel insight">
            <div className="card-header">
                <h2>决策证据</h2>
                <span className="tag cyan">可追溯</span>
            </div>
            <div id="selection-info" className="selection-info">
                点击图上标识或下方记录，查看规则和点位。
            </div>
            <div className="section-label" id="annotation-count">
                当前图窗标注
            </div>
            <div id="events" className="event-list">
                <p className="empty">正在读取结构事件…</p>
            </div>
        </aside>
    );
}
