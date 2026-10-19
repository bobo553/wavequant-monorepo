import { chartPositionTimeline, currentEligibilityRange } from "./chart-position-timeline.js";
import { eligibilityBoundaryRanges } from "./eligibility-boundary-overlay.js";

// 复制点击时的已知截面，不能把结构低点写成交易许可确认日。
export function eligibilityCopyText(view, name = view?.symbol) {
    const timeline = chartPositionTimeline(view);
    if (!timeline.ranges.length) return "";
    const level = view.backtest?.strategy?.buy_interval_level ?? 2;
    const boundaries = eligibilityBoundaryRanges(view);
    const days = (kind) => boundaries.filter((row) => row.kinds.includes(kind)).map((row) => row.start).join("、") || "暂无";
    return [
        `股票：${name}（${view.symbol}）`,
        `交易资格级别：${level}级趋势线`,
        `回测截至：${view.asof} · ${timeline.label}`,
        `已显示资格区间：${timeline.ranges.length} 段`,
        ...timeline.ranges.map((range, index) => `${index + 1}. ${range.start} 至 ${range.end}`),
        `资格确认日：${days("交易资格确认日")}`,
        `资格结束日：${days("交易资格结束日")}`,
        "区间与图中红色背景一致；结构起点不等于资格确认日。未结束区间只显示至当前回测日，不代表已结束。",
    ].join("\n");
}

export function currentEligibilityCopyText(view, position, name = view?.symbol) {
    const range = currentEligibilityRange(view, position);
    if (!range) return "";
    return [
        `股票：${name}（${view.symbol}）`,
        `交易资格级别：${view.backtest?.strategy?.buy_interval_level ?? 2}级趋势线`,
        `进度条当前位置：${range.day}`,
        `当前资格区间：${range.start} 至 ${range.end}`,
        `回测截至：${view.asof}`,
        "仅复制滑块所在的一段红色资格区间；未结束区间只显示至当前回测日。结构起点不等于资格确认日。",
    ].join("\n");
}
