export function knownWavePoint(point, kind, asof, byTime) {
    const bar = byTime.get(point?.time);
    return (
        (kind === "H" || kind === "L") &&
        point?.kind === kind &&
        typeof point.time === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(point.time) &&
        Number.isFinite(Date.parse(point.time)) &&
        new Date(point.time).toISOString().slice(0, 10) === point.time &&
        typeof point.available_at === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(point.available_at) &&
        Number.isFinite(Date.parse(point.available_at)) &&
        new Date(point.available_at).toISOString().slice(0, 10) === point.available_at &&
        point.time <= point.available_at &&
        point.available_at <= asof &&
        Number.isFinite(point.value) &&
        point.value === bar?.[kind === "H" ? "high" : "low"]
    );
}

export function maximumWaveHigh(bars, from, until) {
    return Math.max(...bars.filter((bar) => bar.time >= from && bar.time <= until).map((bar) => bar.high));
}

/** 同一 A 的 B 可持续刷新；只有突破 A 顶后的已确认 C 高点结束本段测幅。 */
export function confirmedCWaveProjection({ bars, asof, event, origin, a, points, isHigh, failureFrom = event.time }) {
    const byTime = new Map(bars.map((bar) => [bar.time, bar]));
    const breakout = bars.find((bar) => bar.time > a.time && bar.high > a.value);
    const lows = points.filter(
        (point) =>
            knownWavePoint(point, "L", asof, byTime) &&
            point.time > a.time &&
            (!breakout || point.time < breakout.time),
    );
    const b = lows.reduce((bottom, point) => (!bottom || point.value < bottom.value ? point : bottom), null);
    if (!b || b.value >= a.value) return null;
    const knownAt = [event.available_at, origin.available_at, a.available_at, b.available_at]
        .filter(Boolean)
        .sort()
        .at(-1);
    if (knownAt > asof) return null;
    // 中途高点在确认前被更高价取代时，仍属同段 C 的升级。
    const completedC = points.find(
        (point) =>
            knownWavePoint(point, "H", asof, byTime) &&
            isHigh(point) &&
            point.time > b.time &&
            point.value > a.value &&
            point.available_at >= knownAt &&
            maximumWaveHigh(bars, b.time, point.available_at) === point.value,
    );
    const end = completedC?.available_at || asof;
    const failedIndex = bars.findIndex(
        (bar) => bar.time > failureFrom && bar.time <= end && bar.low < origin.value && bar.close < origin.value,
    );
    // 确认前双破 A 起点时不发布原组；确认后失效保留历史及截止证据。
    if (failedIndex >= 0 && bars[failedIndex].time <= knownAt) return null;
    const amplitude = a.value - origin.value;
    const anchorVersions = [];
    for (const low of [...lows].sort(
        (left, right) => left.available_at.localeCompare(right.available_at) || left.time.localeCompare(right.time),
    )) {
        if (anchorVersions.length && low.value >= anchorVersions.at(-1).bLow) continue;
        const versionKnownAt = [event.available_at, origin.available_at, a.available_at, low.available_at]
            .filter(Boolean)
            .sort()
            .at(-1);
        // A 确认前的小 B 已被更低 B 取代时，二者同日可知，只发布当天最后一个锚点。
        if (anchorVersions.at(-1)?.knownAt === versionKnownAt) anchorVersions.pop();
        const previous = anchorVersions.at(-1);
        if (previous) {
            previous.supersededAt = versionKnownAt;
            previous.validUntil = bars.filter((bar) => bar.time < versionKnownAt).at(-1)?.time;
        }
        anchorVersions.push({
            id: `${origin.time}:${a.time}:${low.time}`,
            bTime: low.time,
            bLow: low.value,
            knownAt: versionKnownAt,
            target0618: low.value + 0.618 * amplitude,
            target: low.value + amplitude,
            target1618: low.value + 1.618 * amplitude,
        });
    }
    return {
        nTime: event.time,
        nHigh: byTime.get(event.time).high,
        originTime: origin.time,
        origin: origin.value,
        aTime: a.time,
        aHigh: a.value,
        aKnownAt: a.available_at,
        bTime: b.time,
        bLow: b.value,
        bKnownAt: b.available_at,
        confirmedAt: knownAt,
        bBrokeASqueezeLow:
            Number.isFinite(event.defense) &&
            bars.some((bar) => bar.time > a.time && bar.time <= b.time && bar.low < event.defense),
        bRetracementRatio: (a.value - b.value) / amplitude,
        aDuration: bars.filter((bar) => bar.time >= origin.time && bar.time <= a.time).length - 1,
        bDuration: bars.filter((bar) => bar.time > a.time && bar.time <= b.time).length,
        target0618: b.value + 0.618 * amplitude,
        target: b.value + amplitude,
        target1618: b.value + 1.618 * amplitude,
        anchorVersion: `${origin.time}:${a.time}:${b.time}`,
        anchorVersions,
        aIsValid: failedIndex < 0,
        cEligible: failedIndex < 0,
        ...(completedC && failedIndex < 0
            ? {
                  cTime: completedC.time,
                  cHigh: completedC.value,
                  cKnownAt: completedC.available_at,
                  targetValidUntil: completedC.time,
              }
            : {}),
        ...(failedIndex >= 0
            ? {
                  invalidatedAt: bars[failedIndex].time,
                  invalidationReason: "B_BROKE_A_START",
                  targetValidUntil: bars[failedIndex - 1]?.time,
              }
            : {}),
    };
}
