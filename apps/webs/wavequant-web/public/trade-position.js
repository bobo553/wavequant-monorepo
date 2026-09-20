import { pct } from "./labels.js";

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
