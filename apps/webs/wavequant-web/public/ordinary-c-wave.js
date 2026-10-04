function isDate(value) {
    return (
        typeof value === "string" &&
        /^\d{4}-\d{2}-\d{2}$/.test(value) &&
        Number.isFinite(Date.parse(value)) &&
        new Date(value).toISOString().slice(0, 10) === value
    );
}

function knownPoint(point, kind, asof, byTime) {
    const bar = byTime.get(point?.time);
    return (
        point?.kind === kind &&
        isDate(point.time) &&
        isDate(point.available_at) &&
        point.time <= point.available_at &&
        point.available_at <= asof &&
        Number.isFinite(point.value) &&
        point.value === bar?.[kind === "H" ? "high" : "low"]
    );
}

function maximumHigh(bars, from, until) {
    return Math.max(...bars.filter((bar) => bar.time >= from && bar.time <= until).map((bar) => bar.high));
}

/** 普通 A 的结构观察复用正式正 N 与二级确认，不进入交易信号路径。 */
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
        for (const stroke of theory?.secondary_trends?.strokes || []) {
            const points = stroke.points || [];
            for (const a of points) {
                if (
                    !knownPoint(a, "H", asof, byTime) ||
                    a.flip !== "翻多为空" ||
                    a.time <= event.time ||
                    a.value <= oneP ||
                    a.value >= twoT ||
                    maximumHigh(visible, origin.time, a.time) !== a.value
                )
                    continue;
                const afterA = visible.filter((bar) => bar.time > a.time);
                const breakout = afterA.find((bar) => bar.high > a.value);
                const lows = points.filter(
                    (point) =>
                        knownPoint(point, "L", asof, byTime) &&
                        point.time > a.time &&
                        (!breakout || point.time < breakout.time),
                );
                const b = lows.reduce(
                    (bottom, point) => (!bottom || point.value < bottom.value ? point : bottom),
                    null,
                );
                if (!b || b.value >= a.value) continue;
                const knownAt = [event.available_at, a.available_at, b.available_at].sort().at(-1);
                if (knownAt > asof) continue;
                // 中途已确认的高点若在确认前被更高价取代，仍属同段 C 的升级。
                const completedC = points.find(
                    (point) =>
                        knownPoint(point, "H", asof, byTime) &&
                        point.flip === "翻多为空" &&
                        point.time > b.time &&
                        point.value > a.value &&
                        point.available_at >= knownAt &&
                        maximumHigh(visible, b.time, point.available_at) === point.value,
                );
                // 极值日至确认日仍是活动结构；该期间起点双破优先，不能被后来归档洗掉。
                const end = completedC?.available_at || asof;
                const failedIndex = visible.findIndex(
                    (bar) =>
                        bar.time > event.time && bar.time <= end && bar.low < origin.value && bar.close < origin.value,
                );
                // B 确认前已双破 A 起点时，没有原组 C；后续收复不能重建它。
                if (failedIndex >= 0 && visible[failedIndex].time <= knownAt) continue;
                const amplitude = a.value - origin.value;
                const anchorVersions = [];
                for (const low of [...lows].sort(
                    (left, right) =>
                        left.available_at.localeCompare(right.available_at) || left.time.localeCompare(right.time),
                )) {
                    if (anchorVersions.length && low.value >= anchorVersions.at(-1).bLow) continue;
                    const versionKnownAt = [event.available_at, a.available_at, low.available_at].sort().at(-1);
                    const previousVersion = anchorVersions.at(-1);
                    if (previousVersion) {
                        previousVersion.supersededAt = versionKnownAt;
                        previousVersion.validUntil = visible.filter((bar) => bar.time < versionKnownAt).at(-1)?.time;
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
                const observation = {
                    nTime: event.time,
                    nHigh: attack.high,
                    originTime: origin.time,
                    origin: origin.value,
                    oneP,
                    twoT,
                    aTime: a.time,
                    aHigh: a.value,
                    aKnownAt: a.available_at,
                    aAttackClass: "non_strong",
                    bTime: b.time,
                    bLow: b.value,
                    bKnownAt: b.available_at,
                    confirmedAt: knownAt,
                    bBrokeASqueezeLow:
                        Number.isFinite(event.defense) &&
                        visible.some((bar) => bar.time > a.time && bar.time <= b.time && bar.low < event.defense),
                    bRetracementRatio: (a.value - b.value) / amplitude,
                    aDuration: visible.filter((bar) => bar.time >= origin.time && bar.time <= a.time).length - 1,
                    bDuration: visible.filter((bar) => bar.time > a.time && bar.time <= b.time).length,
                    target0618: b.value + 0.618 * amplitude,
                    target: b.value + amplitude,
                    target1618: b.value + 1.618 * amplitude,
                    anchorVersion: `${origin.time}:${a.time}:${b.time}`,
                    anchorVersions,
                    trendLevel: stroke.trend_level || theory.secondary_trends.trend_level || 2,
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
                              invalidatedAt: visible[failedIndex].time,
                              invalidationReason: "B_BROKE_A_START",
                              targetValidUntil: visible[failedIndex - 1]?.time,
                          }
                        : {}),
                };
                const key = `${origin.time}:${a.time}`;
                const previous = observations.get(key);
                if (!previous || observation.bLow < previous.bLow) observations.set(key, observation);
            }
        }
    }
    return [...observations.values()].sort((left, right) => left.aTime.localeCompare(right.aTime));
}

/** 非强攻击 A 的正式 C 保留三档，生效时间与 B 极值时间分别保存。 */
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
