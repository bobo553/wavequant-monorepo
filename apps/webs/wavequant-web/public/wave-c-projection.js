import { cWaveExtensionLevel } from "./c-wave-extension.js";
import { num } from "./labels.js";
import { ordinaryCWaveLevels, ordinaryCWaveProjections } from "./ordinary-c-wave.js";
import { targetLevelGuide } from "./target-level-guides.js";

// A selected candle fixes A. Only later, already visible candles may supply B.
export function waveCProjection(bars, events, selectedTime) {
    const highIndex = bars.findIndex((bar) => bar.time === selectedTime);
    if (highIndex < 0 || highIndex >= bars.length - 1) return null;
    const highBar = bars[highIndex];
    if (!Number.isFinite(highBar.high)) return null;

    for (const event of [...(events || [])].reverse()) {
        if (event.event !== "n_completed" || event.direction !== "up") continue;
        const attackIndex = bars.findIndex((bar) => bar.time === event.time);
        const originIndex = bars.findIndex((bar) => bar.time === event.shape?.[0]?.time);
        const origin = event.shape?.[0]?.value;
        const oneP = event.levels?.find((level) => level.name === "1P 投影")?.price;
        if (
            attackIndex < 0 ||
            originIndex < 0 ||
            originIndex >= attackIndex ||
            attackIndex > highIndex ||
            event.available_at > selectedTime ||
            !Number.isFinite(origin) ||
            !Number.isFinite(oneP) ||
            (highIndex === attackIndex ? highBar.close <= oneP : highBar.high <= oneP) ||
            !Number.isFinite(event.defense) ||
            bars.slice(attackIndex + 1, highIndex + 1).some((bar) => bar.low < event.defense) ||
            bars.slice(attackIndex, highIndex).some((bar) => bar.high > highBar.high)
        )
            continue;

        let bottom = null;
        let corrected = false;
        for (let index = highIndex + 1; index < bars.length; index++) {
            const bar = bars[index];
            // B can briefly pierce the earlier squeeze defense. The A wave's
            // positive-N origin fails only when both low and close lose it.
            if (bar.low < origin && bar.close < origin) return null;
            // Once A is exceeded, later lows belong to a new phase.
            if (bar.high > highBar.high) break;
            if (bar.close < bars[index - 1].close) corrected = true;
            if (!bottom || bar.low < bottom.low) bottom = bar;
        }
        if (!corrected || !bottom || bottom.low >= highBar.high) return null;
        const amplitude = highBar.high - origin;
        return {
            nTime: event.time,
            origin,
            oneP,
            aTime: selectedTime,
            aHigh: highBar.high,
            bTime: bottom.time,
            bLow: bottom.low,
            target0618: bottom.low + 0.618 * amplitude,
            target: bottom.low + amplitude,
        };
    }
    return null;
}

/** C targets are conditional observations known from the B candle onward. */
export function waveCProjectionLevels(projection, bars = [], asof) {
    if (projection.aAttackClass === "non_strong") return ordinaryCWaveLevels(projection, asof);
    const levels = [
        {
            name: "C 浪目标 0.618×A",
            price: projection.target0618,
            stage: "c_0618",
            available_at: projection.bTime,
            anchor_at: projection.bTime,
            ...(projection.targetValidUntil ? { valid_until: projection.targetValidUntil } : {}),
        },
        {
            name: "C 浪目标 1×A",
            price: projection.target,
            stage: "c_equal",
            available_at: projection.bTime,
            anchor_at: projection.bTime,
            ...(projection.targetValidUntil ? { valid_until: projection.targetValidUntil } : {}),
        },
    ];
    const extension = cWaveExtensionLevel(projection, bars, asof);
    if (extension) levels.push(extension);
    return levels;
}

function knownPoint(point, kind, asof) {
    return (
        point?.kind === kind &&
        Number.isFinite(point.value) &&
        typeof point.time === "string" &&
        typeof point.available_at === "string" &&
        point.time <= point.available_at &&
        point.available_at <= asof
    );
}

