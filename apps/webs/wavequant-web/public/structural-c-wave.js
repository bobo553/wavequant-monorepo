import { classifyAAttack } from "./a-wave-rules.js";
import { confirmedCWaveProjection, knownWavePoint, maximumWaveHigh } from "./confirmed-c-wave.js";

/** 已确认父 A 的起点独立于内部正 N；一级小回调不会提前截断整段 B。 */
export function structuralCWaveProjections(bars, theory) {
    const asof = theory?.asof || bars.at(-1)?.time;
    const visible = bars.filter((bar) => bar.time <= asof);
    const byTime = new Map(visible.map((bar) => [bar.time, bar]));
    const observations = new Map();
    const events = [...(theory?.events || [])]
        .filter((event) => event.event === "n_completed" && event.direction === "up")
        .sort((left, right) => (left.time || "").localeCompare(right.time || ""));
    for (const parent of theory?.secondary_trends?.strokes || []) {
        for (const origin of parent.points || []) {
            if (
                origin.flip !== "翻空为多" ||
                !knownWavePoint(origin, "L", asof, byTime) ||
                !knownWavePoint(origin.confirmed_by, "H", asof, byTime)
            )
                continue;
            const a = parent.points.find(
                (point) =>
                    point.flip === "翻多为空" &&
                    knownWavePoint(point, "H", asof, byTime) &&
                    knownWavePoint(point.confirmed_by, "L", asof, byTime) &&
                    point.time === origin.confirmed_by?.time &&
                    point.value === origin.confirmed_by?.value &&
                    point.preceding_turn?.time === origin.time &&
                    point.preceding_turn?.value === origin.value,
            );
            if (!a || a.time <= origin.time || maximumWaveHigh(visible, origin.time, a.time) !== a.value) continue;
            const event = events.find((candidate) => {
                const nOrigin = candidate.shape?.[0];
                const oneP = candidate.one_p ?? candidate.levels?.find((level) => level.stage === "one_p")?.price;
                const twoT = candidate.two_t ?? candidate.levels?.find((level) => level.stage === "two_t")?.price;
                return (
                    knownWavePoint(
                        { ...nOrigin, kind: "L", available_at: candidate.available_at },
                        "L",
                        asof,
                        byTime,
                    ) &&
                    nOrigin.time >= origin.time &&
                    nOrigin.time < candidate.time &&
                    candidate.time < a.time &&
                    candidate.time <= candidate.available_at &&
                    candidate.available_at <= a.time &&
                    Number.isFinite(byTime.get(candidate.time)?.high) &&
                    Number.isFinite(oneP) &&
                    Number.isFinite(twoT) &&
                    oneP > nOrigin.value &&
                    twoT > oneP &&
                    classifyAAttack(a.value, oneP, twoT) !== null
                );
            });
            if (!event) continue;
            const oneP = event.one_p ?? event.levels.find((level) => level.stage === "one_p").price;
            const twoT = event.two_t ?? event.levels.find((level) => level.stage === "two_t").price;
            for (const source of theory?.reversal_trends?.strokes || []) {
                const points = (source.points || []).filter(
                    (point) => point.time >= origin.time && knownWavePoint(point, point.kind, asof, byTime),
                );
                if (
                    !points.some(
                        (point) => point.kind === "L" && point.time === origin.time && point.value === origin.value,
                    ) ||
                    !points.some((point) => point.kind === "H" && point.time === a.time && point.value === a.value)
                )
                    continue;
                const projection = confirmedCWaveProjection({
                    bars: visible,
                    asof,
                    event,
                    origin,
                    a,
                    points,
                    isHigh: () => true,
                    failureFrom: origin.time,
                });
                if (!projection) continue;
                const key = `${origin.time}:${a.time}`;
                if (observations.has(key)) continue;
                observations.set(key, {
                    ...projection,
                    oneP,
                    twoT,
                    nOriginTime: event.shape[0].time,
                    nOrigin: event.shape[0].value,
                    aAttackClass: classifyAAttack(projection.aHigh, oneP, twoT) === "strong" ? "strong" : "non_strong",
                    projectionSource: "confirmed_parent_wave",
                    trendLevel: parent.trend_level || 2,
                    sourceTrendLevel: 1,
                    formalTrend: false,
                    sourcePath: source.id,
                });
            }
        }
    }
    return [...observations.values()].sort((left, right) => left.aTime.localeCompare(right.aTime));
}
