// TradingView series primitive: preserves (date, ordinal), unlike LineSeries.
// Display-only links. Keep source sessions separate for level-2/3 inference.
export function reversalConnections(strokes, baseStrokes = []) {
    return trendConnections(strokes, baseStrokes, 1);
}

export function secondaryConnections(strokes, levelOneStrokes = []) {
    return trendConnections(strokes, levelOneStrokes, 2);
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
        let points = [a, b];
        if (a.kind === b.kind) {
            const kind = a.kind === "L" ? "H" : "L",
                sign = kind === "H" ? 1 : -1;
            const candidates = raw.filter(
                (p) =>
                    order(a, p) < 0 &&
                    order(p, b) < 0 &&
                    p.kind === kind &&
                    (level === 1
                        ? ["confirmed", "teaching"].includes(p.state)
                        : !["seed", "developing"].includes(p.state)) &&
                    p.available_at <= known &&
                    sign * (p.value - a.value) > 0 &&
                    sign * (p.value - b.value) > 0,
            );
            const extreme = candidates.reduce(
                (best, p) => (!best || sign * (p.value - best.value) > 0 ? p : best),
                null,
            );
            if (!extreme) continue; // No L-L/H-H shortcut, and no invented price/date.
            points = [a, extreme, b];
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
        this.container.dataset.secondaryPoints = this.strokes
            .filter((s) => s.kind === "secondary")
            .reduce((n, s) => n + s.points.length, 0);
        this.container.dataset.secondaryConnections = this.strokes.filter(
            (s) => s.kind === "secondary-connection",
        ).length;
        this.container.dataset.tertiaryPoints = this.strokes
            .filter((s) => s.kind === "tertiary")
            .reduce((n, s) => n + s.points.length, 0);
    }
    draw(target) {
        target.useMediaCoordinateSpace(({ context: ctx }) => {
            ctx.save();
            ctx.lineWidth = 2.5;
            ctx.font = "10px sans-serif";
            for (const { stroke, points } of this.projected) {
                const connection = stroke.kind === "reversal-connection" || stroke.kind === "secondary-connection";
                const tertiary = stroke.kind === "tertiary",
                    secondary = stroke.kind === "secondary" || stroke.kind === "secondary-connection",
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
                    ctx.setLineDash(reversal ? [] : [5, 3]);
                    ctx.beginPath();
                    ctx.moveTo(a.x, a.y);
                    ctx.lineTo(b.x, b.y);
                    ctx.stroke();
                }
                ctx.setLineDash([]);
                // No H/L or child-mother ordinal labels on any line layer. Keep all
                // vertices and hit targets intact for the evidence panel.
            }
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