/** Active landmarks omit older flips; their confirmed evidence still belongs on a historical chart. */
function historicalHighs(theory, asof) {
    const candidates = new Map();
    const identity = (high) => `${high.time}:${high.value}:${high.confirmed_low?.time}:${high.confirmed_low?.value}`;
    for (const stroke of theory?.secondary_trends?.strokes || []) {
        for (const low of stroke.points || []) {
            const high = low.confirmed_by;
            const key = low.broken_key;
            if (
                low.flip !== "翻空为多" ||
                !knownPoint(low, "L", asof) ||
                !knownPoint(high, "H", asof) ||
                !knownPoint(key, "H", asof) ||
                low.time >= high.time ||
                high.value <= key.value
            )
                continue;
            const formalHigh = stroke.points.find(
                (point) => point.kind === "H" && point.time === high.time && point.value === high.value,
            );
            const completedB =
                formalHigh?.flip === "翻多为空" &&
                knownPoint(formalHigh, "H", asof) &&
                knownPoint(formalHigh.confirmed_by, "L", asof) &&
                formalHigh.confirmed_by.time > high.time
                    ? {
                          ...formalHigh.confirmed_by,
                          available_at: [formalHigh.available_at, formalHigh.confirmed_by.available_at].sort().at(-1),
                      }
                    : null;
            const candidate = {
                ...high,
                confirmed_low: low,
                available_at: [low.available_at, high.available_at].sort().at(-1),
                completedB,
            };
            candidates.set(identity(candidate), candidate);
        }
    }
    for (const high of theory?.secondary_trends?.bear_to_bull_highs || []) {
        const key = identity(high);
        candidates.set(key, { ...candidates.get(key), ...high });
    }
    return [...candidates.values()].sort((left, right) => left.time.localeCompare(right.time));
}

/** Observe early lecture-path Ns independently from the stricter strategy event reducer. */
export function waveCProjectionsFromStructure(bars, theory) {
    const asof = theory?.asof || bars.at(-1)?.time;
    const visibleBars = bars.filter((bar) => bar.time <= asof);
    const byTime = new Map(visibleBars.map((bar, index) => [bar.time, { bar, index }]));
    const highs = historicalHighs(theory, asof);
    const strokes = theory?.lecture_drawing?.strokes || [];
    const projections = [];
    for (const high of highs) {
        const a = byTime.get(high.time);
        if (!a || high.available_at > asof || high.value !== a.bar.high) continue;
        const completedB = high.completedB;
        if (completedB && completedB.value !== byTime.get(completedB.time)?.bar.low) continue;
        // A confirmed B fixes this historical group; a later wave cannot move or erase its endpoints.
        const projectionBars = completedB
            ? visibleBars.filter((bar) => bar.time <= completedB.available_at)
            : visibleBars;
        let found = null;
        for (const stroke of strokes) {
            const points = stroke.points || [];
            for (let i = 0; i < points.length - 3; i++) {
                const [origin, firstHigh, pullback, attack] = points.slice(i, i + 4);
                if (
                    origin.kind !== "L" ||
                    firstHigh.kind !== "H" ||
                    pullback.kind !== "L" ||
                    attack.kind !== "H" ||
                    origin.state !== "seed" ||
                    pullback.time !== high.confirmed_low?.time ||
                    pullback.value !== high.confirmed_low?.value ||
                    [origin, firstHigh, pullback, attack].some((point) => point.available_at > asof)
                )
                    continue;
                const o = byTime.get(origin.time);
                const h = byTime.get(firstHigh.time);
                const p = byTime.get(pullback.time);
                const n = byTime.get(attack.time);
                if (
                    !o ||
                    !h ||
                    !p ||
                    !n ||
                    !(o.index < h.index && h.index < p.index && p.index < n.index && n.index < a.index) ||
                    origin.value !== o.bar.low ||
                    firstHigh.value !== h.bar.high ||
                    pullback.value !== p.bar.low ||
                    attack.value !== n.bar.high ||
                    !(
                        origin.value < pullback.value &&
                        pullback.value < firstHigh.value &&
                        attack.value > firstHigh.value &&
                        n.bar.close > firstHigh.value
                    )
                )
                    continue;
                const defense = Math.min(n.bar.low, visibleBars[n.index - 1].close);
                const oneP = 2 * attack.value - origin.value;
                if (!Number.isFinite(defense) || !Number.isFinite(oneP)) continue;
                let pullbackSeen = false;
                let squeezeTime = null;
                let squeezeHigh = null;
                for (let index = n.index + 1; index < a.index; index++) {
                    const bar = visibleBars[index];
                    if (bar.low < defense) break;
                    if (bar.close < n.bar.close) pullbackSeen = true;
                    if (
                        pullbackSeen &&
                        bar.close > bar.open &&
                        bar.close > n.bar.close &&
                        bar.high > n.bar.high &&
                        bar.volume > n.bar.volume &&
                        bar.volume > visibleBars[index - 1].volume
                    ) {
                        squeezeTime = bar.time;
                        squeezeHigh = bar.high;
                        break;
                    }
                }
                if (!squeezeTime) continue;
                const event = {
                    event: "n_completed",
                    direction: "up",
                    time: attack.time,
                    available_at: attack.time,
                    defense,
                    shape: [{ time: origin.time, value: origin.value }],
                    levels: [{ name: "1P 投影", price: oneP }],
                };
                const projection = waveCProjection(projectionBars, [event], high.time);
                if (
                    projection &&
                    (!completedB || (projection.bTime === completedB.time && projection.bLow === completedB.value))
                ) {
                    found = {
                        ...projection,
                        originTime: origin.time,
                        nHigh: attack.value,
                        squeezeTime,
                        squeezeHigh,
                        aKnownAt: high.available_at,
                        ...(completedB ? { bKnownAt: completedB.available_at } : {}),
                    };
                    if (completedB) {
                        const failedIndex = visibleBars.findIndex(
                            (bar) =>
                                bar.time > completedB.available_at &&
                                bar.low < projection.origin &&
                                bar.close < projection.origin,
                        );
                        if (failedIndex > 0) {
                            found.invalidatedAt = visibleBars[failedIndex].time;
                            found.targetValidUntil = visibleBars[failedIndex - 1].time;
                        }
                    }
                    break;
                }
            }
            if (found) break;
        }
        if (found) projections.push(found);
    }
    const ordinary = ordinaryCWaveProjections(bars, theory);
    return [
        ...projections.filter(
            (projection) =>
                !ordinary.some((item) => item.originTime === projection.originTime && item.aTime === projection.aTime),
        ),
        ...ordinary,
    ].sort((left, right) => left.aTime.localeCompare(right.aTime));
}

