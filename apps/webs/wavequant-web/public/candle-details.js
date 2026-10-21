import { num, pct } from "./labels.js";
import { volumeExpansion } from "./volume-expansion.js";

export function previousCandleClose(bars, bar) {
    const index = bars?.findIndex((candidate) => candidate.time === bar.time) ?? -1;
    return index > 0 ? bars[index - 1].close : undefined;
}

export function candleDetails(bar, previousClose, previousVolume, timeframe = "1d") {
    const close = Number(bar.close);
    const priorClose = Number(previousClose);
    const change =
        previousClose != null && Number.isFinite(priorClose) && priorClose > 0 && Number.isFinite(close)
            ? (close - priorClose) / priorClose
            : null;
    const expansion = timeframe === "1d" ? volumeExpansion(bar.volume, previousVolume) : null;
    const details = [
        ["日期", String(bar.time)],
        ["开盘", `${num(bar.open)} 元`],
        ["最高", `${num(bar.high)} 元`],
        ["最低", `${num(bar.low)} 元`],
        ["收盘", `${num(bar.close)} 元`],
        ["涨跌幅", change === null ? "—" : `${change > 0 ? "+" : ""}${pct(change)}`],
        [
            "成交量",
            typeof bar.volume === "number" && Number.isFinite(bar.volume) && bar.volume >= 0
                ? `${num(bar.volume / 10000)} 万股`
                : "—",
        ],
    ];
    if (
        bar.raw_close !== null &&
        bar.raw_close !== undefined &&
        bar.raw_close !== "" &&
        Number.isFinite(Number(bar.raw_close))
    ) {
        details.push(["原始收盘", `${num(bar.raw_close)} 元`]);
    }
    if (timeframe === "1d") {
        details.push(["较前日", expansion ? `${num(expansion.ratio)} 倍` : "—"]);
        details.push(["放量等级", expansion?.label ?? "—"]);
    }
    return details;
}

export function candleCopyText(bar, stockLabel = "", previousClose, previousVolume, timeframe = "1d") {
    return [
        ...(stockLabel ? [`股票：${stockLabel}`] : []),
        ...candleDetails(bar, previousClose, previousVolume, timeframe).map(([label, value]) => `${label}：${value}`),
    ].join("\n");
}
