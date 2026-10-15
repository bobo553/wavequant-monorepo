// 仅供行情绘制、光标及导航使用；结构、资格和策略读取 view.bars 的已完成前缀。
export function chartMarketBars(view) {
    return view?.market_bars ?? view?.bars ?? [];
}

// 准备阶段的年度分页只能扩充已显示行情，不能用最新一年替换完整历史。
export function preparationRetainsMarket(previous, candidate, source) {
    const before = chartMarketBars(previous);
    const after = chartMarketBars(candidate);
    if (!before.length || previous?.symbol !== candidate?.symbol
        || (previous.timeframe || "1d") !== (candidate.timeframe || "1d")
        || (previous.data_source || previous.result_scope) !== source) return true;
    return after.length > 0 && after[0].time <= before[0].time && after.at(-1).time >= before.at(-1).time;
}
