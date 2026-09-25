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
        if (proof?.buy_point_type === "shallow_base_breakout")
            reasons.push(
                `交替待选：${proof.origin_index_date} 低点 ${num(proof.origin_price, 4)} → ${proof.flip_high_index_date} 已确认高点 ${num(proof.flip_high_price, 4)}；${proof.alternation_low_index_date} 回撤低点 ${num(proof.alternation_low_price, 4)}，回撤 ${pct(proof.counter_ratio)}，在 0.618 至不足 2/3 之间；${proof.candidate_known_index_date} 才成为待选。`,
                `守低横盘：低点后 ${proof.base_sessions} 根 K 线未破待选低点；此前 40 根区间 ${num(proof.base_low, 4)}–${num(proof.base_high, 4)}，区间波幅 ${pct(proof.base_width_fraction)}。`,
                `放量突破：收盘 ${num(proof.breakout_close, 4)} > 区间高 ${num(proof.base_high, 4)}；阳线实体/开盘 ${pct(proof.breakout_body_fraction)}，成交量 ${num(proof.breakout_volume, 0)} 股，为此前 20 日均量的 ${num(proof.breakout_volume_multiple, 2)} 倍。防守 ${num(proof.stop, 4)}，最近已确认高点目标 ${num(proof.target, 4)}。`,
            );
        if (proof?.alternation_index_date && proof?.attack_date)
            reasons.push(
                proof.joint_alternation_confirmation
                    ? `趋势与入场时序：${proof.attack_date} 出现正 N，${proof.alternation_index_date} 轧空共同确认空多交替与入场资格${proof.trend_level ? `；${proof.trend_level} 级趋势` : ""}。`
                    : `趋势与入场时序：${proof.alternation_index_date} 确认空多交替，${proof.attack_date} 出现新正 N${proof.trend_level ? `；${proof.trend_level} 级趋势` : ""}。`,
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
                `不利形态：${item.pressure_adverse_patterns.map((key) => ({ bearish_body: "阴线实体", close_below_previous: "收盘低于前收", low_below_previous: "跌破前低", long_upper_shadow: "长上影" })[key] || key).join("、")}。`,
            );
        if (item.reason === "pressure_gap_adverse_reduce")
            reasons.push(`跳空未回补：最低 ${num(item.observed_low, 4)} > 前高 ${num(item.previous_high, 4)}，收盘 ${num(item.observed_close, 4)} > 前收 ${num(item.previous_close, 4)}；当日减仓 50%。`);
        if (item.reason === "pressure_reduced_lower_close_clear")
            reasons.push(`${item.pressure_warning_date} 减仓后首次收跌：本日收盘 ${num(item.observed_close, 4)} < 前收 ${num(item.previous_close, 4)}，清空余仓。`);
        if (item.reason === "pressure_breakout_adverse_clear")
            reasons.push(`突破后转弱：${item.pressure_rally_low_date} 低点 ${num(item.pressure_rally_low, 4)} → ${item.pressure_breakout_date} 突破压力高点 ${num(item.pressure_high, 4)}，上涨 ${pct(item.pressure_rally_fraction)}；随后出现不利 K 线，清空余仓。`);
        if (item.volume_trigger_date)
            reasons.push(
                `放量下跌：${item.volume_trigger_date} 成交量 ${num(item.trigger_volume, 0)} > 前日 ${num(item.previous_volume, 0)}，收盘 ${num(item.trigger_close, 4)} < 前收 ${num(item.previous_close, 4)}。`,
            );
        if (item.reason === "volume_massive_gap_reversal_clear")
            reasons.push(`巨量高开反包：开盘 ${num(item.observed_open, 4)} > 前高 ${num(item.previous_high, 4)}，收盘 ${num(item.observed_close, 4)} < 前低 ${num(item.previous_low, 4)}；成交量 ${num(item.observed_volume, 0)} 股，为前 ${item.massive_volume_window} 日均量的 ${num(item.massive_volume_multiple, 2)} 倍且创同期新高；阴线实体/开盘 ${pct(item.bearish_body_fraction)}，当日清空余仓。`);
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
        if (item.reason === "secondary_wave_target_resistance_clear") {
            reasons.push(
                `已确认 A/B/C：${item.trend_origin_date} 二级低 ${num(item.trend_origin_low, 4)} → ${item.trend_key_date} A 高 ${num(item.trend_key_high, 4)} → ${item.wave_b_date} B 低 ${num(item.wave_b_low, 4)}；等幅 C 目标 ${num(item.wave_equal_target, 4)} 元。`,
                `${item.trend_attack_date} 盘中越过 A 高；${(item.trend_resistance_dates || []).join("、")} 连续出现空头抵抗。${item.trend_indecision_date} 触及目标，上下影分别占振幅 ${pct(item.trend_upper_shadow_fraction)}、${pct(item.trend_lower_shadow_fraction)}。`,
                `本日收盘 ${num(item.observed_close, 4)} < 长上下影 K 线最低 ${num(item.trend_indecision_low, 4)}，且为阴线，清空余仓。`,
            );
        }
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
