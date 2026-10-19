function eventDay(timestamp) {
    return typeof timestamp === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(timestamp)
        ? timestamp.slice(0, 10)
        : null;
}

function lowerBound(bars, day) {
    let low = 0;
    let high = bars.length;
    while (low < high) {
        const middle = (low + high) >>> 1;
        if (bars[middle].time < day) low = middle + 1;
        else high = middle;
    }
    return low;
}

// 确认后展示从交替低点开始的结构区间；可交易日期仍由后端确认时钟决定。
export function buyEligibilityRanges(view, asof = view?.asof) {
    const bars = view?.bars;
    if (!bars?.length || !Array.isArray(view.audit) || (view.timeframe || "1d") !== "1d") return [];
    const level = view.backtest?.strategy?.buy_interval_level ?? 2;
    const cutoff = asof && asof < bars.at(-1).time ? asof : bars.at(-1).time;
    const windows = new Map();
    for (const row of view.audit) {
        if (!row || row.trend_level !== level || (row.symbol && row.symbol !== view.symbol)) continue;
        const opened = row.event === "trading_interval_opened";
        if (!opened && row.event !== "trading_interval_closed") continue;
        const day = eventDay(row.timestamp);
        const start = opened ? row.bar_index : row.start_index;
        if (!day || day > cutoff || !Number.isInteger(start) || start < 0 || typeof row.source_path !== "string")
            continue;
        const key = JSON.stringify([row.source_path, start]);
        const window = windows.get(key) || {};
        if (opened) {
            const lowDay = eventDay(row.start_low_time);
            window.start = lowDay && lowDay <= day ? lowDay : day;
        } else if (!window.end || day < window.end) {
            window.end = day;
            window.endInclusive = row.display_end_inclusive === true;
        }
        windows.set(key, window);
    }
    const last = lowerBound(bars, cutoff);
    const limit = last < bars.length && bars[last].time === cutoff ? last + 1 : last;
    const ranges = [];
    for (const window of windows.values()) {
        if (!window.start || (window.end && window.end <= window.start)) continue;
        const from = lowerBound(bars, window.start);
        // 跌破日标出结构终点，不表示当日仍可买入。
        const end = window.end ? lowerBound(bars, window.end) : limit;
        const inclusive = window.endInclusive && bars[end]?.time === window.end ? 1 : 0;
        const to = Math.min(limit, end + inclusive) - 1;
        if (from <= to) ranges.push({ from, to });
    }
    ranges.sort((left, right) => left.from - right.from || left.to - right.to);
    const merged = [];
    for (const range of ranges) {
        const previous = merged.at(-1);
        if (previous && range.from <= previous.to + 1) previous.to = Math.max(previous.to, range.to);
        else merged.push({ ...range });
    }
    return merged.map((range) => ({ ...range, start: bars[range.from].time, end: bars[range.to].time }));
}

export class BuyEligibilityOverlay {
    constructor(color) {
        this.color = color;
        this.ranges = [];
        this.views = [this];
    }

    attached({ chart, requestUpdate }) {
        this.chart = chart;
        this.requestUpdate = requestUpdate;
    }

    detached() {
        this.ranges = [];
        this.chart = null;
        this.requestUpdate = null;
    }

    paneViews() {
        return this.views;
    }

    zOrder() {
        return "bottom";
    }

    renderer() {
        return this;
    }

    setData(view, asof) {
        this.ranges = buyEligibilityRanges(view, asof);
        this.level = view?.backtest?.strategy?.buy_interval_level ?? 2;
        this.requestUpdate?.();
    }

    contains(index) {
        return this.ranges.some(({ from, to }) => from <= index && index <= to);
    }

    draw(target) {
        if (!this.chart || !this.ranges.length) return;
        const scale = this.chart.timeScale();
        const halfBar = scale.options().barSpacing / 2;
        target.useMediaCoordinateSpace(({ context, mediaSize }) => {
            context.save();
            context.fillStyle = this.color;
            context.globalAlpha = 0.2;
            for (const range of this.ranges) {
                const start = scale.timeToCoordinate(range.start);
                const end = scale.timeToCoordinate(range.end);
                if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
                const left = Math.max(0, start - halfBar);
                const right = Math.min(mediaSize.width, end + halfBar);
                if (right > left) context.fillRect(left, 0, right - left, mediaSize.height);
            }
            context.restore();
        });
    }
}
