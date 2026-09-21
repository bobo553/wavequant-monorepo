import assert from "node:assert/strict";
import { test } from "node:test";

import { candleCopyText, candleDetails, previousCandleClose } from "../public/candle-details.js";

test("copied K-line details include the stock name and both code forms", () => {
    const text = candleCopyText(
        {
            time: "2026-09-18",
            open: 100,
            high: 105,
            low: 99,
            close: 103,
            raw_close: 103,
            volume: 1200,
        },
        "600519 贵州茅台（sh.600519）",
    );

    assert.match(text, /^股票：600519 贵州茅台（sh\.600519）\n日期：2026-09-18\n/);
    assert.match(text, /收盘：103\.00 元/);
    assert.match(text, /成交量：1,200 股/);
});

test("K-line change uses the previous trading bar's close in the displayed price series", () => {
    const bars = [
        { time: "2025-05-09", close: 12.68 },
        { time: "2025-05-12", close: 13 },
        { time: "2025-05-15", close: 12.57, raw_close: 9.99 },
    ];
    const previousClose = previousCandleClose(bars, bars[2]);

    assert.equal(previousClose, 13);
    assert.deepEqual(
        candleDetails(bars[2], previousClose).find(([label]) => label === "涨跌幅"),
        ["涨跌幅", "-3.31%"],
    );
    assert.match(candleCopyText(bars[2], "", previousClose), /涨跌幅：-3\.31%/);
});

test("K-line change handles gains, flat closes, and missing previous bars", () => {
    const bar = { time: "2025-05-16", close: 12.99 };
    const change = (previousClose) => candleDetails(bar, previousClose).find(([label]) => label === "涨跌幅")[1];

    assert.equal(change(12.57), "+3.34%");
    assert.equal(change(12.99), "0.00%");
    assert.equal(change(0), "—");
    assert.equal(change(undefined), "—");
    assert.equal(previousCandleClose([bar], bar), undefined);
});
