import { reasonText } from "./annotations.js";
import { num } from "./labels.js";

// All conditions come from the dated engine ledger, never re-inferred from a chart.
export function appendTradeEvidence(panel, item) {
    const add = (text, cls = "") => {
        const p = document.createElement("p");
        p.textContent = text;
        p.className = cls;
        panel.append(p);
        return p;
    };
    const proof = item.decision_evidence?.find((e) => e.buy_point_type);
    if (proof) {
        add(`${proof.priority === 2 ? "第二类 · 重点" : "第一类"}买点 · ${proof.trend_level} 级趋势线`);
        add(
            `翻多高点 ${num(proof.flip_high_price)}；${proof.counter_filter_applied ? `回撤 ${num(proof.counter_ratio * 100)}% ${proof.counter_operator || "<"} ${num(proof.counter_limit * 100)}%` : "不启用回撤比例过滤，仍保留结构防守"}`,
        );
        if (proof.definition === "whole_flip_wave_v3")
            add(
                `整段锚点：L0 ${num(proof.ratio_low_price)} · ${proof.priority === 2 ? "H1" : "H0"} ${num(proof.ratio_high_price)} · ${proof.priority === 2 ? "回撤最低收盘" : "交替低点"} ${num(proof.counter_price)}。局部 N 回撤 ${num(proof.local_n_ratio * 100)}% 仅作对照，不用作本条门槛。`,
            );
        const d = (k) => proof[k + "_date"] || `第 ${proof[k] + 1} 根 K 线（可知日）`;
        add(
            `翻多 ${d("flip_index")} → 交替 ${d("alternation_index")}${proof.priority === 2 ? ` → 再破翻多高 ${d("maturity_index")} → 浅回撤 ${d("pullback_index")}` : ""} → 新 N ${d("attack")}`,
        );
    }
    if (item.kind !== "fill" && item.kind !== "order") return;
    add(
        `决定日 ${item.signal_time || "旧记录未提供"} → ${item.kind === "fill" ? "模拟成交日" : "委托评估日"} ${item.time}`,
        "decision-timeline",
    );
    add(
        `图表价 ${num(item.price, 4)} ÷ 当日因子 ${num(item.adjustment_factor, 6)} = 原始模拟价 ${num(item.raw_price, 4)} 元`,
    );
    add("B / S 为回测引擎的模拟成交，不是券商真实成交；日线条件收盘观察，后续可交易开盘执行。");
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
    } else {
        add(`退出原因：${reasonText(item.decision_reason || item.reason)}`);
        add(
            `触发时：最低 ${num(item.observed_low)} · 最高 ${num(item.observed_high)} · 收盘 ${num(item.observed_close)} · 止损参考 ${num(item.stop)} · 目标 ${num(item.target)}`,
        );
        add("止损／目标触及时不假设在目标价成交；跳空、不可卖或流动性不足可能让退出延期。");
    }
}
