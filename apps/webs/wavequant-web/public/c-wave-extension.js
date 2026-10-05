import { isAOriginBroken } from "./a-wave-rules.js";

/** 原波段达到等浪后才显示延伸；确认当天不能借用先于确认的上影。 */
export function cWaveExtensionLevel(projection, bars, asof) {
    const amplitude = projection.aHigh - projection.origin;
    const knownAt = [projection.bTime, projection.aKnownAt, projection.bKnownAt, projection.confirmedAt]
        .filter((time) => typeof time === "string")
        .sort()
        .at(-1);
    const requestedEnd = asof || bars.at(-1)?.time;
    let validUntil = projection.targetValidUntil;
    const end = validUntil && validUntil < requestedEnd ? validUntil : requestedEnd;
    if (
        !knownAt ||
        !end ||
        knownAt > end ||
        !Number.isFinite(amplitude) ||
        amplitude <= 0 ||
        !Number.isFinite(projection.bLow) ||
        !Number.isFinite(projection.target) ||
        Math.abs(projection.target - (projection.bLow + amplitude)) > 1e-8 ||
        !bars.some((bar) => bar.time === projection.bTime && bar.time <= end)
    )
        return null;

    let reachedAt = null;
    for (let index = 0; index < bars.length; index++) {
        const bar = bars[index];
        if (bar.time < knownAt || bar.time > end) continue;
        // 同棒既达标又最低价破起点时无法确认先后，先结束旧目标观察。
        if (isAOriginBroken(bar, projection.origin)) {
            validUntil = bars[index - 1]?.time;
            break;
        }
        const price = bar.time === knownAt ? bar.close : bar.high;
        const rounding = Number.EPSILON * Math.max(1, Math.abs(price), Math.abs(projection.target)) * 4;
        if (!reachedAt && Number.isFinite(price) && price >= projection.target - rounding) reachedAt = bar.time;
    }
    if (!reachedAt) return null;
    return {
        name: "C 浪目标 1.618×A",
        price: projection.bLow + 1.618 * amplitude,
        stage: "c_1618",
        available_at: reachedAt,
        anchor_at: reachedAt,
        ...(validUntil ? { valid_until: validUntil } : {}),
    };
}
