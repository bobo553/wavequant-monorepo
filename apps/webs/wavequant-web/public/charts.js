import {
    avoidLabelCollisions,
    buildAnnotations,
    lastFallHighAnnotations,
    markerGroups,
    reversalWindowSummary,
    visibleAnnotations,
} from "./annotations.js";
import { num } from "./labels.js";
import { LectureOverlay, reversalConnections, secondaryConnections } from "./lecture-overlay.js";

const L = window.LightweightCharts;
if (!L) throw new Error("TradingView SDK 未加载，请检查本地 npm 依赖。");
const colors = { up: "#ef7180", down: "#3fba97" };
const themedCharts = new Set();
const themedSeries = new Set();

function themePalette() {
    const root = document.documentElement;
    const styles = getComputedStyle(root);
    const token = (name, fallback) => styles.getPropertyValue(name).trim() || fallback;
    const light = root.classList.contains("light");
    return {
        accent: token("--primary", light ? "#087f72" : "#48d6c4"),
        background: token("--card", light ? "#ffffff" : "#111d2d"),
        border: token("--input", light ? "#cbd8e4" : "#263850"),
        crosshair: light ? "#71839a" : "#536e8f",
        crosshairLabel: light ? "#52677f" : "#2d485f",
        grid: light ? "#dce4ed" : "#1a2839",
        text: token("--muted-foreground", light ? "#607087" : "#8291aa"),
    };
}

function themeOptions() {
    const palette = themePalette();
    return {
        layout: {
            background: { type: "solid", color: palette.background },
            textColor: palette.text,
        },
        grid: { vertLines: { color: palette.grid }, horzLines: { color: palette.grid } },
        rightPriceScale: { borderColor: palette.border },
        timeScale: { borderColor: palette.border },
        crosshair: {
            vertLine: { color: palette.crosshair, labelBackgroundColor: palette.crosshairLabel },
            horzLine: { color: palette.crosshair, labelBackgroundColor: palette.crosshairLabel },
        },
    };
}

function withOpacity(color, opacity) {
    if (/^#[0-9a-f]{6}$/i.test(color)) {
        return `${color}${Math.round(opacity * 255)
            .toString(16)
            .padStart(2, "0")}`;
    }
    return color;
}

function refreshChartThemes() {
    const options = themeOptions();
    themedCharts.forEach((chart) => chart.applyOptions(options));
    const palette = themePalette();
    themedSeries.forEach(({ index, series }) => {
        if (index !== 0) return;
        series.applyOptions({ lineColor: palette.accent, topColor: withOpacity(palette.accent, 0.19) });
    });
}

new MutationObserver(refreshChartThemes).observe(document.documentElement, {
    attributeFilter: ["class", "data-theme"],
    attributes: true,
});

