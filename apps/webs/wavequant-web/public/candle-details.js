import { num } from "./labels.js";

export function candleDetails(bar) {
    const details = [
        ["日期", String(bar.time)],
        ["开盘", `${num(bar.open)} 元`],
        ["最高", `${num(bar.high)} 元`],
        ["最低", `${num(bar.low)} 元`],
        ["收盘", `${num(bar.close)} 元`],
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

export function candleCopyText(bar, stockLabel = "") {
    return [
        ...(stockLabel ? [`股票：${stockLabel}`] : []),
        ...candleDetails(bar).map(([label, value]) => `${label}：${value}`),
    ].join("\n");
}
