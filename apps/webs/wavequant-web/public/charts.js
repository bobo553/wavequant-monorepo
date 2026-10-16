import { aWaveAnnotations, aWaveLegs, aWaveObservations } from "./a-wave-observations.js";
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
import { chartMarketBars } from "./chart-market-bars.js";
import { candleDetails } from "./candle-details.js";
import { BuyEligibilityOverlay } from "./buy-eligibility-overlay.js";
import { withEligibilityNTargets } from "./eligibility-n-targets.js";
import { createLifecycleLandmarkReader, mergeLifecycleLandmarks } from "./lifecycle-landmarks.js";
import { bindChartKeyboardNavigation } from "./chart-keyboard-navigation.js";
import {
    chartNavigationState,
    panChartRange,
    seekChartRange,
    tradePointRange,
    zoomChartRange,
} from "./chart-navigation.js";
import { bindChartTooltipLayer, hideChartTooltip } from "./chart-tooltip.js";
import {
    combinedAAnnotations,
    combinedAObservations,
    combinedARetracementGuides,
    combinedAWaveConnections,
    latestCombinedAObservation,
} from "./combined-a-wave.js";
import { FocusFlashOverlay } from "./focus-flash-overlay.js";
import { num } from "./labels.js";
import { LectureOverlay, bullishObservationLabels, lectureConnections, secondaryConnections } from "./lecture-overlay.js";
import { drawdownCandleRange } from "./max-drawdown.js";
import { NStructureOverlay } from "./n-structure-overlay.js";
import {
    isNTargetStage,
    isPositiveNTarget,
    nTargetAt,
    nTargetGroup,
    nTargetGuide,
    nTargetLevels,
    nTargetObservations,
} from "./n-target-focus.js";
import { hoveredTrendThirds, selectedTertiaryThirds, tertiaryRetracementGuides } from "./retracement-guides.js";
import { TargetGuideOverlay, targetLevelGuide } from "./target-level-guides.js";
import { TradeMarkerOverlay } from "./trade-marker-overlay.js";
import { EligibilityBoundaryOverlay } from "./eligibility-boundary-overlay.js";
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

