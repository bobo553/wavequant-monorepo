import { num, pct } from "./labels.js";

/** Consume the engine's dated proof; candle geometry alone never creates a buy. */
export function secondaryReclaimEvidence(evidence) {
    const proof = evidence?.find((entry) => entry.buy_point_type === "secondary_resistance_reclaim");
    if (!proof) return [];
    if (
        ![
            proof.secondary_high,
            proof.secondary_resistance_high,
            proof.secondary_reclaim_close,
            proof.secondary_reclaim_body_fraction,
            proof.breakout_volume,
            proof.previous_volume,
        ].every(Number.isFinite) ||
        ![
            proof.secondary_high_date,
            proof.secondary_high_known_date,
            proof.secondary_attack_date,
            proof.secondary_resistance_date,
            proof.secondary_resistance_known_date,
        ].every((date) => typeof date === "string" && date)
    )
        return ["二级突破抵抗放量收复：引擎已记录该买点，当前结果缺少完整日期、价位或量价依据。"];
    return [
        `二级突破抵抗放量收复：${proof.secondary_high_date} 二级高点 ${num(proof.secondary_high, 4)}（${proof.secondary_high_known_date} 已确认），${proof.secondary_attack_date} 突破后，当笔或次笔出现空头抵抗；抵抗高点 ${proof.secondary_resistance_date} 为 ${num(proof.secondary_resistance_high, 4)}。`,
        `${proof.secondary_reclaim_type === "gap" ? "未回补跳空中大阳收复" : "阳线实体收复"}：收盘 ${num(proof.secondary_reclaim_close, 4)} > 抵抗高点 ${num(proof.secondary_resistance_high, 4)}；实体/开盘 ${pct(proof.secondary_reclaim_body_fraction)} ${proof.secondary_reclaim_type === "gap" ? "≥ 3%，实体/振幅 ≥ 60%" : "> 2%"}。成交量 ${num(proof.breakout_volume, 0)} 股 > 前日 ${num(proof.previous_volume, 0)} 股。`,
        `回调低点 ${proof.secondary_pullback_low_date} ${num(proof.secondary_pullback_low, 4)}；原起点 ${proof.secondary_origin_date} ${num(proof.secondary_origin_low, 4)} 保持。防守 ${num(proof.stop, 4)}，最近未触及的已知目标 ${num(proof.target, 4)}。`,
    ];
}
