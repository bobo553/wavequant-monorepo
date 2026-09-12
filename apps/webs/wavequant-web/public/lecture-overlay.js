// TradingView series primitive: preserves (date, ordinal), unlike LineSeries.
// Display-only links. Keep source sessions separate for level-2/3 inference.
const pivotPriceFormatter = new Intl.NumberFormat("zh-CN", {
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
    useGrouping: false,
});

/** 一级趋势端点使用无千分位、最多两位小数的紧凑价格文本。 */
export function formatPivotPrice(value) {
    const price = Number(value);
    return Number.isFinite(price) ? pivotPriceFormatter.format(price) : "";
}

/**
 * 为被服务端规则边界拆开的相邻讲义路径补一条纯显示边。
 * 只接受相邻 K 线上几何方向成立的 H/L 端点，避免用前端连线掩盖缺失交易日、
 * 同类端点或错误价格方向；连接不会写回讲义结构，也不会成为上级趋势输入。
 */
export function lectureConnections(strokes) {
    const order = (a, b) => a.index - b.index || (a.ordinal ?? 0) - (b.ordinal ?? 0);
    const paths = strokes
        .filter((stroke) => !stroke.display_only && stroke.points.length)
        .slice()
        .sort((a, b) => order(a.points[0], b.points[0]));
    const links = [];
    for (let index = 1; index < paths.length; index++) {
        const left = paths[index - 1],
            right = paths[index],
            from = left.points.at(-1),
            to = right.points[0];
        if (to.index - from.index !== 1 || !isAlternatingTrendLeg(from, to)) continue;
        links.push({
            id: `lecture-connection-${left.id}-${right.id}`,
            kind: "lecture-connection",
            source_paths: [left.id, right.id],
            display_only: true,
            source_level: 0,
            available_at: from.available_at > to.available_at ? from.available_at : to.available_at,
            connection_rule: "adjacent_opposite_endpoints",
            points: [from, to],
        });
    }
    return links;
}

export function reversalConnections(strokes, baseStrokes = []) {
    return trendConnections(strokes, baseStrokes, 1);
}

export function secondaryConnections(strokes, levelOneStrokes = []) {
    return trendConnections(strokes, levelOneStrokes, 2);
}

/**
 * 将正式趋势分段与纯显示连接拼成连续的“图上路径”，只供视窗摘要和标注使用。
 * 中间桥点保留真实来源日期、价格和确认状态，并显式标记 display_bridge；返回新对象，
 * 不写回服务端理论数据，也不作为二级趋势、策略或回测输入。
 */
export function connectedTrendStrokes(strokes, connections) {
    const order = (a, b) => a.index - b.index || (a.ordinal ?? 0) - (b.ordinal ?? 0),
        pointKey = (point) => `${point.index}:${point.ordinal ?? 0}:${point.kind}:${point.value}`;
    const paths = strokes
        .filter((stroke) => !stroke.display_only && stroke.points.length)
        .slice()
        .sort((a, b) => order(a.points[0], b.points[0]));
    if (!paths.length) return [];
    const links = new Map(connections.map((link) => [`${link.source_paths?.[0]}→${link.source_paths?.[1]}`, link]));
    const result = [];
    let current = {
        id: `display-${paths[0].id}`,
        kind: paths[0].kind,
        display_summary: true,
        source_paths: [paths[0].id],
        points: paths[0].points.map((point) => ({ ...point })),
    };
    const append = (point, displayBridge = false) => {
        const next = {
            ...point,
            ...(displayBridge ? { display_bridge: true, label: point.label || `${point.kind}·桥` } : {}),
        };
        if (pointKey(current.points.at(-1)) !== pointKey(next)) current.points.push(next);
    };
    for (let index = 1; index < paths.length; index++) {
        const previous = paths[index - 1],
            next = paths[index],
            link = links.get(`${previous.id}→${next.id}`);
        if (!link) {
            result.push(current);
            current = {
                id: `display-${next.id}`,
                kind: next.kind,
                display_summary: true,
                source_paths: [next.id],
                points: next.points.map((point) => ({ ...point })),
            };
            continue;
        }
        for (const point of link.points.slice(1, -1)) append(point, true);
        for (const point of next.points) append(point);
        current.id += `+${next.id}`;
        current.source_paths.push(next.id);
    }
    result.push(current);
    return result;
}

