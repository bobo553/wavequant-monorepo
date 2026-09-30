const TARGET_STAGES = new Set(["c_0618", "c_equal", "one_p", "two_t", "five_top", "ten_full"]);

/** 目标从结构锚点画到首次突破；未突破时只在锚点上方标示短线。 */
export function targetLevelGuide(item, level, bars, asof) {
    if (!TARGET_STAGES.has(level.stage) || !level.anchor_at || !Number.isFinite(level.price)) return null;
    const knownAt = level.available_at || item.signal_time || item.time;
    const end = asof || bars.at(-1)?.time;
    if (!end || knownAt > end || !bars.some((bar) => bar.time === level.anchor_at && bar.time <= end)) return null;
    // 确认当日的上影可能先于目标成立，只有收盘可以证明当日已经突破。
    const breakout = bars.find(
        (bar) =>
            bar.time >= knownAt &&
            bar.time <= end &&
            Number.isFinite(bar.time === knownAt ? bar.close : bar.high) &&
            (bar.time === knownAt ? bar.close : bar.high) > level.price,
    );
    return { start: level.anchor_at, end: breakout?.time || null, price: level.price, name: level.name };
}

/** 用固定像素宽度表示未突破目标，单根 K 线截面也能显示线段。 */
export class TargetGuideOverlay {
    constructor() {
        this.guides = [];
        this.projected = [];
        this.views = [this];
    }

    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
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

    setGuides(guides) {
        this.guides = guides;
        this.updateAllViews();
        this.requestUpdate?.();
    }

    updateAllViews() {
        if (!this.chart || !this.series) return;
        this.projected = this.guides
            .map((guide) => ({
                ...guide,
                x: this.chart.timeScale().timeToCoordinate(guide.start),
                y: this.series.priceToCoordinate(guide.price),
            }))
            .filter((guide) => guide.x !== null && guide.y !== null);
    }

    draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.font = "600 10px ui-sans-serif, system-ui, sans-serif";
            context.textBaseline = "bottom";
            context.textAlign = "left";
            const visible = this.projected
                .filter(
                    (guide) =>
                        guide.x >= 0 && guide.x <= mediaSize.width && guide.y >= 10 && guide.y <= mediaSize.height - 4,
                )
                .sort((left, right) => left.y - right.y);
            const labelYs = visible.map((guide) => guide.y - 3);
            // 相近的 C 目标在远端 N 目标参与缩放时仍保留可读间距，线段价格不位移。
            for (let index = labelYs.length - 2; index >= 0; index--)
                labelYs[index] = Math.min(labelYs[index], labelYs[index + 1] - 14);
            for (const [index, guide] of visible.entries()) {
                context.strokeStyle = guide.color;
                context.fillStyle = guide.color;
                context.lineWidth = 1;
                context.setLineDash([4, 3]);
                context.beginPath();
                context.moveTo(Math.max(0, guide.x - 18), guide.y);
                context.lineTo(Math.min(mediaSize.width, guide.x + 18), guide.y);
                context.stroke();
                context.setLineDash([]);
                const shortName = guide.name.startsWith("C 浪目标")
                    ? guide.name.replace("C 浪目标", "C")
                    : guide.name.split("（")[0] + (guide.name.includes("预估") ? "（预估）" : "");
                const text = `${shortName} ${guide.price.toFixed(4)} · ${guide.end ? "已突破" : "未突破"}`;
                const labelX = Math.max(
                    4,
                    Math.min(guide.x - 18, mediaSize.width - context.measureText(text).width - 4),
                );
                context.fillText(text, labelX, Math.max(12, labelYs[index]));
            }
            context.restore();
        });
    }
}
