import { combinedAEntryEvidence } from "./combined-a-entry-evidence.js";
import { num } from "./labels.js";

/** Explain the published engine path without reconstructing a buy signal from prices. */
export function attackBarBreakSqueezeEvidence(evidence) {
    const proof = evidence?.find((entry) => entry.squeeze_confirmation === "resistance_attack_bar_break");
    if (!proof) return [];
    if (
        ![
            proof.confirmation_high,
            proof.confirmation_close,
            proof.n_attack_high,
            proof.n_attack_close,
            proof.confirmation_body_open_ratio,
            proof.confirmation_body_range_ratio,
            proof.confirmation_upper_shadow_ratio,
        ].every(Number.isFinite) ||
        proof.confirmation_strong_bullish !== true ||
        typeof proof.attack_date !== "string" ||
        !proof.attack_date ||
        typeof proof.n_resistance_date !== "string" ||
        !proof.n_resistance_date
    )
        return ["原正 N 突破棒确认：引擎记录了该轧空路径；当前结果缺少完整日期、价位或强势形态证据。"];
    return [
        `原正 N 突破棒确认：${proof.attack_date} 正 N；收盘 ${num(proof.confirmation_close, 4)} > 原突破棒收盘 ${num(proof.n_attack_close, 4)}，最高价 ${num(proof.confirmation_high, 4)} > 原突破棒最高 ${num(proof.n_attack_high, 4)}；确认日为中大阳且短上影：实体/开盘 ${num(proof.confirmation_body_open_ratio * 100)}% ≥ 3%，实体/振幅 ${num(proof.confirmation_body_range_ratio * 100)}% ≥ 60%，上影/振幅 ${num(proof.confirmation_upper_shadow_ratio * 100)}% ≤ 20%；原 N 防守完整，突破当天或次日已有空头抵抗（${proof.n_resistance_date}）失败，确认轧空。`,
    ];
}