/**
 * 趋势腿必须同时满足端点类型交替和价格方向：L→H 必须上涨，H→L 必须下跌。
 * 仅检查 H/L 名称会把“更低的 H”直接接到前一个 L，视觉上形成伪低低线。
 */
function isAlternatingTrendLeg(a, b) {
    return a.kind !== b.kind && (a.kind === "L" ? b.value > a.value : b.value < a.value);
}

/**
 * 前后端点类型虽不同、但直接连接方向错误时，寻找两个按时间有序的来源极值。
 * 例如 L(3.04) 与更低的 H(3.01) 之间必须补成 L→H→L→H；桥接只用于显示，
 * 不进入一级/二级趋势确认，也不会改变末跌高、策略或回测证据。
 */
function alternatingPairBridge(a, b, candidates) {
    const firstKind = b.kind,
        firstSign = firstKind === "H" ? 1 : -1;
    let bestFirst = null,
        bestPair = null,
        bestScore = -Infinity;
    for (const point of candidates) {
        if (point.kind === firstKind && isAlternatingTrendLeg(a, point)) {
            if (!bestFirst || firstSign * (point.value - bestFirst.value) > 0) bestFirst = point;
            continue;
        }
        if (
            point.kind !== a.kind ||
            !bestFirst ||
            !isAlternatingTrendLeg(bestFirst, point) ||
            !isAlternatingTrendLeg(point, b)
        )
            continue;
        const score =
            Math.abs(bestFirst.value - a.value) +
            Math.abs(point.value - bestFirst.value) +
            Math.abs(b.value - point.value);
        if (score > bestScore) {
            bestScore = score;
            bestPair = [bestFirst, point];
        }
    }
    return bestPair;
}

function trendConnections(strokes, sourceStrokes, level) {
    const targetKind = level === 2 ? "secondary" : "reversal";
    const order = (a, b) => a.index - b.index || (a.ordinal ?? 0) - (b.ordinal ?? 0);
    // Preserve the base polyline's intrabar projection, not a new compressed rank.
    const raw = sourceStrokes
        .filter((s) => !s.display_only && (level === 1 || s.kind === "reversal"))
        .flatMap((stroke) => {
            const counts = new Map(),
                ranks = new Map();
            for (const p of stroke.points) counts.set(p.time, (counts.get(p.time) || 0) + 1);
            return stroke.points.map((p) => {
                const rank = ranks.get(p.time) || 0;
                ranks.set(p.time, rank + 1);
                return {
                    ...p,
                    source_path: stroke.id,
                    projection_count: p.projection_count ?? counts.get(p.time),
                    projection_rank: p.projection_rank ?? rank,
                };
            });
        })
        .sort(order);
    const paths = strokes
        .filter((s) => s.kind === targetKind && !s.display_only && s.points.length)
        .slice()
        .sort((a, b) => order(a.points[0], b.points[0]));
    const links = [];
    for (let i = 1; i < paths.length; i++) {
        const left = paths[i - 1],
            right = paths[i],
            a = left.points.at(-1),
            b = right.points[0];
        if (order(a, b) >= 0) continue;
        const known = a.available_at > b.available_at ? a.available_at : b.available_at;
        const candidates = raw.filter(
            (p) =>
                order(a, p) < 0 &&
                order(p, b) < 0 &&
                (level === 1
                    ? ["confirmed", "teaching"].includes(p.state)
                    : !["seed", "developing"].includes(p.state)) &&
                p.available_at <= known,
        );
        let points;
        if (a.kind === b.kind) {
            const kind = a.kind === "L" ? "H" : "L",
                sign = kind === "H" ? 1 : -1;
            const extreme = candidates
                .filter((p) => p.kind === kind && sign * (p.value - a.value) > 0 && sign * (p.value - b.value) > 0)
                .reduce((best, p) => (!best || sign * (p.value - best.value) > 0 ? p : best), null);
            if (!extreme) continue; // No L-L/H-H shortcut, and no invented price/date.
            points = [a, extreme, b];
        } else if (isAlternatingTrendLeg(a, b)) {
            points = [a, b];
        } else {
            const bridge = alternatingPairBridge(a, b, candidates);
            if (!bridge) continue; // 没有真实来源极值时宁可断开，也不画伪交替线。
            points = [a, ...bridge, b];
        }
        links.push({
            id: `${level === 1 ? "connection" : "secondary-connection"}-${left.id}-${right.id}`,
            kind: `${targetKind}-connection`,
            source_paths: [left.id, right.id],
            display_only: true,
            trend_level: level,
            source_level: level - 1,
            available_at: known,
            connection_rule:
                points.length === 3
                    ? level === 1
                        ? "opposite_base_extreme"
                        : "opposite_level1_extreme"
                    : points.length === 4
                      ? level === 1
                          ? "alternating_base_extreme_pair"
                          : "alternating_level1_extreme_pair"
                      : "opposite_endpoints",
            points,
        });
    }
    return links;
}

