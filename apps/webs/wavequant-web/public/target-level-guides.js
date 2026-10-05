const TARGET_STAGES = new Set(["c_0618", "c_equal", "c_1618", "one_p", "two_t", "five_top", "ten_full"]);
const C_STAGES = new Set(["c_0618", "c_equal", "c_1618"]);

/** 目标从结构锚点画到首次突破；未突破时只在锚点上方标示短线。 */
export function targetLevelGuide(item, level, bars, asof) {
    if (!TARGET_STAGES.has(level.stage) || !level.anchor_at || !Number.isFinite(level.price)) return null;
    const knownAt = level.available_at || item.signal_time || item.time;
    const requestedEnd = asof || bars.at(-1)?.time;
    const end = level.valid_until && level.valid_until < requestedEnd ? level.valid_until : requestedEnd;
    if (!end || knownAt > end || !bars.some((bar) => bar.time === level.anchor_at && bar.time <= end)) return null;
    // 确认当日的上影可能先于目标成立，只有收盘可以证明当日已经突破。
    const cTarget = C_STAGES.has(level.stage);
    const breakout = bars.find((bar) => {
        const price = bar.time === knownAt ? bar.close : bar.high;
        const rounding = Number.EPSILON * Math.max(1, Math.abs(price), Math.abs(level.price)) * 4;
        return (
            bar.time >= knownAt &&
            bar.time <= end &&
            Number.isFinite(price) &&
            (cTarget ? price >= level.price - rounding : price > level.price)
        );
    });
    const ended = level.c_ended_at && level.c_end_known_at && level.c_end_known_at <= requestedEnd;
    return {
        start: level.anchor_at,
        end: breakout?.time || null,
        price: level.price,
        name: level.display_name || level.name,
        ...(cTarget
            ? {
                  targetState: breakout ? "已触及" : ended ? "本段结束未达成" : "待达成",
                  firstTouchedAt: breakout?.time || null,
              }
            : {}),
    };
}

/** 目标统一在左端直接标注；短线使用固定像素宽度，长线交给价格序列。 */
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
                x: this.chart.timeScale().timeToCoordinate(guide.display_at || guide.start),
                endX: guide.end ? this.chart.timeScale().timeToCoordinate(guide.end) : null,
                y: this.series.priceToCoordinate(guide.price),
            }))
            .filter((guide) => guide.x !== null && guide.y !== null);
    }

    draw(target) {
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.font = "600 12px ui-sans-serif, system-ui, sans-serif";
            context.textBaseline = "bottom";
            context.textAlign = "left";
            const visible = this.projected
                .map((guide) => {
                    const offscreen = guide.y < 4 || guide.y > mediaSize.height - 4;
                    return {
                        ...guide,
                        offscreen,
                        displayY: offscreen ? Math.max(4, Math.min(guide.y, mediaSize.height - 4)) : guide.y,
                    };
                })
                .filter(
                    (guide) =>
                        guide.x <= mediaSize.width &&
                        (guide.x >= 0 || (guide.end > guide.start && guide.endX !== null && guide.endX >= 0)) &&
                        (!guide.offscreen ||
                            ["c_0618", "c_equal", "c_1618", "one_p", "two_t", "five_top", "ten_full"].includes(
                                guide.stage,
                            )),
                )
                .sort((left, right) => left.displayY - right.displayY);
            const labelYs = visible.map((guide) => guide.displayY - 3);
            const spacing = Math.min(18, (mediaSize.height - 20) / Math.max(1, visible.length - 1));
            // 先向下避让，再整体收回图窗底部，避免顶部多个标签被同时夹到同一位置。
            for (let index = 0; index < labelYs.length; index++)
                labelYs[index] = Math.max(labelYs[index], index ? labelYs[index - 1] + spacing : 16);
            if (labelYs.length) labelYs[labelYs.length - 1] = Math.min(labelYs.at(-1), mediaSize.height - 4);
            for (let index = labelYs.length - 2; index >= 0; index--)
                labelYs[index] = Math.min(labelYs[index], labelYs[index + 1] - spacing);
            const background = this.chart.options?.().layout?.background?.color || "#101722";
            for (const [index, guide] of visible.entries()) {
                context.strokeStyle = guide.color;
                context.fillStyle = guide.color;
                context.lineWidth = guide.stage === "c_equal" ? 2 : 1;
                const short = !guide.end || guide.end === guide.start;
                const anchorX = Math.max(0, short ? guide.x - 18 : guide.x);
                if (short && !guide.offscreen) {
                    context.setLineDash(guide.targetState === "已触及" ? [] : [4, 3]);
                    context.beginPath();
                    context.moveTo(anchorX, guide.y);
                    context.lineTo(Math.min(mediaSize.width, guide.x + 18), guide.y);
                    context.stroke();
                }
                context.setLineDash([]);
                let shortName = guide.name.startsWith("C 浪目标")
                    ? guide.name.replace("C 浪目标", "C")
                    : guide.name.split("（")[0] + (guide.name.includes("预估") ? "（预估）" : "");
                if (guide.stage === "c_equal" && !shortName.includes("等浪")) shortName += "（等浪）";
                const direction = guide.offscreen ? (guide.y < 4 ? "↑ " : "↓ ") : "";
                const value = `${direction}${shortName} ${guide.price.toFixed(4)}`;
                const text = guide.targetState
                    ? `${value} · ${guide.targetState}${guide.offscreen ? " · 图外" : ""}`
                    : guide.offscreen
                      ? `${value} · 图外`
                      : guide.statusKnown === false
                        ? value
                        : `${value} · ${guide.end ? "已突破" : "未突破"}`;
                // 窄屏优先保留名称、价格和预估标识，空间不足时省略突破状态。
                const label = context.measureText(text).width <= mediaSize.width - 8 ? text : value;
                const width = Math.min(context.measureText(label).width, mediaSize.width - 8);
                const labelX = Math.max(
                    4,
                    Math.min(
                        guide.labelPosition === "line" ? anchorX + 8 : anchorX - width - 8,
                        mediaSize.width - width - 4,
                    ),
                );
                const labelY = labelYs[index];
                if (!guide.offscreen && (Math.abs(labelY - (guide.y - 3)) > 1 || labelX + width > anchorX - 8)) {
                    const edgeX = anchorX < labelX + width / 2 ? labelX - 2 : labelX + width + 2;
                    context.beginPath();
                    context.moveTo(edgeX, labelY - 6);
                    context.lineTo(anchorX, guide.y);
                    context.stroke();
                }
                context.fillStyle = background;
                context.fillRect(labelX - 2, labelY - 14, width + 4, 16);
                context.fillStyle = guide.color;
                context.fillText(label, labelX, labelY, width);
            }
            context.restore();
        });
    }
}