function clearSeriesCollection(owner, field) {
    const series = owner[field] ?? [];
    owner[field] = [];
    const wasRemoving = owner.removingSeries;
    owner.removingSeries = true;
    // SDK 删除序列时会同步触发十字光标回调；先释放归属并禁止清理期间重绘。
    try {
        for (const item of series) owner.chart.removeSeries(item);
    } finally {
        owner.removingSeries = wasRemoving;
    }
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
        onKeyboardNavigate = () => {},
    ) {
        this.container = container;
        this.onSelect = onSelect;
        this.onVisible = onVisible;
        this.onViewport = onViewport;
        this.chart = base(container);
        this.chart.timeScale().applyOptions({ shiftVisibleRangeOnNewBar: false });
        this.unbindKeyboardNavigation = bindChartKeyboardNavigation(container, this, onKeyboardNavigate);
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
        this.removingSeries = false;
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
        this.readLifecycleLandmarks = createLifecycleLandmarkReader();
        this.autoWaveProjection = null;
        this.autoWaveProjections = [];
        this.autoCombinedAObservations = [];
        this.autoAObservations = [];
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
        this.buyEligibilityOverlay = new BuyEligibilityOverlay(colors.up);
        this.candles.attachPrimitive(this.buyEligibilityOverlay);
        this.eligibilityBoundaryOverlay = new EligibilityBoundaryOverlay();
        this.candles.attachPrimitive(this.eligibilityBoundaryOverlay);
        this.lectureOverlay = new LectureOverlay(container);
        this.candles.attachPrimitive(this.lectureOverlay);
        this.tradeMarkerOverlay = new TradeMarkerOverlay(container);
        this.candles.attachPrimitive(this.tradeMarkerOverlay);
        this.waveEndpointOverlay = new WaveEndpointOverlay(container);
        this.candles.attachPrimitive(this.waveEndpointOverlay);
        this.targetGuideOverlay = new TargetGuideOverlay();
        this.candles.attachPrimitive(this.targetGuideOverlay);
        this.nStructureOverlay = new NStructureOverlay(container);
        this.candles.attachPrimitive(this.nStructureOverlay);
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
            // 卡片按钮拥有键盘焦点时保留当前 K 线，鼠标移出不能中断 Ctrl+C。
            // 焦点真正离开后由 focusout 关闭；没有焦点时仍按原悬停规则即时关闭。
            this.tooltipHovered = false;
            if (this.tooltip.contains(document.activeElement)) return;
            this.hideTooltip();
            this.updateWaveProjectionHover(null);
        });
        this.tooltip.addEventListener("focusin", () => clearTimeout(this.tooltipHideTimer));
        this.tooltip.addEventListener("focusout", (event) => {
            if (!this.tooltipHovered && !this.tooltip.contains(event.relatedTarget)) {
                this.hideTooltip();
                this.updateWaveProjectionHover(null);
            }
        });
        container.append(this.tooltip);
        this.tooltipLayer = bindChartTooltipLayer(container, this.tooltip, () => this.hideTooltip());
        this.chart.subscribeCrosshairMove((p) => {
            if (!this.data || this.removingSeries) return;
            // 鼠标仍在卡片内时保留该 K 线，不让底层画布通知抢走卡片。
            if (this.tooltipHovered) return;
            const marketBars = chartMarketBars(this.data);
            const barIndex = marketBars.findIndex((candidate) => candidate.time === p.time);
            const bar = marketBars[barIndex];
            if (this.tooltip.contains(document.activeElement)) {
                // 卡片按钮的焦点只保护离开画布的无效通知；真实光标回到 K 线
                // 必须更新日期。先转交焦点再重建卡片，避免移除聚焦按钮触发
                // focusout 后关闭刚更新的提示，或继续复制上一次的 K 线。
                if (!p.point || !bar) return;
                container.focus({ preventScroll: true });
            }
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
                    if (!this.tooltipHovered && !this.tooltip.contains(document.activeElement)) this.hideTooltip();
                }, 150);
                return;
            }
            clearTimeout(this.tooltipHideTimer);
            // 同一根K线保留卡片位置，让鼠标能穿过间隙点击复制按钮。
            const positionTooltip = this.tooltip.hidden || this.tooltipBarTime !== bar.time;
            this.tooltipBarTime = bar.time;
            this.tooltipLayer.show();
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
            for (const label of bullishObservationLabels(this.theory, bar.time)) {
                const observation = document.createElement("div");
                observation.textContent = label;
                this.tooltip.append(observation);
            }
            if (this.eligibilityBoundaryOverlay.contains(barIndex)) {
                const confirmation = document.createElement("div");
                confirmation.textContent = `${["", "一级", "二级", "三级"][this.eligibilityBoundaryOverlay.level]}${this.eligibilityBoundaryOverlay.labelAt(barIndex)}：${bar.time}（淡蓝背景；结束日停止新增买入，已有持仓继续按退出规则处理）`;
                this.tooltip.append(confirmation);
            }
            if (this.buyEligibilityOverlay.contains(barIndex)) {
                const eligibility = document.createElement("div");
                eligibility.textContent = `${["", "一级", "二级", "三级"][this.buyEligibilityOverlay.level]}资格结构区间（淡红背景；确认后可交易，跌破日停止买入）`;
                this.tooltip.append(eligibility);
            }
            const previousClose = barIndex > 0 ? marketBars[barIndex - 1].close : undefined;
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
            if (positionTooltip) this.tooltipLayer.position(p.point);
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
    hideTooltip() {
        clearTimeout(this.tooltipHideTimer);
        this.tooltipHovered = false;
        hideChartTooltip(this.tooltip);
    }
    setData(data, options = {}) {
        const oldData = this.data;
        const bars = chartMarketBars(data);
        const oldBars = chartMarketBars(oldData);
        if (options.preserveViewport && oldBars.length && oldData.symbol === data.symbol
            && (oldData.timeframe || "1d") === (data.timeframe || "1d")) {
            Object.assign(this.options, { signals: options.markers !== false, ...options.annotations });
            this.updateData(data);
            return;
        }
        const oldRange =
            options.preserveViewport &&
            oldData?.symbol === data.symbol &&
            (oldData.timeframe || "1d") === (data.timeframe || "1d")
                ? this.chart.timeScale().getVisibleLogicalRange()
                : null;
        const oldFirst = oldBars[0]?.time;
        const shift = oldFirst ? bars.findIndex((bar) => bar.time === oldFirst) : -1;
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
        this.hideTooltip();
        this.clearTheory();
        this.clearLevels();
        this.candles.setData(bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
        this.buyEligibilityOverlay.setData(data);
        this.eligibilityBoundaryOverlay.setData(data);
        this.setVolume(options.volume !== false);
        this.setAnnotationOptions({ signals: options.markers !== false, ...options.annotations });
        const end = bars.length - 1;
        if (bars.length) this.chart
            .timeScale()
            .setVisibleLogicalRange(
                oldRange && shift >= 0
                    ? { from: oldRange.from + shift, to: oldRange.to + shift }
                    : { from: Math.max(0, end - 140), to: end + 6 },
            );
        this.updateViewport();
    }
    updateData(data) {
        const previous = this.data;
        const previousBars = chartMarketBars(previous);
        const bars = chartMarketBars(data);
        const range = this.chart.timeScale().getVisibleLogicalRange();
        const anchorIndex = range ? Math.max(0, Math.min(previousBars.length - 1, Math.floor(range.from))) : 0;
        const anchorTime = previousBars[anchorIndex]?.time;
        const nextAnchorIndex = bars.findIndex((bar) => bar.time === anchorTime);
        const samePrefix = previousBars.length <= bars.length
            && previousBars.every((bar, index) => bar.time === bars[index].time);
        this.data = data;
        if (samePrefix) {
            bars.forEach((bar, index) => {
                const old = previousBars[index];
                const historical = index < previousBars.length - 1;
                if (!old || ["open", "high", "low", "close"].some((key) => old[key] !== bar[key])) {
                    const { time, open, high, low, close } = bar;
                    this.candles.update({ time, open, high, low, close }, historical);
                }
                if (this.volumeVisible && (!old || old.volume !== bar.volume || old.close !== bar.close || old.open !== bar.open)) {
                    this.volume.update({ time: bar.time, value: bar.volume,
                        color: bar.close >= bar.open ? "#ef718050" : "#3fba9750" }, historical);
                }
            });
        } else {
            // 补入更早的历史或替换截面时保留原可见日期，不重新定位最新一根。
            this.candles.setData(bars.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })));
            this.setVolume(this.volumeVisible);
        }
        this.buyEligibilityOverlay.setData(data);
        this.eligibilityBoundaryOverlay.setData(data);
        this.annotations = buildAnnotations(data, this.theory);
        this.annotations.push(...this.autoWaveEvidence, ...this.autoWaveProjections,
            ...combinedAAnnotations(this.displayedCombinedAObservations()));
        if (range && bars.length) {
            const shift = nextAnchorIndex >= 0 ? nextAnchorIndex - anchorIndex : 0;
            const shifted = { from: range.from + shift, to: range.to + shift };
            // 仅行情日期范围真实缩短时回退，回测前缀推进不改变可见范围。
            const unavailableWindow = bars.length < previousBars.length
                && (shifted.to < 0 || shifted.from > bars.length - 1);
            const visible = unavailableWindow
                ? { from: Math.max(0, bars.length - 1 - (range.to - range.from)),
                    to: bars.length - 1 + 6 } : shifted;
            const current = this.chart.timeScale().getVisibleLogicalRange();
            if (current?.from !== visible.from || current?.to !== visible.to)
                this.chart.timeScale().setVisibleLogicalRange(visible);
        }
        this.refreshMarkers();
        this.updateViewport();
    }
    navigationState() {
        return chartNavigationState(this.chart.timeScale().getVisibleLogicalRange(), chartMarketBars(this.data).length);
    }
    updateViewport() {
        const state = this.navigationState();
        this.onViewport(state, chartMarketBars(this.data), this.data);
    }
    pan(direction) {
        const state = this.navigationState();
        if (!state) return;
        this.chart.timeScale().setVisibleLogicalRange(panChartRange(state, direction));
    }
    zoom(direction) {
        const state = this.navigationState();
        if (!state) return;
        this.chart.timeScale().setVisibleLogicalRange(zoomChartRange(state, chartMarketBars(this.data).length, direction));
    }
    seek(position) {
        const state = this.navigationState();
        if (!state || !Number.isFinite(position)) return;
        this.chart.timeScale().setVisibleLogicalRange(seekChartRange(state, position));
    }
    setVolume(show) {
        this.volumeVisible = show;
        if (!this.data) return;
        this.volume.setData(
            show
                ? chartMarketBars(this.data).map((b) => ({
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
        const hit = candidates.find(
            (item) =>
                item.id === id ||
                (evidence &&
                    (item.raw === evidence.raw ||
                        (evidence.raw?.sourceNId && item.raw.sourceNId === evidence.raw.sourceNId))),
        );
        if (hit) return hit;
        if (evidence?.raw?.sourceNId) return null;
        const selected =
            this.selected?.category === "wave-projection"
                ? candidates.find(
                      (item) =>
                          item.id === this.selected.id ||
                          item.raw === this.selected.raw ||
                          (this.selected.raw?.sourceNId && item.raw.sourceNId === this.selected.raw.sourceNId),
                  )
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
                      (item) =>
                          item.id === this.selected.id ||
                          item.raw === this.selected.raw ||
                          (this.selected.raw?.sourceNId && item.raw.sourceNId === this.selected.raw.sourceNId),
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
        const level = this.selected?.levels?.find((level) => level.stage === "one_p");
        const source = level?.n_id;
        if (source) return this.nTargetObservations?.find(({ item }) => item.raw?.n_id === source)?.item.id;
        const date = level?.n_date;
        return this.nTargetObservations?.find(({ item }) => item.time === date)?.item.id;
    }
    /** 悬停只临时展示所在 ABC，保留成交选择与图窗；移出后恢复原目标。 */
    updateWaveProjectionHover(time, id) {
        const availableTime = time && this.data?.bars.some((bar) => bar.time === time) ? time : null;
        const previous = this.hoveredWaveProjection;
        const previousN = this.hoveredNTarget;
        const previousTime = this.hoveredWaveTime;
        this.hoveredWaveTime = availableTime;
        this.drawTertiaryRetracementGuides(this.data?.asof);
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
        if (!this.data?.bars.length) return;
        const range = this.chart.timeScale().getVisibleLogicalRange();
        const bars = chartMarketBars(this.data),
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
        const primaryDeveloping =
            this.showTrend && this.drawingMode === "lecture" && this.polylineEnabled
                ? (this.theory?.reversal_trends?.developing_strokes || [])
                      .filter((stroke) => stroke.points[0].time <= to && stroke.points.at(-1).time >= from)
                      .at(-1) || null
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
        const knownAt = this.theory?.asof || this.data.asof;
        const lifecycle = this.readLifecycleLandmarks(this.data, knownAt);
        const trendLevels = [
            { level: 1, visible: this.showTrend, theory: this.theory?.reversal_trends },
            { level: 2, visible: this.showSecondaryTrend, theory: this.theory?.secondary_trends },
            { level: 3, visible: this.showTertiaryTrend, theory: this.theory?.tertiary_trends },
        ];
        const bullFlipHighs = bearToBullHighAnnotations(
            trendLevels.map(({ level, visible, theory }) => ({ level,
                landmarks: visible ? mergeLifecycleLandmarks(theory?.bear_to_bull_highs,
                    lifecycle[level - 1].highs, lifecycle[level - 1].superseded) : [],
            })),
            from,
            to,
            knownAt,
        );
        const bullAlternationLows = bearBullAlternationLowAnnotations(
            trendLevels.map(({ level, visible, theory }) => ({ level,
                landmarks: visible ? mergeLifecycleLandmarks(theory?.bear_bull_alternation_lows,
                    lifecycle[level - 1].lows) : [],
            })),
            from,
            to,
            knownAt,
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
            {
                from,
                to,
                trend,
                primaryDeveloping,
                secondaryTrend,
                secondaryDeveloping,
                tertiaryTrend,
                tertiaryDeveloping,
            },
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
        clearSeriesCollection(this, "levelLines");
        this.targetGuideOverlay?.setGuides([]);
        this.nStructureOverlay?.setStructure(null);
        this.container.dataset.levelCount = "0";
    }
    clearLastFallHighGuides() {
        clearSeriesCollection(this, "lastFallHighLines");
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
        clearSeriesCollection(this, "bullishTurnGuideLines");
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
        clearSeriesCollection(this, "tertiaryRetracementLines");
        this.tertiaryRetracementKey = "";
        this.container.dataset.tertiaryRetracementGuides = "0";
    }
    drawTertiaryRetracementGuides(to) {
        const secondary = this.showSecondaryTrend && this.polylineEnabled
            ? hoveredTrendThirds(this.theory, this.data?.bars, this.hoveredWaveTime, 2) : [];
        const visible = secondary.length ? secondary :
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
        clearSeriesCollection(this, "combinedAWaveLines");
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
        clearSeriesCollection(this, "combinedARetracementLines");
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
        const asof = this.theory?.asof && this.theory.asof < this.data.asof ? this.theory.asof : this.data.asof;
        const focusedNs = nTargetGroup(this.nTargetObservations || [], focusedN, asof);
        this.nStructureOverlay?.setStructures(focusedNs, asof);
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
        for (const source of focusedNs)
            if (source !== item) levels.push(...nTargetLevels(source, asof).map((level) => ({ item: source, level })));
        const targetGuides = [];
        const seenN = new Set();
        for (const [i, { item, level }] of levels.entries()) {
            if (!Number.isFinite(level.price)) continue;
            if (level.available_at && level.available_at > asof) continue;
            if (isPositiveNTarget(item) && isNTargetStage(level.stage) && !nTargetLevels(item, asof).includes(level))
                continue;
            let nSource = null;
            if (isNTargetStage(level.stage)) {
                const selectedId = item === this.selected && !isPositiveNTarget(item) ? this.selectedNTargetId() : null;
                const selectedSource = this.nTargetObservations?.find(
                    ({ item: source }) => source.id === selectedId,
                )?.item;
                const sourceId = level.n_id || item.raw?.n_id || selectedSource?.raw?.n_id || selectedId || item.id;
                nSource = isPositiveNTarget(item)
                    ? item
                    : this.nTargetObservations?.find(
                          ({ item: source }) => source.raw?.n_id === sourceId || source.id === sourceId,
                      )?.item;
                const identity = `${sourceId}:${level.stage}:${level.price}`;
                if (seenN.has(identity)) continue;
                seenN.add(identity);
            }
            const projectionTargets = item.levels?.some((candidate) => targetStages.has(candidate.stage)) || false;
            const guide =
                isPositiveNTarget(item) && isNTargetStage(level.stage)
                    ? nTargetGuide(item, level, this.data.bars, asof)
                    : targetLevelGuide(item, level, this.data.bars, asof);
            const color = nSource ? nSource.color || "#b69af5" : ["#ebbc70", "#a29ce0", "#5ebeb0"][i % 3];
            const s = this.chart.addSeries(L.LineSeries, {
                color,
                lineStyle: item.kind === "trend" && !targetStages.has(level.stage) ? 0 : 2,
                lineWidth: level.stage === "c_equal" ? 2 : 1,
                title: level.display_name || level.name,
                lastValueVisible: !targetStages.has(level.stage),
                priceLineVisible: !guide && (item.kind === "wave-projection" || targetStages.has(level.stage)),
                crosshairMarkerVisible: false,
                pointMarkersVisible: !guide && item.kind !== "trend",
                pointMarkersRadius: 2,
                // N 的全部参考线只作覆盖绘制，不改变当前 K 线的价格范围。
                ...(!isNTargetStage(level.stage) && item.raw?.event !== "n_completed" &&
                level.stage !== "c_1618" && (item.kind === "wave-projection" || projectionTargets)
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
            if (guide && isNTargetStage(level.stage)) {
                displayGuide = {
                    ...guide,
                    // 横向绑定成 N 日期；悬停只选择来源，不移动它的目标标签。
                    display_at: isPositiveNTarget(item) ? item.time : level.n_date || focusedN?.time || guide.start,
                    fixedAnchor: true,
                    labelPosition: "center",
                    hideWhenPriceOffscreen: true,
                    hideWhenAnchorOffscreen: true,
                    targetState: ["one_p", "two_t"].includes(level.stage)
                        ? guide.end
                            ? "已突破"
                            : "未突破"
                        : guide.targetState,
                };
            }
            if (guide && ["c_0618", "c_equal", "c_1618"].includes(level.stage)) {
                // 显示横向绑定 B 低日期；可知日与首次触及证据仍使用原目标锚点。
                displayGuide = {
                    ...guide,
                    display_at:
                        item.raw?.bTime ||
                        item.levels?.find((candidate) => ["c_0618", "c_equal"].includes(candidate.stage))?.anchor_at ||
                        guide.start,
                    fixedAnchor: true,
                    labelPosition: "center",
                    hideWhenAnchorOffscreen: true,
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
                    ...(isNTargetStage(level.stage)
                        ? { hideWhenPriceOffscreen: true, hideWhenAnchorOffscreen: true } : {}),
                });
            s.setData(points);
            this.levelLines.push(s);
        }
        this.targetGuideOverlay?.setGuides(targetGuides);
        this.container.dataset.levelCount = String(this.levelLines.length);
    }
    clearPolyline() {
        clearSeriesCollection(this, "polylineLines");
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
        clearSeriesCollection(this, "lines");
        this.clearWaveAbPath();
        this.windowAnnotations = [];
        this.clearLastFallHighGuides();
        this.clearBullishTurnGuides();
        this.clearTertiaryRetracementGuides();
        this.autoCombinedAObservations = [];
        this.container.dataset.combinedAWaveCount = "0";
        this.autoAObservations = [];
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
        clearSeriesCollection(this, "waveAbLines");
        this.container.dataset.waveAbcLegs = "0";
    }
    drawWaveAbPath() {
        this.clearWaveAbPath();
        if (!this.options.tertiaryAbc || !this.geometryVisible || !this.data) return;
        const connections = selectWaveConnections([
            ...this.autoWaveProjections.flatMap((item) =>
                waveCProjectionLegs(item.raw).map((leg) => ({
                    ...leg,
                    id: `${item.id}:${leg.title}`,
                    group: `abc:${item.raw.sourceNId || item.raw.trendLevel || 2}:${leg.title}`,
                    ...(item.raw.sourceNId && leg.title === "A 浪" ? { lineStyle: 0 } : {}),
                })),
            ),
            ...aWaveLegs(this.autoAObservations || []),
        ]);
        for (const { title, color, points, lineStyle } of connections) {
            const series = this.chart.addSeries(L.LineSeries, {
                color,
                lineStyle: lineStyle ?? 2,
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
            this.hideTooltip();
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
            this.hideTooltip();
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
            this.hideTooltip();
        }
        this.clearPolyline();
        this.refreshMarkers();
    }
    renderPolyline(from, to) {
        if (!this.polylineEnabled || !this.theory) return;
        const lecture = this.drawingMode === "lecture" && this.theory.lecture_drawing;
        const first = this.showTrend ? this.theory.reversal_trends?.strokes || [] : [];
        const primaryDeveloping = this.showTrend ? this.theory.reversal_trends?.developing_strokes || [] : [];
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
                  ...primaryDeveloping,
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
            : this.theory.polyline_segments || [{ id: "current", points: this.theory.points || [] }];
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
            autoscaleInfoProvider: () => null,
        });
        s.setData(unique);
        this.lines.push(s);
    }
    setTheory(theory, geometry = true, preserveInteraction = false) {
        theory = withEligibilityNTargets(this.data, theory);
        const focusedTime = this.focusedWaveTime;
        const focusedId = this.focusedWaveId;
        const focusedNTime = this.focusedNTime;
        const focusedNId = this.focusedNTarget?.id;
        this.theory = theory;
        if (preserveInteraction) {
            clearSeriesCollection(this, "lines");
            this.polylineKey = "";
            if (!geometry || !theory) this.clearPolyline();
            this.polylineEnabled = Boolean(theory && geometry);
        } else this.clearTheory();
        this.geometryVisible = geometry;
        const projections = theory && this.data ? waveCProjectionsFromStructure(this.data.bars, theory) : [];
        const asof = theory?.asof && theory.asof < this.data?.asof ? theory.asof : this.data?.asof;
        this.buyEligibilityOverlay.setData(this.data, asof);
        this.eligibilityBoundaryOverlay.setData(this.data, asof);
        this.autoWaveProjections = projections.map((projection) =>
            waveCProjectionAnnotation(projection, this.data.bars, asof),
        );
        this.autoWaveProjection = this.autoWaveProjections.at(-1) || null;
        this.autoAObservations = theory && this.data ? aWaveObservations(this.data.bars, { ...theory, asof }) : [];
        this.autoWaveEvidence = [
            ...projections.flatMap((projection) =>
                waveCProjectionEvidenceAnnotations(projection, this.data.bars, asof),
            ),
            ...aWaveAnnotations(this.autoAObservations, this.data?.bars || []),
        ];
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
        const nShapeColors = new Map(
            this.annotations
                .filter((item) => item.raw?.event === "n_completed" && item.side === "up" && item.raw.n_id)
                .map((item) => [item.raw.n_id, item.color]),
        );
        const nConnections = selectWaveConnections(
            theory.shapes.flatMap((shape) =>
                shape.points.slice(1).map((point, index) => ({
                    group: `n:${shape.n_id || `${shape.trend_level || 1}:${shape.direction}`}`,
                    color: shape.direction === "up" ? nShapeColors.get(shape.n_id) || "#60cfc3" : "#cba271",
                    points: [shape.points[index], point],
                })),
            ),
        );
        for (const { points, color } of nConnections) this.addLine(points, color, 0, 2);
        this.polylineEnabled = true;
        this.refreshMarkers();
    }
    focus(time) {
        const bar = chartMarketBars(this.data).find((bar) => bar.time >= time);
        return bar ? this.focusTrade(bar.time) : false;
    }
    focusWaveProjection() {
        const item = this.autoWaveProjection || this.autoWaveProjections.at(-1);
        if (!item || !this.data) return false;
        const first = chartMarketBars(this.data).findIndex((bar) => bar.time === item.raw.originTime);
        const last = chartMarketBars(this.data).findIndex((bar) => bar.time === item.time);
        if (first < 0 || last < first) return false;
        if (!this.focusTrade(item.time)) return false;
        this.refreshMarkers();
        this.selectAnnotation(item.id, false);
        return true;
    }
    focusTrade(time) {
        const index = chartMarketBars(this.data).findIndex((bar) => bar.time === time) ?? -1;
        const timeScale = this.chart.timeScale();
        const range = tradePointRange(index, chartMarketBars(this.data).length, timeScale.getVisibleLogicalRange());
        if (!range) return false;
        timeScale.setVisibleLogicalRange(range);
        this.flashCandle(chartMarketBars(this.data)[index]);
        this.focusCandleTargets(chartMarketBars(this.data)[index].time);
        return true;
    }
    focusRange(from, to) {
        const timeScale = this.chart.timeScale();
        const range = drawdownCandleRange({ from, to }, chartMarketBars(this.data), timeScale.getVisibleLogicalRange());
        if (!range) return false;
        timeScale.setVisibleLogicalRange(range);
        const bars = chartMarketBars(this.data).filter((bar) => bar.time >= from && bar.time <= to);
        this.flashCandle(bars[Math.floor((bars.length - 1) / 2)]);
        return true;
    }
    destroy() {
        if (this.frame) cancelAnimationFrame(this.frame);
        clearTimeout(this.tooltipHideTimer);
        this.focusFlashOverlay.clear();
        this.container.removeEventListener("pointerup", this.onTertiaryPointerUp);
        this.unbindKeyboardNavigation();
        this.tooltipLayer.destroy();
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
