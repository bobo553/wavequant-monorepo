import {
    avoidLabelCollisions,
    bearBullAlternationLowAnnotations,
    bearToBullHighAnnotations,
    buildAnnotations,
    bullishTurnSignalAnnotations,
    lastFallHighAnnotations,
    markerGroups,
    postAlternationBullHighAnnotations,
    reversalWindowSummary,
    visibleAnnotations,
} from "./annotations.js";
import { candleDetails } from "./candle-details.js";
import { FocusFlashOverlay } from "./focus-flash-overlay.js";
import { num } from "./labels.js";
import { LectureOverlay, lectureConnections, secondaryConnections } from "./lecture-overlay.js";
import { tertiaryRetracementGuides } from "./retracement-guides.js";
import { TradeMarkerOverlay } from "./trade-marker-overlay.js";

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
    constructor(container, onHover, onSelect = () => {}, onVisible = () => {}, onCopyCandle = () => {}) {
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
        // 普通规则仍使用库标记；真实成交只由顶层文字图层绘制，避免箭头缩成圆点。
        this.lines = [];
        this.polylineLines = [];
        this.polylineEnabled = false;
        this.polylineKey = "";
        this.levelLines = [];
        this.lastFallHighLines = [];
        this.lastFallHighLineKey = "";
        this.bullishTurnGuideLines = [];
        this.bullishTurnGuideKey = "";
        this.tertiaryRetracementLines = [];
        this.tertiaryRetracementKey = "";
        this.windowAnnotations = [];
        this.data = null;
        this.theory = null;
        this.annotations = [];
        this.options = {
            signals: true,
            fills: true,
            candidateRejections: true,
            rules: true,
            diagnostics: false,
            levels: true,
            trendKeys: true,
            bullFlipHighs: true,
            bullAlternationLows: true,
            postAlternationBullHighs: true,
            bullishTurnSignals: true,
            tertiaryRetracement: true,
            tertiaryAbc: true,
        };
        this.showTeaching = true;
        this.drawingMode = "lecture";
        this.showTrend = true;
        this.showSecondaryTrend = true;
        this.showTertiaryTrend = true;
        this.lectureOverlay = new LectureOverlay(container);
        this.candles.attachPrimitive(this.lectureOverlay);
        this.tradeMarkerOverlay = new TradeMarkerOverlay(container);
        this.candles.attachPrimitive(this.tradeMarkerOverlay);
        this.focusFlashOverlay = new FocusFlashOverlay(container);
        this.candles.attachPrimitive(this.focusFlashOverlay);
        this.tooltip = document.createElement("div");
        this.tooltip.className = "chart-tooltip";
        this.tooltip.hidden = true;
        this.tooltipHovered = false;
        this.tooltipHideTimer = null;
        this.tooltipBarTime = null;
        this.tooltip.addEventListener("pointerenter", () => {
            this.tooltipHovered = true;
            clearTimeout(this.tooltipHideTimer);
        });
        this.tooltip.addEventListener("pointerleave", () => {
            this.tooltipHovered = false;
            this.tooltip.hidden = true;
        });
        this.tooltip.addEventListener("focusin", () => clearTimeout(this.tooltipHideTimer));
        this.tooltip.addEventListener("focusout", () => {
            if (!this.tooltipHovered) this.tooltip.hidden = true;
        });
        container.append(this.tooltip);
        this.chart.subscribeCrosshairMove((p) => {
            if (!this.data) return;
            // 鼠标进入卡片时保留当前 K 线，不让图表的离开事件清空卡片。
            if (this.tooltipHovered || this.tooltip.contains(document.activeElement)) return;
            const bar = this.data.bars.find((candidate) => candidate.time === p.time);
            if (bar) onHover(bar);
            const items = this.itemsAt(p.time, p.hoveredObjectId);
            if (!p.point || !bar) {
                clearTimeout(this.tooltipHideTimer);
                this.tooltipHideTimer = setTimeout(() => {
                    if (!this.tooltipHovered && !this.tooltip.contains(document.activeElement))
                        this.tooltip.hidden = true;
                }, 150);
                return;
            }
            clearTimeout(this.tooltipHideTimer);
            // Keep the tooltip still while traversing the chart-to-card gap. If it
            // follows every pointer move, its copy button continually escapes the cursor.
            const positionTooltip = this.tooltip.hidden || this.tooltipBarTime !== bar.time;
            this.tooltipBarTime = bar.time;
            this.tooltip.hidden = false;
            this.tooltip.replaceChildren();
            const heading = document.createElement("div");
            heading.className = "chart-tooltip-heading";
            const title = document.createElement("b");
            title.textContent = `${bar.time} · K 线`;
            const shortcut = document.createElement("button");
            shortcut.type = "button";
            shortcut.className = "chart-tooltip-shortcut";
            shortcut.textContent = "Ctrl+C 复制";
            shortcut.dataset.copyLabel = shortcut.textContent;
            shortcut.setAttribute("aria-label", `复制 ${bar.time} K 线数据，也可按 Ctrl+C`);
            shortcut.addEventListener("click", () => onCopyCandle(bar, shortcut));
            heading.append(title, shortcut);
            this.tooltip.append(heading);
            for (const [label, value] of candleDetails(bar).slice(1)) {
                const line = document.createElement("div");
                line.className = "chart-tooltip-price";
                const name = document.createElement("span");
                name.textContent = label;
                const amount = document.createElement("span");
                amount.textContent = value;
                line.append(name, amount);
                this.tooltip.append(line);
            }
            if (items.length) {
                const annotationHeading = document.createElement("b");
                annotationHeading.className = "chart-tooltip-annotations";
                annotationHeading.textContent = `${items.length} 项标注`;
                this.tooltip.append(annotationHeading);
            }
            for (const item of items.slice(0, items[0]?.category === "entry-rejections" ? items.length : 5)) {
                const line = document.createElement("div");
                line.textContent = `${item.title} · ${num(item.price)}${item.category === "entry-rejections" ? " 收盘参考价（未下单）" : item.category === "risk-rejections" ? " 拟买价（未成交）" : item.kind === "fill" ? " 成交价" : " 参考价"}`;
                this.tooltip.append(line);
                if (item.category === "entry-rejections" || item.category === "risk-rejections") {
                    const reason = document.createElement("small");
                    reason.textContent = item.description;
                    this.tooltip.append(reason);
                }
                for (const rejection of item.executionRiskRejections || []) {
                    const execution = document.createElement("div");
                    execution.textContent = `${rejection.time} 执行风控未通过 · 拟买价 ${num(rejection.price)} 元（未成交）`;
                    const reason = document.createElement("small");
                    reason.textContent = rejection.description;
                    this.tooltip.append(execution, reason);
                }
            }
            const hint = document.createElement("small");
            hint.textContent = items.length ? "点击标识查看规则 · 提示文字可选中复制" : "选中提示文字可单独复制";
            this.tooltip.append(hint);
            if (positionTooltip) {
                this.tooltip.style.left =
                    Math.max(4, Math.min(p.point.x + 16, container.clientWidth - this.tooltip.offsetWidth - 4)) + "px";
                this.tooltip.style.top =
                    Math.max(4, Math.min(p.point.y + 12, container.clientHeight - this.tooltip.offsetHeight - 4)) + "px";
            }
        });
        this.chart.subscribeClick((p) => {
            const items = this.itemsAt(p.time, p.hoveredObjectId);
            if (items.length) this.selectAnnotation(items[0].id, false, items);
        });
        this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.scheduleMarkers());
    }
    setData(data, options = {}) {
        this.focusFlashOverlay.clear();
        clearTimeout(this.tooltipHideTimer);
        this.tooltipHovered = false;
        this.tooltipBarTime = null;
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
        // Python returns the authoritative continuous level-1 paths.  Rebuilding
        // business pivots in the browser would make level-2 input disagree with
        // the line and last-fall-high that the user is inspecting.
        const levelOneStrokes = this.theory?.reversal_trends?.strokes || [];
        const trend =
            this.showTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(levelOneStrokes, from, to, bars)
                : null;
        const secondaryTrend =
            this.showSecondaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(this.theory?.secondary_trends?.strokes || [], from, to, bars)
                : null;
        const secondaryDeveloping =
            this.showSecondaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? (this.theory?.secondary_trends?.developing_strokes || [])
                      .filter((stroke) => stroke.points[0].time <= to && stroke.points.at(-1).time >= from)
                      .at(-1) || null
                : null;
        const tertiaryTrend =
            this.showTertiaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? reversalWindowSummary(this.theory?.tertiary_trends?.strokes || [], from, to, bars)
                : null;
        const tertiaryDeveloping =
            this.showTertiaryTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? (this.theory?.tertiary_trends?.developing_strokes || [])
                      .filter((stroke) => stroke.points[0].time <= to && stroke.points.at(-1).time >= from)
                      .at(-1) || null
                : null;
        const trendKeys = lastFallHighAnnotations([
            { level: 1, summary: trend },
            { level: 2, summary: secondaryTrend },
            { level: 3, summary: tertiaryTrend },
        ]);
        const bullFlipHighs = bearToBullHighAnnotations(
            [
                {
                    level: 1,
                    landmarks: this.showTrend ? this.theory?.reversal_trends?.bear_to_bull_highs || [] : [],
                },
                {
                    level: 2,
                    landmarks: this.showSecondaryTrend ? this.theory?.secondary_trends?.bear_to_bull_highs || [] : [],
                },
                {
                    level: 3,
                    landmarks: this.showTertiaryTrend ? this.theory?.tertiary_trends?.bear_to_bull_highs || [] : [],
                },
            ],
            from,
            to,
            this.theory?.asof || this.data.asof,
        );
        const bullAlternationLows = bearBullAlternationLowAnnotations(
            [
                {
                    level: 1,
                    landmarks: this.showTrend ? this.theory?.reversal_trends?.bear_bull_alternation_lows || [] : [],
                },
                {
                    level: 2,
                    landmarks: this.showSecondaryTrend
                        ? this.theory?.secondary_trends?.bear_bull_alternation_lows || []
                        : [],
                },
                {
                    level: 3,
                    landmarks: this.showTertiaryTrend
                        ? this.theory?.tertiary_trends?.bear_bull_alternation_lows || []
                        : [],
                },
            ],
            from,
            to,
            this.theory?.asof || this.data.asof,
        );
        const postAlternationBullHighs = postAlternationBullHighAnnotations(
            [
                {
                    level: 1,
                    landmarks: this.showTrend ? this.theory?.reversal_trends?.post_alternation_bull_highs || [] : [],
                },
                {
                    level: 2,
                    landmarks: this.showSecondaryTrend
                        ? this.theory?.secondary_trends?.post_alternation_bull_highs || []
                        : [],
                },
                {
                    level: 3,
                    landmarks: this.showTertiaryTrend
                        ? this.theory?.tertiary_trends?.post_alternation_bull_highs || []
                        : [],
                },
            ],
            from,
            to,
        );
        const bullishTurnSignals = bullishTurnSignalAnnotations(
            [
                {
                    level: 1,
                    landmarks: this.showTrend ? this.theory?.reversal_trends?.bullish_turn_signals || [] : [],
                },
                {
                    level: 2,
                    landmarks: this.showSecondaryTrend ? this.theory?.secondary_trends?.bullish_turn_signals || [] : [],
                },
                {
                    level: 3,
                    landmarks: this.showTertiaryTrend ? this.theory?.tertiary_trends?.bullish_turn_signals || [] : [],
                },
            ],
            from,
            to,
        );
        this.windowAnnotations = [
            ...this.annotations.filter(
                (item) =>
                    item.category !== "tertiary-abc" ||
                    (this.showTertiaryTrend && item.time <= (this.theory?.asof || this.data.asof)),
            ),
            ...trendKeys,
            ...bullFlipHighs,
            ...bullAlternationLows,
            ...postAlternationBullHighs,
            ...bullishTurnSignals,
        ];
        this.groups = markerGroups(this.windowAnnotations, this.options, span).filter(
            (g) => g.time >= from && g.time <= to,
        );
        avoidLabelCollisions(this.groups, (time) => this.chart.timeScale().timeToCoordinate(time));
        const tradeGroups = this.groups.filter((g) => g.items[0].kind === "fill");
        this.markers.setMarkers(this.groups.filter((g) => g.items[0].kind !== "fill").map((g) => g.marker));
        this.tradeMarkerOverlay.setMarkers(
            tradeGroups.map((g) => ({
                id: g.id,
                time: g.time,
                side: g.items[0].side,
                price: g.items[0].price,
            })),
        );
        this.drawLastFallHighGuides(trendKeys);
        this.drawBullishTurnGuides(bullishTurnSignals);
        this.drawTertiaryRetracementGuides(to);
        this.container.dataset.markerCount = this.groups.length;
        this.container.dataset.tertiaryAbcCount = String(
            this.groups.flatMap((group) => group.items).filter((item) => item.category === "tertiary-abc").length,
        );
        this.container.dataset.lastFallHighCount = String(trendKeys.length);
        this.container.dataset.bearToBullHighCount = String(this.options.bullFlipHighs ? bullFlipHighs.length : 0);
        this.container.dataset.bearBullAlternationLowCount = String(
            this.options.bullAlternationLows ? bullAlternationLows.length : 0,
        );
        this.container.dataset.postAlternationBullHighCount = String(
            this.options.postAlternationBullHighs ? postAlternationBullHighs.length : 0,
        );
        this.container.dataset.bullishTurnSignalCount = String(
            this.options.bullishTurnSignals ? bullishTurnSignals.length : 0,
        );
        this.onVisible(
            this.groups.flatMap((g) => g.items),
            { from, to, trend, secondaryTrend, secondaryDeveloping, tertiaryTrend, tertiaryDeveloping },
        );
        this.renderPolyline(from, to);
    }
    itemsAt(time, id) {
        const drawing = this.lectureOverlay.annotation(id);
        if (drawing) {
            // A development vertex can occupy the exact confirmed landmark.
            // Preserve the confirmed evidence when the polyline wins hit testing.
            const landmarks = this.groups
                .flatMap((group) => group.items)
                .filter((item) => item.kind === "trend-key" && item.time === time && item.price === drawing.price);
            return [...landmarks, drawing];
        }
        const group = this.groups?.find((g) => g.id === id);
        return (
            group?.items ||
            visibleAnnotations(this.windowAnnotations || this.annotations, this.options)
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
    flashSelectedAnnotation(id, stage) {
        if (this.selected?.id !== id) return;
        this.focusFlashOverlay.flash(this.selected, stage);
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
            const end = item.raw.breakout || item.raw.selected_low;
            if (!end || item.time >= end.time) continue;
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
                { time: end.time, value: item.price },
            ]);
            this.lastFallHighLines.push(series);
        }
        this.container.dataset.lastFallHighGuides = String(this.lastFallHighLines.length);
    }
    clearBullishTurnGuides() {
        for (const series of this.bullishTurnGuideLines) this.chart.removeSeries(series);
        this.bullishTurnGuideLines = [];
        this.bullishTurnGuideKey = "";
        this.container.dataset.bullishTurnGuides = "0";
    }
    drawBullishTurnGuides(items) {
        const visible = this.options.bullishTurnSignals ? items : [];
        const key = visible.map((item) => item.id).join("|");
        if (key === this.bullishTurnGuideKey) return;
        this.clearBullishTurnGuides();
        this.bullishTurnGuideKey = key;
        for (const item of visible) {
            const start = item.raw.confirmed_flip_high;
            if (!start || start.time >= item.time || !Number.isFinite(start.value)) continue;
            const series = this.chart.addSeries(L.LineSeries, {
                color: withOpacity(item.color, 0.82),
                lineStyle: 3,
                lineWidth: 1,
                title: `${item.raw.trend_level}级转多突破`,
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData([
                { time: start.time, value: start.value },
                { time: item.time, value: start.value },
            ]);
            this.bullishTurnGuideLines.push(series);
        }
        this.container.dataset.bullishTurnGuides = String(this.bullishTurnGuideLines.length);
    }
    clearTertiaryRetracementGuides() {
        for (const series of this.tertiaryRetracementLines) this.chart.removeSeries(series);
        this.tertiaryRetracementLines = [];
        this.tertiaryRetracementKey = "";
        this.container.dataset.tertiaryRetracementGuides = "0";
    }
    drawTertiaryRetracementGuides(to) {
        const guides =
            this.options.tertiaryRetracement && this.showTertiaryTrend && this.polylineEnabled
                ? tertiaryRetracementGuides(this.theory, this.data?.bars, to)
                : [];
        const key = JSON.stringify(guides);
        if (key === this.tertiaryRetracementKey) return;
        this.clearTertiaryRetracementGuides();
        this.tertiaryRetracementKey = key;
        for (const guide of guides) {
            const series = this.chart.addSeries(L.LineSeries, {
                color: "#d98638",
                lineStyle: 2,
                lineWidth: 1,
                title: guide.title,
                lastValueVisible: true,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData([
                { time: guide.start, value: guide.price },
                { time: guide.end, value: guide.price },
            ]);
            this.tertiaryRetracementLines.push(series);
        }
        this.container.dataset.tertiaryRetracementGuides = String(this.tertiaryRetracementLines.length);
    }
    drawLevels() {
        this.clearLevels();
        const item = this.selected;
        // 拒单不产生常驻图标；从右侧账本主动定位时，仅临时标示对应 K 线的参考价。
        const blockedOrder = item?.kind === "order" && item.status === "cancelled";
        const blockedCandidate = item?.kind === "candidate";
        if (
            !item ||
            !this.data ||
            !this.options.levels ||
            (!blockedOrder && !blockedCandidate && !visibleAnnotations([item], this.options).length)
        )
            return;
        const levels = blockedOrder
            ? item.levels.slice(0, 1)
            : blockedCandidate
              ? [{ name: "候选参考价（未下单）", price: item.price }]
              : item.levels;
        for (const [i, level] of levels.entries()) {
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
                // Selected N targets must remain visible even above the candle
                // range; deselection removes these series and restores scaling.
                ...(item.raw?.event === "n_completed" ? {} : { autoscaleInfoProvider: () => null }),
            });
            const start = level.available_at && level.available_at > item.time ? level.available_at : item.time;
            const points = [{ time: start, value: level.price }];
            if (start < this.data.bars.at(-1).time)
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
        this.clearBullishTurnGuides();
        this.clearTertiaryRetracementGuides();
        this.container.dataset.lastFallHighCount = "0";
        this.container.dataset.bearToBullHighCount = "0";
        this.container.dataset.bearBullAlternationLowCount = "0";
        this.container.dataset.postAlternationBullHighCount = "0";
        this.container.dataset.bullishTurnSignalCount = "0";
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
    setTrendPriceLabelsVisible(show) {
        this.lectureOverlay.setReversalPriceLabelsVisible(show);
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
        if (
            !show &&
            (this.selected?.kind === "trend" || this.selected?.category === "tertiary-abc") &&
            this.selected.raw.trend_level === 3
        ) {
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
        const second = this.showSecondaryTrend ? this.theory?.secondary_trends?.strokes || [] : [];
        const secondaryDeveloping = this.showSecondaryTrend
            ? this.theory?.secondary_trends?.developing_strokes || []
            : [];
        const tertiaryDeveloping = this.showTertiaryTrend ? this.theory.tertiary_trends?.developing_strokes || [] : [];
        const all = lecture
            ? [
                  ...this.theory.lecture_drawing.strokes,
                  ...lectureConnections(this.theory.lecture_drawing.strokes),
                  ...first,
                  ...secondaryConnections(second, this.theory.reversal_trends?.strokes || []),
                  ...second,
                  // Development paths remain separate from formal points so
                  // level 3 never consumes an unfinished level-2 reversal.
                  ...secondaryDeveloping,
                  // Python owns the active level-3 endpoint.  Rendering its
                  // display-only stroke here avoids a second browser rule.
                  ...tertiaryDeveloping,
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
        clearTimeout(this.tooltipHideTimer);
        this.focusFlashOverlay.clear();
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
