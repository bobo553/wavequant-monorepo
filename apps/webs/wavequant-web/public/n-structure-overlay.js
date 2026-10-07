import { isPositiveNTarget } from "./n-target-focus.js";

/** 只标出已发布测幅来源的原箱顶/底，不从鼠标价或后续行情重选端点。 */
export function nStructureBounds(item, asof) {
    if (!isPositiveNTarget(item, asof) || !asof || item.time > asof || item.raw.available_at > asof) return null;
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
        this.structures = [];
        this.projectedStructures = [];
        this.sameDaySegments = [];
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
        this.setStructures(item ? [item] : [], asof);
    }

    setStructures(items, asof) {
        this.sameDaySegments = items.flatMap((item) =>
            nStructureBounds(item, asof)
                ? (item.raw.shape || []).slice(1).flatMap((end, index) => {
                      const start = item.raw.shape[index];
                      return start.time === end.time ? [{ start, end, color: item.color || "#b69af5" }] : [];
                  })
                : [],
        );
        this.structures = items
            .map((item) => ({
                ...nStructureBounds(item, asof),
                source: item.raw?.reformed_from_date || item.raw?.shape?.[0]?.time,
                color: item.color || "#b69af5",
            }))
            .filter((bounds) => bounds.start);
        this.bounds = this.structures[0] || null;
        if (this.container) this.container.dataset.nStructureBounds = JSON.stringify(this.bounds);
        if (this.container) this.container.dataset.nStructureCount = String(this.structures.length);
        this.updateAllViews();
        this.requestUpdate?.();
    }

    updateAllViews() {
        this.projected = null;
        this.projectedStructures = [];
        if (!this.chart || !this.series) return;
        const scale = this.chart.timeScale();
        for (const bounds of this.structures) {
            const { start, end, top, bottom } = bounds;
            const x1 = scale.timeToCoordinate(start),
                x2 = scale.timeToCoordinate(end),
                topY = this.series.priceToCoordinate(top),
                bottomY = this.series.priceToCoordinate(bottom);
            if ([x1, x2, topY, bottomY].every(Number.isFinite))
                this.projectedStructures.push({ x1, x2, topY, bottomY, bounds });
        }
        this.projected = this.projectedStructures[0] || null;
    }

    draw(target) {
        if (!this.chart || !this.series || !this.bounds) return;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            for (const { start, end, color } of this.sameDaySegments) {
                const x = this.chart.timeScale().timeToCoordinate(start.time);
                const y1 = this.series.priceToCoordinate(start.value),
                    y2 = this.series.priceToCoordinate(end.value);
                if (![x, y1, y2].every(Number.isFinite) || x < 0 || x > mediaSize.width) continue;
                context.save();
                context.strokeStyle = color;
                context.lineWidth = 2;
                context.setLineDash([]);
                context.beginPath();
                context.moveTo(x, y1);
                context.lineTo(x, y2);
                context.stroke();
                context.restore();
            }
            for (const { x1, x2, topY, bottomY, bounds } of this.projectedStructures) {
                const left = Math.min(x1, x2),
                    right = Math.max(x1, x2);
                if (right < 0 || left > mediaSize.width) continue;
                // 很短的母子 N 保留可辨认的横线宽度；滚动缩放仍由结构坐标驱动。
                const center = (left + right) / 2,
                    half = Math.max(18, (right - left) / 2);
                const from = Math.max(0, center - half),
                    to = Math.min(mediaSize.width, center + half);
                context.save();
                context.strokeStyle = bounds.color;
                context.fillStyle = bounds.color;
                context.lineWidth = 2;
                context.setLineDash([]);
                context.font = "600 11px ui-sans-serif, system-ui, sans-serif";
                context.textAlign = "left";
                context.textBaseline = "alphabetic";
                for (const [name, value, y, offset] of [
                    ["正 N 顶", bounds.top, topY, -5],
                    ["正 N 底", bounds.bottom, bottomY, 15],
                ]) {
                    if (y < 0 || y > mediaSize.height) continue;
                    context.beginPath();
                    context.moveTo(from, y);
                    context.lineTo(to, y);
                    context.stroke();
                    const label = `${name} ${value.toFixed(4)}${this.structures.length > 1 ? ` · ${bounds.source}` : ""}`;
                    const width = Math.min(context.measureText(label).width, Math.max(0, mediaSize.width - 8));
                    const labelX = Math.max(4, Math.min(from, mediaSize.width - width - 4));
                    const labelY = Math.max(12, Math.min(y + offset, mediaSize.height - 4));
                    context.fillText(label, labelX, labelY, width);
                }
                context.restore();
            }
        });
    }
}
