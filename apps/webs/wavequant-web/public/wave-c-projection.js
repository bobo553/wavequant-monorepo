import { num } from "./labels.js";

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
export function waveCProjectionLevels(projection) {
    return [
        {
            name: "C 浪目标 0.618×A",
            price: projection.target0618,
            stage: "c_0618",
            available_at: projection.bTime,
            anchor_at: projection.bTime,
        },
        {
            name: "C 浪目标 1×A",
            price: projection.target,
            stage: "c_equal",
            available_at: projection.bTime,
            anchor_at: projection.bTime,
        },
    ];
}

/** Observe an early lecture-path N that the stricter strategy event reducer may omit. */
export function waveCProjectionFromStructure(bars, theory) {
    const asof = theory?.asof || bars.at(-1)?.time;
    const visibleBars = bars.filter((bar) => bar.time <= asof);
    const byTime = new Map(visibleBars.map((bar, index) => [bar.time, { bar, index }]));
    const highs = theory?.secondary_trends?.bear_to_bull_highs || [];
    const strokes = theory?.lecture_drawing?.strokes || [];
    for (const high of [...highs].reverse()) {
        const a = byTime.get(high.time);
        if (!a || high.available_at > asof || high.value !== a.bar.high) continue;
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
                const projection = waveCProjection(visibleBars, [event], high.time);
                if (projection)
                    return { ...projection, originTime: origin.time, squeezeTime, aKnownAt: high.available_at };
            }
        }
    }
    return null;
}

export function waveCProjectionForSelection(bars, events, selectedTime, structuralProjection) {
    return structuralProjection?.aTime === selectedTime
        ? structuralProjection
        : waveCProjection(bars, events, selectedTime);
}

export function waveCProjectionAnnotation(projection) {
    const observed = Boolean(projection.squeezeTime);
    return {
        id: `wave-c:${projection.nTime}:${projection.aTime}`,
        time: projection.bTime,
        sourceTime: projection.aTime,
        kind: "wave-projection",
        category: "wave-projection",
        price: projection.bLow,
        title: "C 浪 0.618 倍与等浪观察目标",
        description: `${observed ? "讲义折线结构观察：" : ""}正 N ${projection.nTime} 后${observed ? `，${projection.squeezeTime} 出现轧空式放量续攻` : ""}，A 浪高点 ${projection.aTime} ${num(projection.aHigh)} 高于一饱 ${num(projection.oneP)}；B 浪低点 ${projection.bTime} ${num(projection.bLow)}。B 期间未出现最低价与收盘价同时跌破正 N 起点 ${num(projection.origin)}。A 幅度 = A 高 − 正 N 起点；B 低 + 0.618×A = ${num(projection.target0618, 4)} 元，B 低 + 1×A = ${num(projection.target)} 元。仅为测幅观察，不保证到达。`,
        sourceLabel: observed ? "讲义折线起点与已确认二级 A 高 · 图表观察" : "所选 A 浪高点与当前历史截面 B 浪低点",
        levels: waveCProjectionLevels(projection),
        raw: projection,
    };
}
