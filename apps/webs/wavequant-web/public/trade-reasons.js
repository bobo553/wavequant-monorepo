import { reasonText } from "./annotations.js";
import { num, pct } from "./labels.js";
import { waveEntryEvidence } from "./wave-entry-evidence.js";

// Explain the dated engine decision; never infer a signal from plotted prices.
export function tradeReasonItems(item) {
    const reasons = (item.decision_reason || item.reason || "")
        .split("|")
        .filter(Boolean)
        .map(reasonText);
    const evidence = item.decision_evidence || [];
    if (item.side === "BUY") {
        reasons.push(...waveEntryEvidence(evidence).slice(0, 2));
        const reversal = evidence.find((e) => e.squeeze_confirmation === "volume_reversal_record_break");
        if (reversal)
            reasons.push(
                `放量反转确认：${reversal.n_close_break_date} 的 N 收盘突破；收盘 ${num(reversal.reversal_close, 4)} > 抵抗高 ${num(reversal.reversal_record_high, 4)}，成交量 ${num(reversal.reversal_volume, 0)} > 前日 ${num(reversal.reversal_previous_volume, 0)}。`,
            );
        const gap = evidence.find((e) => e.squeeze_confirmation === "defended_n_consolidation_gap");
        if (gap)
            reasons.push(
                `整理后跳空确认：最低 ${num(gap.gap_low, 4)} > 前日高 ${num(gap.gap_previous_high, 4)}，累计成交量 ${num(gap.gap_volume, 0)} > 前日 ${num(gap.gap_previous_volume, 0)}。`,
            );
        const squeeze = evidence.find((e) => e.squeeze_confirmation === "uninterrupted_squeeze");
        if (squeeze)
            reasons.push(
                `连续上攻确认：最低 ${num(squeeze.confirmation_low, 4)} ≥ 前根虚拟低 ${num(squeeze.prior_virtual_low, 4)}，收盘 ${num(squeeze.confirmation_close, 4)} > 前收 ${num(squeeze.prior_close, 4)}。`,
            );
        const record = evidence.find((e) =>
            ["resistance_record_break", "fresh_n_defeats_old_n_resistance"].includes(e.squeeze_confirmation),
        );
        if (record)
            reasons.push(
                `抵抗高点突破：${record.attack_date} 正 N，确认收盘 ${num(record.confirmation_close, 4)} > 抵抗阶段高 ${num(record.confirmation_record_high, 4)}。`,
            );
        const local = evidence.find((e) => e.squeeze_confirmation === "local_resistance_failure");
        if (local)
            reasons.push(
                `抵抗失败确认：${local.attack_date} 正 N 后，最低 ${num(local.confirmation_low, 4)} ≥ 虚拟低 ${num(local.prior_virtual_low, 4)}，收盘 ${num(local.confirmation_close, 4)} > 前收 ${num(local.prior_close, 4)}。`,
            );
        const proof = evidence.find((e) => e.buy_point_type);
        if (proof?.alternation_index_date && proof?.attack_date)
            reasons.push(
                `趋势与入场时序：${proof.alternation_index_date} 确认空多交替，${proof.attack_date} 出现新正 N${proof.trend_level ? `；${proof.trend_level} 级趋势` : ""}。`,
            );
        if (proof?.secondary_resistance_resolved)
            reasons.push(
                `二级压力解除：${proof.secondary_resolution_date} 收盘 ${num(proof.secondary_confirmation_close, 4)} > 抵抗阶段高 ${num(proof.secondary_resistance_high, 4)}。`,
            );
    } else if (item.side === "SELL") {
        if (item.wave_reached_stage) {
            const names = { two_t: "二吐", five_top: "五顶", ten_full: "十满", ordinary_equal: "普通 A 的 C 等浪" };
            reasons.push(
                `目标背景：${item.wave_reached_date} 已达到${names[item.wave_reached_stage] || item.wave_reached_stage} ${num(item.wave_reached_price, 4)} 元。`,
            );
        }
        if (item.pressure_date)
            reasons.push(
                `前期压力：${item.pressure_date} 压力区 ${num(item.pressure_low, 4)}–${num(item.pressure_high, 4)} 元。`,
            );
        if (item.pressure_adverse_patterns?.length)
            reasons.push(
                `不利形态：${item.pressure_adverse_patterns.map((key) => ({ close_below_previous: "收盘低于前收", low_below_previous: "跌破前低", long_upper_shadow: "长上影" })[key] || key).join("、")}。`,
            );
        if (item.volume_trigger_date)
            reasons.push(
                `放量下跌：${item.volume_trigger_date} 成交量 ${num(item.trigger_volume, 0)} > 前日 ${num(item.previous_volume, 0)}，收盘 ${num(item.trigger_close, 4)} < 前收 ${num(item.previous_close, 4)}。`,
            );
        if (item.reason === "volume_down_next_gap_fade_clear")
            reasons.push(
                `次日低开走弱：开盘 ${num(item.observed_open, 4)} < 前收 ${num(item.gap_previous_close, 4)}，收盘 ${num(item.observed_close, 4)} < 开盘 ${num(item.observed_open, 4)}。`,
            );
        if (item.reason === "volume_down_next_followthrough_clear")
            reasons.push(
                `次日下跌确认：收盘 ${num(item.observed_close, 4)} < 警示日低点 ${num(item.warning_low, 4)}，且低于当日开盘 ${num(item.observed_open, 4)}。`,
            );
        if (item.positive_n_date)
            reasons.push(`小实体例外：${item.positive_n_date} 正 N 的实体 ${pct(item.small_body_fraction)}，上限 ${pct(item.small_body_cap)}。`);
        if (item.reason === "wave_gap_reversal_reduce" && item.observed_open != null)
            reasons.push(
                `高开回落：开盘 ${num(item.observed_open, 4)} > 前高 ${num(item.previous_high, 4)}，成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}。`,
            );
        if (item.reason === "wave_abnormal_followthrough_clear" && item.abnormal_date)
            reasons.push(
                `次日确认：${item.abnormal_date} 异常 K 线后，收盘 ${num(item.observed_close, 4)} < 异常 K 线收盘 ${num(item.abnormal_close, 4)}。`,
            );
        if (item.reason === "wave_ordinary_equal_upper_shadow_reduce")
            reasons.push(`普通 A：前 A 高 ${num(item.wave_a_high, 4)} 达一饱 ${num(item.wave_one_p, 4)}、未达二吐 ${num(item.wave_two_t, 4)}；C 浪触及等浪目标后放量长上影，上影占振幅 ${pct(item.wave_upper_shadow_fraction)}，按累计 80% 目标减仓。`);
        if (item.reason === "wave_ordinary_equal_lower_close_clear")
            reasons.push(`异常后首次收低：${item.abnormal_date} 出现长上影；本日收盘 ${num(item.observed_close, 4)} < 前收 ${num(item.previous_close, 4)}，当日清空余仓。`);
        if (["wave_upper_rejection_reduce", "wave_volume_shadows_reduce"].includes(item.reason) && item.wave_range_fraction != null)
            reasons.push(
                `异常波动：振幅/前收 ${pct(item.wave_range_fraction)}，上影占振幅 ${pct(item.wave_upper_shadow_fraction)}，成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}。`,
            );
        if (item.trend_adverse_patterns?.length)
            reasons.push(`趋势转弱形态：${item.trend_adverse_patterns.join("、")}。`);
        if (item.resistance_date && item.failure_close != null)
            reasons.push(`多头抵抗失败：${item.resistance_date} 虚拟低 ${num(item.resistance_virtual_low, 4)}，失败收盘 ${num(item.failure_close, 4)}。`);
        if (item.support_date && item.support_low != null && item.observed_low != null)
            reasons.push(`前回踩低点失守：${item.support_date} 最低 ${num(item.support_low, 4)}，触发时最低 ${num(item.observed_low, 4)}。`);
    }
    return reasons.filter(Boolean);
}

export function numberedTradeReasons(item) {
    const reasons = tradeReasonItems(item);
    return reasons.length ? reasons.map((reason, index) => `${index + 1}. ${reason}`) : ["1. 本次成交记录未提供具体决策原因。"];
}