export function waveEntryEvidence(evidence) {
    const combined = combinedAEntryEvidence(evidence);
    if (combined.length) return combined;
    const wave = evidence?.find((e) =>
        [
            "two_t_held_defense_volume_gap",
            "two_t_held_defense_gap_attack",
            "two_t_strong_a_resistance_rebreak",
            "one_p_held_defense_rebound",
        ].includes(e.wave_entry_path),
    );
    if (!wave) return [];
    const breakout = ["breakout", "breakout_and_volume"].includes(wave.wave_gap_trigger);
    const volume = wave.wave_gap_trigger !== "breakout";
    const bodyBreakout = wave.wave_gap_trigger === "volume_body_breakout";
    const ordinary = wave.wave_a_class === "ordinary";
    const strongRebreak = wave.wave_entry_path === "two_t_strong_a_resistance_rebreak";
    return [
        strongRebreak
            ? `强 A 浪再攻击买点：${wave.wave_a_origin_date} 起涨，${wave.wave_two_t_break_date} 收盘突破二吐 ${num(wave.wave_entry_two_t, 4)}；${wave.wave_resistance_date} 冲至 A 高 ${num(wave.wave_resistance_high, 4)} 后遇阻，${wave.wave_b_low_date} B 低 ${num(wave.wave_b_low, 4)}。突破阳线实体中位 ${num(wave.wave_two_t_body_midpoint, 4)}，此后各交易日收盘均未跌破。`
            : ordinary
              ? `普通 A 浪反弹买点：原正 N ${wave.wave_entry_n_date}，${wave.wave_entry_milestone_date} 达到一饱 ${num(wave.wave_entry_one_p, 4)}；A 高 ${wave.wave_a_high_date} ${num(wave.wave_a_high, 4)} 尚未达到二吐 ${num(wave.wave_entry_two_t, 4)}。B 低 ${wave.wave_b_low_date} ${num(wave.wave_b_low, 4)} 守住轧空低 ${num(wave.wave_defense, 4)}。`
              : `堆箱买点：原正 N ${wave.wave_entry_n_date}，${wave.wave_entry_two_t_date} 达到二吐 ${num(wave.wave_entry_two_t, 4)}；B 低 ${wave.wave_b_low_date} ${num(wave.wave_b_low, 4)} 守住轧空低 ${num(wave.wave_defense, 4)}。`,
        strongRebreak
            ? `C 浪确认：${wave.wave_breakout_date} A 高 ${num(wave.wave_breakout_high, 4)} 被 ${num(wave.wave_breakout_close, 4)} 收盘突破，成交量 ${num(wave.wave_gap_volume, 0)} > 前日 ${num(wave.wave_gap_previous_volume, 0)}。`
            : ordinary
              ? `C 浪反弹确认：阳线确认价 ${num(wave.wave_breakout_close, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；无需跳空，量能按所选过滤设置执行。等浪是观察目标，不保证一定到达。`
              : bodyBreakout
                ? `C 浪确认：放量中大阳线突破，确认价 ${num(wave.wave_breakout_close, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；确认时累计成交量 ${num(wave.wave_gap_volume, 0)} > 前日全天 ${num(wave.wave_gap_previous_volume, 0)}；阳线实体/开盘 ≥3%，实体/振幅 ≥60%，无需跳空。`
                : `C 浪确认：${volume ? "放量跳空" : "跳空突破"}，观察时最低 ${num(wave.wave_gap_low, 4)} > 前日最高 ${num(wave.wave_gap_previous_high, 4)}。${volume ? `确认时累计成交量 ${num(wave.wave_gap_volume, 0)} > 前日全天 ${num(wave.wave_gap_previous_volume, 0)}。` : ""}${breakout ? `观察时最高 ${num(wave.wave_gap_high, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；突破或放量满足其一。` : ""}`,
        strongRebreak
            ? `强 A 浪后续观察位：A 幅度 = ${num(wave.wave_a_high, 4)} − ${num(wave.wave_a_origin, 4)}；B 低 ${num(wave.wave_b_low, 4)} + 0.618×A = ${num(wave.wave_c_0618_target, 4)} 元，B 低 + 1×A = ${num(wave.wave_equal_target, 4)} 元。`
            : Number.isFinite(wave.wave_c_0618_target)
              ? `C 浪观察位：A 幅度 = ${wave.wave_a_high_date} 高点 ${num(wave.wave_a_high, 4)} − ${wave.wave_a_origin_date ? `${wave.wave_a_origin_date} ` : ""}起点 ${num(wave.wave_a_origin, 4)}；B 低 ${num(wave.wave_b_low, 4)} + 0.618×A = ${num(wave.wave_c_0618_target, 4)} 元，B 低 + 1×A = ${num(wave.wave_equal_target, 4)} 元。`
              : `等浪观察位：B 低 ${num(wave.wave_b_low, 4)} + A 浪幅度（${wave.wave_a_high_date} 高点 ${num(wave.wave_a_high, 4)} − ${wave.wave_a_origin_date ? `${wave.wave_a_origin_date} ` : ""}起点 ${num(wave.wave_a_origin, 4)}）= ${num(wave.wave_equal_target, 4)} 元。`,
        ...(strongRebreak && Number.isFinite(wave.wave_five_top_target)
            ? [
                  `五顶观察位：B 低 + 原正 N 的 3 倍箱高 = ${num(wave.wave_five_top_target, 4)} 元；十满需五顶达成后按当时已知高点重新测算。`,
              ]
            : []),
        ...(Number.isFinite(wave.wave_c_1618_target) && Number.isFinite(wave.wave_c_2618_target)
            ? [
                  `大 C 浪扩展目标：B 低 + 1.618×整段 A 幅度 = ${num(wave.wave_c_1618_target, 4)} 元；B 低 + 2.618×整段 A 幅度 = ${num(wave.wave_c_2618_target, 4)} 元。2.618 倍不是上涨上限，达标本身不触发卖出。`,
              ]
            : []),
    ];
}
