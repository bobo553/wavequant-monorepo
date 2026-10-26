import { nTargetAt } from "./n-target-focus.js";
import { priceUnit } from "./market-indices.js";

const levelNames = ["折线", "一级趋势", "二级趋势", "三级趋势"];
const targetNames = ["一饱", "二吐", "五顶", "十满"];
const priceText = new Intl.NumberFormat("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 4, useGrouping: false });
function latest(rows, now, clock) {
    let left = 0, right = rows.length;
    while (left < right) { const middle = (left + right) >>> 1; if (clock(rows[middle]) <= now) left = middle + 1; else right = middle; }
    return rows[left - 1] ?? null;
}
/** 预建时间与级别索引；悬停不请求接口、不识别N，也不扫描行情历史。 */
export function createTrendNReader(data) {
    const display = data?.trendState?.n;
    const bars = data?.bars ?? [];
    const indices = new Map(bars.map((bar, index) => [bar.time, index]));
    const byPath = new Map();
    const sharedByShape = new Map(), stageReferences = new Map();
    const observations = [[], [], [], []];
    display?.spans.forEach((spans, level) => {
        for (const span of spans) {
            const key = `${level}/${span.path}`, list = byPath.get(key) ?? []; list.push(span); byPath.set(key, list);
            const record = display.records[span.nIndex];
            if (level !== 1) {
                const shapeKey = `${span.path}/${record.direction}`, references = sharedByShape.get(shapeKey) ?? [];
                references.push({ level, span }); sharedByShape.set(shapeKey, references);
            }
            const end = Math.min(span.endIndex === null ? bars.length - 1 : span.endIndex - 1,
                record.observedThrough ?? -1, record.invalidatedIndex === null ? bars.length - 1 : record.invalidatedIndex - 1);
            if (end >= span.startIndex) observations[level].push({ from: bars[span.startIndex].time, to: bars[end].time,
                item: { id: `trend-n-${level}-${span.path}-${span.startIndex}`, time: bars[span.startIndex].time, path: span.path } });
        }
    });
    for (const rows of observations) rows.sort((left, right) => right.from.localeCompare(left.from));
    for (const rows of sharedByShape.values()) rows.sort((left, right) => {
        const a = display.records[left.span.nIndex], b = display.records[right.span.nIndex];
        return a.points[0].barIndex - b.points[0].barIndex || a.knownIndex - b.knownIndex
            || left.span.nIndex - right.span.nIndex || left.level - right.level || left.span.startIndex - right.span.startIndex;
    });
    const sharedReferences = (stage, from, to) => {
        const key = `${stage.path}/${stage.direction}/${from}/${to}`;
        if (stageReferences.has(key)) return stageReferences.get(key);
        const rows = sharedByShape.get(`${stage.path}/${stage.direction}`) ?? [];
        let left = 0, right = rows.length;
        while (left < right) {
            const middle = (left + right) >>> 1;
            if (display.records[rows[middle].span.nIndex].points[0].barIndex < from) left = middle + 1; else right = middle;
        }
        const selected = [];
        for (let index = left; index < rows.length; index++) {
            const item = rows[index], record = display.records[item.span.nIndex];
            if (record.points[0].barIndex > to) break;
            if (record.points[2].barIndex <= to && item.span.startIndex <= to
                && (record.attackIndex === null || record.attackIndex <= to)) selected.push(item);
        }
        stageReferences.set(key, selected); return selected;
    };
    return {
        /** 沿用原波段悬停选择器；K线区域、日期定位和点线命中读取同一份引用。 */
        contextAt(level, time, asof, direction = null) {
            if (!display || !time || !asof || time > asof) return null;
            const item = nTargetAt(observations[level] ?? [], time);
            return { level, path: item?.path ?? 0, direction, state: null, n: item ? this.read(level, item.path, time, asof, direction) : null };
        },
        available: Boolean(display),
        indexAt: time => indices.get(time),
        /** 一级整段日期范围用于查看本段N；形成前可回看段内已知来源，回放截止仍有效。 */
        inspectStage(stage, time, asof) {
            const day = indices.get(time), cutoff = indices.get(asof), from = indices.get(stage.from), to = indices.get(stage.to);
            if (!display || day === undefined || cutoff === undefined || from === undefined || to === undefined
                || day > cutoff || day < from || day > to) return null;
            const rows = byPath.get(`${stage.level}/${stage.path}`) ?? [];
            const belongs = span => {
                const record = display.records[span.nIndex];
                return record.direction === stage.direction && record.points[0].barIndex >= from && record.points[2].barIndex <= to;
            };
            const current = latest(rows, day, row => row.startIndex);
            if (current && belongs(current)) {
                const active = this.inspect(stage.level, stage.path, time, asof, stage.direction);
                if (active) return active;
            }
            // 只允许形成前查看本段内后续已知N；失效后不能沿用旧N，不能跨段或跨路径借用。
            const upcoming = rows.find(span => span.startIndex > day && span.startIndex <= cutoff && belongs(span));
            if (upcoming) {
                const model = this.inspect(stage.level, stage.path, bars[upcoming.startIndex].time, asof, stage.direction);
                if (model) return { ...model, time };
            }
            if (stage.level !== 1) return null;
            // 算法订阅级别与图形所在一级段不同；只引用后端已发布的同段共享N，不增加本级资格或维护订阅。
            const references = sharedReferences(stage, from, to);
            for (const future of [false, true]) for (const { level, span } of references) {
                if (future !== (span.startIndex > day) || span.startIndex > cutoff) continue;
                const selectedTime = future ? bars[span.startIndex].time : time;
                const model = this.inspect(level, span.path, selectedTime, asof, stage.direction);
                if (model?.nIndex !== span.nIndex) continue;
                return { ...model, time, displayStage: { level: 1, path: stage.path, direction: stage.direction, from: stage.from, to: stage.to } };
            }
            return null;
        },
        /** 回看选中N在本级使用期内已发布的目标；光标日期不充当回放截止日。 */
        inspect(level, path, time, asof, direction = null) {
            const selected = this.read(level, path, time, asof, direction);
            if (!selected) return null;
            const span = latest(byPath.get(`${level}/${path}`) ?? [], selected.day, row => row.startIndex);
            const record = display.records[selected.nIndex];
            const through = Math.min(indices.get(asof), record.observedThrough,
                span.endIndex === null ? bars.length - 1 : span.endIndex - 1,
                record.invalidatedIndex === null ? bars.length - 1 : record.invalidatedIndex - 1);
            const snapshot = this.read(level, path, bars[through].time, asof, direction);
            if (!snapshot || snapshot.nIndex !== selected.nIndex) return selected;
            return { ...snapshot, time, inspected: true, snapshotTime: snapshot.time };
        },
        read(level, path, time, asof, direction = null) {
            const day = indices.get(time), cutoff = indices.get(asof);
            if (!display || day === undefined || cutoff === undefined || day > cutoff) return null;
            const span = latest(byPath.get(`${level}/${path}`) ?? [], day, row => row.startIndex);
            if (!span || span.endIndex !== null && day >= span.endIndex) return null;
            const record = display.records[span.nIndex];
            if (!record || record.knownIndex > day || record.observedThrough === null || record.observedThrough < day
                || record.invalidatedIndex !== null && record.invalidatedIndex <= day) return null;
            if (direction && record.direction !== direction) return null;
            const up = record.direction === "up";
            const shape = record.points.map((point, index) => {
                const bar = bars[point.barIndex], high = (index === 1) === up;
                return { time: bar.time, value: high ? bar.high : bar.low, ordinal: point.ordinal, label: ["N·A", "N·B", "N·C"][index] };
            });
            const attack = record.attackIndex !== null && record.attackIndex <= day ? bars[record.attackIndex] : null;
            if (attack) shape.push({ time: attack.time, value: up ? attack.high : attack.low, ordinal: 0, label: "攻击" });
            const snapshot = latest(record.targets, day, row => row.knownIndex);
            return { level, path, nIndex: span.nIndex, originIndex: span.originIndex, day, time, up, shape, priceUnit: priceUnit(data.symbol),
                attack: attack?.time ?? null, squeeze: record.squeezeIndex !== null && record.squeezeIndex <= day ? bars[record.squeezeIndex].time : null,
                defense: attack ? record.defense : null, snapshotIndex: snapshot?.knownIndex ?? -1,
                targets: targetNames.map((name, index) => ({ name, price: snapshot?.prices[index] ?? null,
                    reached: index < 2 && snapshot?.reachedAt[index] !== null && snapshot?.reachedAt[index] <= day ? bars[snapshot.reachedAt[index]]?.time ?? null : null })),
            };
        },
    };
}
/** 仅命中实际绘制的本级趋势段，排除显示衔接；不改变既有趋势几何。 */
export function trendSegmentAt(groups, x, y) {
    let result = null, distance = 7;
    for (const { stroke, segments } of [...groups].reverse()) {
        const level = stroke.trend_level;
        if (!Number.isInteger(level) || !levelNames[level] || stroke.kind.endsWith("-connection")) continue;
        for (const [a, b] of segments) {
            if (![a.x, a.y, b.x, b.y].every(Number.isFinite) || x < Math.min(a.x, b.x) - 7 || x > Math.max(a.x, b.x) + 7) continue;
            const dx = b.x - a.x, dy = b.y - a.y, length = dx * dx + dy * dy;
            const fraction = length ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / length)) : 0;
            const delta = Math.hypot(x - a.x - fraction * dx, y - a.y - fraction * dy);
            if (delta < distance) { distance = delta; result = trendStageContext(stroke, a, b); }
        }
    }
    return result;
}
function trendStageContext(stroke, a, b) {
    const rising = b.point.value > a.point.value, falling = b.point.value < a.point.value;
    return { level: stroke.trend_level, path: b.point.path ?? 0, endpoint: b.point,
        direction: rising ? "up" : falling ? "down" : null, from: a.point.time, to: b.point.time };
}
/** 读取已绘制线段的上下行方向，仅用于显示归属，不重新识别趋势六态。 */
export function trendStageAt(groups, time, level) {
    for (const { stroke, segments } of [...groups].reverse()) {
        if (stroke.trend_level !== level || stroke.kind.endsWith("-connection")) continue;
        for (let index = segments.length - 1; index >= 0; index--) {
            const [a, b] = segments[index];
            if (a.point.time <= time && time <= b.point.time) return trendStageContext(stroke, a, b);
        }
    }
    return null;
}
/** N与目标使用行情涨跌语义色，不能沿用趋势级别的蓝/紫色。 */
export function trendNColor(up, palette = { up: "#ef7180", down: "#3fba97" }) {
    return up ? palette.up : palette.down;
}

