import assert from "node:assert/strict";
import test from "node:test";

import { comparisonValues, ratioContextKey, ratioPlans } from "../public/ratio-comparison.js";

test("six explicit independent ratios and empty sample is not a zero win rate", () => {
    assert.equal(new Set(ratioPlans.map((p) => p[0])).size, 6);
    assert.ok(ratioPlans.some(([id, label]) => id === "lecture_v3_close_d50_c50" && label.includes("收盘 <1/2")));
    const view = {
        backtest: { counts: { long_signals: 4 } },
        metrics: { entry_fills: 0, trades: 0, win_rate: null, total_return: 0, max_drawdown: 0 },
    };
    assert.equal(comparisonValues(view)[3], "无平仓样本");
    view.metrics = { ...view.metrics, entry_fills: 2, trades: 2, win_rate: 0 };
    assert.notEqual(comparisonValues(view)[3], "无平仓样本");
    assert.match(comparisonValues(view)[3], /0/);
});
test("comparison invalidates source date cost symbol start and volume filter but not chosen ratio", () => {
    const p = {
        run: "r",
        symbol: "sh.600009",
        asof: "2026-09-07",
        start: "2018-01-01",
        scenario: "base",
        volume_filter: true,
        initial_capital: 100_000,
        max_position_weight: 0.5,
        local: true,
    };
    for (const field of ["run", "symbol", "asof", "start", "scenario", "local"])
        assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, [field]: "changed" }));
    assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, volume_filter: false }));
    assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, net_reward_risk_filter: true }));
    assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, initial_capital: 200_000 }));
    assert.notEqual(ratioContextKey(p), ratioContextKey({ ...p, max_position_weight: 0.25 }));
    assert.equal(ratioContextKey(p), ratioContextKey({ ...p, net_reward_risk_filter: false }));
    assert.equal(ratioContextKey(p), ratioContextKey({ ...p, variant: "lecture_v3_d50_c50" }));
});

test("default V3 uses inclusive third and half is independently selectable", () => {
    assert.match(ratioPlans.find(([id]) => id === "lecture_v3")[1], /≤1\/3/);
    assert.match(ratioPlans.find(([id]) => id === "lecture_v3_c50")[1], /<1\/2/);
});
