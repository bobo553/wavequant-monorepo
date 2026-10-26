import { trendNColor } from "./trend-n-hover.js";

/** 临时绘制悬停趋势的共享N形态；目标复用TargetGuideOverlay，不创建新series、不改变价格范围。 */
export class TrendNHoverOverlay {
    constructor() { this.views = [this]; this.model = null; this.key = ""; this.projected = []; }
    attached({ chart, series, requestUpdate }) { this.chart = chart; this.series = series; this.requestUpdate = requestUpdate; }
    detached() { this.chart = null; this.series = null; this.requestUpdate = null; this.model = null; this.projected = []; }
    paneViews() { return this.views; }
    zOrder() { return "top"; }
    renderer() { return this; }
    setModel(model) {
        const stage = model?.displayStage;
        const key = model ? `${model.level}/${model.nIndex}/${model.snapshotIndex}/${model.day}/${model.time}/${model.color}/${stage?.level ?? ""}/${stage?.path ?? ""}/${stage?.from ?? ""}/${stage?.to ?? ""}` : "";
        if (this.key === key) return;
        this.key = key; this.model = model; this.updateAllViews(); this.requestUpdate?.();
    }
    /** 标识、字母和N线段均命中同一来源，不能按其A/B/C日期切换到别的N。 */
    hitTest(x, y) {
        if (!this.model) return null;
        for (const point of this.projected) {
            if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
            if (Math.hypot(x - point.x, y - point.y) <= 9 ||
                x >= point.x + 3 && x <= point.x + 42 && y >= point.y - 20 && y <= point.y - 2)
                return { externalId: `trend-n:${this.model.level}:${this.model.nIndex}`, zOrder: "top", cursorStyle: "pointer" };
        }
        for (let index = 1; index < this.projected.length; index++) {
            const a = this.projected[index - 1], b = this.projected[index];
            if (![a.x, a.y, b.x, b.y].every(Number.isFinite)) continue;
            const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
            const position = length ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / length)) : 0;
            if (Math.hypot(x - a.x - position * dx, y - a.y - position * dy) <= 6)
                return { externalId: `trend-n:${this.model.level}:${this.model.nIndex}`, zOrder: "top", cursorStyle: "pointer" };
        }
        return null;
    }
    updateAllViews() {
        this.projected = [];
        if (!this.model || !this.chart || !this.series) return;
        const scale = this.chart.timeScale(), counts = new Map(), ranks = new Map();
        for (const point of this.model.shape) counts.set(point.time, (counts.get(point.time) ?? 0) + 1);
        this.projected = this.model.shape.map(point => {
            const rank = ranks.get(point.time) ?? 0; ranks.set(point.time, rank + 1);
            const offset = counts.get(point.time) > 1 ? (rank - (counts.get(point.time) - 1) / 2) * Math.min(4, scale.options().barSpacing * 0.3) : 0;
            const x = scale.timeToCoordinate(point.time);
            return { ...point, x: x === null ? null : x + offset, y: this.series.priceToCoordinate(point.value) };
        });
    }
    draw(target) {
        if (!this.model || !this.projected.length || !this.chart || !this.series) return;
        target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
            const model = this.model, color = model.color ?? trendNColor(model.up);
            ctx.save(); ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 3; ctx.font = "600 11px ui-sans-serif, system-ui, sans-serif";
            const points = this.projected;
            for (let index = 1; index < points.length; index++) {
                const a = points[index - 1], b = points[index];
                if (![a.x, a.y, b.x, b.y].every(Number.isFinite)) continue;
                ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
            }
            for (const point of points) {
                if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.x < 0 || point.x > mediaSize.width || point.y < 0 || point.y > mediaSize.height) continue;
                ctx.beginPath(); ctx.arc(point.x, point.y, 3, 0, Math.PI * 2); ctx.fill();
                ctx.fillText(point.label, Math.max(4, point.x + 5), Math.max(12, point.y - 6));
            }
            ctx.restore();
        });
    }
}
