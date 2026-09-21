import { reasonText } from "./annotations.js";
import { num, pct } from "./labels.js";
import { closedPositionLabel, positionProfit } from "./trade-position.js";

// All conditions come from the dated engine ledger, never re-inferred from a chart.
export function appendTradeEvidence(panel, item, openPosition = null) {
    const add = (text, cls = "") => {
        const p = document.createElement("p");
        p.textContent = text;
        p.className = cls;
        panel.append(p);
        return p;
    };
    const proof = item.decision_evidence?.find((e) => e.buy_point_type);
    const squeeze = item.decision_evidence?.find((e) => e.squeeze_confirmation === "local_resistance_failure");
    if (squeeze?.n_level >= 2) add(`正 N 级别 ${squeeze.n_level}：A低 ${squeeze.n_origin_date} → B高 ${squeeze.n_neckline_date} → C低 ${squeeze.n_pullback_date}`);
    if (squeeze) add(`正 N ${squeeze.attack_date} → 抵抗 K ${squeeze.prior_bar_date} → 该回不回：确认日最低 ${num(squeeze.confirmation_low, 4)} 守住虚拟低 ${num(squeeze.prior_virtual_low, 4)}，收盘 ${num(squeeze.confirmation_close, 4)} 高于前收 ${num(squeeze.prior_close, 4)}。`);
    if (proof) {
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
            `翻多 ${d("flip_index")} → 交替 ${d("alternation_index")}${proof.priority === 2 ? ` → 再破翻多高 ${d("maturity_index")} → 浅回撤 ${d("pullback_index")}` : ""} → 新 N ${d("attack")}`,
        );
    }
    if (item.kind !== "fill" && item.kind !== "order") return;
    add(
        `决定 ${item.decision_timestamp || item.signal_time || "旧记录未提供"} → ${item.kind === "fill" ? "模拟成交" : "委托评估"} ${item.timestamp || item.time}`,
        "decision-timeline",
    );
    add(
        `图表价 ${num(item.price, 4)} ÷ 当日因子 ${num(item.adjustment_factor, 6)} = 原始模拟价 ${num(item.raw_price, 4)} 元`,
    );
    add(item.execution_model === "intraday_5m_next_open"
        ? `已完成五分钟 K 线判定，下一根五分钟线开盘原价 ${num(item.minute_next_open_raw, 4)} 元，计入回测滑点后模拟成交；非逐笔成交或券商回报。`
        : item.execution_model === "same_day_close"
        ? "本笔按触发当日收盘价计算回测成交，未还原尾盘分钟路径，不是券商成交回报。"
        : "B / S 为回测引擎的模拟成交，不是券商真实成交；本笔条件收盘观察，后续可交易开盘执行。");
    if (item.minute_fallback) add("当日缺少完整同源分钟线，已按日线收盘价模拟成交。");
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
        add(`退出原因：${reasonText(item.decision_reason || item.reason)}`);
        if (item.kind === "fill") add(closedPositionLabel(item));
        const profit = positionProfit(item);
        if (profit) add(profit.text);
        if (item.positive_n_date) {
            add(`小实体例外：正 N ${item.positive_n_date} · K线范围 ${num(item.positive_n_low, 4)}–${num(item.positive_n_high, 4)}；实体 ${pct(item.small_body_fraction)}，上限 ${pct(item.small_body_cap)}；前 ${item.small_body_lookback} 日平均实体 ${num(item.small_body_mean, 4)} 元`);
        }
        if (item.volume_trigger_date) {
            add(`放量下跌 ${item.volume_trigger_date}：成交量 ${num(item.trigger_volume, 0)} > 前日 ${num(item.previous_volume, 0)}；收盘 ${num(item.trigger_close, 4)} < 前收 ${num(item.previous_close, 4)}`);
            if (item.volume_support_date) add(`冻结回踩低点：${item.volume_support_date} · ${num(item.volume_support_low, 4)} 元`);
        }
        if (item.resistance_date) {
            add(`倒 N ${item.inverse_n_date} 后多头抵抗 ${item.resistance_date}：虚拟低 ${num(item.resistance_virtual_low, 4)}；失败收盘 ${num(item.failure_close, 4)}`);
        }
        if (item.support_date) {
            add(`回踩参照 ${item.support_date}：最低 ${num(item.support_low, 4)}、收盘 ${num(item.support_close, 4)}`);
            add(`冻结下跌段 ${item.decline_high_date} 高 ${num(item.decline_high, 4)} → ${item.breakdown_date} 低 ${num(item.breakdown_low, 4)}；反弹最高价须突破 ${num(item.rebound_threshold, 4)}（2/3 位）`);
        }
        if (item.position_closed === false) add(`本次为减仓；成交后仍持有 ${num(item.remaining_quantity)} 等价份额。`);
        add(
            `触发时：最低 ${num(item.observed_low)} · 最高 ${num(item.observed_high)} · 收盘 ${num(item.observed_close)} · 止损参考 ${num(item.stop)} · 目标 ${num(item.target)}`,
        );
        add("止损／目标触及时不假设在目标价成交；跳空、不可卖或流动性不足可能让退出延期。");
    }
}
