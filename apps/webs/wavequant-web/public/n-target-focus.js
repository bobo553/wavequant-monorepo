import { bottomNTargetCaption, isBottomNTargetSource } from "./bottom-n-targets.js";
import { targetLevelGuide } from "./target-level-guides.js";

const BASE_N_STAGES = new Set(["one_p", "two_t"]);
const N_STAGES = ["one_p", "two_t", "five_top", "ten_full"];

export function isNTargetStage(stage) {
    return N_STAGES.includes(stage);
}

/** 只读取原 N 已发布的阶段；预估价和缺少可知日的远端目标不延长观察窗口。 */
export function nTargetLevels(item, asof) {
    if (!isPositiveNTarget(item) || !asof || item.time > asof || item.raw.available_at > asof) return [];
    return item.levels.filter((level) => {
        if (!isNTargetStage(level.stage) || !Number.isFinite(level.price)) return false;
        if (!BASE_N_STAGES.has(level.stage) && (!level.available_at || level.estimated)) return false;
        const knownAt = level.available_at || item.signal_time || item.time;
        return knownAt <= asof;
    });
}

/** 五顶、十满缺少结构锚点时从发布日画起；达到含等号，与一饱二吐突破线区分。 */
export function nTargetGuide(item, level, bars, asof) {
    if (!nTargetLevels(item, asof).includes(level)) return null;
    const knownAt = level.available_at || item.time;
    const guide = targetLevelGuide(item, { ...level, anchor_at: level.anchor_at || knownAt }, bars, asof);
    const caption = bottomNTargetCaption(item.raw);
    if (guide && caption) guide.name = `${guide.name} · ${caption}`;
    if (!guide || BASE_N_STAGES.has(level.stage)) return guide;
    const end = level.valid_until && level.valid_until < asof ? level.valid_until : asof;
    const reached = bars.find((bar) => {
        const price = bar.time === knownAt ? bar.close : bar.high;
        const rounding = Number.EPSILON * Math.max(1, Math.abs(price), Math.abs(level.price)) * 4;
        return bar.time >= knownAt && bar.time <= end && Number.isFinite(price) && price >= level.price - rounding;
    });
    return {
        ...guide,
        end: reached?.time || null,
        targetState: level.status && level.status !== "已满足" ? level.status : reached ? "已满足" : "推演中",
    };
}

export function isPositiveNTarget(item) {
    return (
        isBottomNTargetSource(item?.raw) &&
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
            const levels = nTargetLevels(item, asof);
            const guides = levels.map((level) => nTargetGuide(item, level, bars, asof));
            if (["one_p", "two_t"].some((stage) => !guides[levels.findIndex((level) => level.stage === stage)]))
                return [];
            const twoT = guides[levels.findIndex((level) => level.stage === "two_t")];
            const activeLevels = levels.filter(
                (level, index) => guides[index] && !["回调暂停", "已失效"].includes(level.status),
            );
            const latest =
                [...N_STAGES]
                    .reverse()
                    .map(
                        (stage) =>
                            guides[levels.findIndex((level) => level.stage === stage && activeLevels.includes(level))],
                    )
                    .find(Boolean) || twoT;
            const from = item.raw.shape?.[0]?.time || twoT.start;
            const to = activeLevels.reduce(
                (end, level) => (level.valid_until && level.valid_until < end ? level.valid_until : end),
                latest.end || asof,
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
    const explicit =
        candidates.find(({ item }) => item.id === id) || candidates.find(({ item }) => item.id === selectedId);
    if (explicit) return explicit.item;
    const completed = candidates.find(({ item }) => item.time <= time);
    const upcoming = candidates
        .filter(({ item }) => item.time > time)
        .reduce(
            (nearest, candidate) => (!nearest || candidate.item.time < nearest.item.time ? candidate : nearest),
            null,
        );
    // 允许回看已知 N 的形成段，但后续新 N 不覆盖此前已完成的本段 N。
    if (upcoming && (!completed || completed.item.time < upcoming.from)) return upcoming.item;
    return completed?.item || upcoming?.item || null;
}
