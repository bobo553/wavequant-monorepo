import assert from "node:assert/strict";
import test from "node:test";

import { comparisonValues, ratioContextKey, ratioPlans } from "../public/ratio-comparison.js";

test("four explicit independent ratios and empty sample is not a zero win rate", () => {
    assert.equal(new Set(ratioPlans.map((p) => p[0])).size, 4);
    const view = {
        backtest: { counts: { long_signals: 4 } },
        metrics: { entry_fills: 0, trades: 0, win_rate: null, total_return: 0, max_drawdown: 0 },
    };
    assert.equal(comparisonValues(view)[3], "无平仓样本");
    view.metrics = { ...view.metrics, entry_fills: 2, trades: 2, win_rate: 0 };
    assert.notEqual(comparisonValues(view)[3], "无平仓样本");
    assert.match(comparisonValues(view)[3], /0/);
});
test("comparison invalidates source date cost symbol and start but not chosen ratio", () => {
    const p = { run: "r", symbol: "sh.600009", asof: "2026-09-07", start: "2018-01-01", scenario: "base", local: true };
    for (const field of ["run", "symbol", "asof", "start", "scenario", "local"])
        assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, [field]: "changed" }));
    assert.equal(ratioContextKey(p), ratioContextKey({ ...p, variant: "lecture_v3_d50_c50" }));
});
