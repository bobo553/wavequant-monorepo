/** 与回测指标相同，以初始资金 1 和每日净值的历史峰值计算最大回撤。 */
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
