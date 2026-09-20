import assert from "node:assert/strict";
import { test } from "node:test";

import { candleCopyText } from "../public/candle-details.js";

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