function base(container) {
    const chart = L.createChart(container, {
        autoSize: true,
        ...themeOptions(),
        layout: {
            ...themeOptions().layout,
            fontSize: 10,
            attributionLogo: true,
        },
        timeScale: { ...themeOptions().timeScale, rightOffset: 5, barSpacing: 7, timeVisible: false },
        crosshair: {
            ...themeOptions().crosshair,
            mode: 0,
        },
        localization: { locale: "zh-CN" },
    });
    themedCharts.add(chart);
    return chart;
}
export class PriceChart {
    constructor(container, onHover, onSelect = () => {}, onVisible = () => {}) {
        this.container = container;
        this.onSelect = onSelect;
        this.onVisible = onVisible;
        this.chart = base(container);
        this.candles = this.chart.addSeries(L.CandlestickSeries, {
            upColor: colors.up,
            downColor: colors.down,
            borderVisible: false,
            wickUpColor: colors.up,
            wickDownColor: colors.down,
        });
        this.candles.priceScale().applyOptions({ scaleMargins: { top: 0.18, bottom: 0.23 } });
        this.volume = this.chart.addSeries(L.HistogramSeries, {
            priceFormat: { type: "volume" },
            priceScaleId: "volume",
        });
        this.volume.priceScale().applyOptions({ scaleMargins: { top: 0.83, bottom: 0 } });
        this.markers = L.createSeriesMarkers(this.candles, []);
        this.lines = [];
        this.polylineLines = [];
        this.polylineEnabled = false;
        this.polylineKey = "";
        this.levelLines = [];
        this.lastFallHighLines = [];
        this.lastFallHighLineKey = "";
        this.windowAnnotations = [];
        this.data = null;
        this.theory = null;
        this.annotations = [];
        this.options = {
            signals: true,
            fills: true,
            rules: true,
            diagnostics: false,
            levels: true,
            trendKeys: true,
        };
        this.showTeaching = true;
        this.drawingMode = "lecture";
        this.showTrend = true;
        this.showSecondaryTrend = true;
        this.showTertiaryTrend = true;
        this.lectureOverlay = new LectureOverlay(container);
        this.candles.attachPrimitive(this.lectureOverlay);
        this.tooltip = document.createElement("div");
        this.tooltip.className = "chart-tooltip";
        this.tooltip.hidden = true;
        container.append(this.tooltip);
        this.chart.subscribeCrosshairMove((p) => {
            if (!this.data) return;
            onHover(this.data.bars.find((b) => b.time === p.time) || this.data.bars.at(-1));
            const items = this.itemsAt(p.time, p.hoveredObjectId);
            this.tooltip.hidden = !p.point || !items.length;
            if (this.tooltip.hidden) return;
            this.tooltip.replaceChildren();
            const heading = document.createElement("b");
            heading.textContent = `${p.time} · ${items.length} 项标注`;
            this.tooltip.append(heading);
            for (const item of items.slice(0, 5)) {
                const line = document.createElement("div");
                line.textContent = `${item.title} · ${num(item.price)}${item.kind === "fill" ? " 成交价" : " 参考价"}`;
                this.tooltip.append(line);
            }
            const hint = document.createElement("small");
            hint.textContent = "点击标识或 K 线查看规则与点位";
            this.tooltip.append(hint);
            this.tooltip.style.left =
                Math.max(4, Math.min(p.point.x + 16, container.clientWidth - this.tooltip.offsetWidth - 4)) + "px";
            this.tooltip.style.top =
                Math.max(4, Math.min(p.point.y + 12, container.clientHeight - this.tooltip.offsetHeight - 4)) + "px";
        });
        this.chart.subscribeClick((p) => {
            const items = this.itemsAt(p.time, p.hoveredObjectId);
            if (items.length) this.selectAnnotation(items[0].id, false, items);
        });
        this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.scheduleMarkers());
    }
    setData(data, options = {}) {
        this.data = data;
        this.theory = null;
        this.selected = null;
        this.tooltip.hidden = true;
        this.clearTheory();
        this.clearLevels();
        this.candles.setData(data.bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        this.setVolume(options.volume !== false);
        this.setAnnotationOptions({ signals: options.markers !== false, ...options.annotations });
        const end = data.bars.length - 1;
        this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, end - 140), to: end + 6 });
    }
    setVolume(show) {
        if (!this.data) return;
        this.volume.setData(
            show
                ? this.data.bars.map((b) => ({
                      time: b.time,
                      value: b.volume,
                      color: b.close >= b.open ? "#ef718050" : "#3fba9750",
                  }))
                : [],
        );
    }
    setMarkers(show) {
        this.setAnnotationOptions({ signals: show });
    }
    setAnnotationOptions(options) {
        Object.assign(this.options, options);
        this.annotations = buildAnnotations(this.data, this.theory);
        this.refreshMarkers();
        this.drawLevels();
    }
    scheduleMarkers() {
        if (!this.frame)
            this.frame = requestAnimationFrame(() => {
                this.frame = null;
                this.refreshMarkers();
            });
    }
    refreshMarkers() {
        if (!this.data) return;
        const range = this.chart.timeScale().getVisibleLogicalRange();
        const bars = this.data.bars,
            from = bars[Math.max(0, Math.floor(range?.from || 0))]?.time || bars[0].time;
        const to = bars[Math.min(bars.length - 1, Math.ceil(range?.to ?? bars.length - 1))]?.time || bars.at(-1).time;
        const span = range ? range.to - range.from : 140;
        const trend =
            this.showTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(this.theory?.reversal_trends?.strokes || [], from, to)
                : null;
        const secondaryTrend =
            this.showSecondaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(this.theory?.secondary_trends?.strokes || [], from, to)
                : null;
        const tertiaryTrend =
            this.showTertiaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(this.theory?.tertiary_trends?.strokes || [], from, to)
                : null;
        const trendKeys = lastFallHighAnnotations([
            { level: 1, summary: trend },
            { level: 2, summary: secondaryTrend },
            { level: 3, summary: tertiaryTrend },
        ]);
        this.windowAnnotations = [...this.annotations, ...trendKeys];
        this.groups = markerGroups(this.windowAnnotations, this.options, span).filter(
            (g) => g.time >= from && g.time <= to,
        );
        avoidLabelCollisions(this.groups, (time) => this.chart.timeScale().timeToCoordinate(time));
        this.markers.setMarkers(this.groups.map((g) => g.marker));
        this.drawLastFallHighGuides(trendKeys);
        this.container.dataset.markerCount = this.groups.length;
        this.container.dataset.lastFallHighCount = String(trendKeys.length);
        this.onVisible(
            this.groups.flatMap((g) => g.items),
            { from, to, trend, secondaryTrend, tertiaryTrend },
        );
        this.renderPolyline(from, to);
    }
    itemsAt(time, id) {
        const drawing = this.lectureOverlay.annotation(id);
        if (drawing) return [drawing];
        const group = this.groups?.find((g) => g.id === id);
        return (
            group?.items ||
            visibleAnnotations(this.annotations, this.options)
                .filter((m) => m.time === time)
                .sort((a, b) => b.priority - a.priority)
        );
    }
    selectAnnotation(id, focus = true, items = null) {
        const selected = this.windowAnnotations.find((m) => m.id === id) || this.lectureOverlay.annotation(id);
        if (!selected) return;
        this.selected = selected;
        if (focus) this.focus(selected.time);
        this.drawLevels();
        this.onSelect(items || [selected]);
    }
    clearLevels() {
        for (const s of this.levelLines) this.chart.removeSeries(s);
        this.levelLines = [];
        this.container.dataset.levelCount = "0";
    }
    clearLastFallHighGuides() {
        for (const series of this.lastFallHighLines) this.chart.removeSeries(series);
        this.lastFallHighLines = [];
        this.lastFallHighLineKey = "";
        this.container.dataset.lastFallHighGuides = "0";
    }
    drawLastFallHighGuides(items) {
        const visible = this.options.trendKeys ? items : [];
        const key = visible.map((item) => item.id).join("|");
        if (key === this.lastFallHighLineKey) return;
        this.clearLastFallHighGuides();
        this.lastFallHighLineKey = key;
        for (const item of visible) {
            const low = item.raw.selected_low;
            if (!low || item.time >= low.time) continue;
            const series = this.chart.addSeries(L.LineSeries, {
                color: withOpacity(item.color, 0.78),
                lineStyle: 3,
                lineWidth: 1,
                title: item.title.split(" · ")[0],
                lastValueVisible: true,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData([
                { time: item.time, value: item.price },
                { time: low.time, value: item.price },
            ]);
            this.lastFallHighLines.push(series);
        }
        this.container.dataset.lastFallHighGuides = String(this.lastFallHighLines.length);
    }
    drawLevels() {
        this.clearLevels();
        const item = this.selected;
        if (!item || !this.data || !this.options.levels || !visibleAnnotations([item], this.options).length) return;
        for (const [i, level] of item.levels.entries()) {
            if (!Number.isFinite(level.price)) continue;
            const s = this.chart.addSeries(L.LineSeries, {
                color: ["#ebbc70", "#a29ce0", "#5ebeb0"][i % 3],
                lineStyle: item.kind === "trend" ? 0 : 2,
                lineWidth: 1,
                title: level.name,
                lastValueVisible: true,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: item.kind !== "trend",
                pointMarkersRadius: 2,
                autoscaleInfoProvider: () => null,
            });
            const points = [{ time: item.time, value: level.price }];
            if (item.time < this.data.bars.at(-1).time)
                points.push({ time: this.data.bars.at(-1).time, value: level.price });
            s.setData(points);
            this.levelLines.push(s);
        }
        this.container.dataset.levelCount = this.levelLines.length;
    }
    clearPolyline() {
        for (const series of this.polylineLines) this.chart.removeSeries(series);
        this.polylineLines = [];
        this.polylineKey = "";
        this.lectureOverlay.setStrokes([]);
        this.container.dataset.polylineSegments = "0";
        this.container.dataset.polylinePoints = "0";
        this.container.dataset.polylineConnections = "0";
    }
    clearTheory() {
        for (const series of this.lines) this.chart.removeSeries(series);
        this.lines = [];
        this.windowAnnotations = [];
        this.clearLastFallHighGuides();
        this.container.dataset.lastFallHighCount = "0";
        this.polylineEnabled = false;
        this.clearPolyline();
    }
    setDrawingMode(mode, teaching = true) {
        this.drawingMode = mode;
        this.showTeaching = teaching;
        this.lectureOverlay.setTeachingHighlight(teaching);
        this.clearPolyline();
        this.refreshMarkers();
    }
    setTrendVisible(show) {
        this.showTrend = show;
        if (!show && this.selected?.kind === "trend" && this.selected.raw.trend_level === 1) {
            this.selected = null;
            this.clearLevels();
            this.tooltip.hidden = true;
        }
        this.clearPolyline();
        this.refreshMarkers();
    }
    setSecondaryTrendVisible(show) {
        this.showSecondaryTrend = show;
        if (!show && this.selected?.kind === "trend" && this.selected.raw.trend_level === 2) {
            this.selected = null;
            this.clearLevels();
            this.tooltip.hidden = true;
        }
        this.clearPolyline();
        this.refreshMarkers();
    }
    setTertiaryTrendVisible(show) {
        this.showTertiaryTrend = show;
        if (!show && this.selected?.kind === "trend" && this.selected.raw.trend_level === 3) {
            this.selected = null;
            this.clearLevels();
            this.tooltip.hidden = true;
        }
        this.clearPolyline();
        this.refreshMarkers();
    }
    renderPolyline(from, to) {
        if (!this.polylineEnabled || !this.theory) return;
        const lecture = this.drawingMode === "lecture" && this.theory.lecture_drawing;
        const first = this.showTrend ? this.theory.reversal_trends?.strokes || [] : [];
        // Connect before viewport filtering, so a gap with both anchors off-screen
        // still has its crossing edge. These links never enter theory data.
        const second = this.showSecondaryTrend ? this.theory?.secondary_trends?.strokes || [] : [];
        const all = lecture
            ? [
                  ...this.theory.lecture_drawing.strokes,
                  ...reversalConnections(first, this.theory.lecture_drawing.strokes),
                  ...first,
                  ...secondaryConnections(second, this.theory.reversal_trends?.strokes || []),
                  ...second,
                  ...(this.showTertiaryTrend ? this.theory.tertiary_trends?.strokes || [] : []),
              ]
            : this.theory.polyline_segments || [{ id: "current", points: this.theory.points }];
        const visible = all.filter((s) => s.points.length && s.points[0].time <= to && s.points.at(-1).time >= from);
        const key = visible.map((s) => s.id).join("|");
        if (this.polylineKey === key) return;
        this.clearPolyline();
        this.polylineKey = key;
        if (lecture) {
            this.lectureOverlay.setStrokes(visible);
            this.container.dataset.polylineSegments = visible.length;
            this.container.dataset.polylinePoints = visible.reduce((count, s) => count + s.points.length, 0);
            return;
        }
        for (const segment of visible) {
            const series = this.chart.addSeries(L.LineSeries, {
                color: "#ffd36d",
                lineWidth: 3,
                lineStyle: 2,
                pointMarkersVisible: false,
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData(segment.points.map((p) => ({ time: p.time, value: p.value })));
            this.polylineLines.push(series);
        }
        this.container.dataset.polylineSegments = visible.length;
        this.container.dataset.polylinePoints = visible.reduce((count, s) => count + s.points.length, 0);
        this.container.dataset.polylineConnections = visible.filter((s) => s.kind === "auxiliary").length;
    }
    addLine(points, color, style = 0, width = 1) {
        const unique = [...new Map(points.map((p) => [p.time, { time: p.time, value: p.value }])).values()].sort(
            (a, b) => a.time.localeCompare(b.time),
        );
        if (unique.length < 2) return;
        const s = this.chart.addSeries(L.LineSeries, {
            color,
            lineWidth: width,
            lineStyle: style,
            lastValueVisible: false,
            priceLineVisible: false,
            crosshairMarkerVisible: false,
        });
        s.setData(unique);
        this.lines.push(s);
    }
    setTheory(theory, geometry = true) {
        this.theory = theory;
        this.clearTheory();
        this.annotations = buildAnnotations(this.data, theory);
        this.refreshMarkers();
        if (!theory || !this.data || !geometry) return;
        for (const n of theory.shapes) this.addLine(n.points, n.direction === "up" ? "#60cfc3" : "#cba271", 0, 2);
        this.polylineEnabled = true;
        this.refreshMarkers();
    }
    focus(time) {
        if (!this.data) return;
        const i = this.data.bars.findIndex((b) => b.time >= time);
        if (i < 0) return;
        this.chart
            .timeScale()
            .setVisibleLogicalRange({ from: Math.max(0, i - 55), to: Math.min(this.data.bars.length + 3, i + 30) });
    }
    destroy() {
        if (this.frame) cancelAnimationFrame(this.frame);
        this.tooltip.remove();
        themedCharts.delete(this.chart);
        this.chart.remove();
    }
}
export class PerformanceCharts {
    constructor(containers) {
        this.instances = containers.map((c, i) => {
            const chart = base(c);
            // Daily portfolio histories must fit even in a narrow half-width panel.
            chart.applyOptions({ timeScale: { minBarSpacing: 0.01 } });
            const palette = themePalette();
            const series = chart.addSeries(L.AreaSeries, {
                lineColor: i === 1 ? "#ec7c8a" : i === 2 ? "#829ddd" : palette.accent,
                topColor: i === 1 ? "#ec7c8a30" : i === 2 ? "#829ddd30" : withOpacity(palette.accent, 0.19),
                bottomColor: "#00000000",
                lineWidth: 2,
                priceFormat: { type: "custom", formatter: (p) => (i ? `${p.toFixed(2)}%` : p.toFixed(4)) },
            });
            themedSeries.add({ index: i, series });
            chart.timeScale().subscribeSizeChange(() => chart.timeScale().fitContent());
            chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
                if (range) {
                    c.dataset.visibleFromIndex = range.from;
                    c.dataset.visibleToIndex = range.to;
                }
            });
            return { chart, series };
        });
    }
    update(curve) {
        ["value", "drawdown", "exposure"].forEach((key, i) => {
            this.instances[i].series.setData(curve.map((r) => ({ time: r.time, value: r[key] })));
            this.instances[i].chart.timeScale().fitContent();
        });
    }
    resize() {
        this.instances.forEach(({ chart }) => chart.timeScale().fitContent());
    }
}