/** 价格与日期均取后端索引的同源行情；缺少远端目标明确显示尚未确认。 */
export function appendTrendNTooltip(host, context) {
    if (!context) return;
    const document = host.ownerDocument;
    const add = (text, bold = false, directional = false) => {
        const row = document.createElement(bold ? "b" : "div"); row.textContent = text;
        if (directional) row.classList.add(context.n.up ? "trend-n-up" : "trend-n-down");
        host.append(row);
    };
    const directionLabel = context.direction === "up" ? "上涨段" : context.direction === "down" ? "下跌段" : "本级 N 参考";
    add(`${levelNames[context.level]} · ${directionLabel}${context.state ? ` · ${context.state}` : ""}`, true);
    const n = context.n;
    if (!n) { add(context.direction === "up" ? "本上涨段此时尚无有效正 N" : context.direction === "down" ? "本下跌段此时尚无有效倒 N" : "本趋势此时尚无有效 N"); return; }
    if (n.displayStage && n.level !== context.level) add(`N 来源：${levelNames[n.level]}；在所属一级趋势段展示。`);
    add(`${n.up ? "正 N · 轧空" : "倒 N · 杀多"}：${n.squeeze ? `${n.squeeze} 已确认` : "等待确认"}`, true, true);
    add(n.shape.slice(0, 3).map(point => `${point.label} ${point.time} ${priceText.format(point.value)}`).join(" · "));
    if (n.defense !== null) add(`${n.up ? "轧空低" : "杀多高"} ${priceText.format(n.defense)} ${n.priceUnit ?? "元"}`, false, true);
    for (const target of n.targets) add(`${target.name}：${target.price === null ? "待确认" : `${priceText.format(target.price)} ${n.priceUnit ?? "元"}`}${target.reached ? ` · ${target.reached} 已达` : ""}`, false, true);
    add(n.inspected ? `N 目标回看截至 ${n.snapshotTime}；非光标当日状态` : context.sourceReference ? `N 来源快照截至 ${n.time}；仅查看已展示的目标` : `数据截至 ${n.time}；仅显示当时已知目标`);
}

/** 复用原目标图层的避让和图外提示；不在显示层计算目标或达标日期。 */
export function trendNTargetGuides(model) {
    if (!model) return [];
    const stages = ["one_p", "two_t", "five_top", "ten_full"];
    const levels = model.targets.flatMap((target, index) => target.price === null ? [] : [{
        stage: stages[index], price: target.price, name: target.name,
        targetState: target.reached ? `${target.reached} 已达` : index < 2 ? "未达" : "已确认目标",
    }]);
    if (model.defense !== null) levels.push({ stage: "n_defense", price: model.defense, name: model.up ? "轧空低" : "杀多高", targetState: "有效防守" });
    const anchor = model.attack ?? model.shape[2].time;
    return levels.map(level => ({ ...level, start: anchor, end: null, display_at: anchor,
        color: model.color ?? trendNColor(model.up), fixedAnchor: true, labelPosition: "center", statusKnown: true }));
}
