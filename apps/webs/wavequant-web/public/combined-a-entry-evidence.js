import { num, pct } from "./labels.js";

// 只解释引擎冻结的入场证据，不从图表重新推断组合或买点。
export function combinedAEntryEvidence(evidence) {
    const proof =
        evidence?.find((entry) => entry.buy_point_type === "combined_a_pullback_breakout") ||
        evidence?.find((entry) => entry.channel === "combined_a_pullback_breakout" && entry.combined_a_origin_date);
    if (!proof) return [];
    const hasInternal = Number.isInteger(proof.combined_a_internal_pullback_sessions);
    const hasChild = Number.isInteger(proof.combined_a_child_pullback_sessions);
    const comparisons = [
        hasInternal ? `组合内部回调 ${proof.combined_a_internal_pullback_sessions} 个交易日` : null,
        hasChild ? `子级回调 ${proof.combined_a_child_pullback_sessions} 个交易日` : null,
    ].filter(Boolean);
    const missing = [hasInternal ? null : "组合内部回调时长未提供", hasChild ? null : "子级回调时长未提供"].filter(
        Boolean,
    );
    const timeCondition = comparisons.length ? `须超过${comparisons.join("或")}（满足其一）` : "回调时间比较基准未提供";
    const confirmationDate = proof.time || proof.timestamp?.slice(0, 10) || "—";
    const reference = proof.combined_a_breakout_type === "gap" ? "前日最高价" : "回调参考高";
    const gap = proof.combined_a_breakout_type === "gap" ? "突破时缺口未回补；" : "";
    return [
        `组合 A：${proof.combined_a_origin_date} 起点 ${num(proof.combined_a_origin_price, 4)} → ${proof.combined_a_top_date} 顶 ${num(proof.combined_a_top_price, 4)}；${proof.combined_a_known_date} 起可知。顶后回调及整理 ${proof.combined_a_pullback_sessions} 个交易日，${timeCondition}。${missing.length ? `${missing.join("，")}，仅比较已知基准。` : ""}回调低点 ${proof.combined_a_pullback_date} ${num(proof.combined_a_pullback_low, 4)}；期间最低收盘 ${num(proof.combined_a_minimum_close, 4)} ≥ 2/3 回撤价 ${num(proof.combined_a_two_thirds_price, 4)}，按收盘守线。`,
        `放量中大阳线确认：${confirmationDate} 收盘 ${num(proof.confirmation_close, 4)} 严格突破${reference} ${proof.combined_a_breakout_date} ${num(proof.combined_a_breakout_price, 4)}（${proof.combined_a_breakout_known_date} 起可知）；${gap}成交量 ${num(proof.breakout_volume, 0)} 股 > 前日 ${num(proof.previous_volume, 0)} 股；阳线实体/开盘 ${pct(proof.breakout_body_pct)} ≥3%，实体/振幅 ${pct(proof.breakout_body_ratio)} ≥60%。收盘相等或仅盘中越线不算突破。`,
        `组合 A 入场防守 ${num(proof.stop, 4)} 元，尚未到达的组合 A 顶目标 ${num(proof.target, 4)} 元；信号仍须通过原有风险、执行与成交门禁。`,
    ];
}