export function waveCProjectionFromStructure(bars, theory) {
    return waveCProjectionsFromStructure(bars, theory).at(-1) || null;
}

export function waveCProjectionForSelection(bars, events, selectedTime, structuralProjection) {
    return structuralProjection?.aTime === selectedTime
        ? structuralProjection
        : waveCProjection(bars, events, selectedTime);
}

export function waveCProjectionAnnotation(projection, bars = [], asof) {
    const observed = Boolean(projection.squeezeTime);
    const ordinary = projection.aAttackClass === "non_strong";
    const levels = waveCProjectionLevels(projection, bars, asof);
    const extension = levels.find((level) => level.stage === "c_1618");
    const completed = projection.cTime && (!asof || projection.cKnownAt <= asof);
    const targetStates = ordinary
        ? levels.map((level) => targetLevelGuide({ time: projection.bTime }, level, bars, asof))
        : [];
    return {
        id: `wave-c:${projection.nTime}:${projection.aTime}${ordinary ? `:${projection.anchorVersion}` : ""}`,
        time: projection.bTime,
        sourceTime: projection.aTime,
        kind: "wave-projection",
        category: "wave-projection",
        price: projection.bLow,
        markerPosition: "atPriceBottom",
        markerShape: "arrowUp",
        color: "#e6ba64",
        priority: 200,
        title: "B / C",
        description: ordinary
            ? `普通 A 结构观察：A 起点 ${projection.originTime} ${num(projection.origin, 4)} 元；内部正 N ${projection.nTime}，不替换 A 起点。A 顶 ${projection.aTime} ${num(projection.aHigh, 4)} 元，未达既有二吐 ${num(projection.twoT, 4)}，属于非强攻击 A。B 低 ${projection.bTime} ${num(projection.bLow, 4)} 元，于 ${projection.bKnownAt} 按二级结构确认；${projection.bBrokeASqueezeLow ? "B 期间已破 A 的轧空低，仍守住 A 起点；" : ""}回撤 ${(projection.bRetracementRatio * 100).toFixed(2)}%，A / B 为 ${projection.aDuration} / ${projection.bDuration} 个交易日。A 幅度 = A 高 − A 起点；三档 C 目标为 ${num(projection.target0618, 4)} / ${num(projection.target, 4)}（等浪）/ ${num(projection.target1618, 4)} 元，目标生效 ${projection.confirmedAt}。${targetStates.map((state, index) => (state ? `${["0.618", "1（等浪）", "1.618"][index]}×A：${state.targetState}${state.firstTouchedAt ? `，首次 ${state.firstTouchedAt}` : ""}；` : "")).join("")}${completed ? `C 顶 ${projection.cTime} ${num(projection.cHigh, 4)} 元，于 ${projection.cKnownAt} 确认结束，目标触及状态冻结在本段；` : ""}${projection.invalidatedAt ? `${projection.invalidatedAt} 最低价与收盘价双破 A 起点，A 失效，原组 C 目标作废；` : ""}波段级别 ${projection.trendLevel}，锚点版本 ${projection.anchorVersion}。仅为结构与测幅观察，不产生买卖信号。`
            : `${observed ? "讲义折线结构观察：" : ""}正 N ${projection.nTime} 后${observed ? `，${projection.squeezeTime} 出现轧空式放量续攻` : ""}，A 浪高点 ${projection.aTime} ${num(projection.aHigh)} 高于一饱 ${num(projection.oneP)}；B 浪低点 ${projection.bTime} ${num(projection.bLow)}${projection.bKnownAt ? `，于 ${projection.bKnownAt} 确认并固定历史端点` : ""}。B 期间未出现最低价与收盘价同时跌破正 N 起点 ${num(projection.origin)}。A 幅度 = A 高 − 正 N 起点；B 低 + 0.618×A = ${num(projection.target0618, 4)} 元，B 低 + 1×A = ${num(projection.target)} 元。${extension ? `${extension.available_at} 已满足 1×A，增加 B 低 + 1.618×A = ${num(extension.price, 4)} 元。` : ""}${projection.invalidatedAt ? `${projection.invalidatedAt} 最低价与收盘价双破起点，目标有效区间截至 ${projection.targetValidUntil}；保留历史标识。` : ""}仅为测幅观察，不保证到达。`,
        sourceLabel: ordinary
            ? "正式正 N 与同级已确认 A/B · 结构观察"
            : observed
              ? "讲义折线起点与已确认二级 A 高 · 图表观察"
              : "所选 A 浪高点与当前历史截面 B 浪低点",
        levels,
        raw: projection,
    };
}

