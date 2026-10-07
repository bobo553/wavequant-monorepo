import { num } from "./labels.js";

const POLICY = "one_p_break_confirmed_a_origin_lifetime_v1";
const EVENTS = new Set([
    "a_wave_confirmed",
    "a_wave_upgraded",
    "a_wave_extended",
    "a_wave_top_confirmed",
    "a_wave_invalidated",
    "b_wave_updated",
    "c_wave_started",
    "c_wave_extended",
    "c_wave_completed",
]);

/** A 的确认、升级和失效由 Core 发布；截面只选择当时已知的状态。 */
export function aWaveObservations(bars, theory) {
    if (theory?.a_wave_policy !== POLICY) return [];
    const asof = theory.asof || bars.at(-1)?.time;
    const byTime = new Map(bars.filter((bar) => bar.time <= asof).map((bar) => [bar.time, bar]));
    const sources = new Map();
    const events = [...(theory.events || [])]
        .filter((event) => EVENTS.has(event.event) && event.time <= event.available_at && event.available_at <= asof)
        .sort((left, right) => left.available_at.localeCompare(right.available_at));
    for (const event of events) {
        if (
            typeof event.source_id !== "string" ||
            !event.source_id ||
            ![event.origin_price, event.a_high_price, event.one_p, event.two_t].every(Number.isFinite) ||
            !event.confirmed_date ||
            event.confirmed_date > event.available_at ||
            event.origin_price !== byTime.get(event.origin_date)?.low ||
            event.a_high_price !== byTime.get(event.a_high_date)?.high ||
            (event.b_low_date && event.b_low_price !== byTime.get(event.b_low_date)?.low)
        )
            continue;
        const previous = sources.get(event.source_id);
        if (previous?.phase === "invalidated") continue;
        const amplitude = event.a_high_price - event.origin_price;
        const anchors = [...(previous?.anchorVersions || [])];
        if (event.b_low_date && anchors.at(-1)?.bTime !== event.b_low_date) {
            if (anchors.length) {
                anchors.at(-1).supersededAt = event.b_known_at_date;
                anchors.at(-1).validUntil = bars.filter((bar) => bar.time < event.b_known_at_date).at(-1)?.time;
            }
            anchors.push({
                id: `${event.source_id}:${event.b_low_date}`,
                bTime: event.b_low_date,
                bLow: event.b_low_price,
                knownAt: event.b_known_at_date,
                target0618: event.b_low_price + 0.618 * amplitude,
                target: event.b_low_price + amplitude,
                target1618: event.b_low_price + 1.618 * amplitude,
            });
        }
        sources.set(event.source_id, {
            sourceNId: event.source_id,
            nTime: event.attack_date,
            originTime: event.origin_date,
            origin: event.origin_price,
            aTime: event.a_high_date,
            aHigh: event.a_high_price,
            aConfirmedAt: event.confirmed_date,
            strongAt: event.strong_date,
            aKnownAt: event.a_top_known_at_date || event.available_at,
            oneP: event.one_p,
            twoT: event.two_t,
            aAttackClass: event.a_class === "strong" ? "strong" : "non_strong",
            phase: event.phase,
            aIsValid: event.phase !== "invalidated",
            cEligible: event.phase !== "invalidated",
            bTime: event.b_low_date,
            bLow: event.b_low_price,
            bKnownAt: event.b_known_at_date,
            confirmedAt: event.b_known_at_date,
            anchorVersion: `${event.source_id}:${event.b_low_date}`,
            anchorVersions: anchors,
            projectionSource: "one_p_confirmed_a",
            trendLevel: 1,
            ...(event.event === "c_wave_completed"
                ? {
                      cTime: event.c_high_date,
                      cHigh: event.c_high_price,
                      cKnownAt: event.c_known_at_date,
                      targetValidUntil: event.c_high_date,
                  }
                : previous?.cTime
                  ? {
                        cTime: previous.cTime,
                        cHigh: previous.cHigh,
                        cKnownAt: previous.cKnownAt,
                        targetValidUntil: previous.targetValidUntil,
                    }
                  : {}),
            ...(event.phase === "invalidated"
                ? {
                      invalidatedAt: event.available_at,
                      targetValidUntil: bars.filter((bar) => bar.time < event.available_at).at(-1)?.time,
                  }
                : {}),
        });
    }
    return [...sources.values()];
}

