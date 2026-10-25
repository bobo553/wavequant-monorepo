import { BuyEligibilityOverlay, eligibilityLevels } from "./buy-eligibility-overlay.js";

// 蓝色只使用后端资格的真实确认/结束日期，同日确认和结束同时保留。
export function eligibilityBoundaryRanges(view, asof = view?.asof) {
    const bars = view?.bars;
    if (!bars?.length || !Array.isArray(view.audit) || (view.timeframe || "1d") !== "1d") return [];
    const levels = eligibilityLevels(view);
    const cutoff = asof && asof < bars.at(-1).time ? asof : bars.at(-1).time;
    const dates = new Map(bars.map((bar, index) => [bar.time, index]));
    const windows = new Map();
    for (const event of view.audit) {
        if (!event || !levels.includes(event.trend_level) || (event.symbol && event.symbol !== view.symbol)) continue;
        const opened = event.event === "trading_interval_opened";
        if (!opened && event.event !== "trading_interval_closed") continue;
        const start = opened ? event.bar_index : event.start_index;
        const day = typeof event.timestamp === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(event.timestamp)
            ? event.timestamp.slice(0, 10) : null;
        if (!day || day > cutoff || !Number.isInteger(start) || start < 0 || typeof event.source_path !== "string") continue;
        const key = JSON.stringify([event.trend_level, event.source_path, start]);
        const window = windows.get(key) || {};
        if (opened) window.confirmed = day;
        else if (!window.closed || day < window.closed) window.closed = day;
        windows.set(key, window);
    }
    const boundaries = new Map();
    const add = (day, kind) => {
        const index = dates.get(day);
        if (index === undefined) return;
        const kinds = boundaries.get(index) || new Set();
        kinds.add(kind);
        boundaries.set(index, kinds);
    };
    for (const window of windows.values()) {
        if (!window.confirmed || (window.closed && window.closed < window.confirmed)) continue;
        add(window.confirmed, "交易资格确认日");
        if (window.closed) add(window.closed, "交易资格结束日");
    }
    return [...boundaries].sort(([left], [right]) => left - right).map(([index, kinds]) => ({
        from: index, to: index, start: bars[index].time, end: bars[index].time,
        kinds: ["交易资格确认日", "交易资格结束日"].filter((kind) => kinds.has(kind)),
    }));
}

export class EligibilityBoundaryOverlay extends BuyEligibilityOverlay {
    constructor() {
        super("#3b82f6");
    }

    setData(view, asof = view?.asof) {
        this.level = view?.backtest?.strategy?.buy_interval_level ?? 2;
        this.ranges = eligibilityBoundaryRanges(view, asof);
        this.requestUpdate?.();
    }

    labelAt(index) {
        return this.ranges.find((range) => range.from === index)?.kinds.join("、") || "";
    }
}
