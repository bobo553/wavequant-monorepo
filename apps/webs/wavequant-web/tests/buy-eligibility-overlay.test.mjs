import assert from "node:assert/strict";
import test from "node:test";
import { buyEligibilityRanges } from "../public/buy-eligibility-overlay.js";

test("confirmed structure starts at alternation low and includes the reversal-low breach endpoint", () => {
    const dates = ["2019-10-31", "2019-12-31", "2020-09-17", "2020-10-23", "2020-10-26", "2023-09-13"];
    const view = {
        symbol: "sh.600812", timeframe: "1d", asof: dates.at(-1),
        bars: dates.map((time) => ({ time })), backtest: { strategy: { buy_interval_level: 2 } },
        audit: [
            { event: "trading_interval_opened", timestamp: dates[1], bar_index: 1,
                start_low_time: dates[0], trend_level: 2, source_path: "p" },
            { event: "trading_interval_closed", timestamp: dates[4], start_index: 1,
                trend_level: 2, source_path: "p", display_end_inclusive: true },
        ],
    };
    assert.deepEqual(buyEligibilityRanges(view), [{ from: 0, to: 4, start: dates[0], end: dates[4] }]);
    assert.deepEqual(buyEligibilityRanges(view, dates[0]), []);
    assert.deepEqual(buyEligibilityRanges(view, dates[3]), [{ from: 0, to: 3, start: dates[0], end: dates[3] }]);
    const old = { ...view, audit: view.audit.map(({ start_low_time, display_end_inclusive, ...row }) => row) };
    assert.deepEqual(buyEligibilityRanges(old), [{ from: 1, to: 3, start: dates[1], end: dates[3] }]);
});
