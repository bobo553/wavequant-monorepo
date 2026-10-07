import { isPositiveNTarget } from "./n-target-focus.js";

/** 只标出已发布测幅来源的原箱顶/底，不从鼠标价或后续行情重选端点。 */
export function nStructureBounds(item, asof) {
    if (!isPositiveNTarget(item) || !asof || item.time > asof || item.raw.available_at > asof) return null;
    const shape = item.raw.shape;
    if (!Array.isArray(shape) || shape.length < 4) return null;
    const origin = shape[0];
    const top = Number.isFinite(item.raw.box_anchor) ? item.raw.box_anchor : shape.at(-1)?.value;
    if (
        !origin?.time ||
        origin.time >= item.time ||
        !Number.isFinite(origin.value) ||
        !Number.isFinite(top) ||
        !(top > origin.value)
    )
        return null;
    return { start: origin.time, end: item.time, top, bottom: origin.value };
}

export class NStructureOverlay {
    constructor(container) {
        this.container = container;
        this.bounds = null;
        this.projected = null;
        this.views = [this];
    }

    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
    }

    detached() {
        this.chart = null;
        this.series = null;
        this.requestUpdate = null;
        this.projected = null;
    }

    paneViews() {
        return this.views;
    }

    zOrder() {
        return "top";
    }

    renderer() {
        return this;
    }

    setStructure(item, asof) {
        this.bounds = nStructureBounds(item, asof);
        if (this.container) this.container.dataset.nStructureBounds = JSON.stringify(this.bounds);
        this.updateAllViews();
        this.requestUpdate?.();
    }

    updateAllViews() {
        this.projected = null;
        if (!this.chart || !this.series || !this.bounds) return;
        const { start, end, top, bottom } = this.bounds;
        const scale = this.chart.timeScale();
        const x1 = scale.timeToCoordinate(start),
            x2 = scale.timeToCoordinate(end),
            topY = this.series.priceToCoordinate(top),
            bottomY = this.series.priceToCoordinate(bottom);
        if ([x1, x2, topY, bottomY].every(Number.isFinite)) this.projected = { x1, x2, topY, bottomY };
    }

    draw(target) {
        if (!this.projected || !this.bounds) return;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            const { x1, x2, topY, bottomY } = this.projected;
            const left = Math.min(x1, x2),
                right = Math.max(x1, x2);
            if (right < 0 || left > mediaSize.width) return;
            // 很短的母子 N 保留可辨认的横线宽度；滚动缩放仍由结构坐标驱动。
            const center = (left + right) / 2,
                half = Math.max(18, (right - left) / 2);
            const from = Math.max(0, center - half),
                to = Math.min(mediaSize.width, center + half);
            context.save();
            context.strokeStyle = "#a29ce0";
            context.fillStyle = "#a29ce0";
            context.lineWidth = 2;
            context.setLineDash([]);
            context.font = "600 11px ui-sans-serif, system-ui, sans-serif";
            context.textAlign = "left";
            context.textBaseline = "alphabetic";
            for (const [name, value, y, offset] of [
                ["正 N 顶", this.bounds.top, topY, -5],
                ["正 N 底", this.bounds.bottom, bottomY, 15],
            ]) {
                if (y < 0 || y > mediaSize.height) continue;
                context.beginPath();
                context.moveTo(from, y);
                context.lineTo(to, y);
                context.stroke();
                const label = `${name} ${value.toFixed(4)}`;
                const width = Math.min(context.measureText(label).width, Math.max(0, mediaSize.width - 8));
                const labelX = Math.max(4, Math.min(from, mediaSize.width - width - 4));
                const labelY = Math.max(12, Math.min(y + offset, mediaSize.height - 4));
                context.fillText(label, labelX, labelY, width);
            }
            context.restore();
        });
    }
}
