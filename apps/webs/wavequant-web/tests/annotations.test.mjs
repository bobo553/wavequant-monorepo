import assert from "node:assert/strict";
import test from "node:test";

import {
    avoidLabelCollisions,
    bearToBullHighAnnotations,
    buildAnnotations,
    lastFallHighAnnotations,
    markerGroups,
    reasonText,
    reversalWindowSummary,
    ruleTitle,
    visibleAnnotations,
} from "../public/annotations.js";

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
test("server close-break event moves level-two last-fall-high to the next confirmed segment", () => {
    const points = [
            { index: 10, time: "2025-07-10", kind: "H", value: 8.72, label: "H33", available_at: "2025-08-26" },
            { index: 20, time: "2026-01-23", kind: "L", value: 6.32, label: "L34", available_at: "2026-03-23" },
            { index: 30, time: "2026-04-02", kind: "H", value: 7.52, label: "H34", available_at: "2026-05-22" },
            { index: 40, time: "2026-06-29", kind: "L", value: 6.34, label: "L35", available_at: "2026-08-04" },
        ],
        transition = {
            kind: "last_fall_high_reanchor",
            available_at: "2026-08-31",
            previous_key: points[0],
            broken_low: points[1],
            new_key: points[2],
            active_low: points[3],
            confirmed_by: { time: "2026-08-31", value: 6.23, previous_close: 6.55, break_basis: "close_cross" },
        },
        strokes = [{ id: "secondary-huaxia", points, key_transitions: [transition] }];

    const before = reversalWindowSummary(strokes, "2025-01-01", "2026-08-30");
    assert.equal(before.low.time, "2026-01-23");
    assert.equal(before.lastFallHigh.time, "2025-07-10");
    assert.equal(before.lastFallHighReanchor, undefined);

    const after = reversalWindowSummary(strokes, "2025-01-01", "2026-09-07");
    assert.equal(after.low.time, "2026-06-29");
    assert.equal(after.low.value, 6.34);
    assert.equal(after.lowestLow.time, "2026-01-23");
    assert.equal(after.lastFallHigh.time, "2026-04-02");
    assert.equal(after.lastFallHigh.value, 7.52);
    assert.equal(after.lastFallHighReanchor.available_at, "2026-08-31");
    const annotation = lastFallHighAnnotations([{ level: 2, summary: after }])[0];
    assert.equal(annotation.time, "2026-04-02");
    assert.equal(annotation.raw.definition, "server_confirmed_close_break_reanchor");
    assert.equal(annotation.raw.displaced_low.time, "2026-01-23");
    assert.match(annotation.description, /2026-08-31 被收盘 6\.23 严格跌破/);
    assert.match(annotation.description, /2026-04-02/);
});
test("server tail reanchor uses the confirmed source low without inventing a level-two point", () => {
    const points = [
            { index: 5241, time: "2026-01-26", kind: "H", value: 24.85, label: "H26", available_at: "2026-03-05" },
            { index: 5300, time: "2026-04-28", kind: "L", value: 16.73, label: "L26", available_at: "2026-06-04" },
            { index: 5320, time: "2026-05-29", kind: "H", value: 22.35, label: "H27", available_at: "2026-07-17" },
        ],
        activeLow = {
            index: 5351,
            time: "2026-07-14",
            kind: "L",
            value: 13.26,
            label: "L283",
            available_at: "2026-07-17",
        },
        transition = {
            kind: "last_fall_high_reanchor",
            available_at: "2026-07-17",
            active_low_source_level: 1,
            previous_key: points[0],
            broken_low: points[1],
            new_key: points[2],
            active_low: activeLow,
            confirmed_by: { time: "2026-06-23", value: 16.29, previous_close: 16.74, break_basis: "close_cross" },
        },
        summary = reversalWindowSummary(
            [{ id: "secondary-shanghai-power", points, key_transitions: [transition] }],
            "2026-01-01",
            "2026-09-07",
        );

    assert.equal(summary.low.time, "2026-07-14");
    assert.equal(summary.low.value, 13.26);
    assert.equal(summary.lowestLow.time, "2026-04-28");
    assert.equal(summary.lastFallHigh.time, "2026-05-29");
    assert.equal(summary.lastFallHigh.value, 22.35);
    const annotation = lastFallHighAnnotations([{ level: 2, summary }])[0];
    assert.equal(annotation.raw.selected_low.time, "2026-07-14");
    assert.match(annotation.description, /一级确认低点 L283/);
    assert.match(annotation.description, /2026-05-29/);
});
test("last-fall-high breakout is the first later confirmed high strictly above the key", () => {
    const points = [
        { index: 1, time: "a", kind: "H", value: 30, available_at: "a" },
        {
            index: 2,
            time: "b",
            kind: "L",
            value: 10,
            preceding_turn: { index: 1, time: "a", kind: "H", value: 30 },
            available_at: "b",
        },
        { index: 3, time: "c", kind: "H", value: 30, available_at: "c" },
        { index: 4, time: "d", kind: "L", value: 12, available_at: "d" },
        { index: 5, time: "e", kind: "H", value: 31, available_at: "e", trend: "多头趋势" },
        { index: 6, time: "f", kind: "H", value: 35, available_at: "f" },
    ];
    const summary = reversalWindowSummary([{ id: "p", points }], "a", "f");
    assert.equal(summary.lastFallHigh.value, 30);
    assert.equal(summary.lastFallHighBreakout.time, "e");
    assert.notEqual(summary.lastFallHighBreakout.time, "c", "equal-price touch is not a breakout");
    assert.equal(
        reversalWindowSummary([{ id: "p", points }], "a", "d").lastFallHighBreakout,
        null,
        "a breakout outside the current chart window must not extend the guide early",
    );
});
test("last-fall-high guide ends at the first close breakout bar after the selected low is known", () => {
    const points = [
            { index: 5384, time: "2025-06-20", kind: "H", value: 18.58, label: "H3", available_at: "2025-07-02" },
            {
                index: 5451,
                time: "2025-09-23",
                kind: "L",
                value: 12.73,
                label: "L3",
                available_at: "2026-06-08",
            },
            { index: 5613, time: "2026-06-02", kind: "H", value: 17.68, label: "H4", available_at: "2026-06-30" },
            { index: 5629, time: "2026-06-25", kind: "L", value: 15.23, label: "L4", available_at: "2026-08-14" },
        ],
        bars = [
            { time: "2026-06-07", high: 19, close: 18.9 },
            { time: "2026-07-17", high: 18.85, close: 17.25 },
            { time: "2026-07-18", high: 18.7, close: 18.58 },
            { time: "2026-07-20", high: 18.85, close: 18.78 },
            { time: "2026-07-21", high: 19.57, close: 18.06 },
        ];
    const summary = reversalWindowSummary(
        [{ id: "secondary-wantong", kind: "secondary", points }],
        "2025-05-01",
        "2026-09-07",
        bars,
    );

    assert.equal(summary.lastFallHigh.time, "2025-06-20");
    assert.equal(summary.low.time, "2025-09-23");
    assert.equal(summary.lastFallHighBreakout.time, "2026-07-20");
    assert.equal(summary.lastFallHighBreakout.value, 18.78);
    assert.equal(summary.lastFallHighBreakout.breakout_basis, "close_cross");
    assert.equal(summary.lastFallHighBreakout.label, "收盘突破 K线");
    const annotation = lastFallHighAnnotations([{ level: 2, summary }])[0];
    assert.equal(annotation.raw.breakout.time, "2026-07-20");
    assert.match(annotation.description, /K 线收盘 18\.78 首次从关键位下方严格突破/);
    assert.equal(
        reversalWindowSummary(
            [{ id: "secondary-wantong", kind: "secondary", points }],
            "2025-05-01",
            "2026-07-18",
            bars,
        ).lastFallHighBreakout,
        null,
        "an intraday high or equal close must not extend the guide",
    );
});
test("confirmed same-level high remains a breakout fallback when no market close crosses", () => {
    const points = [
            { index: 100, time: "2026-06-15", kind: "H", value: 3.65, label: "H293", available_at: "2026-06-24" },
            { index: 110, time: "2026-06-30", kind: "L", value: 3.2, label: "L294", available_at: "2026-07-17" },
            { index: 120, time: "2026-08-03", kind: "H", value: 3.67, label: "H294", available_at: "2026-08-11" },
        ],
        bars = [
            { time: "2026-07-17", high: 3.49, close: 3.46 },
            { time: "2026-07-30", high: 3.63, close: 3.63 },
            { time: "2026-08-03", high: 3.67, close: 3.65 },
            { time: "2026-08-10", high: 3.57, close: 3.53 },
        ],
        strokes = [{ id: "reversal-minsheng", points }];
    const summary = reversalWindowSummary(strokes, "2026-06-01", "2026-09-07", bars);

    assert.equal(summary.lastFallHigh.time, "2026-06-15");
    assert.equal(summary.low.time, "2026-06-30");
    assert.equal(summary.lastFallHighBreakout.time, "2026-08-03");
    assert.equal(summary.lastFallHighBreakout.value, 3.67);
    assert.equal(summary.lastFallHighBreakout.breakout_basis, "confirmed_same_level_high");
    assert.equal(
        reversalWindowSummary(strokes, "2026-06-01", "2026-08-10", bars).lastFallHighBreakout,
        null,
        "the August 3 pivot must not be used before its August 11 confirmation date",
    );
    const annotation = lastFallHighAnnotations([{ level: 1, summary }])[0];
    assert.equal(annotation.raw.breakout.time, "2026-08-03");
    assert.match(annotation.description, /H294（2026-08-03，3\.67）首次严格突破/);
});
test("continuous level-one display path uses the bridge low and its preceding bridge high", () => {
    const points = [
        { index: 1, time: "2026-05-22", kind: "L", value: 3.04, label: "L7", available_at: "2026-05-25" },
        {
            index: 2,
            time: "2026-06-02",
            kind: "H",
            value: 3.28,
            label: "H·桥",
            available_at: "2026-06-03",
            display_bridge: true,
        },
        {
            index: 3,
            time: "2026-06-30",
            kind: "L",
            value: 2.68,
            label: "L·桥",
            available_at: "2026-07-01",
            display_bridge: true,
        },
        { index: 4, time: "2026-08-04", kind: "H", value: 3.01, label: "H1", available_at: "2026-08-04" },
        { index: 5, time: "2026-08-17", kind: "L", value: 2.79, label: "L1", available_at: "2026-08-18" },
    ];
    const summary = reversalWindowSummary(
        [{ id: "display-level-one", display_summary: true, points }],
        "2026-05-01",
        "2026-09-07",
    );
    assert.equal(summary.low.time, "2026-06-30");
    assert.equal(summary.lastFallHigh.time, "2026-06-02");
    assert.equal(summary.lastFallHigh.value, 3.28);
    assert.equal(summary.lastFallHighBreakout, null);
    assert.equal(summary.displaySummary, true);
    const annotation = lastFallHighAnnotations([{ level: 1, summary }])[0];
    assert.equal(annotation.time, "2026-06-02");
    assert.equal(annotation.raw.selected_low.time, "2026-06-30");
    assert.equal(annotation.raw.display_summary, true);
    assert.match(annotation.description, /连续显示路径/);
});
test("last-fall-high labels identify each level at the source high and retain the selected low", () => {
    const makeSummary = (level) => ({
        path: `level-${level}`,
        lastFallHigh: { index: level, label: `H${level}`, time: `2026-0${level}-01`, value: 100 + level },
        low: {
            available_at: `2026-0${level}-10`,
            index: level + 10,
            kind: "L",
            label: `L${level}`,
            time: `2026-0${level}-05`,
            value: 90 - level,
        },
    });
    const summaries = [1, 2, 3].map((level) => ({ level, summary: makeSummary(level) }));
    summaries[0].summary.lastFallHighBreakout = {
        index: 21,
        kind: "H",
        label: "H-break",
        time: "2026-01-08",
        value: 108,
    };
    const items = lastFallHighAnnotations(summaries);
    assert.deepEqual(
        items.map((item) => [item.raw.trend_level, item.time, item.price, item.raw.selected_low.label]),
        [
            [1, "2026-01-01", 101, "L1"],
            [2, "2026-02-01", 102, "L2"],
            [3, "2026-03-01", 103, "L3"],
        ],
    );
    assert.match(items[0].description, /左侧最近的同级已确认高点/);
    assert.match(items[0].description, /水平虚线延长到这根 K 线/);
    assert.equal(items[0].raw.breakout.label, "H-break");
    assert.deepEqual(lastFallHighAnnotations([{ level: 1, summary: null }]), []);
    const marker = markerGroups(items, { ...options, trendKeys: true })[0].marker;
    assert.equal(marker.position, "atPriceTop");
    assert.equal(marker.price, 101);
    assert.match(marker.text, /Ⅰ 末跌高/);
    assert.equal(visibleAnnotations(items, { ...options, trendKeys: false }).length, 0);
});

