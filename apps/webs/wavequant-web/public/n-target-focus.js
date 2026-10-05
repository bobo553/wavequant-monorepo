import { targetLevelGuide } from "./target-level-guides.js";

const N_STAGES = new Set(["one_p", "two_t"]);

export function isPositiveNTarget(item) {
    return (
        item?.raw?.event === "n_completed" &&
        item.raw.direction === "up" &&
        ["one_p", "two_t"].every((stage) =>
            item.levels?.some((level) => level.stage === stage && Number.isFinite(level.price) && level.anchor_at),
        )
    );
}

/** 范围在理论更新时冻结；悬停只查询已发布目标，不重算测幅或扫描全部历史。 */
export function nTargetObservations(items, bars, asof) {
    if (!asof) return [];
    return items
        .flatMap((item) => {
            if (!isPositiveNTarget(item) || item.time > asof) return [];
            const levels = item.levels.filter((level) => N_STAGES.has(level.stage));
            const guides = levels.map((level) => targetLevelGuide(item, level, bars, asof));
            if (guides.some((guide) => !guide)) return [];
            const twoT = guides[levels.findIndex((level) => level.stage === "two_t")];
            const from = item.raw.shape?.[0]?.time || twoT.start;
            const to = levels.reduce(
                (end, level) => (level.valid_until && level.valid_until < end ? level.valid_until : end),
                twoT.end || asof,
            );
            if (from > to || !bars.some((bar) => bar.time === from)) return [];
            return [{ item, from, to }];
        })
        .sort(
            (left, right) =>
                right.item.time.localeCompare(left.item.time) ||
                right.from.localeCompare(left.from) ||
                left.item.id.localeCompare(right.item.id),
        );
}

export function nTargetAt(observations, time, id, selectedId) {
    if (!time) return null;
    const candidates = observations.filter((observation) => observation.from <= time && time <= observation.to);
    return (
        (
            candidates.find(({ item }) => item.id === id) ||
            candidates.find(({ item }) => item.id === selectedId) ||
            candidates[0]
        )?.item || null
    );
}
