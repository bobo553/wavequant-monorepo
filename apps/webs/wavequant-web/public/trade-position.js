import { num, pct } from "./labels.js";

export function openPositionForMarker(view, marker) {
    if (marker?.kind !== "fill" || marker.side !== "BUY") return null;
    const entryTime = marker.timestamp || marker.time;
    return view?.backtest?.open_positions?.find((position) => {
        if (position.symbol !== marker.symbol) return false;
        if (position.trade_id && marker.trade_id) return position.trade_id === marker.trade_id;
        if (!position.entry_time || !entryTime) return false;
        return position.entry_time.includes("T") && entryTime.includes("T")
            ? position.entry_time === entryTime
            : position.entry_time.slice(0, 10) === entryTime.slice(0, 10);
    }) || null;
}

export function openPositionProfit(position) {
    if (!position || !Number.isFinite(position.total_pnl) || !Number.isFinite(position.net_return)) return null;
    const markDate = position.mark_time?.slice(0, 10) || "回放日";
    return {
        pnl: position.total_pnl,
        text: `截至 ${markDate} 未卖出 · 已实现盈亏：${num(position.realized_pnl)} 元 + 未实现盈亏：${num(position.unrealized_pnl)} 元 = 整笔盈亏：${num(position.total_pnl)} 元 · 整笔收益率：${pct(position.net_return)}（以买入含费成本为基数；未扣除未来卖出费用）`,
    };
}

export function positionProfit(marker, trade, openPosition = null) {
    if (marker.side === "BUY" && marker.kind === "fill") return openPositionProfit(openPosition);
    if (marker.side !== "SELL" || marker.kind !== "fill") return null;
    const closed = marker.position_closed !== false;
    const pnl = marker.position_pnl ?? (closed ? trade?.pnl : null);
    const netReturn = marker.position_net_return ?? (closed ? trade?.net_return : null);
    if (!Number.isFinite(pnl) || !Number.isFinite(netReturn)) return null;
    return {
        pnl,
        text: `${closed ? "整笔清仓盈亏" : "累计已实现盈亏"}：${num(pnl)} 元 · ${closed ? "整笔净收益率" : "累计已实现收益率"}：${pct(netReturn)}（以整笔买入含费成本为基数${closed ? "" : "，尚未清仓"}）`,
    };
}

export function closedPositionFraction(marker) {
    if (marker.side !== "SELL" || marker.kind !== "fill") return null;
    const sold = marker.quantity;
    const remaining = marker.remaining_quantity ?? (marker.position_closed === true ? 0 : null);
    if (!Number.isFinite(sold) || sold <= 0 || !Number.isFinite(remaining) || remaining < 0) return null;
    const before = sold + remaining;
    return Number.isFinite(before) ? sold / before : null;
}

export function closedPositionLabel(marker) {
    const fraction = closedPositionFraction(marker);
    return fraction === null ? "平仓比例：—（缺少成交前持仓数据）" : `平仓比例：${pct(fraction)}（占卖出前该股票持仓）`;
}
