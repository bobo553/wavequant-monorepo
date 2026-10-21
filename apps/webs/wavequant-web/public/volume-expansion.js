/** 日线相邻交易日比较；无效基数不产生倍数或标记。 */
export function volumeExpansion(volume, previousVolume) {
    if (
        typeof volume !== "number" ||
        !Number.isFinite(volume) ||
        volume < 0 ||
        typeof previousVolume !== "number" ||
        !Number.isFinite(previousVolume) ||
        previousVolume <= 0
    )
        return null;
    const ratio = volume / previousVolume;
    if (!Number.isFinite(ratio)) return null;
    const [label, marker, token] =
        ratio >= 10
            ? ["极端放量", "B10+", "--red"]
            : ratio >= 5
              ? ["剧烈放量", "B5", "--amber"]
              : ratio >= 3
                ? ["显著放量", "B3", "--purple"]
                : ratio >= 2
                  ? ["倍量", "B2", "--cyan"]
                  : ["未达倍量", "", "--cyan"];
    return { ratio, label, marker, token };
}

/** 与成交量序列共用坐标轴，缩放和更新时始终锚定柱顶。 */
export class VolumeExpansionOverlay {
    constructor(container) {
        this.container = container;
        this.points = [];
        this.views = [this];
    }
    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
    }
    detached() {
        this.points = [];
        this.chart = null;
        this.series = null;
        this.requestUpdate = null;
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
    setData(bars, enabled) {
        this.points = enabled
            ? bars.flatMap((bar, index) => {
                  const expansion = volumeExpansion(bar.volume, bars[index - 1]?.volume);
                  return expansion?.marker ? [{ time: bar.time, volume: bar.volume, ...expansion }] : [];
              })
            : [];
        this.requestUpdate?.();
    }
    draw(target) {
        if (!this.chart || !this.series || !this.points.length) return;
        const scale = this.chart.timeScale();
        const range = scale.getVisibleRange();
        if (!range) return;
        const styles = getComputedStyle(this.container);
        const background = styles.getPropertyValue("--panel").trim() || "#111d2d";
        const fallback = { "--cyan": "#48d6c4", "--purple": "#b597ed", "--amber": "#d5af71", "--red": "#ef7180" };
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.font = "bold 10px sans-serif";
            context.textAlign = "center";
            context.textBaseline = "bottom";
            const occupied = [];
            for (const point of this.points) {
                if (point.time < range.from || point.time > range.to) continue;
                const x = scale.timeToCoordinate(point.time);
                const y = this.series.priceToCoordinate(point.volume);
                if (x === null || y === null || x < 0 || x > mediaSize.width) continue;
                const width = context.measureText(point.marker).width + 6;
                const left = Math.max(0, Math.min(x - width / 2, mediaSize.width - width));
                let bottom = Math.max(14, y - 3);
                // 密集柱子的文字向上错开，保持每根倍量柱的独立标识。
                while (
                    occupied.some(
                        (box) =>
                            left < box.right && left + width > box.left && bottom > box.top && bottom - 14 < box.bottom,
                    )
                )
                    bottom -= 15;
                if (bottom < 14) continue;
                occupied.push({ left, right: left + width, top: bottom - 14, bottom });
                context.fillStyle = styles.getPropertyValue(point.token).trim() || fallback[point.token];
                context.fillRect(x - 1, y - 3, 2, 3);
                context.fillStyle = background;
                context.fillRect(left, bottom - 13, width, 14);
                context.fillStyle = styles.getPropertyValue(point.token).trim() || fallback[point.token];
                context.fillText(point.marker, left + width / 2, bottom);
            }
            context.restore();
        });
    }
}