export function projectStroke(stroke, xForTime, yForPrice, barWidth = 8) {
    const counts = new Map(),
        ranks = new Map();
    for (const point of stroke.points) counts.set(point.time, (counts.get(point.time) || 0) + 1);
    return stroke.points.map((point, index) => {
        const localRank = ranks.get(point.time) || 0;
        ranks.set(point.time, localRank + 1);
        const count = point.projection_count ?? counts.get(point.time),
            rank = point.projection_rank ?? localRank;
        const offset = count > 1 ? (rank - (count - 1) / 2) * Math.min(4, barWidth * 0.3) : 0;
        const x = xForTime(point.time),
            y = yForPrice(point.value);
        return { point, index, x: x === null ? null : x + offset, y };
    });
}
export class LectureOverlay {
    constructor(container) {
        this.container = container;
        this.strokes = [];
        this.projected = [];
        this.views = [this];
        this.highlightTeaching = true;
        this.showReversalPrices = true;
    }
    attached({ chart, series, requestUpdate }) {
        this.chart = chart;
        this.series = series;
        this.requestUpdate = requestUpdate;
    }
    paneViews() {
        return this.views;
    }
    zOrder() {
        return "top";
    }
    renderer() {
        return this;
    }
    setStrokes(strokes) {
        this.strokes = strokes;
        this.updateAllViews();
        this.requestUpdate?.();
    }
    setTeachingHighlight(show) {
        this.highlightTeaching = show;
        this.container.dataset.teachingHighlight = String(show);
        this.requestUpdate?.();
    }
    /** 独立控制一级趋势端点价格，不改变趋势线、端点或点击证据。 */
    setReversalPriceLabelsVisible(show) {
        this.showReversalPrices = show;
        if (!show) this.container.dataset.reversalPriceLabels = "0";
        this.requestUpdate?.();
    }
    updateAllViews() {
        if (!this.chart) return;
        const scale = this.chart.timeScale(),
            width = scale.options().barSpacing;
        this.projected = this.strokes.map((stroke) => ({
            stroke,
            points: projectStroke(
                stroke,
                (t) => scale.timeToCoordinate(t),
                (p) => this.series.priceToCoordinate(p),
                width,
            ),
        }));
        this.container.dataset.lectureVertices = this.strokes.reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.sameBarLegs = this.strokes.reduce(
            (n, s) => n + s.points.slice(1).filter((p, i) => p.time === s.points[i].time).length,
            0,
        );
        this.container.dataset.reversalPoints = this.strokes
            .filter((s) => s.kind === "reversal")
            .reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.reversalConnections = this.strokes.filter(
            (s) => s.kind === "reversal-connection",
        ).length;
        this.container.dataset.lectureConnections = this.strokes.filter((s) => s.kind === "lecture-connection").length;
        this.container.dataset.secondaryPoints = this.strokes
            .filter((s) => s.kind === "secondary")
            .reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.secondaryDevelopingPoints = this.strokes
            .filter((s) => s.kind === "secondary-developing")
            .reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.secondaryConnections = this.strokes.filter(
            (s) => s.kind === "secondary-connection",
        ).length;
        this.container.dataset.tertiaryPoints = this.strokes
            .filter((s) => s.kind === "tertiary")
            .reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.tertiaryDevelopingPoints = this.strokes
            .filter((s) => s.kind === "tertiary-developing")
            .reduce((n, s) => n + s.points.length, 0);
    }
    draw(target) {
        target.useMediaCoordinateSpace(({ context: ctx }) => {
            ctx.save();
            ctx.lineWidth = 2.5;
            ctx.font = "10px sans-serif";
            // Map 同时收集正式一级端点和一级显示桥节点；连接首尾与正式端点重合时只画一次。
            const levelOneLabels = new Map();
            for (const { stroke, points } of this.projected) {
                const connection = stroke.kind === "reversal-connection" || stroke.kind === "secondary-connection";
                const secondaryDeveloping = stroke.kind === "secondary-developing",
                    tertiaryDeveloping = stroke.kind === "tertiary-developing",
                    tertiary = stroke.kind === "tertiary" || tertiaryDeveloping,
                    secondary =
                        stroke.kind === "secondary" || stroke.kind === "secondary-connection" || secondaryDeveloping,
                    reversal = stroke.kind === "reversal" || connection || secondary || tertiary;
                ctx.lineWidth = tertiary ? 3.5 : secondary ? 3 : reversal ? 2 : 2.5;
                for (let i = 1; i < points.length; i++) {
                    const a = points[i - 1],
                        b = points[i];
                    if (a.x === null || b.x === null || a.y === null || b.y === null) continue;
                    const teaching = stroke.kind === "teaching" || b.point.edge_kind === "teaching";
                    ctx.strokeStyle = tertiary
                        ? "#ffad72"
                        : secondary
                          ? "#d6a3ff"
                          : reversal
                            ? "#b7cdf4"
                            : this.highlightTeaching && teaching
                              ? "#50dfd2"
                              : "#ffd36d";
                    // 发展路径会随已确认的下一级结构继续延伸，虚线用于
                    // 避免把内部转折误读成正式二级或三级反转点。
                    ctx.setLineDash(secondaryDeveloping || tertiaryDeveloping ? [7, 4] : reversal ? [] : [5, 3]);
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.stroke();
                }
                ctx.setLineDash([]);
                if (this.showReversalPrices && (stroke.kind === "reversal" || stroke.kind === "reversal-connection")) {
                    for (const point of points) {
                        if (
                            point.x !== null &&
                            point.y !== null &&
                            ["H", "L"].includes(point.point.kind) &&
                            formatPivotPrice(point.point.value)
                        )
                            levelOneLabels.set(
                                `${point.point.index}:${point.point.ordinal ?? 0}:${point.point.kind}:${point.point.value}`,
                                point,
                            );
                    }
                }
            }
            // 价格只属于一级已确认端点；小字号与上下分置减少对 K 线的遮挡。
            const light = typeof document !== "undefined" && document.documentElement.classList.contains("light");
            ctx.font = "500 9px ui-monospace, SFMono-Regular, Consolas, monospace";
            ctx.textAlign = "center";
            ctx.lineJoin = "round";
            ctx.lineWidth = 3;
            ctx.strokeStyle = light ? "rgba(255,255,255,0.92)" : "rgba(8,16,27,0.9)";
            ctx.fillStyle = light ? "#365b89" : "#d3e0f5";
            for (const point of levelOneLabels.values()) {
                const high = point.point.kind === "H",
                    text = formatPivotPrice(point.point.value),
                    y = point.y + (high ? -4 : 4);
                ctx.textBaseline = high ? "bottom" : "top";
                ctx.strokeText?.(text, point.x, y);
                ctx.fillText(text, point.x, y);
            }
            this.container.dataset.reversalPriceLabels = String(levelOneLabels.size);
            ctx.restore();
        });
    }
    hitTest(x, y) {
        // Real vertices take priority over display-only connectors.
        for (const { stroke, points } of [...this.projected].reverse())
            for (const p of points)
                if (
                    !["reversal-connection", "secondary-connection"].includes(stroke.kind) &&
                    p.x !== null &&
                    p.y !== null &&
                    Math.hypot(x - p.x, y - p.y) <= 7
                )
                    return { externalId: `drawing:${stroke.id}:${p.index}`, zOrder: "top", cursorStyle: "pointer" };
        for (const { stroke, points } of [...this.projected].reverse()) {
            if (!["reversal-connection", "secondary-connection"].includes(stroke.kind)) continue;
            for (let i = 1; i < points.length; i++) {
                const a = points[i - 1],
                    b = points[i];
                if ([a.x, a.y, b.x, b.y].some((v) => v === null)) continue;
                const dx = b.x - a.x,
                    dy = b.y - a.y,
                    length = dx * dx + dy * dy;
                const t = length ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / length)) : 0;
                if (Math.hypot(x - a.x - t * dx, y - a.y - t * dy) <= 5)
                    return { externalId: `drawing:${stroke.id}:${i - 1}`, zOrder: "top", cursorStyle: "pointer" };
            }
        }
        return null;
    }
    annotation(id) {
        if (typeof id !== "string" || !id.startsWith("drawing:")) return null;
        const [, strokeId, index] = id.split(":");
        const stroke = this.strokes.find((s) => s.id === strokeId);
        const p = stroke?.points[Number(index)];
        if (!p) return null;
        const teaching = stroke.kind === "teaching" || !!p.teaching_path_id;
        if (stroke.kind === "reversal-connection" || stroke.kind === "secondary-connection") {
            const secondary = stroke.kind === "secondary-connection",
                name = secondary ? "二级" : "一级",
                source = secondary ? "一级趋势线" : "原折线";
            return {
                id,
                time: stroke.available_at,
                sourceTime: p.time,
                kind: "trend",
                category: "rules",
                price: p.value,
                title: `${name}趋势线 · 跨原路径衔接（仅显示）`,
                description:
                    (stroke.points.length === 3
                        ? `同类端点之间，经${source} ${stroke.points[1].time} 的${stroke.points[1].kind === "H" ? "最高" : "最低"}点 ${stroke.points[1].value} 衔接，不直连两个低点或两个高点。`
                        : stroke.points.length === 4
                          ? `前后异类端点的价格方向不成立，经${source} ${stroke.points[1].time} 的 ${stroke.points[1].kind} ${stroke.points[1].value} 与 ${stroke.points[2].time} 的 ${stroke.points[2].kind} ${stroke.points[2].value} 补成严格高低交替，不绘制伪低低或伪高高线。`
                          : `连接前后两段异类${name}端点。`) +
                    `中间${source}路径分段；此线不证明期间方向连续，不新增反转，不参与末跌高、末升低、后续趋势级别或买卖判断。`,
                sourceLabel: "显示衔接 · 非已确认趋势段",
                levels: [],
                raw: {
                    trend_level: secondary ? 2 : 1,
                    source_level: secondary ? 1 : 0,
                    scope: "display_only_connection",
                    stroke,
                },
            };
        }
        if (stroke.kind === "secondary-developing" || stroke.kind === "tertiary-developing") {
            const secondary = stroke.kind === "secondary-developing",
                level = secondary ? 2 : 3,
                sourceLevel = level - 1,
                prefix = secondary ? "Ⅱ·" : "Ⅲ·",
                name = secondary ? "二级" : "三级",
                sourceName = secondary ? "一级" : "二级";
            const start = stroke.points[0],
                endpoint = stroke.points.at(-1),
                rising = stroke.wave_direction === "up",
                nestedCount = stroke.nested_turn_count ?? Math.max(0, stroke.points.length - 2),
                pendingCount = stroke.pending_point_count ?? 0;
            return {
                id,
                time: endpoint.available_at,
                sourceTime: p.time,
                kind: "trend",
                category: "rules",
                price: p.value,
                title: `${prefix}完整发展路径 · 当前${rising ? "上涨" : "下跌"}候选`,
                description: `正式${name}${start.kind === "L" ? "低点" : "高点"} ${start.label}（${start.time}，${start.value}）确认后，Python 继续串联 ${nestedCount} 个已确认${sourceName}内部转折，并保留 ${pendingCount} 个待决尾部${sourceName}点，直到当前 ${endpoint.label}（${endpoint.time}，${endpoint.value}）。所有点均按各自确认日期可见；它们是${name}发展检查路径，不是正式${name}反转点，不参与后续级别、策略或回测。`,
                sourceLabel: `${name}趋势线 · Python 完整发展路径（仅显示）`,
                levels: [],
                raw: { stroke, trend_level: level, source_level: sourceLevel, scope: "display_only_developing_path" },
            };
        }
        if (stroke.kind === "secondary" || stroke.kind === "tertiary") {
            const third = stroke.kind === "tertiary",
                name = third ? "三级" : "二级",
                source = third ? "二级" : "一级",
                prefix = third ? "Ⅲ·" : "Ⅱ·",
                level = third ? 3 : 2;
            const key = p.broken_key,
                proof = p.confirmed_by;
            return {
                id,
                time: p.available_at,
                sourceTime: p.time,
                kind: "trend",
                category: "rules",
                price: p.value,
                title: `${prefix}${p.label} · ${name}${p.kind === "H" ? "高点" : "低点"} · ${p.flip}`,
                description: `基于${source}趋势线：${proof.label}（${proof.value}）${p.flip === "翻空为多" ? "突破末跌高" : "跌破末升低"} ${key.label}（${key.value}），确认整段${p.kind === "H" ? "最高" : "最低"}点。可跨多个${source}拐点，不等待67%交替；不等于买卖信号。`,
                sourceLabel: `${name}趋势线 · 在${source}结构突破确认日可知`,
                levels: p.levels,
                raw: { point: p, trend_level: level, scope: `lecture_level${level}_not_strategy_confirmation` },
            };
        }
        if (stroke.kind === "reversal") {
            const events = p.observations
                .map(
                    (e) =>
                        e.title +
                        (e.formation ? `／${e.formation}` : "") +
                        (e.ratio !== undefined ? `（${(e.ratio * 100).toFixed(2)}%）` : ""),
                )
                .join("；");
            return {
                id,
                time: p.available_at,
                sourceTime: p.time,
                kind: "trend",
                category: "rules",
                price: p.value,
                title: `${p.label} · ${p.reversal}${p.kind === "H" ? "高点" : "低点"}`,
                description: `一级趋势线：原折线的高、低点同时转向才确认，实线跨过中间小拐点。最近两组波段高低点：${p.trend}。${events || "此点无新增转换观察。"} 不等于策略确认或买卖信号。`,
                sourceLabel: "一级趋势线 · 在短期方向转换确认日可知",
                levels: p.levels,
                raw: { point: p, trend_level: 1, scope: "lecture_wave_structure_not_strategy_confirmation" },
            };
        }
        return {
            id,
            time: p.available_at,
            sourceTime: p.time,
            kind: "drawing",
            category: "rules",
            price: p.value,
            title: teaching
                ? `子母路径第 ${p.teaching_ordinal || Number(index) + 1} 点 · ${p.kind}${p.teaching_extended ? " · 极值已延伸" : ""}`
                : `${p.kind === "H" ? "高点" : "低点"} · ${p.state === "developing" ? "发展中" : p.state === "seed" ? "初始化" : "已确认"}`,
            description: p.drawing_rule
                ? `${p.drawing_rule}。只按基础规则连接单个极值，未确认子线日内高低顺序。新版另经收盘确认与 N 几何过滤，不直接凭绘图点买卖。`
                : teaching
                  ? "按子母三点规则接入主路径，后续沿同一端点延伸。属于讲义约定，非实测盘中顺序；旧封存策略不使用该路径，新版另取收盘确认点。"
                  : "使用普通棒高低比较及发展中端点；不把端点当作已确认 N 拐点。",
            sourceLabel: "讲义绘图层 · 与策略拐点独立",
            levels: [{ name: "高低点价格", price: p.value }],
            raw: stroke,
        };
    }
}
