import { candleCopyText, previousCandleClose } from "./candle-details.js";
import { num, pct, symbolName } from "./labels.js";
import { closedPositionLabel, openPositionForMarker, positionProfit } from "./trade-position.js";
import { waveEntryEvidence } from "./wave-entry-evidence.js";
import { numberedTradeReasons } from "./trade-reasons.js";

export function formatFilledTradeCopy(view, marker, variantName, positionLabel, trade) {
    const lines = [
        `股票：${symbolName(view.symbol)}（${view.symbol}）`,
        `策略：${variantName}（${view.variant}）`,
        `回测区间：${view.backtest.start} 至 ${view.asof}`,
        `成交日期：${marker.time}`,
        ...(marker.execution_model === "intraday_5m_next_open"
            ? [`成交时间：${marker.execution_timestamp || marker.timestamp}`]
            : []),
        `方向：${marker.side === "BUY" ? "买入 B" : "卖出 S"}`,
        `${marker.side === "BUY" ? "买入" : "卖出"}原因：`,
        ...numberedTradeReasons(marker),
        `决策代码：${marker.decision_reason || marker.reason || "—"}`,
        `决定日期：${marker.signal_time || "—"}`,
        ...(marker.decision_timestamp ? [`决定时间：${marker.decision_timestamp}`] : []),
        `成交等价价：${num(marker.price)} 元`,
        `原始成交价：${num(marker.raw_price)} 元`,
        `成交数量：${num(marker.quantity)} 等价份额`,
        `费用：${num(marker.fee)} 元`,
    ];
    if (marker.side === "BUY") lines.push(`买入后仓位：${positionLabel}`);
    lines.push(...waveEntryEvidence(marker.decision_evidence));
    const reversal = marker.decision_evidence?.find((e) => e.squeeze_confirmation === "volume_reversal_record_break");
    if (reversal)
        lines.push(
            `放量反转轧空：N 收盘突破 ${reversal.n_close_break_date}（结构突破 ${reversal.attack_date}）；收盘 ${num(reversal.reversal_close, 4)} > 抵抗阶段高 ${num(reversal.reversal_record_high, 4)}，成交量 ${num(reversal.reversal_volume, 0)} > 前日 ${num(reversal.reversal_previous_volume, 0)}；最低 ${num(reversal.reversal_low, 4)} 守住 N 起点 ${num(reversal.reversal_structural_low, 4)}。`,
        );
    const pressure = marker.decision_evidence?.find((e) => e.secondary_resistance_resolved);
    if (pressure)
        lines.push(
            `二级压力复核：${pressure.secondary_high_date} 高点 ${num(pressure.secondary_high, 4)}；${pressure.secondary_attack_date} 再攻击后出现抵抗，${pressure.secondary_resolution_date} 收盘 ${num(pressure.secondary_confirmation_close, 4)} > 抵抗阶段高点 ${num(pressure.secondary_resistance_high, 4)}，抵抗解除。`,
        );
    if (marker.side === "BUY" && Number.isFinite(marker.net_reward_risk))
        lines.push(
            `费后盈亏比：${num(marker.net_reward_risk)}；门槛 ${num(marker.required_reward_risk)}；过滤${marker.net_reward_risk_filter === false ? "未启用（不据此拦截买入）" : marker.net_reward_risk_filter === true ? "已启用" : "状态未提供"}`,
        );
    const dual = marker.decision_evidence?.find((e) => e.buy_point_type === "multilevel_breakout_squeeze");
    if (dual)
        lines.push(
            `双重轧空：正 N ${dual.attack_date} 同时突破 ${dual.trend_level} 级波段高 ${dual.key_source_index_date}（${num(dual.key_price, 4)}）；${dual.higher_confirmation_index_date} 放量收盘 ${num(dual.confirmation_close, 4)} > 抵抗阶段高 ${num(dual.higher_resistance_high, 4)}，并守住虚拟防守。`,
        );
    const mature = marker.decision_evidence?.find((e) => e.buy_point_type === "mature_shallow_squeeze");
    if (mature)
        lines.push(
            `第二类背景：起涨 ${mature.origin_index_date} ${num(mature.ratio_low_price, 4)}，成熟确认 ${mature.maturity_index_date}；阶段高 ${mature.peak_index_date} ${num(mature.ratio_high_price, 4)}，最低收盘 ${mature.minimum_close_index_date} ${num(mature.counter_price, 4)}；整段收盘回撤 ${pct(mature.counter_ratio)} ${mature.counter_operator} ${pct(mature.counter_limit)}。`,
        );
    const gap = marker.decision_evidence?.find((e) => e.squeeze_confirmation === "defended_n_consolidation_gap");
    const gapContext = marker.decision_evidence?.find((e) => e.buy_point_type);
    if (gap && gapContext)
        lines.push(
            `${gapContext.trend_level} 级空多交替低点：${gapContext.alternation_low_index_date}；确认可知日：${gapContext.alternation_index_date}`,
        );
    if (gap)
        lines.push(
            `买点依据：正 N ${gap.consolidation_n_date}；整理守住防守低 ${num(gap.consolidation_defense, 4)}，跳空放量重新站上原 N 高点 ${num(gap.consolidation_high, 4)}；观察时最低 ${num(gap.gap_low, 4)} > 前日高 ${num(gap.gap_previous_high, 4)}，累计成交量 ${num(gap.gap_volume, 0)} > 前日 ${num(gap.gap_previous_volume, 0)}`,
        );
    const strongSqueeze = marker.decision_evidence?.find((e) => e.squeeze_confirmation === "uninterrupted_squeeze");
    if (strongSqueeze)
        lines.push(
            `买点依据：正 N ${strongSqueeze.attack_date}；连续上攻确认强轧空：逐根守住虚拟低，确认日最低 ${num(strongSqueeze.confirmation_low, 4)} ≥ 前根虚拟低 ${num(strongSqueeze.prior_virtual_low, 4)}，收盘 ${num(strongSqueeze.confirmation_close, 4)} > 前收 ${num(strongSqueeze.prior_close, 4)}`,
        );
    const record = marker.decision_evidence?.find((e) =>
        ["resistance_record_break", "fresh_n_defeats_old_n_resistance"].includes(e.squeeze_confirmation),
    );
    if (record)
        lines.push(
            `买点依据：正 N ${record.attack_date}；收盘 ${num(record.confirmation_close, 4)} > 本次 N 抵抗阶段高点 ${num(record.confirmation_record_high, 4)}，确认轧空。`,
        );
    const recovery = marker.decision_evidence?.find(
        (e) => e.inverse_reentry_path === "deep_alternation_kill_high_record_squeeze",
    );
    if (recovery)
        lines.push(
            `深回撤恢复：整段 ${recovery.origin_index_date} → ${recovery.flip_high_index_date}，${recovery.alternation_low_index_date} 回撤 ${pct(recovery.recovery_whole_retracement)}；守住回调低点，收复 ${recovery.recovery_inverse_date} 杀多高 ${num(recovery.recovery_kill_high, 4)}。`,
        );
    const squeeze = marker.decision_evidence?.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    if (squeeze?.n_level >= 2)
        lines.push(
            `正 N 级别：${squeeze.n_level}；A低 ${squeeze.n_origin_date} → B高 ${squeeze.n_neckline_date} → C低 ${squeeze.n_pullback_date}`,
        );
    if (squeeze)
        lines.push(
            `买点依据：正 N ${squeeze.attack_date}；抵抗 K ${squeeze.prior_bar_date}；该回不回确认：最低 ${num(squeeze.confirmation_low, 4)} ≥ 虚拟低 ${num(squeeze.prior_virtual_low, 4)}，收盘 ${num(squeeze.confirmation_close, 4)} > 前收 ${num(squeeze.prior_close, 4)}`,
        );
    if (marker.side === "SELL") lines.push(closedPositionLabel(marker));
    if (Number.isFinite(marker.exit_target_fraction)) {
        lines.push(`目标累计减仓：${pct(marker.exit_target_fraction)}（占首次减仓前该股票持仓）`);
    }
    if (marker.execution_model === "same_day_close") lines.push("成交口径：当日收盘价（日线回测，未还原尾盘分钟路径）");
    if (marker.fill_assumption === "nonflat_limit_close_without_queue_verification")
        lines.push("成交假设：非一字涨停按当日收盘价模拟成交，未验证涨停排队成交；成交价不另加正滑点。");
    if (marker.fill_assumption === "observed_nonflat_limit_intraday_without_queue_verification")
        lines.push(
            "成交假设：买点前已观察到非一字交易，按下一根分钟开盘价模拟成交；未验证涨停排队，成交价不另加正滑点。",
        );
    if (marker.minute_fallback)
        lines.push(
            marker.minute_fallback.reason === "minute_volume_incomplete"
                ? `日线成交原因：分钟成交量比同源日线少 ${num(marker.minute_fallback.coverage.missing_volume, 0)} 股，分钟路径不完整`
                : "日线成交原因：当日缺少完整同源分钟线",
        );
    if (marker.execution_model === "intraday_5m_next_open") {
        lines.push(`下一根五分钟开盘原价：${num(marker.minute_next_open_raw, 4)} 元`);
        lines.push("成交口径：已完成五分钟线判定，下一根五分钟线开盘价加回测滑点模拟成交；非券商成交回报");
    }
    if (Number.isFinite(marker.remaining_quantity)) {
        lines.push(`成交后剩余：${num(marker.remaining_quantity)} 等价份额`);
    }
    if (marker.positive_n_date) {
        lines.push(
            `小实体例外：正 N ${marker.positive_n_date} · K线范围 ${num(marker.positive_n_low, 4)}–${num(marker.positive_n_high, 4)}；实体 ${pct(marker.small_body_fraction)}，上限 ${pct(marker.small_body_cap)}；前 ${marker.small_body_lookback} 日平均实体 ${num(marker.small_body_mean, 4)} 元`,
        );
    }
    if (marker.wave_reached_stage) {
        const stageNames = { two_t: "二吐", five_top: "五顶", ten_full: "十满", ordinary_equal: "普通 A 的 C 等浪" };
        lines.push(
            `目标背景：本笔正 N ${marker.wave_n_date}；${marker.wave_reached_date} 已到 ${stageNames[marker.wave_reached_stage] || marker.wave_reached_stage} ${num(marker.wave_reached_price, 4)} 元`,
        );
        if (marker.reason === "wave_gap_reversal_reduce")
            lines.push(
                `高开回落：开盘 ${num(marker.observed_open, 4)} > 前高 ${num(marker.previous_high, 4)}；阴线实体/开盘 ${pct(marker.wave_body_fraction)}，振幅/前收 ${pct(marker.wave_range_fraction)}；成交量 ${num(marker.observed_volume, 0)} > 前日 ${num(marker.previous_volume, 0)}；即使收盘高于前收也触发减仓`,
            );
        if (marker.reason === "wave_abnormal_followthrough_clear")
            lines.push(
                `次日确认：${marker.abnormal_date} 异常K线收盘 ${num(marker.abnormal_close, 4)}；下一交易日收盘 ${num(marker.observed_close, 4)} 严格低于该收盘，清空余仓，无需再次放量或等待倒 N`,
            );
        if (marker.reason === "wave_ordinary_equal_upper_shadow_reduce")
            lines.push(`普通 A：前 A 高 ${num(marker.wave_a_high, 4)} 已达一饱 ${num(marker.wave_one_p, 4)}、未达二吐 ${num(marker.wave_two_t, 4)}；C 浪到等浪目标后，成交量 ${num(marker.observed_volume, 0)} > 前日 ${num(marker.previous_volume, 0)}，上影占振幅 ${pct(marker.wave_upper_shadow_fraction)}，当日按累计 80% 目标减仓`);
        if (marker.reason === "wave_ordinary_equal_lower_close_clear")
            lines.push(`异常后首次收低：${marker.abnormal_date} 出现长上影；本日收盘 ${num(marker.observed_close, 4)} < 前收 ${num(marker.previous_close, 4)}，当日清空余仓`);
        if (marker.reason === "wave_upper_rejection_reduce")
            lines.push(
                `冲高收阴：上影不短于阴线实体；振幅/前收 ${pct(marker.wave_range_fraction)}，上影占振幅 ${pct(marker.wave_upper_shadow_fraction)}，下影占振幅 ${pct(marker.wave_lower_shadow_fraction)}；成交量 ${num(marker.observed_volume, 0)} > 前日 ${num(marker.previous_volume, 0)}；不要求日涨跌幅为负`,
            );
        if (marker.reason === "wave_volume_shadows_reduce")
            lines.push(
                `异常波动：振幅/前收 ${pct(marker.wave_range_fraction)}，上影占振幅 ${pct(marker.wave_upper_shadow_fraction)}，下影占振幅 ${pct(marker.wave_lower_shadow_fraction)}；成交量 ${num(marker.observed_volume, 0)} > 前日 ${num(marker.previous_volume, 0)}`,
            );
        if (marker.reason === "wave_bull_resistance_failed_clear")
            lines.push(
                `抵抗失败：${marker.resistance_date} 多头抵抗虚拟低 ${num(marker.resistance_virtual_low, 4)}；大阴线收盘 ${num(marker.observed_close, 4)} 严格跌破，清空余仓，无需高开或再次放量`,
            );
        if (marker.reason === "wave_bearish_engulf_clear")
            lines.push(
                `反包依据：开盘 ${num(marker.observed_open, 4)} ≥ 前收 ${num(marker.previous_close, 4)}，收盘 ${num(marker.observed_close, 4)} ≤ 前开 ${num(marker.previous_open, 4)}；大阴线反包无需再次放量`,
            );
    }
    if (marker.reason === "trend_flip_resistance_adverse_clear") {
        const patterns = {
            bearish_body: "阴线实体",
            close_below_previous: "收盘低于前收",
            low_below_previous: "最低价跌破前低",
            long_upper_shadow: "长上影",
        };
        lines.push(
            `趋势风险：${marker.trend_level}级末跌高 ${marker.trend_key_date} · ${num(marker.trend_key_high, 4)} 元；${marker.trend_attack_date} 收盘突破；空头抵抗：${(marker.trend_resistance_dates || []).join("、")}`,
        );
        lines.push(
            `清仓形态：${(marker.trend_adverse_patterns || []).map((name) => patterns[name] || name).join("、")}；收盘 ${num(marker.observed_close, 4)} / 前收 ${num(marker.previous_close, 4)}；最低 ${num(marker.observed_low, 4)} / 前低 ${num(marker.previous_low, 4)}`,
        );
    }
    if (marker.pressure_date) {
        const adverseNames = {
            close_below_previous: "收盘低于前收",
            low_below_previous: "跌破前日低点",
            long_upper_shadow: "长上影（占振幅至少 50%）",
        };
        lines.push(
            `压力来源：${marker.pressure_date} · 区间 ${num(marker.pressure_low, 4)}–${num(marker.pressure_high, 4)} 元；成交量为此前 20 日均量的 ${num(marker.pressure_volume_multiple)} 倍`,
        );
        lines.push(
            `正 N：${marker.pressure_n_date}；清仓依据：${(marker.pressure_adverse_patterns || []).map((key) => adverseNames[key] || key).join("、")}`,
        );
    }
    if (marker.volume_trigger_date) {
        lines.push(
            `放量下跌 ${marker.volume_trigger_date}：成交量 ${num(marker.trigger_volume, 0)} > 前日 ${num(marker.previous_volume, 0)}；收盘 ${num(marker.trigger_close, 4)} < 前收 ${num(marker.previous_close, 4)}`,
        );
        if (marker.volume_support_date)
            lines.push(`冻结回踩低点：${marker.volume_support_date} · ${num(marker.volume_support_low, 4)} 元`);
    }
    if (marker.reason === "volume_massive_gap_reversal_clear")
        lines.push(`巨量高开反包：开盘 ${num(marker.observed_open, 4)} > 前高 ${num(marker.previous_high, 4)}，收盘 ${num(marker.observed_close, 4)} < 前低 ${num(marker.previous_low, 4)}；成交量 ${num(marker.observed_volume, 0)} 股，为前 ${marker.massive_volume_window} 日均量的 ${num(marker.massive_volume_multiple, 2)} 倍且创同期新高；阴线实体/开盘 ${pct(marker.bearish_body_fraction)}，当日清空余仓`);
    if (marker.resistance_date) {
        lines.push(
            `倒 N 日期：${marker.inverse_n_date}`,
            `多头抵抗 K：${marker.resistance_date} · 虚拟低 ${num(marker.resistance_virtual_low, 4)} 元`,
            `失败收盘：${num(marker.failure_close, 4)} 元`,
        );
    }
    if (marker.support_date) {
        lines.push(
            `前回踩 K 线：${marker.support_date} · 最低 ${num(marker.support_low, 4)} · 收盘 ${num(marker.support_close, 4)}`,
            `下跌段：${marker.decline_high_date} 高 ${num(marker.decline_high, 4)} → ${marker.breakdown_date} 低 ${num(marker.breakdown_low, 4)}`,
            `反弹 2/3 位：${num(marker.rebound_threshold, 4)} 元（按最高价判断）`,
        );
    }
    const profit = positionProfit(marker, trade, openPositionForMarker(view, marker));
    if (profit) lines.push(profit.text);
    lines.push(`事件 ID：${marker.id}`);
    const bar = view.bars.find((item) => item.time === marker.time);
    if (bar) lines.push("", "成交日 K 线：", candleCopyText(bar, "", previousCandleClose(view.bars, bar)));
    return lines.join("\n");
}
