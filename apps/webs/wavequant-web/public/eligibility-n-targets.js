const STAGES = ["one_p", "two_t", "five_top", "ten_full"];
const day = (value) => typeof value === "string" && /^\d{4}-\d{2}-\d{2}(?:T|$)/.test(value) ? value.slice(0, 10) : null;

export function isEligibilityNTargetSource(event) {
    return event?.event === "n_completed" && event.direction === "up" && event.display_target_eligible === true &&
        event.target_eligible === true && isSelectedStructuralNSource(event);
}

/** 交易区间只约束展示时间，不能替代 Core 的一级趋势底部 N 测量资格。 */
export function withEligibilityNTargets(view, theory) {
    if (!theory || !view?.bars?.length || !Array.isArray(view.audit) || (view.timeframe || "1d") !== "1d") return theory;
    // 唯一来源及目标已由 Core 完整发布，审计副本不能恢复被淘汰的 N 或另算目标。
    if (theory.structural_n_policy === STRUCTURAL_N_POLICY) return theory;
    const bars = view.bars;
    const asof = [view.asof, theory.asof, bars.at(-1).time].filter(Boolean).sort()[0];
    const dates = new Map(bars.map((bar, index) => [bar.time, index]));
    const rows = view.audit.filter((row) => row && (!row.symbol || row.symbol === view.symbol));
    const level = view.backtest?.strategy?.buy_interval_level ?? 2;
    const windows = new Map();
    for (const row of rows) {
        const opened = row.event === "trading_interval_opened";
        const date = day(row.timestamp);
        const start = opened ? row.bar_index : row.start_index;
        if ((!opened && row.event !== "trading_interval_closed") || row.trend_level !== level ||
            !date || date > asof || !Number.isInteger(start) || typeof row.source_path !== "string") continue;
        const key = JSON.stringify([row.source_path, start]);
        const window = windows.get(key) || {};
        if (opened) Object.assign(window, { confirmed: date, low: day(row.start_low_time) || date });
        else if (!window.closed || date < window.closed) window.closed = date;
        windows.set(key, window);
    }
    const intervals = [...windows.values()].filter((window) => window.confirmed && window.low <= window.confirmed);
    if (!intervals.length) return theory;
    const events = [...(theory.events || [])];
    const positions = new Map();
    events.forEach((event, index) => { if (event.event === "n_completed" && event.n_id) positions.set(event.n_id, index); });
    const invalidations = new Map();
    const extensions = new Map();
    for (const row of rows) {
        const date = day(row.timestamp);
        const local = dates.get(date);
        if (!date || date > asof || local === undefined || !Number.isInteger(row.bar_index)) continue;
        const known = bars[local + Math.max(0, (row.known_at ?? row.bar_index) - row.bar_index)]?.time;
        if (!known || known > asof) continue;
        if (row.event === "n_invalidated" && row.n_id) invalidations.set(row.n_id, date);
        if (!row.event?.startsWith("wave_projection_")) continue;
        const stages = extensions.get(row.attack) || new Map();
        const reached = stages.get(row.reached_stage);
        if (reached) reached.status = "已满足";
        if (["five_top", "ten_full"].includes(row.target_stage)) {
            const previous = stages.get(row.target_stage);
            if (Number.isFinite(row.target)) stages.set(row.target_stage, {
                stage: row.target_stage, price: row.target, status: "推演中",
                name: `${row.target_stage === "five_top" ? "五顶" : "十满"}（${row.state === "stacking" ? "叠箱" : "堆箱"}）`,
                available_at: previous?.price === row.target ? previous.available_at : known,
            });
            else if (previous && ["pullback", "invalidated"].includes(row.state))
                previous.status = row.state === "pullback" ? "回调暂停" : "已失效";
        }
        extensions.set(row.attack, stages);
    }
    for (const row of rows) {
        if (row.event !== "n_completed" || row.direction !== "up" || !Number.isInteger(row.bar_index)) continue;
        const date = day(row.timestamp);
        const local = dates.get(date);
        if (local === undefined) continue;
        const offset = row.bar_index - local;
        const at = (index) => Number.isInteger(index) ? bars[index - offset] : null;
        const known = at(Math.max(row.bar_index, row.known_at ?? row.bar_index))?.time;
        if (!known || known > asof) continue;
        const vertices = [row.origin, row.neckline, row.pullback, row.bar_index].map(at);
        if (vertices.some((bar) => !bar)) continue;
        const [origin, neckline, pullback, attack] = vertices;
        const window = intervals.find((candidate) => origin.time >= candidate.low &&
            (!candidate.closed || (known < candidate.closed && date < candidate.closed)));
        if (!window) continue;
        const available = known > window.confirmed ? known : window.confirmed;
        const id = row.n_id || `up:${row.origin}:${row.neckline}:${row.pullback}:${row.bar_index}`;
        const position = positions.get(id);
        const original = position === undefined ? null : events[position];
        if (!isSelectedStructuralNSource({ ...row, ...original }, theory)) continue;
        // 缺失资格也不能由前端猜测；完整理论中的明确拒绝优先于审计副本。
        if ((original?.target_eligible ?? row.target_eligible) !== true) continue;
        const shape = original?.shape || vertices.map((bar, index) => ({ time: bar.time, value: index % 2 ? bar.high : bar.low }));
        const anchor = Number.isFinite(row.box_anchor) ? row.box_anchor : Math.max(attack.high, at(row.bar_index - 1)?.close ?? attack.high);
        const a = shape[0]?.value;
        if (!Number.isFinite(a) || !Number.isFinite(anchor) || anchor <= a) continue;
        const oneP = 2 * anchor - a;
        const twoT = 3 * anchor - 2 * a;
        const five = twoT + 3 * (twoT - oneP);
        const prices = [oneP, twoT, five, 2 * five - a];
        const invalidated = original?.n_invalidated_at || invalidations.get(id);
        const lastBefore = (date) => bars.findLast((bar) => bar.time < date)?.time;
        const validUntil = [window.closed && lastBefore(window.closed), invalidated && lastBefore(invalidated)]
            .filter(Boolean).sort()[0];
        if (validUntil && validUntil < available) continue;
        const levels = [...(original?.levels || []).filter((entry) => !STAGES.includes(entry.stage))];
        for (const [index, stage] of STAGES.entries()) {
            const published = original?.levels?.find((entry) => entry.stage === stage && !entry.estimated &&
                Number.isFinite(entry.price) && (entry.available_at || known) <= asof) ||
                (row.target_primary !== false ? extensions.get(row.bar_index)?.get(stage) : null);
            const target = published || {
                stage, price: prices[index], name: ["一饱（正 N）", "二吐（正 N）", "五顶（预估叠箱）", "十满（预估叠箱）"][index],
                available_at: available, anchor_at: pullback.time,
                ...(index >= 2 ? { estimated: true, display_only: true } : {}),
            };
            const until = [target.valid_until, validUntil].filter(Boolean).sort()[0];
            levels.push({ ...target, available_at: target.available_at > available ? target.available_at : available,
                anchor_at: target.anchor_at || target.available_at || available,
                ...(until ? { valid_until: until } : {}) });
        }
        const event = { ...row, ...original, id: original?.id || `n:${id}`, n_id: id,
            time: date, available_at: available, n_confirmed_at: known,
            price: bars[dates.get(available)]?.close ?? attack.close,
            shape, levels, display_target_eligible: true, display_target_level: level,
            display_target_confirmed_at: window.confirmed,
            ...(invalidated ? { n_invalidated_at: invalidated } : {}),
        };
        if (position === undefined) { positions.set(id, events.length); events.push(event); }
        else events[position] = event;
    }
    return { ...theory, events };
}
import { isSelectedStructuralNSource, STRUCTURAL_N_POLICY } from "./bottom-n-targets.js";
