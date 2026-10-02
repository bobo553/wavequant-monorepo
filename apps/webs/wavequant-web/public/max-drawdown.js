import { num, pct } from "./labels.js";

export const holdingDrawdownVersion = "holding_entry_cost_mae_v1";

/** 当前回测的买入至卖出最大亏损；旧账户回撤不可作为替代值。 */
export function holdingDrawdownValue(metrics) {
    return metrics?.holding_drawdown_version === holdingDrawdownVersion && Number.isFinite(metrics.holding_max_drawdown)
        ? metrics.holding_max_drawdown
        : null;
}

export function holdingDrawdownText(metrics) {
    const value = holdingDrawdownValue(metrics);
    return value === null ? "—" : pct(value);
}

export function holdingDrawdownNote(metrics) {
    if (metrics?.holding_drawdown_version !== holdingDrawdownVersion) return "旧结果未计算持仓最大亏损，请重新回测";
    if (metrics.holding_drawdown_status === "no_entry_fills") return "无买入成交，暂无持仓最大亏损";
    if (metrics.holding_drawdown_status === "incomplete") return "盘中成交边界缺少完整分钟线，最大亏损尚无法确认";
    return "买入至清仓的最大浮亏；加仓按当时均价；未清仓算至回放截止；不含费用";
}

export function holdingDrawdownInterval(view) {
    const interval = view?.metrics?.holding_drawdown_interval;
    if (
        view?.metrics?.holding_drawdown_version !== holdingDrawdownVersion ||
        !interval ||
        interval.symbol !== view.symbol ||
        !Number.isFinite(interval.observed_max_drawdown) ||
        !interval.entry_time ||
        !interval.asof
    )
        return null;
    return { ...interval, from: interval.entry_time.slice(0, 10), to: interval.asof.slice(0, 10) };
}

export function holdingDrawdownForMarker(view, marker) {
    if (marker?.kind !== "fill" || view?.metrics?.holding_drawdown_version !== holdingDrawdownVersion) return null;
    return (
        view.backtest?.holding_drawdowns?.find(
            (episode) =>
                episode.symbol === view.symbol &&
                (marker.trade_id
                    ? episode.trade_id === marker.trade_id
                    : marker.side === "BUY" && episode.entry_order_time === marker.timestamp),
        ) || null
    );
}

export function holdingDrawdownLines(episode) {
    if (!episode || episode.metric_version !== holdingDrawdownVersion) return [];
    const period = `${episode.entry_time} → ${episode.exit_time || `${episode.asof}（未清仓）`}`;
    const incomplete = episode.coverage !== "complete";
    return [
        `整笔持仓最大亏损${incomplete ? "（已观察下界，数据不完整）" : ""}：${pct(episode.observed_max_drawdown)}；买入至卖出 ${period}`,
        `成本 ${num(episode.cost_price, 4)} 元 → ${episode.low_time} 最低 ${num(episode.low_price, 4)} 元；该低点浮亏 ${num(episode.loss_amount)} 元（复权等价口径，不含费用）；截至 ${episode.asof}`,
    ];
}

/** 账户净值历史峰值回撤，保留给账户分析，不用于持仓最大亏损。 */
export function maxDrawdownInterval(curve, backtestStart) {
    if (!Array.isArray(curve) || !curve.length) return null;
    let peak = 1;
    let peakIndex = -1;
    let fromIndex = -1;
    let toIndex = -1;
    let drawdown = 0;
    for (const [index, point] of curve.entries()) {
        if (typeof point?.time !== "string" || !Number.isFinite(point.value) || point.value <= 0) return null;
        if (point.value >= peak) {
            peak = point.value;
            peakIndex = index;
        }
        const current = point.value / peak - 1;
        if (current < drawdown) {
            drawdown = current;
            fromIndex = peakIndex;
            toIndex = index;
        }
    }
    if (toIndex < 0) return null;
    return {
        from: fromIndex < 0 ? backtestStart || curve[0].time : curve[fromIndex].time,
        to: curve[toIndex].time,
        initialPeak: fromIndex < 0,
        drawdown,
    };
}

export function drawdownCandleRange(interval, bars) {
    if (!interval || !Array.isArray(bars) || !bars.length) return null;
    const fromIndex = bars.findIndex((bar) => bar.time >= interval.from);
    const toIndex = bars.findLastIndex((bar) => bar.time <= interval.to);
    if (fromIndex < 0 || toIndex < fromIndex) return null;
    const padding = Math.max(3, Math.ceil((toIndex - fromIndex + 1) * 0.1));
    return {
        from: Math.max(0, fromIndex - padding),
        to: Math.min(bars.length + 3, toIndex + padding),
    };
}
