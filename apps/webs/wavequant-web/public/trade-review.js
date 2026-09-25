import { num, pct } from "./labels.js";
import { closedPositionLabel, positionProfit } from "./trade-position.js";
import { waveEntryEvidence } from "./wave-entry-evidence.js";
import { numberedTradeReasons } from "./trade-reasons.js";

// All conditions come from the dated engine ledger, never re-inferred from a chart.
export function appendTradeEvidence(panel, item, openPosition = null) {
    const add = (text, cls = "") => {
        const p = document.createElement("p");
        p.textContent = text;
        p.className = cls;
        panel.append(p);
        return p;
    };
    if (item.side === "BUY" || item.side === "SELL") {
        add(`${item.side === "BUY" ? "买入" : "卖出"}原因：`);
        const list = document.createElement("div");
        list.className = "trade-reason-list";
        for (const line of numberedTradeReasons(item)) {
            const entry = document.createElement("p");
            entry.textContent = line;
            list.append(entry);
        }
        panel.append(list);
    }
    const proof = item.decision_evidence?.find((e) => e.buy_point_type);
    waveEntryEvidence(item.decision_evidence).forEach((line) => add(line));
    const reversal = item.decision_evidence?.find((e) => e.squeeze_confirmation === "volume_reversal_record_break");
    if (reversal)
        add(
            `放量反转轧空：N 收盘突破 ${reversal.n_close_break_date}（结构突破 ${reversal.attack_date}）；收盘 ${num(reversal.reversal_close, 4)} > 抵抗阶段高 ${num(reversal.reversal_record_high, 4)}，成交量 ${num(reversal.reversal_volume, 0)} > 前日 ${num(reversal.reversal_previous_volume, 0)}；最低 ${num(reversal.reversal_low, 4)} 守住 N 起点 ${num(reversal.reversal_structural_low, 4)}。`,
        );
    const gap = item.decision_evidence?.find((e) => e.squeeze_confirmation === "defended_n_consolidation_gap");
    if (gap && proof)
        add(
            `${proof.trend_level} 级空多交替低点：${proof.alternation_low_index_date}；确认可知日：${proof.alternation_index_date}`,
        );
    if (gap)
        add(
            `买点依据：正 N ${gap.consolidation_n_date}；整理守住防守低 ${num(gap.consolidation_defense, 4)}，跳空放量重新站上原 N 高点 ${num(gap.consolidation_high, 4)}；观察时最低 ${num(gap.gap_low, 4)} > 前日高 ${num(gap.gap_previous_high, 4)}，累计成交量 ${num(gap.gap_volume, 0)} > 前日 ${num(gap.gap_previous_volume, 0)}。`,
        );
    const strongSqueeze = item.decision_evidence?.find((e) => e.squeeze_confirmation === "uninterrupted_squeeze");
    if (strongSqueeze)
        add(
            `买点依据：正 N ${strongSqueeze.attack_date}；连续上攻确认强轧空：逐根守住虚拟低，确认日最低 ${num(strongSqueeze.confirmation_low, 4)} ≥ 前根虚拟低 ${num(strongSqueeze.prior_virtual_low, 4)}，收盘 ${num(strongSqueeze.confirmation_close, 4)} > 前收 ${num(strongSqueeze.prior_close, 4)}`,
        );
    const record = item.decision_evidence?.find((e) =>
        ["resistance_record_break", "fresh_n_defeats_old_n_resistance"].includes(e.squeeze_confirmation),
    );
    if (record)
        add(
            `买点依据：正 N ${record.attack_date}；收盘 ${num(record.confirmation_close, 4)} > 本次 N 抵抗阶段高点 ${num(record.confirmation_record_high, 4)}，确认轧空。`,
        );
    if (proof?.inverse_reentry_path === "deep_alternation_kill_high_record_squeeze")
        add(
            `深回撤恢复：整段 ${proof.origin_index_date} → ${proof.flip_high_index_date}，${proof.alternation_low_index_date} 回撤 ${pct(proof.recovery_whole_retracement)}；守住回调低点，收复 ${proof.recovery_inverse_date} 杀多高 ${num(proof.recovery_kill_high, 4)}。`,
        );
    const squeeze = item.decision_evidence?.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    if (squeeze?.n_level >= 2)
        add(
            `正 N 级别 ${squeeze.n_level}：A低 ${squeeze.n_origin_date} → B高 ${squeeze.n_neckline_date} → C低 ${squeeze.n_pullback_date}`,
        );
    if (squeeze)
        add(
            `正 N ${squeeze.attack_date} → 抵抗 K ${squeeze.prior_bar_date} → 该回不回：确认日最低 ${num(squeeze.confirmation_low, 4)} 守住虚拟低 ${num(squeeze.prior_virtual_low, 4)}，收盘 ${num(squeeze.confirmation_close, 4)} 高于前收 ${num(squeeze.prior_close, 4)}。`,
        );
    if (proof?.buy_point_type === "shallow_base_breakout") {
        add(`${proof.trend_level} 级交替待选 · 横盘放量突破；待选低点不作为正式二/三级交替点。`);
    } else if (proof?.buy_point_type === "multilevel_breakout_squeeze") {
        add(
            `双重轧空：正 N ${proof.attack_date} 同时突破 ${proof.trend_level} 级波段高 ${proof.key_source_index_date}（${num(proof.key_price, 4)}）；${proof.higher_confirmation_index_date} 守住防守、放量收盘 ${num(proof.confirmation_close, 4)} > 抵抗阶段高 ${num(proof.higher_resistance_high, 4)}。`,
        );
    } else if (proof) {
        if (proof.secondary_resistance_resolved)
            add(
                `二级压力复核：${proof.secondary_high_date} 高点 ${num(proof.secondary_high, 4)}；${proof.secondary_attack_date} 再攻击后出现抵抗，${proof.secondary_resolution_date} 收盘 ${num(proof.secondary_confirmation_close, 4)} > 抵抗阶段高点 ${num(proof.secondary_resistance_high, 4)}，抵抗解除。`,
            );
        add(`${proof.priority === 2 ? "第二类 · 重点" : "第一类"}买点 · ${proof.trend_level} 级趋势线`);
        add(
            `翻多高点 ${num(proof.flip_high_price)}；${proof.counter_filter_applied ? `回撤 ${num(proof.counter_ratio * 100)}% ${proof.counter_operator || "<"} ${num(proof.counter_limit * 100)}%` : "不启用回撤比例过滤，仍保留结构防守"}`,
        );
        if (proof.definition === "whole_flip_wave_v3")
            add(
                `整段锚点：L0 ${num(proof.ratio_low_price)} · ${proof.priority === 2 ? "H1" : "H0"} ${num(proof.ratio_high_price)} · ${proof.priority === 2 ? "回撤最低收盘" : proof.counter_basis === "minimum_close_from_flip_high_to_alternation" ? "交替前最低收盘" : "交替低点"} ${num(proof.counter_price)}。局部 N 回撤 ${num(proof.local_n_ratio * 100)}% 仅作对照，不用作本条门槛。`,
            );
        const d = (k) => proof[k + "_date"] || `第 ${proof[k] + 1} 根 K 线（可知日）`;
        add(
            proof.joint_alternation_confirmation
                ? `翻多 ${d("flip_index")} → 新 N ${d("attack")} → ${d("alternation_index")} 轧空共同确认交替与入场资格`
                : `翻多 ${d("flip_index")} → 交替 ${d("alternation_index")}${proof.priority === 2 ? ` → 再破翻多高 ${d("maturity_index")} → 浅回撤 ${d("pullback_index")}` : ""} → 新 N ${d("attack")}`,
        );
    }
    if (item.kind !== "fill" && item.kind !== "order") return;
    add(
        `决定 ${item.decision_timestamp || item.signal_time || "旧记录未提供"} → ${item.kind === "fill" ? "模拟成交" : "委托评估"} ${item.execution_timestamp || item.timestamp || item.time}`,
        "decision-timeline",
    );
    add(
        `图表价 ${num(item.price, 4)} ÷ 当日因子 ${num(item.adjustment_factor, 6)} = 原始模拟价 ${num(item.raw_price, 4)} 元`,
    );
    add(
        item.execution_model === "intraday_5m_next_open"
            ? `已完成五分钟 K 线判定，下一根五分钟线开盘原价 ${num(item.minute_next_open_raw, 4)} 元，计入回测滑点后模拟成交；非逐笔成交或券商回报。`
            : item.execution_model === "same_day_close"
              ? "本笔按触发当日收盘价检查并模拟执行，未还原尾盘分钟路径；实际是否成交以委托状态为准。"
              : "B / S 为回测引擎的模拟成交，不是券商真实成交；本笔条件收盘观察，后续可交易开盘执行。",
    );
    if (item.fill_assumption === "nonflat_limit_close_without_queue_verification")
        add("成交假设：非一字涨停按当日收盘价模拟成交，未验证涨停排队成交；成交价不另加正滑点。");
    if (item.fill_assumption === "observed_nonflat_limit_intraday_without_queue_verification")
        add("成交假设：买点前已观察到非一字交易，按下一根分钟开盘价模拟成交；未验证涨停排队，成交价不另加正滑点。");
    if (item.minute_fallback)
        add(
            item.minute_fallback.reason === "minute_volume_incomplete"
                ? `分钟成交量比同源日线少 ${num(item.minute_fallback.coverage.missing_volume, 0)} 股，分钟路径不完整，已回退到日线收盘执行检查；是否成交以委托状态为准。`
                : "当日缺少完整同源分钟线，已回退到日线收盘执行检查；是否成交以委托状态为准。",
        );
    if (item.side === "BUY") {
        for (const condition of item.entry_conditions || []) {
            const status =
                condition.passed === true ? "通过" : condition.passed === false ? "未通过／证据缺失" : "未启用／未执行";
            if (condition.name === "分级双买点证据") {
                add(`${status} · ${condition.name}：${condition.required}`);
            } else if (condition.name === "趋势交替证据") {
                const e = condition.actual;
                add(
                    `${status} · ${condition.name}：${e ? `翻多 ${e.flip_index_date} → 交替 ${e.alternation_index_date} → 多头确认 ${e.bullish_index_date} → N 攻击 ${e.attack_date}` : "当前记录未提供趋势链证据"}`,
                );
            } else
                add(
                    `${status} · ${condition.name}：${typeof condition.actual === "number" ? num(condition.actual, 4) : (condition.actual ?? "—")}；门槛 ${typeof condition.required === "number" ? num(condition.required, 4) : condition.required}`,
                );
        }
        const constraints = {
            risk_budget: "单笔风险预算",
            position_weight: "仓位上限",
            liquidity: "历史流动性",
            cash: "可用现金",
        };
        add(
            `入场风控：失效位 ${num(item.stop)} · 最近目标 ${num(item.target)} · 风险预算 ${num(item.risk_budget)} 元 · 数量约束 ${constraints[item.limiting_constraint] || item.limiting_constraint || "—"}`,
        );
        const profit = positionProfit(item, null, openPosition);
        if (profit) add(profit.text);
    } else {
        if (item.kind === "fill") add(closedPositionLabel(item));
        if (Number.isFinite(item.exit_target_fraction))
            add(`目标累计减仓：${pct(item.exit_target_fraction)}（占本笔初始持仓；整手限制可能使实际比例略低）`);
        const profit = positionProfit(item);
        if (profit) add(profit.text);
        if (item.positive_n_date) {
            add(
                `小实体例外：正 N ${item.positive_n_date} · K线范围 ${num(item.positive_n_low, 4)}–${num(item.positive_n_high, 4)}；实体 ${pct(item.small_body_fraction)}，上限 ${pct(item.small_body_cap)}；前 ${item.small_body_lookback} 日平均实体 ${num(item.small_body_mean, 4)} 元`,
            );
        }
        if (item.wave_reached_stage) {
            const stageNames = { two_t: "二吐", five_top: "五顶", ten_full: "十满", ordinary_equal: "普通 A 的 C 等浪" };
            add(
                `目标背景：本笔正 N ${item.wave_n_date}；${item.wave_reached_date} 已到 ${stageNames[item.wave_reached_stage] || item.wave_reached_stage} ${num(item.wave_reached_price, 4)} 元`,
            );
            if (item.reason === "wave_gap_reversal_reduce")
                add(
                    `高开回落：开盘 ${num(item.observed_open, 4)} > 前高 ${num(item.previous_high, 4)}；阴线实体/开盘 ${pct(item.wave_body_fraction)}，振幅/前收 ${pct(item.wave_range_fraction)}；成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}；即使收盘高于前收也触发减仓`,
                );
            if (item.reason === "wave_abnormal_followthrough_clear")
                add(
                    `次日确认：${item.abnormal_date} 异常K线收盘 ${num(item.abnormal_close, 4)}；下一交易日收盘 ${num(item.observed_close, 4)} 严格低于该收盘，清空余仓，无需再次放量或等待倒 N`,
                );
            if (item.reason === "wave_ordinary_equal_upper_shadow_reduce")
                add(`普通 A：前 A 高 ${num(item.wave_a_high, 4)} 已达一饱 ${num(item.wave_one_p, 4)}、未达二吐 ${num(item.wave_two_t, 4)}；C 浪到等浪目标后，成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}，上影占振幅 ${pct(item.wave_upper_shadow_fraction)}，当日按累计 80% 目标减仓`);
            if (item.reason === "wave_ordinary_equal_lower_close_clear")
                add(`异常后首次收低：${item.abnormal_date} 出现长上影；本日收盘 ${num(item.observed_close, 4)} < 前收 ${num(item.previous_close, 4)}，当日清空余仓`);
            if (item.reason === "wave_upper_rejection_reduce")
                add(
                    `冲高收阴：上影不短于阴线实体；振幅/前收 ${pct(item.wave_range_fraction)}，上影占振幅 ${pct(item.wave_upper_shadow_fraction)}，下影占振幅 ${pct(item.wave_lower_shadow_fraction)}；成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}；不要求日涨跌幅为负`,
                );
            if (item.reason === "wave_volume_shadows_reduce")
                add(
                    `异常波动：振幅/前收 ${pct(item.wave_range_fraction)}，上影占振幅 ${pct(item.wave_upper_shadow_fraction)}，下影占振幅 ${pct(item.wave_lower_shadow_fraction)}；成交量 ${num(item.observed_volume, 0)} > 前日 ${num(item.previous_volume, 0)}`,
                );
            if (item.reason === "wave_bull_resistance_failed_clear")
                add(
                    `抵抗失败：${item.resistance_date} 多头抵抗虚拟低 ${num(item.resistance_virtual_low, 4)}；大阴线收盘 ${num(item.observed_close, 4)} 严格跌破，清空余仓，无需高开或再次放量`,
                );
            if (item.reason === "wave_bearish_engulf_clear")
                add(
                    `反包依据：开盘 ${num(item.observed_open, 4)} ≥ 前收 ${num(item.previous_close, 4)}，收盘 ${num(item.observed_close, 4)} ≤ 前开 ${num(item.previous_open, 4)}；大阴线反包无需再次放量`,
                );
        }
        if (item.reason === "trend_flip_resistance_adverse_clear") {
            const patterns = {
                bearish_body: "阴线实体",
                close_below_previous: "收盘低于前收",
                low_below_previous: "最低价跌破前低",
                long_upper_shadow: "长上影",
            };
            add(
                `趋势风险：${item.trend_level}级末跌高 ${item.trend_key_date} · ${num(item.trend_key_high, 4)} 元；${item.trend_attack_date} 收盘突破；空头抵抗：${(item.trend_resistance_dates || []).join("、")}`,
            );
            add(
                `清仓形态：${(item.trend_adverse_patterns || []).map((name) => patterns[name] || name).join("、")}；收盘 ${num(item.observed_close, 4)} / 前收 ${num(item.previous_close, 4)}；最低 ${num(item.observed_low, 4)} / 前低 ${num(item.previous_low, 4)}`,
            );
        }
        if (item.reason === "secondary_wave_target_resistance_clear") {
            add(
                `二级 C 浪：${item.trend_origin_date} 低 ${num(item.trend_origin_low, 4)} → ${item.trend_key_date} A 高 ${num(item.trend_key_high, 4)} → ${item.wave_b_date} B 低 ${num(item.wave_b_low, 4)}；等幅目标 ${num(item.wave_equal_target, 4)} 元`,
            );
            add(`突破与抵抗：${item.trend_attack_date} 盘中突破；空头抵抗 ${item.trend_resistance_dates.join("、")}`);
            add(
                `目标后转弱：${item.trend_indecision_date} 上下长影，上影 ${pct(item.trend_upper_shadow_fraction)}、下影 ${pct(item.trend_lower_shadow_fraction)}；本日收盘 ${num(item.observed_close, 4)} < 前日最低 ${num(item.trend_indecision_low, 4)}，当日清仓`,
            );
        }
        if (item.pressure_date) {
            const adverseNames = {
                close_below_previous: "收盘低于前收",
                low_below_previous: "跌破前日低点",
                long_upper_shadow: "长上影（占振幅至少 50%）",
            };
            add(
                `压力来源：${item.pressure_date} · 区间 ${num(item.pressure_low, 4)}–${num(item.pressure_high, 4)} 元；成交量为此前 20 日均量的 ${num(item.pressure_volume_multiple)} 倍`,
            );
            add(
                `正 N：${item.pressure_n_date}；清仓依据：${(item.pressure_adverse_patterns || []).map((key) => adverseNames[key] || key).join("、")}`,
            );
        }
        if (item.volume_trigger_date) {
            add(
                `放量下跌 ${item.volume_trigger_date}：成交量 ${num(item.trigger_volume, 0)} > 前日 ${num(item.previous_volume, 0)}；收盘 ${num(item.trigger_close, 4)} < 前收 ${num(item.previous_close, 4)}`,
            );
            if (item.volume_support_date)
                add(`冻结回踩低点：${item.volume_support_date} · ${num(item.volume_support_low, 4)} 元`);
        }
        if (item.reason === "volume_massive_gap_reversal_clear")
            add(`巨量高开反包：开盘 ${num(item.observed_open, 4)} > 前高 ${num(item.previous_high, 4)}，收盘 ${num(item.observed_close, 4)} < 前低 ${num(item.previous_low, 4)}；成交量 ${num(item.observed_volume, 0)} 股，为前 ${item.massive_volume_window} 日均量的 ${num(item.massive_volume_multiple, 2)} 倍且创同期新高；阴线实体/开盘 ${pct(item.bearish_body_fraction)}，当日清空余仓`);
        if (item.resistance_date) {
            add(
                `倒 N ${item.inverse_n_date} 后多头抵抗 ${item.resistance_date}：虚拟低 ${num(item.resistance_virtual_low, 4)}；失败收盘 ${num(item.failure_close, 4)}`,
            );
        }
        if (item.support_date) {
            add(`回踩参照 ${item.support_date}：最低 ${num(item.support_low, 4)}、收盘 ${num(item.support_close, 4)}`);
            add(
                `冻结下跌段 ${item.decline_high_date} 高 ${num(item.decline_high, 4)} → ${item.breakdown_date} 低 ${num(item.breakdown_low, 4)}；反弹最高价须突破 ${num(item.rebound_threshold, 4)}（2/3 位）`,
            );
        }
        if (item.position_closed === false) add(`本次为减仓；成交后仍持有 ${num(item.remaining_quantity)} 等价份额。`);
        add(
            `触发时：最低 ${num(item.observed_low)} · 最高 ${num(item.observed_high)} · 收盘 ${num(item.observed_close)} · 止损参考 ${num(item.stop)} · 目标 ${num(item.target)}`,
        );
        add("止损／目标触及时不假设在目标价成交；跳空、不可卖或流动性不足可能让退出延期。");
    }
}