/** 已确认 A 的 B/C 不依赖二级翻转；失效后保留历史端点并撤销未来 C 目标。 */
export function aWaveCProjections(bars, theory) {
    return aWaveObservations(bars, theory)
        .filter((item) => item.bTime)
        .map((item) => {
            const amplitude = item.aHigh - item.origin;
            return {
                ...item,
                target0618: item.bLow + 0.618 * amplitude,
                target: item.bLow + amplitude,
                target1618: item.bLow + 1.618 * amplitude,
                aDuration:
                    bars.findIndex((bar) => bar.time === item.aTime) -
                    bars.findIndex((bar) => bar.time === item.originTime),
                bDuration:
                    bars.findIndex((bar) => bar.time === item.bTime) - bars.findIndex((bar) => bar.time === item.aTime),
            };
        });
}

/** A 即使尚无 B 也显示实线端点；确认日、强势升级日和来源身份保持独立。 */
export function aWaveAnnotations(observations, bars) {
    const byTime = new Map(bars.map((bar) => [bar.time, bar]));
    return observations.flatMap((item) => {
        const description = `正 N ${item.nTime}；${item.aConfirmedAt} 严格突破一饱 ${num(item.oneP, 4)} 元确认 A。${item.strongAt ? `${item.strongAt} 达到二吐 ${num(item.twoT, 4)} 元升级强势 A。` : "当前为普通 A。"}A 起点 ${item.originTime} ${num(item.origin, 4)} 元；高点 ${item.aTime} ${num(item.aHigh, 4)} 元${item.phase === "a" ? "，仍可延伸" : ""}。${item.invalidatedAt ? `${item.invalidatedAt} 最低价严格跌破起点，A 永久失效，原组没有后续 C。` : "起点未严格跌破，保留后续 C 观察；相等仍有效。"}`;
        const shared = {
            kind: "wave-projection",
            category: "wave-projection",
            color: "#dca466",
            priority: 205,
            description,
            sourceLabel: "正 N 一饱突破确认 A",
            levels: [],
            raw: item,
        };
        const marker = (role, time, price, title, top = false) => ({
            ...shared,
            id: `a-wave:${item.sourceNId}:${role}`,
            time,
            price,
            title,
            markerPosition: top ? "atPriceTop" : "atPriceBottom",
            markerShape: top ? "arrowDown" : "arrowUp",
        });
        return [
            marker("origin", item.originTime, item.origin, "A 起"),
            marker("confirmed", item.aConfirmedAt, byTime.get(item.aConfirmedAt)?.close, "A 确认"),
            ...(item.strongAt
                ? [marker("strong", item.strongAt, byTime.get(item.strongAt)?.high, "强势 A", true)]
                : []),
            marker(
                "top",
                item.aTime,
                item.aHigh,
                item.invalidatedAt ? "A 顶 · 已失效" : item.phase === "a" ? "A 高 · 可延伸" : "A 顶",
                true,
            ),
            ...(item.cTime ? [marker("c-top", item.cTime, item.cHigh, "C 顶", true)] : []),
        ];
    });
}

export function aWaveLegs(observations) {
    return observations
        .filter((item) => !item.bTime)
        .map((item) => ({
            id: `a-wave:${item.sourceNId}`,
            group: `a-wave:${item.sourceNId}`,
            title: "A 浪",
            color: "#dca466",
            lineStyle: 0,
            points: [
                { time: item.originTime, value: item.origin },
                { time: item.aTime, value: item.aHigh },
            ],
        }));
}
