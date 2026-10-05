import { classifyAAttack } from "./a-wave-rules.js";
import {
    confirmedCWaveProjection,
    knownWavePoint as knownPoint,
    maximumWaveHigh as maximumHigh,
} from "./confirmed-c-wave.js";
import { ordinaryLocalWavePoints } from "./ordinary-local-wave.js";

function isDate(value) {
    return (
        typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(value) &&
        Number.isFinite(Date.parse(value)) &&
        new Date(value).toISOString().slice(0, 10) === value
    );
}

function localWaveStrokes(theory, origin, asof, byTime) {
    const strokes = [];
    for (const source of theory?.reversal_trends?.strokes || []) {
        const points = (source.points || []).filter(
            (point) =>
                (point.kind === "H" || point.kind === "L") &&
                point.time >= origin.time &&
                knownPoint(point, point.kind, asof, byTime),
        );
        if (points[0]?.time !== origin.time || points[0]?.kind !== "L" || points[0]?.value !== origin.value) continue;
        strokes.push({
            id: `ordinary-local:${source.id}:${origin.time}`,
            source_path: source.id,
            trend_level: 2,
            projection_source: "n_origin_local_structure",
            points: ordinaryLocalWavePoints(points),
        });
    }
    return strokes;
}

function isWaveHigh(point) {
    return point.flip === "翻多为空" || point.local_wave_turn === "up_to_down";
}

/** 普通 A 复用正式正 N；优先正式二级，缺少时以同路径一级点推导局部观察，不进入交易信号路径。 */
export function ordinaryCWaveProjections(bars, theory) {
    const asof = theory?.asof || bars.at(-1)?.time;
    const visible = bars.filter((bar) => bar.time <= asof);
    const byTime = new Map(visible.map((bar) => [bar.time, bar]));
    const observations = new Map();
    for (const event of theory?.events || []) {
        if (
            event.event !== "n_completed" ||
            event.direction !== "up" ||
            !isDate(event.time) ||
            !isDate(event.available_at) ||
            event.available_at < event.time ||
            event.available_at > asof
        )
            continue;
        const origin = event.shape?.[0];
        const attack = byTime.get(event.time);
        const oneP = event.one_p ?? event.levels?.find((level) => level.name === "1P 投影")?.price;
        const twoT = event.two_t ?? event.levels?.find((level) => level.name === "2T 投影")?.price;
        if (
            !origin ||
            !isDate(origin.time) ||
            !Number.isFinite(origin.value) ||
            origin.time >= event.time ||
            origin.value !== byTime.get(origin.time)?.low ||
            !attack ||
            !Number.isFinite(attack.high) ||
            !Number.isFinite(oneP) ||
            !Number.isFinite(twoT) ||
            oneP <= origin.value ||
            twoT <= oneP
        )
            continue;
        const strokes = [
            ...(theory?.secondary_trends?.strokes || []),
            ...localWaveStrokes(theory, origin, asof, byTime),
        ];
        for (const stroke of strokes) {
            const points = stroke.points || [];
            for (const a of points) {
                if (
                    !knownPoint(a, "H", asof, byTime) ||
                    !isWaveHigh(a) ||
                    a.time <= event.time ||
                    classifyAAttack(a.value, oneP, twoT) !== "ordinary" ||
                    maximumHigh(visible, origin.time, a.time) !== a.value
                )
                    continue;
                const projection = confirmedCWaveProjection({
                    bars: visible,
                    asof,
                    event,
                    origin,
                    a,
                    points,
                    isHigh: isWaveHigh,
                });
                if (!projection) continue;
                const observation = {
                    ...projection,
                    oneP,
                    twoT,
                    aAttackClass: "non_strong",
                    trendLevel: stroke.trend_level || theory.secondary_trends?.trend_level || 2,
                    ...(stroke.projection_source
                        ? {
                              projectionSource: stroke.projection_source,
                              sourceTrendLevel: 1,
                              formalTrend: false,
                              sourcePath: stroke.source_path,
                          }
                        : {}),
                };
                const key = `${origin.time}:${a.time}`;
                const previous = observations.get(key);
                if (previous && !previous.projectionSource && observation.projectionSource) continue;
                if (
                    !previous ||
                    (!observation.projectionSource && previous.projectionSource) ||
                    observation.bLow < previous.bLow
                )
                    observations.set(key, observation);
            }
        }
    }
    return [...observations.values()].sort((left, right) => left.aTime.localeCompare(right.aTime));
}

/** 非强攻击 A 的 C 观察保留三档，生效时间与 B 极值时间分别保存。 */
export function ordinaryCWaveLevels(projection, asof) {
    const knownAt = projection.confirmedAt || projection.bKnownAt || projection.bTime;
    if ((asof && knownAt > asof) || (projection.invalidatedAt && (!asof || projection.invalidatedAt <= asof)))
        return [];
    const completed = projection.cTime && projection.cKnownAt && (!asof || projection.cKnownAt <= asof);
    return [
        ["C 浪目标 0.618×A", projection.target0618, "c_0618"],
        ["C 浪目标 1×A（等浪）", projection.target, "c_equal"],
        ["C 浪目标 1.618×A", projection.target1618, "c_1618"],
    ].map(([name, price, stage]) => ({
        name,
        price,
        stage,
        available_at: knownAt,
        anchor_at: projection.bTime,
        ...(completed
            ? { c_ended_at: projection.cTime, c_end_known_at: projection.cKnownAt, valid_until: projection.cTime }
            : {}),
    }));
}
