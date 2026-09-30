import { reasonText } from "./annotations.js";
import { num, symbolName } from "./labels.js";

export function blockedTradeNodes(view) {
    const orders = view.markers
        .filter((marker) => marker.kind === "order" && marker.status === "cancelled")
        .map((marker) => ({ ...marker, blockedStage: "execution" }));
    // 策略筛选发生在委托之前；保留理论响应中的稳定事件 ID，供图表定位使用。
    const candidates = (view.theory?.events || [])
        .filter((event) => event.event === "entry_rejected" || event.event === "entry_preflight_rejected")
        .map((event) => ({
            ...event,
            time: event.available_at,
            sourceTime: event.time,
            kind: "candidate",
            blockedStage: "screening",
        }));
    return [...orders, ...candidates].sort((a, b) => b.time.localeCompare(a.time) || a.id.localeCompare(b.id));
}

export function groupBlockedTradeNodes(nodes) {
    const byDate = new Map();
    for (const node of nodes) {
        // API 当前返回交易日；兼容带时分秒的时间戳，避免同一天拆成多张卡。
        const date = String(node.time).slice(0, 10);
        if (!byDate.has(date)) byDate.set(date, { time: date, nodes: [] });
        byDate.get(date).nodes.push(node);
    }
    return [...byDate.values()].map((group) => {
        // 执行层取消优先作为日期卡的定位点；其它原始事件仍可在卡内逐条选择。
        group.nodes.sort(
            (a, b) =>
                (a.blockedStage === "execution" ? -1 : 0) - (b.blockedStage === "execution" ? -1 : 0) ||
                a.id.localeCompare(b.id),
        );
        group.primary = group.nodes[0];
        return group;
    });
}

export function blockedNodeMeta(marker, view) {
    const candidate = marker.blockedStage === "screening";
    const context = candidate
        ? `判定 ${marker.sourceTime || marker.time}${Number.isInteger(marker.attack) && view.bars[marker.attack] ? ` · N 字攻击 ${view.bars[marker.attack].time}` : ""}`
        : `决定 ${marker.signal_time || "—"}${Number.isFinite(marker.raw_price) ? ` · 原价 ${num(marker.raw_price)} 元` : ""}`;
    const comparison =
        candidate && Number.isFinite(marker.gross_reward_risk)
            ? ` · 收盘收益风险比 ${num(marker.gross_reward_risk)}，要求至少 ${num(marker.required_reward_risk)}`
            : Number.isFinite(marker.risk_budget) && Number.isFinite(marker.one_lot_price_risk)
              ? ` · 风险预算 ${num(marker.risk_budget)} 元 < 一手预估风险 ${num(marker.one_lot_price_risk)} 元`
              : "";
    return context + comparison;
}

/** 复制原始事件而非日期卡摘要，避免同日不同原因或价格被合并后丢失。 */
export function formatBlockedTradeCopy(view, groups, variantName) {
    const lines = [
        `股票：${symbolName(view.symbol)}（${view.symbol}）`,
        `策略：${variantName}`,
        `回测区间：${view.backtest.start} 至 ${view.asof}`,
        `被拦截：${groups.length} 日 · ${groups.reduce((count, group) => count + group.nodes.length, 0)} 次`,
    ];
    for (const group of groups) {
        lines.push("", `${group.time} · ${group.nodes.length} 次`);
        for (const [index, node] of group.nodes.entries()) {
            const candidate = node.blockedStage === "screening";
            lines.push(
                `${index + 1}. ${candidate ? "策略候选未下单" : "执行委托未成交"} · ${candidate ? "收盘参考价" : "拟价"} ${num(node.price)} 元`,
                `   拦截原因：${reasonText(node.reason)}`,
                `   ${blockedNodeMeta(node, view)}`,
                `   事件 ID：${node.id}`,
            );
        }
    }
    return lines.join("\n");
}
