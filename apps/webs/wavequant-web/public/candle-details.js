import { num, pct } from "./labels.js";

export function previousCandleClose(bars, bar) {
    const index = bars?.findIndex((candidate) => candidate.time === bar.time) ?? -1;
    return index > 0 ? bars[index - 1].close : undefined;
}

export function candleDetails(bar, previousClose) {
    const close = Number(bar.close);
    const priorClose = Number(previousClose);
    const change =
        previousClose != null && Number.isFinite(priorClose) && priorClose > 0 && Number.isFinite(close)
            ? (close - priorClose) / priorClose
            : null;
    const details = [
        ["日期", String(bar.time)],
        ["开盘", `${num(bar.open)} 元`],
        ["最高", `${num(bar.high)} 元`],
        ["最低", `${num(bar.low)} 元`],
        ["收盘", `${num(bar.close)} 元`],
        ["涨跌幅", change === null ? "—" : `${change > 0 ? "+" : ""}${pct(change)}`],
        ["成交量", `${num(bar.volume, 0)} 股`],
    ];
    if (
        bar.raw_close !== null &&
        bar.raw_close !== undefined &&
        bar.raw_close !== "" &&
        Number.isFinite(Number(bar.raw_close))
    ) {
        details.push(["原始收盘", `${num(bar.raw_close)} 元`]);
    }
    return details;
}

export function candleCopyText(bar, stockLabel = "", previousClose) {
    return [
        ...(stockLabel ? [`股票：${stockLabel}`] : []),
        ...candleDetails(bar, previousClose).map(([label, value]) => `${label}：${value}`),
    ].join("\n");
}
