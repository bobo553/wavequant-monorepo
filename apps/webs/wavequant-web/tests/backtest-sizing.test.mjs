import assert from "node:assert/strict";
import test from "node:test";

import { parseBacktestSizing } from "../public/backtest-sizing.js";

test("backtest sizing converts ten thousand yuan and percent into engine units", () => {
    assert.deepEqual(parseBacktestSizing("10", "50"), { initial_capital: 100_000, max_position_weight: 0.5 });
    assert.deepEqual(parseBacktestSizing("12.5", "25"), { initial_capital: 125_000, max_position_weight: 0.25 });
});

test("backtest sizing rejects empty, non-finite and out-of-range values", () => {
    for (const capital of ["", "0", "-1", "Infinity", "100001", "abc"])
        assert.throws(() => parseBacktestSizing(capital, "50"), RangeError);
    for (const percent of ["", "0", "-1", "101", "NaN"])
        assert.throws(() => parseBacktestSizing("10", percent), RangeError);
});
