import { num } from "./labels.js";

export function waveEntryEvidence(evidence) {
    const wave = evidence?.find((e) => e.wave_entry_path === "two_t_held_defense_volume_gap");
    if (!wave) return [];
    return [
        `堆箱买点：原正 N ${wave.wave_entry_n_date}，${wave.wave_entry_two_t_date} 达到二吐 ${num(wave.wave_entry_two_t, 4)}；B 低 ${wave.wave_b_low_date} ${num(wave.wave_b_low, 4)} 守住轧空低 ${num(wave.wave_defense, 4)}。`,
        `C 浪确认：放量跳空阳线，最低 ${num(wave.wave_gap_low, 4)} > 前日最高 ${num(wave.wave_gap_previous_high, 4)}，成交量 ${num(wave.wave_gap_volume, 0)} > 前日 ${num(wave.wave_gap_previous_volume, 0)}。`,
        `等浪观察位：B 低 ${num(wave.wave_b_low, 4)} + A 浪幅度（${wave.wave_a_high_date} 高点 ${num(wave.wave_a_high, 4)} − 起点 ${num(wave.wave_a_origin, 4)}）= ${num(wave.wave_equal_target, 4)} 元。`,
        ...(Number.isFinite(wave.wave_c_1618_target) && Number.isFinite(wave.wave_c_2618_target)
            ? [
                  `大 C 浪扩展目标：B 低 + 1.618×整段 A 幅度 = ${num(wave.wave_c_1618_target, 4)} 元；B 低 + 2.618×整段 A 幅度 = ${num(wave.wave_c_2618_target, 4)} 元。2.618 倍不是上涨上限，达标本身不触发卖出。`,
              ]
            : []),
    ];
}
