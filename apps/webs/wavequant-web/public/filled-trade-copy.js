import { reasonText } from "./annotations.js";
import { candleCopyText } from "./candle-details.js";
import { num, pct, symbolName } from "./labels.js";
import { closedPositionLabel } from "./trade-position.js";

export function formatFilledTradeCopy(view, marker, variantName, positionLabel, trade) {
    const lines = [
        `股票：${symbolName(view.symbol)}（${view.symbol}）`,
        `策略：${variantName}（${view.variant}）`,
        `回测区间：${view.backtest.start} 至 ${view.asof}`,
        `成交日期：${marker.time}`,
        ...(marker.execution_model === "intraday_5m_next_open" ? [`成交时间：${marker.timestamp}`] : []),
        `方向：${marker.side === "BUY" ? "买入 B" : "卖出 S"}`,
        `原因：${reasonText(marker.reason)}（${marker.reason}）`,
        `决定日期：${marker.signal_time || "—"}`,
        ...(marker.decision_timestamp ? [`决定时间：${marker.decision_timestamp}`] : []),
        `成交等价价：${num(marker.price)} 元`,
        `原始成交价：${num(marker.raw_price)} 元`,
        `成交数量：${num(marker.quantity)} 等价份额`,
        `费用：${num(marker.fee)} 元`,
    ];
    if (marker.side === "BUY") lines.push(`买入后仓位：${positionLabel}`);
    if (marker.side === "SELL") lines.push(closedPositionLabel(marker));
    if (Number.isFinite(marker.exit_target_fraction)) {
        lines.push(`目标累计减仓：${pct(marker.exit_target_fraction)}（占首次减仓前该股票持仓）`);
    }
    if (marker.execution_model === "same_day_close") lines.push("成交口径：当日收盘价（日线回测，未还原尾盘分钟路径）");
    if (marker.minute_fallback) lines.push("日线成交原因：当日缺少完整同源分钟线");
    if (marker.execution_model === "intraday_5m_next_open") {
        lines.push(`下一根五分钟开盘原价：${num(marker.minute_next_open_raw, 4)} 元`);
        lines.push("成交口径：已完成五分钟线判定，下一根五分钟线开盘价加回测滑点模拟成交；非券商成交回报");
    }
    if (Number.isFinite(marker.remaining_quantity)) {
        lines.push(`成交后剩余：${num(marker.remaining_quantity)} 等价份额`);
    }
    if (marker.support_date) {
        lines.push(
            `前回踩 K 线：${marker.support_date} · 最低 ${num(marker.support_low, 4)} · 收盘 ${num(marker.support_close, 4)}`,
            `下跌段：${marker.decline_high_date} 高 ${num(marker.decline_high, 4)} → ${marker.breakdown_date} 低 ${num(marker.breakdown_low, 4)}`,
            `反弹 2/3 位：${num(marker.rebound_threshold, 4)} 元（按最高价判断）`,
        );
    }
    if (trade && Number.isFinite(trade.pnl)) {
        lines.push(`本次已平仓盈亏：${num(trade.pnl)} 元`, `净收益：${pct(trade.net_return)}`);
    }
    lines.push(`事件 ID：${marker.id}`);
    const bar = view.bars.find((item) => item.time === marker.time);
    if (bar) lines.push("", "成交日 K 线：", candleCopyText(bar));
    return lines.join("\n");
}
