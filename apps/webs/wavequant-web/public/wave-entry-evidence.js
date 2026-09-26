import { num } from "./labels.js";

export function waveEntryEvidence(evidence) {
    const wave = evidence?.find((e) =>
        ["two_t_held_defense_volume_gap", "two_t_held_defense_gap_attack", "one_p_held_defense_rebound"].includes(
            e.wave_entry_path,
        ),
    );
    if (!wave) return [];
    const breakout = ["breakout", "breakout_and_volume"].includes(wave.wave_gap_trigger);
    const volume = wave.wave_gap_trigger !== "breakout";
    const bodyBreakout = wave.wave_gap_trigger === "volume_body_breakout";
    const ordinary = wave.wave_a_class === "ordinary";
    return [
        ordinary
            ? `普通 A 浪反弹买点：原正 N ${wave.wave_entry_n_date}，${wave.wave_entry_milestone_date} 达到一饱 ${num(wave.wave_entry_one_p, 4)}；A 高 ${wave.wave_a_high_date} ${num(wave.wave_a_high, 4)} 尚未达到二吐 ${num(wave.wave_entry_two_t, 4)}。B 低 ${wave.wave_b_low_date} ${num(wave.wave_b_low, 4)} 守住轧空低 ${num(wave.wave_defense, 4)}。`
            : `堆箱买点：原正 N ${wave.wave_entry_n_date}，${wave.wave_entry_two_t_date} 达到二吐 ${num(wave.wave_entry_two_t, 4)}；B 低 ${wave.wave_b_low_date} ${num(wave.wave_b_low, 4)} 守住轧空低 ${num(wave.wave_defense, 4)}。`,
        ordinary
            ? `C 浪反弹确认：阳线确认价 ${num(wave.wave_breakout_close, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；无需跳空，量能按所选过滤设置执行。等浪是观察目标，不保证一定到达。`
            : bodyBreakout
              ? `C 浪确认：放量中大阳线突破，确认价 ${num(wave.wave_breakout_close, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；确认时累计成交量 ${num(wave.wave_gap_volume, 0)} > 前日全天 ${num(wave.wave_gap_previous_volume, 0)}；阳线实体/开盘 ≥3%，实体/振幅 ≥60%，无需跳空。`
              : `C 浪确认：${volume ? "放量跳空" : "跳空突破"}，观察时最低 ${num(wave.wave_gap_low, 4)} > 前日最高 ${num(wave.wave_gap_previous_high, 4)}。${volume ? `确认时累计成交量 ${num(wave.wave_gap_volume, 0)} > 前日全天 ${num(wave.wave_gap_previous_volume, 0)}。` : ""}${breakout ? `观察时最高 ${num(wave.wave_gap_high, 4)} > 回调折线高点 ${wave.wave_breakout_date} ${num(wave.wave_breakout_high, 4)}；突破或放量满足其一。` : ""}`,
        `等浪观察位：B 低 ${num(wave.wave_b_low, 4)} + A 浪幅度（${wave.wave_a_high_date} 高点 ${num(wave.wave_a_high, 4)} − ${wave.wave_a_origin_date ? `${wave.wave_a_origin_date} ` : ""}起点 ${num(wave.wave_a_origin, 4)}）= ${num(wave.wave_equal_target, 4)} 元。`,
        ...(Number.isFinite(wave.wave_c_1618_target) && Number.isFinite(wave.wave_c_2618_target)
            ? [
                  `大 C 浪扩展目标：B 低 + 1.618×整段 A 幅度 = ${num(wave.wave_c_1618_target, 4)} 元；B 低 + 2.618×整段 A 幅度 = ${num(wave.wave_c_2618_target, 4)} 元。2.618 倍不是上涨上限，达标本身不触发卖出。`,
              ]
            : []),
    ];
}
