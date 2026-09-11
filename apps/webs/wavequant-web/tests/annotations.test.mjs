import assert from "node:assert/strict";
import test from "node:test";

import {
    avoidLabelCollisions,
    buildAnnotations,
    markerGroups,
    reasonText,
    reversalWindowSummary,
    ruleTitle,
    visibleAnnotations,
} from "../src/annotations.js";

const options = { signals: true, fills: true, rules: true, diagnostics: false };
test("sizing rejection and structural cutoff have explicit Chinese explanations", () => {
    assert.equal(reasonText("risk_budget_below_one_lot"), "单笔风险预算不足以买入一手");
    assert.equal(reasonText("insufficient_net_reward_risk"), "开盘含费净盈亏比不足");
    assert.match(reasonText("same_bar_vertices_require_lower_timeframe_n"), /同日高低点不能组成日线 N/);
});
test("window key follows extreme predecessor, not latest opposite pivot", () => {
    const points = [
        { time: "a", kind: "H", value: 30, available_at: "b" },
        { time: "b", kind: "L", value: 10, preceding_turn: { value: 30 }, available_at: "c" },
        { time: "c", kind: "H", value: 25, preceding_turn: { value: 10 }, available_at: "d" },
        {
            time: "d",
            kind: "L",
            value: 15,
            preceding_turn: { value: 25 },
            trend: "高低点不同向或相等",
            available_at: "e",
        },
    ];
    const summary = reversalWindowSummary([{ id: "p", points }], "b", "d");
    assert.equal(summary.lastFallHigh.value, 30);
    assert.equal(summary.lastRiseLow.value, 10);
    assert.equal(summary.low.time, "b");
    assert.equal(summary.high.time, "c");
    assert.equal(reversalWindowSummary([], "a", "z"), null);
});
const view = {
    asof: "2026-01-03",
    bars: [1, 2, 3].map((i) => ({ time: `2026-01-0${i}` })),
    markers: [
        { id: "s1", time: "2026-01-01", kind: "signal", side: "LONG", price: 10, stop: 9, target: 12 },
        { id: "o1", time: "2026-01-02", kind: "fill", side: "BUY", price: 10.1 },
        { id: "s2", time: "2026-01-03", kind: "signal", side: "EXIT", price: 11 },
        { id: "future", time: "2026-01-04", kind: "fill", side: "SELL", price: 999 },
    ],
};
test("exit signal circle is smaller without shrinking buys or actual fills", () => {
    const groups = markerGroups(buildAnnotations(view, null), options);
    assert.equal(groups.find((g) => g.id === "s2").marker.size, 0.45);
    assert.equal(groups.find((g) => g.id === "s1").marker.size, 1);
    assert.equal(groups.find((g) => g.id === "o1").marker.size, 1.5);
});
const theory = {
    events: [
        { id: "r1", event: "bear_to_bull_flip", time: "2026-01-01", available_at: "2026-01-02", price: 10.1 },
        { id: "r2", event: "bear_bull_alternation", time: "2026-01-02", available_at: "2026-01-02", price: 10.1 },
        { id: "r3", event: "entry_rejected", time: "2026-01-03", available_at: "2026-01-03", price: 11 },
    ],
};
test("LONG and EXIT visible, no future SELL", () => {
    const items = buildAnnotations(view, theory);
    assert.equal(items.find((i) => i.id === "s2").title, "退出信号");
    assert.equal(
        items.some((i) => i.id === "future"),
        false,
    );
    assert.equal(items.filter((i) => i.category === "fills").length, 1);
});
test("rules are dated at availability, not their historical pivot", () => {
    const item = buildAnnotations(view, theory).find((i) => i.id === "r1");
    assert.equal(item.time, "2026-01-02");
    assert.equal(item.sourceTime, "2026-01-01");
});
test("actual fill markers use exact price coordinates", () => {
    const groups = markerGroups(buildAnnotations(view, theory), options);
    const marker = groups.find((g) => g.id === "o1").marker;
    assert.equal(marker.price, 10.1);
    assert.equal(marker.position, "atPriceBottom");
    assert.match(marker.text, /B 买入 10.10/);
});
test("same-day rules grouped, no evidence lost", () => {
    const rules = markerGroups(buildAnnotations(view, theory), options).filter((g) => g.items[0].kind === "rule");
    assert.equal(rules.length, 1);
    assert.equal(rules[0].items.length, 2);
    assert.match(rules[0].marker.text, /\+1/);
});
test("all six regimes retain their exact names", () => {
    for (const regime of ["轧空", "强轧空", "盘坚", "盘跌", "追杀", "强追杀"])
        assert.equal(ruleTitle({ event: "regime_confirmation", regime }), regime);
});
test("signal, fill, rule and diagnostic filters independent", () => {
    const items = buildAnnotations(view, theory);
    assert.equal(
        visibleAnnotations(items, { ...options, signals: false }).some((m) => m.kind === "signal"),
        false,
    );
    assert.equal(
        visibleAnnotations(items, { ...options, fills: false }).some((m) => m.kind === "fill"),
        false,
    );
    assert.equal(
        visibleAnnotations(items, { ...options, rules: false, diagnostics: true }).some((m) => m.kind === "rule"),
        false,
    );
    assert.equal(
        visibleAnnotations(items, options).some((m) => m.id === "r3"),
        false,
    );
    assert.equal(
        visibleAnnotations(items, { ...options, diagnostics: true }).some((m) => m.id === "r3"),
        true,
    );
});
test("a deferred order is never rendered as an actual fill", () => {
    const items = buildAnnotations(
        {
            ...view,
            markers: [{ id: "d1", time: "2026-01-02", kind: "order", status: "deferred", side: "SELL", price: null }],
        },
        null,
    );
    const groups = markerGroups(items, { ...options, diagnostics: true });
    assert.match(groups[0].marker.text, /延迟/);
    assert.notEqual(groups[0].marker.position, "atPriceTop");
});
test("text collision handling keeps every marker and prioritizes fills", () => {
    const groups = markerGroups(buildAnnotations(view, theory), options);
    const count = groups.length;
    avoidLabelCollisions(groups, () => 100);
    assert.equal(groups.length, count);
    assert.ok(groups.find((g) => g.id === "o1").marker.text);
    assert.equal(groups.find((g) => g.id === "s1").marker.text, "");
});
