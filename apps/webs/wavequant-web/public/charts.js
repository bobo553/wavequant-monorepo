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
    visibleLastFallHighGuides,
} from "./annotations.js";
import { candleDetails } from "./candle-details.js";
import {
    chartNavigationState,
    panChartRange,
    seekChartRange,
    tradePointRange,
    zoomChartRange,
} from "./chart-navigation.js";
import {
    combinedAAnnotations,
    combinedAObservations,
    combinedARetracementGuides,
    combinedAWaveConnections,
    latestCombinedAObservation,
} from "./combined-a-wave.js";
import { FocusFlashOverlay } from "./focus-flash-overlay.js";
import { num } from "./labels.js";
import { LectureOverlay, lectureConnections, secondaryConnections } from "./lecture-overlay.js";
import { drawdownCandleRange } from "./max-drawdown.js";
import { isPositiveNTarget, nTargetAt, nTargetObservations } from "./n-target-focus.js";
import { selectedTertiaryThirds, tertiaryRetracementGuides } from "./retracement-guides.js";
import { TargetGuideOverlay, targetLevelGuide } from "./target-level-guides.js";
import { TradeMarkerOverlay } from "./trade-marker-overlay.js";
import {
    waveCProjectionAnnotation,
    waveCProjectionEvidenceAnnotations,
    waveCProjectionForSelection,
    waveCProjectionLegs,
    waveCProjectionsFromStructure,
} from "./wave-c-projection.js";
import { selectWaveConnections } from "./wave-connections.js";
import { WaveEndpointOverlay, projectionWaveEndpoints, selectedWaveEndpoints } from "./wave-endpoint-overlay.js";

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

function waveProjectionRange(item, asof) {
    const projection = item?.raw;
    const knownAt = [projection?.aKnownAt, projection?.bKnownAt, projection?.confirmedAt, item?.time]
        .filter((time) => typeof time === "string")
        .sort()
        .at(-1);
    if (!projection?.originTime || !asof || !knownAt || knownAt > asof) return null;
    const completed = projection.cTime && projection.cKnownAt && projection.cKnownAt <= asof;
    let until = completed ? projection.cTime : asof;
    if (projection.targetValidUntil && projection.targetValidUntil < until) until = projection.targetValidUntil;
    return { from: projection.originTime, to: until };
}

