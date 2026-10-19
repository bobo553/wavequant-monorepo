function dayOf(value) {
    return typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value) ? value.slice(0, 10) : null;
}

function pointKey(point) {
    // 旧审计没有高点 ordinal；同路径、同日、同方向、同价只画一个标识。
    return JSON.stringify([point.source_path, point.index, point.kind, point.value]);
}

// 只投影后端已经确认的六态事件；不在浏览器重新判断突破、回撤或交易资格。
export function lifecycleBullLandmarks(view, knownAt = view?.asof) {
    const levels = [1, 2, 3].map((level) => ({ level, highs: [], lows: [], confirmations: [], superseded: new Set() }));
    if (!view?.bars?.length || !Array.isArray(view.audit) || (view.timeframe || "1d") !== "1d") return levels;
    const cutoff = knownAt && knownAt < view.bars.at(-1).time ? knownAt : view.bars.at(-1).time;
    const dates = new Map(view.bars.map((bar, index) => [bar.time, index]));
    const cycles = new Map();
    const active = new Map();
    const events = view.audit.filter((event) => event && [1, 2, 3].includes(event.trend_level)
        && (!event.symbol || event.symbol === view.symbol)
        && ["trend_lifecycle_state", "trend_lifecycle_anchor", "trend_lifecycle_invalidated"].includes(event.event)
        && dayOf(event.timestamp) && dayOf(event.timestamp) <= cutoff
        && Number.isInteger(event.bar_index) && event.bar_index >= 0)
        .sort((left, right) => left.bar_index - right.bar_index);
    for (const event of events) {
        const pathKey = JSON.stringify([event.trend_level, event.source_path]);
        if (typeof event.source_path !== "string") continue;
        if (event.event === "trend_lifecycle_invalidated") {
            // 行情后续破位不抹除历史确认；来源证据被撤销才删除对应标注。
            if (["source_evidence_revised", "source_path_ended"].includes(event.reason)) {
                cycles.delete(active.get(pathKey));
            }
            active.delete(pathKey);
            continue;
        }
        if (event.direction !== "up") {
            active.delete(pathKey);
            continue;
        }
        if (!Number.isInteger(event.cycle_started_index)) continue;
        const available = dayOf(event.timestamp);
        const currentBar = dates.get(available);
        if (currentBar === undefined || (Number.isInteger(event.available_index) && event.available_index > event.bar_index)) continue;
        // 完整结果可能裁掉预热行情；用事件确认日期定位索引偏移，不能直接以图表数组下标取点。
        const offset = currentBar - event.bar_index;
        const point = (index, price, kind, ordinal = 0) => {
            if (!Number.isInteger(index) || index < 0 || index > event.bar_index || !Number.isFinite(price)) return null;
            const time = view.bars[index + offset]?.time;
            return time && time <= available
                ? { index, ordinal: ordinal ?? 0, time, value: price, kind, label: `${kind} ${time}`, available_at: available }
                : null;
        };
        const cycleKey = JSON.stringify([event.trend_level, event.source_path, event.cycle_started_index]);
        const previous = cycles.get(cycleKey);
        const isFlip = ["翻空为多", "短暂跌破后同级再次空翻多", "特殊空翻多同腿高点延伸"].includes(event.source_event);
        const isAlternation = event.state === "空多交替" && event.event === "trend_lifecycle_state";
        // 底部疑虑带有 impulse 也不是已确认空翻多。
        if (!isFlip && !isAlternation) continue;
        const high = point(event.flip_index, event.breakout_level, "H", event.flip_ordinal);
        const origin = point(event.origin_index, event.origin_price, "L", event.origin_ordinal);
        if (!high || !origin || origin.index >= high.index) continue;
        if (Number.isInteger(event.flip_known_index) && event.flip_known_index > event.bar_index) continue;
        const broken = point(event.broken_high_index, event.broken_high_price, "H", event.broken_high_ordinal)
            || previous?.high.broken_key || null;
        const metadata = { source_path: event.source_path, trend_level: event.trend_level,
            lifecycle: true, special_bull_recovery: event.special_bull_recovery === true,
            cycle_started_index: event.cycle_started_index, broken_key: broken };
        const highLandmark = { ...high, ...metadata, id: `${cycleKey}:high`, confirmed_low: origin };
        // 后续交替确认不推迟已有高点的可知日，同腿延伸则替换原高点。
        if (previous && pointKey(previous.high) === pointKey(highLandmark)) {
            highLandmark.available_at = previous.high.available_at;
        }
        if (previous && pointKey(previous.high) !== pointKey(highLandmark)) {
            levels[event.trend_level - 1].superseded.add(pointKey(previous.high));
        }
        let low = previous?.low || null;
        if (isAlternation) {
            const counter = point(event.counter_index, event.counter_price, "L", event.counter_ordinal);
            if (!counter || counter.index <= high.index || event.alternation_index !== event.bar_index) continue;
            low = { ...counter, ...metadata, id: `${cycleKey}:low`, confirmed_flip_high: highLandmark,
                confirmed_bear_low: origin, retracement_origin: origin,
                confirmation_rule: event.confirmation_rule,
                confirmation_evidence: event.confirmation_evidence,
                source_level: event.counter_source_level ?? event.trend_level,
                retracement_ratio: (high.value - counter.value) / (high.value - origin.value) };
        }
        const confirmation = previous?.confirmation || (isFlip ? {
            id: `${cycleKey}:confirmation`, time: available, index: currentBar,
            price: view.bars[currentBar].close, trend_level: event.trend_level,
            source_path: event.source_path, special_bull_recovery: event.special_bull_recovery === true,
        } : null);
        cycles.set(cycleKey, { level: event.trend_level, high: highLandmark, low, confirmation });
        active.set(pathKey, cycleKey);
    }
    for (const cycle of cycles.values()) {
        levels[cycle.level - 1].highs.push(cycle.high);
        if (cycle.low) levels[cycle.level - 1].lows.push(cycle.low);
        if (cycle.confirmation) levels[cycle.level - 1].confirmations.push(cycle.confirmation);
    }
    return levels;
}

export function mergeLifecycleLandmarks(legacy = [], confirmed = [], superseded = new Set()) {
    const replacements = new Set(confirmed.map(pointKey));
    return [...legacy.filter((point) => !replacements.has(pointKey(point)) && !superseded.has(pointKey(point))),
        ...confirmed];
}

// 拖动/缩放不重放审计；新快照或回放截面变化时才更新，单个图表只保留一份投影。
export function createLifecycleLandmarkReader() {
    let cached;
    return (view, knownAt) => {
        const signature = [view?.symbol, view?.timeframe, view?.bars, view?.bars?.length,
            view?.audit, view?.audit?.length, knownAt];
        if (!cached || signature.some((value, index) => value !== cached.signature[index])) {
            cached = { signature, levels: lifecycleBullLandmarks(view, knownAt) };
        }
        return cached.levels;
    };
}
