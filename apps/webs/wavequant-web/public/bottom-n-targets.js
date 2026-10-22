export const STRUCTURAL_N_POLICY = "level_one_successful_bottom_n_v2";

/** 只接受 Core 发布的一级趋势归属；旧快照未声明此策略时保持历史契约。 */
export function isSelectedStructuralNSource(event, theory) {
    return Boolean(event) && event.structural_target_selected !== false &&
        (theory?.structural_n_policy !== STRUCTURAL_N_POLICY || event.structural_target_selected === true);
}

/** 合并前来源只通过 Core 发布的同几何别名解析；内部 N 的自行声明不能借用目标。 */
export function matchesNTargetSourceId(event, sourceId) {
    if (!isSelectedStructuralNSource(event) || typeof sourceId !== "string" || !sourceId) return false;
    return event.n_id === sourceId || event.id === sourceId ||
        (event.structural_target_selected === true && Array.isArray(event.structural_target_aliases) &&
            event.structural_target_aliases.includes(sourceId));
}

/** N 折线与目标共用发布归属；形状副本不能绕过对应事件的明确拒绝。 */
export function isSelectedStructuralNShape(shape, theory) {
    if (shape.direction !== "up") return true;
    if (shape.structural_target_selected === false) return false;
    const event = shape.n_id && theory?.events?.find((candidate) =>
        candidate.event === "n_completed" && candidate.direction === "up" && candidate.n_id === shape.n_id);
    return isSelectedStructuralNSource(event || shape, theory);
}

/** 当前测幅资格由 Core 发布；封存旧版本保持各自的历史契约。 */
export function isActivePositiveN(event, asof) {
    return !event?.n_invalidated_at || (Boolean(asof) && event.n_invalidated_at > asof);
}

export function isBottomNTargetSource(event, theory, asof = theory?.asof) {
    return isQualifiedNTargetSource(event, theory) && isActivePositiveN(event, asof);
}

/** 结构测幅可供历史悬停；有效区间由 Core 发布，不能充当交易测幅资格。 */
export function isStructuralNTargetSource(event, theory) {
    return event?.event === "n_completed" && event.direction === "up" && event.structural_target === true &&
        isSelectedStructuralNSource(event, theory);
}

/** 历史资格保留给已经形成的父 A/B；当前 N 的存活另按截面判断。 */
export function isQualifiedNTargetSource(event, theory) {
    return (
        event?.event === "n_completed" &&
        event.direction === "up" &&
        isSelectedStructuralNSource(event, theory) &&
        event.target_eligible !== false &&
        (!theory?.n_target_policy || event.target_eligible === true)
    );
}

export function bottomNTargetCaption(event) {
    if (event?.structural_target_selected === true && event.structural_trend_id)
        return `一级趋势底部正 N ${event.time}`;
    if (isStructuralNTargetSource(event)) return `折线正 N ${event.time}`;
    if (event?.display_target_eligible) return `${event.display_target_level}级资格区间 · 正 N ${event.time}`;
    if (event?.target_eligible !== true) return "";
    const bottom = event.target_bottom_date || event.shape?.[0]?.time;
    return `底部 ${bottom} · 正 N ${event.time}${event.reformed_from_date ? ` · 重算自 ${event.reformed_from_date}` : ""}`;
}
