import assert from "node:assert/strict";
import test from "node:test";
import { chartMarketBars, preparationRetainsMarket } from "../public/chart-market-bars.js";

const bars = (...days) => days.map((time) => ({ time, open: 5, high: 6, low: 4, close: 5 }));
const previous = { symbol: "sh.601086", data_source: "akshare", timeframe: "1d",
    bars: bars("2018-01-02", "2018-10-11"),
    market_bars: bars("2018-01-02", "2019-03-07", "2026-10-14") };

test("latest-year preparation cannot replace the full market behind a short backtest prefix", () => {
    const page = { symbol: previous.symbol, timeframe: "1d", bars: bars("2026-01-29", "2026-10-14") };
    assert.equal(preparationRetainsMarket(previous, page, "akshare"), false);
    assert.equal(chartMarketBars(previous)[0].time, "2018-01-02");
});

test("preparation may extend the history but cannot truncate either edge", () => {
    const candidate = { symbol: previous.symbol, bars: bars("2018-01-02", "2026-10-14") };
    assert.equal(preparationRetainsMarket(previous, candidate, "akshare"), true);
    assert.equal(preparationRetainsMarket(previous, { ...candidate, bars: bars("2018-01-02", "2025-07-25") }, "akshare"), false);
    assert.equal(preparationRetainsMarket(previous, { ...candidate, bars: [] }, "akshare"), false);
    assert.equal(preparationRetainsMarket(previous, { ...candidate, symbol: "sh.600812" }, "akshare"), true);
    assert.equal(preparationRetainsMarket(previous, candidate, "tdx"), true);
});