export class PriceChart {
    constructor(
        container,
        onHover,
        onSelect = () => {},
        onVisible = () => {},
        onCopyCandle = () => {},
        onViewport = () => {},
    ) {
        this.container = container;
        this.onSelect = onSelect;
        this.onVisible = onVisible;
        this.onViewport = onViewport;
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
        this.waveAbLines = [];
        this.lastFallHighLines = [];
        this.lastFallHighLineKey = "";
        this.bullishTurnGuideLines = [];
        this.bullishTurnGuideKey = "";
        this.tertiaryRetracementLines = [];
        this.tertiaryRetracementKey = "";
        this.combinedARetracementLines = [];
        this.combinedARetracementKey = "";
        this.combinedAWaveLines = [];
        this.combinedAWaveKey = "";
        this.windowAnnotations = [];
        this.data = null;
        this.theory = null;
        this.geometryVisible = false;
        this.annotations = [];
        this.autoWaveProjection = null;
        this.autoWaveProjections = [];
        this.autoCombinedAObservations = [];
        this.autoWaveEvidence = [];
        this.hoveredWaveProjection = null;
        this.hoveredWaveTime = null;
        this.hoveredWaveId = null;
        this.focusedWaveProjection = null;
        this.focusedWaveTime = null;
        this.focusedWaveId = null;
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
        this.waveEndpointOverlay = new WaveEndpointOverlay(container);
        this.candles.attachPrimitive(this.waveEndpointOverlay);
        this.targetGuideOverlay = new TargetGuideOverlay();
        this.candles.attachPrimitive(this.targetGuideOverlay);
        this.combinedAGuideOverlay = new TargetGuideOverlay();
        this.candles.attachPrimitive(this.combinedAGuideOverlay);
        this.focusFlashOverlay = new FocusFlashOverlay(container);
        this.candles.attachPrimitive(this.focusFlashOverlay);
        this.onTertiaryPointerUp = (event) => {
            if (!(event.target instanceof HTMLCanvasElement)) return;
            const bounds = container.getBoundingClientRect();
            const hit = this.lectureOverlay.hitTest(event.clientX - bounds.left, event.clientY - bounds.top);
            const selected = hit && this.lectureOverlay.annotation(hit.externalId);
            if (
                selected?.raw?.trend_level === 3 &&
                ["lecture_level3_not_strategy_confirmation", "display_only_developing_path"].includes(
                    selected.raw.scope,
                )
            )
                this.selectAnnotation(selected.id, false);
        };
        container.addEventListener("pointerup", this.onTertiaryPointerUp);
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
            this.updateWaveProjectionHover(null);
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
            const barIndex = this.data.bars.findIndex((candidate) => candidate.time === p.time);
            const bar = this.data.bars[barIndex];
            this.updateWaveProjectionHover(p.point && bar ? bar.time : null, p.hoveredObjectId);
            if (bar) onHover(bar);
            const candleItems = this.itemsAt(p.time, p.hoveredObjectId);
            const items =
                this.hoveredWaveProjection && !candleItems.some((item) => item.id === this.hoveredWaveProjection.id)
                    ? [this.hoveredWaveProjection, ...candleItems]
                    : candleItems;
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
            const previousClose = barIndex > 0 ? this.data.bars[barIndex - 1].close : undefined;
            for (const [label, value] of candleDetails(bar, previousClose).slice(1)) {
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
                if (item === this.hoveredWaveProjection) {
                    for (const { tag, text } of this.waveProjectionHoverLines()) {
                        const detail = document.createElement(tag);
                        detail.textContent = text;
                        this.tooltip.append(detail);
                    }
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
                    Math.max(4, Math.min(p.point.y + 12, container.clientHeight - this.tooltip.offsetHeight - 4)) +
                    "px";
            }
        });
        this.chart.subscribeClick((p) => {
            this.focusCandleTargets(p.time, p.hoveredObjectId);
            const drawingId = p.point && this.lectureOverlay.hitTest(p.point.x, p.point.y)?.externalId;
            if (drawingId && drawingId === this.selected?.id) return;
            const items = this.itemsAt(p.time, drawingId || p.hoveredObjectId);
            if ((drawingId || p.hoveredObjectId) && items.length) {
                if (items[0].id === this.selected?.id) return;
                this.selectAnnotation(items[0].id, false, items);
                return;
            }
            const projection =
                this.data && this.theory && p.time
                    ? waveCProjectionForSelection(
                          this.data.bars,
                          this.theory.events,
                          p.time,
                          this.autoWaveProjections.find((item) => item.raw.aTime === p.time)?.raw,
                      )
                    : null;
            if (!projection) {
                if (items.length) this.selectAnnotation(items[0].id, false, items);
                return;
            }
            const selected =
                this.autoWaveProjections.find((item) => item.raw === projection) ||
                waveCProjectionAnnotation(
                    projection,
                    this.data.bars,
                    this.theory?.asof && this.theory.asof < this.data.asof ? this.theory.asof : this.data.asof,
                );
            this.selected = selected;
            this.waveEndpointOverlay.setPoints([]);
            this.drawLevels();
            this.scheduleMarkers();
            this.onSelect([selected]);
        });
        this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => {
            this.scheduleMarkers();
            this.updateViewport();
        });
    }
    setData(data, options = {}) {
        this.focusFlashOverlay.clear();
        clearTimeout(this.tooltipHideTimer);
        this.tooltipHovered = false;
        this.tooltipBarTime = null;
        this.data = data;
        this.theory = null;
        this.geometryVisible = false;
        this.selected = null;
        this.autoWaveProjection = null;
        this.autoWaveProjections = [];
        this.autoCombinedAObservations = [];
        this.autoWaveEvidence = [];
        this.hoveredWaveProjection = null;
        this.hoveredWaveTime = null;
        this.hoveredWaveId = null;
        this.focusedWaveProjection = null;
        this.focusedWaveTime = null;
        this.focusedWaveId = null;
        this.waveEndpointOverlay.setPoints([]);
        this.tooltip.hidden = true;
        this.clearTheory();
        this.clearLevels();
        this.candles.setData(data.bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        this.setVolume(options.volume !== false);
        this.setAnnotationOptions({ signals: options.markers !== false, ...options.annotations });
        const end = data.bars.length - 1;
        this.chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, end - 140), to: end + 6 });
        this.updateViewport();
    }
    navigationState() {
        return chartNavigationState(this.chart.timeScale().getVisibleLogicalRange(), this.data?.bars.length ?? 0);
    }
    updateViewport() {
        const state = this.navigationState();
        this.onViewport(state, this.data?.bars ?? []);
    }
    pan(direction) {
        const state = this.navigationState();
        if (!state) return;
        this.chart.timeScale().setVisibleLogicalRange(panChartRange(state, direction));
    }
    zoom(direction) {
        const state = this.navigationState();
        if (!state) return;
        this.chart.timeScale().setVisibleLogicalRange(zoomChartRange(state, this.data.bars.length, direction));
    }
    seek(position) {
        const state = this.navigationState();
        if (!state || !Number.isFinite(position)) return;
        this.chart.timeScale().setVisibleLogicalRange(seekChartRange(state, position));
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
        this.annotations.push(
            ...this.autoWaveEvidence,
            ...this.autoWaveProjections,
            ...combinedAAnnotations(this.displayedCombinedAObservations()),
        );
        this.drawWaveAbPath();
        this.refreshMarkers();
        this.drawLevels();
    }
    waveProjectionAsOf() {
        return this.theory?.asof && this.theory.asof < this.data?.asof ? this.theory.asof : this.data?.asof;
    }
    displayedCombinedAObservations() {
        const latest = latestCombinedAObservation(this.autoCombinedAObservations, this.waveProjectionAsOf());
        return latest ? [latest] : [];
    }
    waveProjectionAt(time, id) {
        if (!time || !this.options.tertiaryAbc) return null;
        const asof = this.waveProjectionAsOf();
        const candidates = this.autoWaveProjections.filter((item) => {
            const range = waveProjectionRange(item, asof);
            return range && range.from <= time && time <= range.to;
        });
        const evidence = this.autoWaveEvidence.find((item) => item.id === id);
        const hit = candidates.find((item) => item.id === id || (evidence && item.raw === evidence.raw));
        if (hit) return hit;
        const selected =
            this.selected?.category === "wave-projection"
                ? candidates.find((item) => item.id === this.selected.id || item.raw === this.selected.raw)
                : null;
        return (
            selected ||
            candidates.sort(
                (left, right) =>
                    (right.raw.trendLevel || 2) - (left.raw.trendLevel || 2) ||
                    right.raw.originTime.localeCompare(left.raw.originTime) ||
                    right.raw.aTime.localeCompare(left.raw.aTime) ||
                    right.time.localeCompare(left.time) ||
                    left.id.localeCompare(right.id),
            )[0] ||
            null
        );
    }
    displayedWaveProjection() {
        if (!this.options.tertiaryAbc) return null;
        const selected =
            this.selected?.category === "wave-projection"
                ? this.autoWaveProjections.find(
                      (item) => item.id === this.selected.id || item.raw === this.selected.raw,
                  )
                : null;
        return this.hoveredWaveProjection || this.focusedWaveProjection || selected || null;
    }
    focusWaveTargets(time, id, redraw = true) {
        this.focusedWaveTime = this.data?.bars.some((bar) => bar.time === time) ? time : null;
        this.focusedWaveId = id || null;
        this.focusedWaveProjection = this.waveProjectionAt(this.focusedWaveTime, this.focusedWaveId);
        if (redraw) this.drawLevels();
    }
    focusCandleTargets(time, id) {
        this.focusNTargets(time, id, false);
        this.focusWaveTargets(time, id);
    }
    selectedNTargetId() {
        if (isPositiveNTarget(this.selected)) return this.selected.id;
        const date = this.selected?.levels?.find((level) => level.stage === "one_p")?.n_date;
        return this.nTargetObservations?.find(({ item }) => item.time === date)?.item.id;
    }
    /** 悬停只临时展示所在 ABC，保留成交选择与图窗；移出后恢复原目标。 */
    updateWaveProjectionHover(time, id) {
        const availableTime = time && this.data?.bars.some((bar) => bar.time === time) ? time : null;
        const previous = this.hoveredWaveProjection;
        const previousN = this.hoveredNTarget;
        const previousTime = this.hoveredWaveTime;
        this.hoveredWaveTime = availableTime;
        this.hoveredWaveId = id || null;
        this.hoveredWaveProjection = this.waveProjectionAt(availableTime, id);
        this.hoveredNTarget = this.options.levels
            ? nTargetAt(this.nTargetObservations || [], availableTime, id, this.selectedNTargetId())
            : null;
        if (
            previous !== this.hoveredWaveProjection ||
            previousN !== this.hoveredNTarget ||
            (this.hoveredNTarget && previousTime !== availableTime)
        )
            this.drawLevels();
    }
    focusNTargets(time, id, redraw = true) {
        this.focusedNTime = this.data?.bars.some((bar) => bar.time === time) ? time : null;
        this.focusedNTarget = nTargetAt(
            this.nTargetObservations || [],
            this.focusedNTime,
            id,
            this.selectedNTargetId(),
        );
        if (redraw) this.drawLevels();
    }
    waveProjectionHoverLines() {
        const item = this.hoveredWaveProjection;
        if (!item) return [];
        const projection = item.raw;
        return [
            {
                tag: "small",
                text: `A 起 ${projection.originTime} ${num(projection.origin, 4)} → A 顶 ${projection.aTime} ${num(projection.aHigh, 4)}；B ${projection.bTime} ${num(projection.bLow, 4)}${projection.cTime ? `；C 顶 ${projection.cTime} ${num(projection.cHigh, 4)}（${projection.cKnownAt} 确认）` : ""}`,
            },
            {
                tag: "small",
                text: `按 ${this.waveProjectionAsOf()} 截面复盘；目标生效 ${projection.confirmedAt || projection.bKnownAt || projection.bTime}`,
            },
            ...item.levels
                .filter((level) => ["c_0618", "c_equal", "c_1618"].includes(level.stage))
                .map((level) => {
                    const guide = targetLevelGuide(item, level, this.data.bars, this.waveProjectionAsOf());
                    return {
                        tag: "div",
                        text: `${level.name}｜${num(level.price, 4)}｜${guide?.targetState || "待达成"}`,
                    };
                }),
        ];
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
        const previousProjection = this.displayedWaveProjection() || this.selected || this.autoWaveProjection;
        const wasHoveringProjection = Boolean(this.hoveredWaveProjection);
        const viewportStartChanged = this.waveProjectionViewportFrom !== from;
        this.waveProjectionViewportFrom = from;
        const viewportEndChanged = this.nTargetViewportTo !== to;
        this.nTargetViewportTo = to;
        const asof = this.waveProjectionAsOf();
        this.autoWaveProjection =
            this.autoWaveProjections
                .filter((item) => {
                    const projectionRange = waveProjectionRange(item, asof);
                    return projectionRange && projectionRange.from <= to && projectionRange.to >= from;
                })
                .at(-1) || null;
        if (this.hoveredWaveTime && (this.hoveredWaveTime < from || this.hoveredWaveTime > to)) {
            this.hoveredWaveTime = null;
            this.hoveredWaveId = null;
        }
        if (this.focusedWaveTime && (this.focusedWaveTime < from || this.focusedWaveTime > to)) {
            this.focusedWaveTime = null;
            this.focusedWaveId = null;
        }
        this.focusedWaveProjection = this.waveProjectionAt(this.focusedWaveTime, this.focusedWaveId);
        const previousN = this.hoveredNTarget;
        this.hoveredWaveProjection = this.waveProjectionAt(this.hoveredWaveTime, this.hoveredWaveId);
        this.hoveredNTarget = this.options.levels
            ? nTargetAt(
                  this.nTargetObservations || [],
                  this.hoveredWaveTime,
                  this.hoveredWaveId,
                  this.selectedNTargetId(),
              )
            : null;
        const displayedProjection = this.displayedWaveProjection() || this.selected || this.autoWaveProjection;
        if (
            previousProjection !== displayedProjection ||
            previousN !== this.hoveredNTarget ||
            ((this.hoveredNTarget || this.focusedNTarget || isPositiveNTarget(this.selected)) &&
                (viewportStartChanged || viewportEndChanged)) ||
            (displayedProjection?.kind === "wave-projection" &&
                (viewportStartChanged ||
                    viewportEndChanged ||
                    wasHoveringProjection !== Boolean(this.hoveredWaveProjection)))
        )
            this.drawLevels();
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
        this.drawLastFallHighGuides(trendKeys, from, to);
        this.drawBullishTurnGuides(bullishTurnSignals);
        this.drawTertiaryRetracementGuides(to);
        this.drawCombinedARetracementGuides(from, to);
        this.drawCombinedAWavePath(from, to);
        this.container.dataset.markerCount = this.groups.length;
        this.container.dataset.tertiaryAbcCount = String(
            this.groups.flatMap((group) => group.items).filter((item) => item.category === "tertiary-abc").length,
        );
        this.container.dataset.waveAbcCount = String(
            new Set(
                this.groups
                    .flatMap((group) => group.items)
                    .filter((item) => item.kind === "wave-projection")
                    .map((item) => item.raw.aTime),
            ).size,
        );
        this.container.dataset.combinedAWaveCount = String(
            new Set(
                this.groups
                    .flatMap((group) => group.items)
                    .filter((item) => item.kind === "combined-a-wave")
                    .map((item) => item.raw.id),
            ).size,
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
        this.focusNTargets(selected.time, selected.id, false);
        this.focusWaveTargets(selected.kind === "fill" ? null : selected.time, selected.id, false);
        this.drawLevels();
        this.scheduleMarkers();
        this.onSelect(items || [selected]);
    }
    flashSelectedAnnotation(id, stage) {
        if (this.selected?.id !== id) return;
        this.focusFlashOverlay.flash(this.selected, stage);
    }
    flashCandle(bar) {
        this.focusFlashOverlay.flash({ id: `candle-focus-${bar.time}`, time: bar.time, price: bar.close });
    }
    clearLevels() {
        for (const s of this.levelLines) this.chart.removeSeries(s);
        this.levelLines = [];
        this.targetGuideOverlay?.setGuides([]);
        this.container.dataset.levelCount = "0";
    }
    clearLastFallHighGuides() {
        for (const series of this.lastFallHighLines) this.chart.removeSeries(series);
        this.lastFallHighLines = [];
        this.lastFallHighLineKey = "";
        this.container.dataset.lastFallHighGuides = "0";
    }
    drawLastFallHighGuides(items, from, to) {
        const visible = this.options.trendKeys ? visibleLastFallHighGuides(items, from, to) : [];
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
        const visible =
            this.options.tertiaryRetracement && this.showTertiaryTrend && this.polylineEnabled
                ? this.selected?.raw?.trend_level === 3 &&
                  ["lecture_level3_not_strategy_confirmation", "display_only_developing_path"].includes(
                      this.selected.raw.scope,
                  )
                    ? selectedTertiaryThirds(
                          this.selected,
                          this.theory,
                          this.data?.bars,
                          this.theory?.asof || this.data?.asof || to,
                      )
                    : tertiaryRetracementGuides(this.theory, this.data?.bars, to)
                : [];
        const key = JSON.stringify(visible);
        if (key === this.tertiaryRetracementKey) return;
        this.clearTertiaryRetracementGuides();
        this.tertiaryRetracementKey = key;
        for (const guide of visible) {
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
    clearCombinedAWavePath() {
        for (const series of this.combinedAWaveLines || []) this.chart.removeSeries(series);
        this.combinedAWaveLines = [];
        this.combinedAWaveKey = "";
        this.container.dataset.combinedAWaveLegs = "0";
    }
    drawCombinedAWavePath(from, to) {
        const visible = this.options.tertiaryAbc
            ? combinedAWaveConnections(this.displayedCombinedAObservations()).filter(
                  ({ points }) => points[0].time <= to && points[1].time >= from,
              )
            : [];
        const key = JSON.stringify(visible.map(({ id, group, points }) => [id, group, points]));
        if (key === this.combinedAWaveKey) return;
        this.clearCombinedAWavePath();
        this.combinedAWaveKey = key;
        for (const { points } of visible) {
            const series = this.chart.addSeries(L.LineSeries, {
                color: "#d986aa",
                lineStyle: 0,
                lineWidth: 2,
                title: "组合 A 浪",
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData(points);
            this.combinedAWaveLines.push(series);
        }
        this.container.dataset.combinedAWaveLegs = String(this.combinedAWaveLines.length);
    }
    clearCombinedARetracementGuides() {
        for (const series of this.combinedARetracementLines || []) this.chart.removeSeries(series);
        this.combinedARetracementLines = [];
        this.combinedARetracementKey = "";
        this.container.dataset.combinedARetracementGuides = "0";
        this.combinedAGuideOverlay?.setGuides([]);
    }
    drawCombinedARetracementGuides(from, to) {
        const visible =
            this.options.tertiaryAbc && this.options.levels
                ? combinedARetracementGuides(
                      this.displayedCombinedAObservations(),
                      this.data?.bars || [],
                      this.waveProjectionAsOf(),
                  ).filter(
                      (guide) =>
                          Number.isFinite(guide.price) &&
                          guide.start < guide.end &&
                          guide.start <= to &&
                          guide.end >= from,
                  )
                : [];
        const key = JSON.stringify(visible);
        if (key === this.combinedARetracementKey) return;
        this.clearCombinedARetracementGuides();
        this.combinedARetracementKey = key;
        for (const guide of visible) {
            const series = this.chart.addSeries(L.LineSeries, {
                color: "#d986aa",
                lineStyle: 2,
                lineWidth: 1,
                title: guide.name + " · " + guide.targetState,
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData([
                { time: guide.start, value: guide.price },
                { time: guide.end, value: guide.price },
            ]);
            this.combinedARetracementLines.push(series);
        }
        this.combinedAGuideOverlay?.setGuides(visible);
        this.container.dataset.combinedARetracementGuides = String(this.combinedARetracementLines.length);
    }
    drawLevels() {
        this.clearLevels();
        const projection = this.displayedWaveProjection();
        this.waveEndpointOverlay.setPoints(
            projection
                ? projectionWaveEndpoints(projection.raw, this.data?.bars || [], this.waveProjectionAsOf())
                : selectedWaveEndpoints(this.selected, this.data?.bars || []),
        );
        const focusedN =
            this.hoveredNTarget || this.focusedNTarget || (isPositiveNTarget(this.selected) ? this.selected : null);
        let item = projection || this.selected || this.autoWaveProjection;
        if (isPositiveNTarget(item) && focusedN) item = focusedN;
        // 拒单不产生常驻图标；从右侧账本主动定位时，仅临时标示对应 K 线的参考价。
        const blockedOrder = item?.kind === "order" && item.status === "cancelled";
        const blockedCandidate = item?.kind === "candidate";
        if (!this.data || !this.options.levels) return;
        const primaryVisible =
            item &&
            (blockedOrder ||
                blockedCandidate ||
                isPositiveNTarget(item) ||
                visibleAnnotations([item], this.options).length);
        const primaryLevels = !primaryVisible
            ? []
            : blockedOrder
              ? item.levels.slice(0, 1)
              : blockedCandidate
                ? [{ name: "候选参考价（未下单）", price: item.price }]
                : item.levels;
        const targetStages = new Set(["c_0618", "c_equal", "c_1618", "one_p", "two_t", "five_top", "ten_full"]);
        const levels = primaryLevels.map((level) => ({ item, level }));
        if (focusedN && focusedN !== item)
            levels.push(
                ...focusedN.levels
                    .filter((level) => ["one_p", "two_t"].includes(level.stage))
                    .map((level) => ({ item: focusedN, level })),
            );
        const targetGuides = [];
        const seenN = new Set();
        for (const [i, { item, level }] of levels.entries()) {
            if (!Number.isFinite(level.price)) continue;
            if (["one_p", "two_t"].includes(level.stage)) {
                const identity = `${level.stage}:${level.price}`;
                if (seenN.has(identity)) continue;
                seenN.add(identity);
            }
            const projectionTargets = item.levels?.some((candidate) => targetStages.has(candidate.stage)) || false;
            const guide = targetLevelGuide(
                item,
                level,
                this.data.bars,
                this.theory?.asof && this.theory.asof < this.data.asof ? this.theory.asof : this.data.asof,
            );
            const color = ["#ebbc70", "#a29ce0", "#5ebeb0"][i % 3];
            const s = this.chart.addSeries(L.LineSeries, {
                color,
                lineStyle: guide?.targetState === "已触及" || item.kind === "trend" ? 0 : 2,
                lineWidth: level.stage === "c_equal" ? 2 : 1,
                title: level.display_name || level.name,
                lastValueVisible: !targetStages.has(level.stage),
                priceLineVisible: !guide && (item.kind === "wave-projection" || targetStages.has(level.stage)),
                crosshairMarkerVisible: false,
                pointMarkersVisible: !guide && item.kind !== "trend",
                pointMarkersRadius: 2,
                // 延伸目标和远端五顶、十满不扩展价格轴；图外目标由边缘标签提示。
                ...(!["c_1618", "five_top", "ten_full"].includes(level.stage) &&
                (item.raw?.event === "n_completed" || item.kind === "wave-projection" || projectionTargets)
                    ? {}
                    : { autoscaleInfoProvider: () => null }),
            });
            const start =
                guide?.start || (level.available_at && level.available_at > item.time ? level.available_at : item.time);
            const points = [{ time: start, value: level.price }];
            if (guide?.end && guide.end > start) points.push({ time: guide.end, value: level.price });
            else if (!guide && start < this.data.bars.at(-1).time)
                points.push({ time: this.data.bars.at(-1).time, value: level.price });
            let displayGuide = guide;
            if (guide && ["one_p", "two_t"].includes(level.stage) && focusedN) {
                const range = this.chart.timeScale().getVisibleLogicalRange();
                const first = Math.max(0, Math.ceil(range?.from ?? 0));
                const last = Math.min(this.data.bars.length - 1, Math.floor(range?.to ?? this.data.bars.length - 1));
                const from = this.data.bars[first]?.time;
                const to = this.data.bars[last]?.time;
                const time = this.hoveredNTarget ? this.hoveredWaveTime : this.focusedNTime;
                displayGuide = {
                    ...guide,
                    display_at:
                        time && time >= from && time <= to
                            ? time
                            : from && (guide.start < from || guide.start > to)
                              ? from
                              : guide.start,
                    targetState: guide.end ? "已突破" : "未突破",
                };
            }
            if (guide && item.kind === "wave-projection" && ["c_0618", "c_equal", "c_1618"].includes(level.stage)) {
                const visibleRange = this.chart.timeScale().getVisibleLogicalRange();
                const visibleStart = this.data.bars[Math.max(0, Math.ceil(visibleRange?.from || 0))]?.time;
                const visibleEnd =
                    this.data.bars[
                        Math.min(this.data.bars.length - 1, Math.floor(visibleRange?.to ?? this.data.bars.length - 1))
                    ]?.time;
                const focusTime =
                    this.hoveredWaveProjection === item
                        ? this.hoveredWaveTime
                        : this.focusedWaveProjection === item
                          ? this.focusedWaveTime
                          : null;
                const displayStart =
                    focusTime && focusTime >= visibleStart && focusTime <= visibleEnd
                        ? focusTime
                        : visibleStart && (guide.start < visibleStart || guide.start > visibleEnd)
                          ? visibleStart
                          : guide.start;
                // 标签随复盘光标或可见 C 区间出现；历史锚点、可知日与首次触及时间不改。
                displayGuide = {
                    ...guide,
                    display_at: displayStart,
                };
            }
            if (targetStages.has(level.stage))
                targetGuides.push({
                    ...(displayGuide || {
                        start,
                        end: points.at(-1).time,
                        price: level.price,
                        name: level.display_name || level.name,
                        // 旧记录缺少结构锚点，仍标注目标，但不推断突破状态。
                        statusKnown: false,
                    }),
                    color,
                    stage: level.stage,
                });
            s.setData(points);
            this.levelLines.push(s);
        }
        this.targetGuideOverlay?.setGuides(targetGuides);
        this.container.dataset.levelCount = String(this.levelLines.length);
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
        this.nTargetObservations = [];
        this.hoveredNTarget = null;
        this.focusedNTarget = null;
        this.focusedNTime = null;
        this.nTargetViewportTo = null;
        this.geometryVisible = false;
        this.hoveredWaveProjection = null;
        this.hoveredWaveTime = null;
        this.hoveredWaveId = null;
        this.focusedWaveProjection = null;
        this.focusedWaveTime = null;
        this.focusedWaveId = null;
        this.waveEndpointOverlay.setPoints(selectedWaveEndpoints(this.selected, this.data?.bars || []));
        for (const series of this.lines) this.chart.removeSeries(series);
        this.lines = [];
        this.clearWaveAbPath();
        this.windowAnnotations = [];
        this.clearLastFallHighGuides();
        this.clearBullishTurnGuides();
        this.clearTertiaryRetracementGuides();
        this.autoCombinedAObservations = [];
        this.container.dataset.combinedAWaveCount = "0";
        this.clearCombinedARetracementGuides();
        this.container.dataset.lastFallHighCount = "0";
        this.clearCombinedAWavePath();
        this.container.dataset.bearToBullHighCount = "0";
        this.container.dataset.bearBullAlternationLowCount = "0";
        this.container.dataset.postAlternationBullHighCount = "0";
        this.container.dataset.bullishTurnSignalCount = "0";
        this.polylineEnabled = false;
        this.clearPolyline();
    }
    clearWaveAbPath() {
        for (const series of this.waveAbLines) this.chart.removeSeries(series);
        this.waveAbLines = [];
        this.container.dataset.waveAbcLegs = "0";
    }
    drawWaveAbPath() {
        this.clearWaveAbPath();
        if (!this.options.tertiaryAbc || !this.geometryVisible || !this.data) return;
        const connections = selectWaveConnections(
            this.autoWaveProjections.flatMap((item) =>
                waveCProjectionLegs(item.raw).map((leg) => ({
                    ...leg,
                    id: `${item.id}:${leg.title}`,
                    group: `abc:${item.raw.trendLevel || 2}:${leg.title}`,
                })),
            ),
        );
        for (const { title, color, points } of connections) {
            const series = this.chart.addSeries(L.LineSeries, {
                color,
                lineStyle: 2,
                lineWidth: 2,
                title,
                lastValueVisible: false,
                priceLineVisible: false,
                crosshairMarkerVisible: false,
                pointMarkersVisible: false,
                autoscaleInfoProvider: () => null,
            });
            series.setData(points);
            this.waveAbLines.push(series);
        }
        this.container.dataset.waveAbcLegs = String(this.waveAbLines.length);
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
        const focusedTime = this.focusedWaveTime;
        const focusedId = this.focusedWaveId;
        const focusedNTime = this.focusedNTime;
        const focusedNId = this.focusedNTarget?.id;
        this.theory = theory;
        this.clearTheory();
        this.geometryVisible = geometry;
        const projections = theory && this.data ? waveCProjectionsFromStructure(this.data.bars, theory) : [];
        const asof = theory?.asof && theory.asof < this.data?.asof ? theory.asof : this.data?.asof;
        this.autoWaveProjections = projections.map((projection) =>
            waveCProjectionAnnotation(projection, this.data.bars, asof),
        );
        this.autoWaveProjection = this.autoWaveProjections.at(-1) || null;
        this.autoWaveEvidence = projections.flatMap((projection) =>
            waveCProjectionEvidenceAnnotations(projection, this.data.bars, asof),
        );
        this.autoCombinedAObservations =
            theory && this.data ? combinedAObservations(this.data.bars, { ...theory, asof }, projections) : [];
        this.annotations = buildAnnotations(this.data, theory);
        this.nTargetObservations = nTargetObservations(this.annotations, this.data?.bars || [], asof);
        if (isPositiveNTarget(this.selected))
            this.selected = this.nTargetObservations.find(({ item }) => item.id === this.selected.id)?.item || null;
        if (this.selected?.category === "wave-projection")
            this.selected =
                [...this.autoWaveProjections, ...this.autoWaveEvidence].find((item) => item.id === this.selected.id) ||
                null;
        this.annotations.push(
            ...this.autoWaveEvidence,
            ...this.autoWaveProjections,
            ...combinedAAnnotations(this.displayedCombinedAObservations()),
        );
        if (theory) {
            this.focusNTargets(focusedNTime, focusedNId, false);
            this.focusWaveTargets(focusedTime, focusedId, false);
        }
        this.drawWaveAbPath();
        this.refreshMarkers();
        this.drawLevels();
        if (!theory || !this.data || !geometry) return;
        const nConnections = selectWaveConnections(
            theory.shapes.flatMap((shape) =>
                shape.points.slice(1).map((point, index) => ({
                    group: `n:${shape.trend_level || 1}:${shape.direction}`,
                    color: shape.direction === "up" ? "#60cfc3" : "#cba271",
                    points: [shape.points[index], point],
                })),
            ),
        );
        for (const { points, color } of nConnections) this.addLine(points, color, 0, 2);
        this.polylineEnabled = true;
        this.refreshMarkers();
    }
    focus(time) {
        const bar = this.data?.bars.find((bar) => bar.time >= time);
        return bar ? this.focusTrade(bar.time) : false;
    }
    focusWaveProjection() {
        const item = this.autoWaveProjection || this.autoWaveProjections.at(-1);
        if (!item || !this.data) return false;
        const first = this.data.bars.findIndex((bar) => bar.time === item.raw.originTime);
        const last = this.data.bars.findIndex((bar) => bar.time === item.time);
        if (first < 0 || last < first) return false;
        if (!this.focusTrade(item.time)) return false;
        this.refreshMarkers();
        this.selectAnnotation(item.id, false);
        return true;
    }
    focusTrade(time) {
        const index = this.data?.bars.findIndex((bar) => bar.time === time) ?? -1;
        const timeScale = this.chart.timeScale();
        const range = tradePointRange(index, this.data?.bars.length ?? 0, timeScale.getVisibleLogicalRange());
        if (!range) return false;
        timeScale.setVisibleLogicalRange(range);
        this.flashCandle(this.data.bars[index]);
        this.focusCandleTargets(this.data.bars[index].time);
        return true;
    }
    focusRange(from, to) {
        const timeScale = this.chart.timeScale();
        const range = drawdownCandleRange({ from, to }, this.data?.bars, timeScale.getVisibleLogicalRange());
        if (!range) return false;
        timeScale.setVisibleLogicalRange(range);
        const bars = this.data.bars.filter((bar) => bar.time >= from && bar.time <= to);
        this.flashCandle(bars[Math.floor((bars.length - 1) / 2)]);
        return true;
    }
    destroy() {
        if (this.frame) cancelAnimationFrame(this.frame);
        clearTimeout(this.tooltipHideTimer);
        this.focusFlashOverlay.clear();
        this.container.removeEventListener("pointerup", this.onTertiaryPointerUp);
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
    update(curve, holdingCurve = []) {
        ["value", "drawdown", "exposure"].forEach((key, i) => {
            this.instances[i].series.setData(
                i === 1 ? holdingCurve : curve.map((r) => ({ time: r.time, value: r[key] })),
            );
            this.instances[i].chart.timeScale().fitContent();
        });
    }
    resize() {
        this.instances.forEach(({ chart }) => chart.timeScale().fitContent());
    }
}
