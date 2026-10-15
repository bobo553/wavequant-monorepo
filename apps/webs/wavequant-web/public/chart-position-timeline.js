import { buyEligibilityRanges } from "./buy-eligibility-overlay.js";
import { chartMarketBars } from "./chart-market-bars.js";

const snapshots = new WeakMap();

// 使用完整行情的交易日格子，缩放和平移不会改变回测进度或资格位置。
export function chartPositionTimeline(view) {
    if (view && snapshots.has(view)) return snapshots.get(view);
    const bars = chartMarketBars(view);
    const running = Boolean(view?.preview_job);
    const total = running ? Math.max(bars.length, view.total || 0) : bars.length;
    const available = Boolean(view?.backtest) && view.result_scope !== "stock-pending"
        && view.backtest.status !== "data_unavailable";
    const known = available ? bars.filter((bar) => bar.time <= view.asof).length : 0;
    const processed = running ? Math.min(known, Math.max(0, view.processed || 0)) : known;
    const progress = total ? processed / total * 100 : 0;
    const indices = new Map(bars.map((bar, index) => [bar.time, index]));
    const ranges = available && processed > 0 ? buyEligibilityRanges(view).flatMap((range) => {
        const first = indices.get(range.start);
        const last = indices.get(range.end);
        if (first === undefined || last === undefined || first >= processed) return [];
        const end = Math.min(last + 1, processed);
        return [{ start: range.start, end: bars[end - 1].time,
            left: first / total * 100, width: (end - first) / total * 100 }];
    }) : [];
    const result = { processed, total, progress, ranges,
        label: available ? `回测 ${processed}/${total} · ${progress.toFixed(1)}%` : "等待回测",
    };
    if (view) snapshots.set(view, result);
    return result;
}

// 原导航依然按图窗起点移动；滑块在全历史上标记图窗中心。
export function chartPositionValue(viewport, barCount) {
    return viewport ? Math.max(0, Math.min(barCount, (viewport.from + viewport.to) / 2 + 0.5)) : 0;
}

export function chartPositionStart(value, viewport) {
    return viewport ? Math.max(0, Math.min(viewport.maxStart, value - 0.5 - viewport.span / 2)) : 0;
}

export function currentEligibilityRange(view, position) {
    if (!Number.isFinite(position) || position < 0) return null;
    const day = chartMarketBars(view)[Math.floor(position)]?.time;
    if (!day) return null;
    const range = chartPositionTimeline(view).ranges.find((row) => row.start <= day && day <= row.end);
    return range ? { ...range, day } : null;
}

export function renderChartPositionTimeline(host, view, viewport = null) {
    const timeline = chartPositionTimeline(view);
    host.style.setProperty("--chart-backtest-progress", `${timeline.progress}%`);
    const intervals = host.querySelector("[data-eligibility-segments]");
    const previous = host.dataset.timelineSignature;
    const signature = JSON.stringify(timeline);
    if (previous !== signature) {
        host.dataset.timelineSignature = signature;
        intervals.replaceChildren(...timeline.ranges.map((range) => {
            const segment = document.createElement("span");
            segment.style.left = `${range.left}%`;
            segment.style.width = `${range.width}%`;
            return segment;
        }));
    }
    host.querySelector("[data-backtest-progress-label]").textContent = timeline.label;
    host.querySelector("[data-eligibility-count]").textContent = `资格区间 ${timeline.ranges.length} 段`;
    const copyContext = JSON.stringify([view?.symbol, view?.preview_job, view?.backtest?.strategy?.buy_interval_level ?? 2]);
    const current = viewport ? currentEligibilityRange(view, chartPositionValue(viewport, chartMarketBars(view).length)) : null;
    for (const copy of host.ownerDocument.querySelectorAll("[data-copy-eligibility]")) {
        const currentOnly = copy.dataset.copyEligibility === "current";
        const context = currentOnly ? JSON.stringify([copyContext, current?.start, current?.end, current?.day]) : copyContext;
        if (copy.dataset.context !== context) {
            copy.dataset.context = context;
            copy.textContent = copy.dataset.defaultLabel || (copy.id === "copy-eligibility-ranges-summary" ? "复制资格区间" : "复制");
            host.ownerDocument.getElementById(copy.dataset.feedbackId).textContent = "";
        }
        if (currentOnly) {
            copy.hidden = !current;
            copy.closest("[data-current-eligibility-action]").hidden = !current;
            copy.title = current ? `${current.day} 所在资格区间：${current.start} 至 ${current.end}` : "当前位置没有资格区间";
        }
        copy.disabled = (currentOnly ? !current : !timeline.ranges.length) || copy.dataset.busy === "true";
    }
    host.title = `${timeline.label}；红色与图表资格背景一致，确认后显示，未结束区间只画到已回测日。`;
    return timeline;
}