/** Price-anchored evidence makes the A/B/C observation legible without a click. */
export function waveCProjectionEvidenceAnnotations(projection, bars = [], asof) {
    if (!projection.originTime || (!projection.squeezeTime && projection.aAttackClass !== "non_strong")) return [];
    const shared = waveCProjectionAnnotation(projection, bars, asof);
    return [
        {
            ...shared,
            id: `${shared.id}:origin`,
            time: projection.originTime,
            price: projection.origin,
            title: "A 起",
            color: "#dca466",
        },
        {
            ...shared,
            id: `${shared.id}:n`,
            time: projection.nTime,
            price: projection.nHigh,
            markerPosition: "aboveBar",
            markerShape: "circle",
            title: "正 N",
            color: "#82cfc0",
        },
        ...(projection.squeezeTime
            ? [
                  {
                      ...shared,
                      id: `${shared.id}:squeeze`,
                      time: projection.squeezeTime,
                      price: projection.squeezeHigh,
                      markerPosition: "aboveBar",
                      markerShape: "circle",
                      title: "轧空",
                      color: "#82cfc0",
                  },
              ]
            : []),
        {
            ...shared,
            id: `${shared.id}:a-high`,
            time: projection.aTime,
            price: projection.aHigh,
            markerPosition: "atPriceTop",
            markerShape: "arrowDown",
            title: "A 顶",
            color: "#dca466",
        },
        ...(projection.cTime && (!asof || projection.cKnownAt <= asof)
            ? [
                  {
                      ...shared,
                      id: `${shared.id}:c-high`,
                      time: projection.cTime,
                      price: projection.cHigh,
                      markerPosition: "atPriceTop",
                      markerShape: "arrowDown",
                      title: "C 顶",
                      color: "#e6ba64",
                  },
              ]
            : []),
    ];
}

export function waveCProjectionLegs(projection) {
    if (!projection.originTime || !projection.aTime || !projection.bTime) return [];
    return [
        {
            title: "A 浪",
            color: "#dca466",
            points: [
                { time: projection.originTime, value: projection.origin },
                { time: projection.aTime, value: projection.aHigh },
            ],
        },
        {
            title: "B 浪",
            color: "#82cfc0",
            points: [
                { time: projection.aTime, value: projection.aHigh },
                { time: projection.bTime, value: projection.bLow },
            ],
        },
    ];
}