test("bear-to-bull high labels use Python landmarks and respect their causal availability", () => {
    const landmark = {
        id: "level2-bear-to-bull-high-1195",
        time: "2022-08-03",
        available_at: "2022-08-09",
        index: 1195,
        kind: "H",
        label: "H70",
        value: 49.56,
        trend_level: 2,
        source_path: "secondary-sample",
        confirmed_low: { time: "2022-05-27", label: "L9", value: 13.16 },
        broken_key: { time: "2021-12-01", label: "H8", value: 27.71 },
    };

    assert.deepEqual(bearToBullHighAnnotations([{ level: 2, landmarks: [landmark] }], "2022-05-01", "2022-08-08"), []);
    const [item] = bearToBullHighAnnotations([{ level: 2, landmarks: [landmark] }], "2022-05-01", "2022-08-09");
    assert.equal(item.time, "2022-08-03");
    assert.equal(item.price, 49.56);
    assert.equal(item.category, "trend-flip-highs");
    assert.match(item.title, /Ⅱ 空翻多高点 · H70 49.56/);
    assert.match(item.description, /L9（2022-05-27，13.16）/);
    assert.match(item.description, /H8（2021-12-01，27.71）/);
    assert.equal(visibleAnnotations([item], { ...options, bullFlipHighs: false }).length, 0);
    assert.equal(visibleAnnotations([item], { ...options, bullFlipHighs: true }).length, 1);
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
