export function parseBacktestSizing(capitalWan, buyPercent) {
    const capital = Number(capitalWan);
    const percent = Number(buyPercent);
    if (!String(capitalWan).trim() || !Number.isFinite(capital) || capital < 0.01 || capital > 100_000) {
        throw new RangeError("初始资金请输入 0.01～100000 万元");
    }
    if (!String(buyPercent).trim() || !Number.isFinite(percent) || percent <= 0 || percent > 100) {
        throw new RangeError("每次买入比例请输入大于 0 且不超过 100 的百分数");
    }
    return { initial_capital: capital * 10_000, max_position_weight: percent / 100 };
}
