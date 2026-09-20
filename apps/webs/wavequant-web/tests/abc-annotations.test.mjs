import assert from "node:assert/strict";
import test from "node:test";

import { buildAnnotations, markerGroups, visibleAnnotations } from "../public/annotations.js";

const bars = Array.from({ length: 11 }, (_, i) => ({ time: `2026-01-${String(i + 1).padStart(2, "0")}` }));
const view = { bars, markers: [], asof: "2026-01-11" };
const evidence = {
    a_origin_index: 0,
    a_high_index: 2,
    b_low_index: 5,
    a_origin_price: 4,
    a_high_price: 16,
    b_low_price: 7,
    two_thirds_price: 8,
    half_price: 10,
    b_minimum_close: 9,
    a_duration: 2,
    b_duration: 3,
    price_path: false,
    time_path: true,
    attack: 8,
    candidate_index: 9,
    regime: "轧空",
    price: 13,
    levels: [],
};
const events = [
    { ...evidence, id: "abc-candidate", event: "tertiary_c_candidate", time: bars[9].time, available_at: bars[9].time },
    { ...evidence, id: "abc-break", event: "tertiary_c_breakout", time: bars[10].time, available_at: bars[10].time },
];

test("ABC markers explain the corrected strict close inequality without claiming a buy", () => {
    const items = buildAnnotations(view, { events });
    assert.equal(items[0].title, "Ⅲ c 段启动候选");
    assert.equal(items[1].title, "Ⅲ c 突破 a 高点");
    assert.match(items[0].description, /b 3 根 > a 2 根/);
    assert.match(items[0].description, /最低收盘 .* < 1\/2 回撤价/);
    assert.match(items[0].description, /尚不保证突破/);
    assert.match(items[0].sourceLabel, /非成交/);
    assert.equal(items[0].kind, "trend-key");
    const groups = markerGroups(items, { tertiaryAbc: true }, 500);
    assert.equal(groups[0].marker.text, "Ⅲ c 段启动候选");
    assert.equal(groups[0].marker.price, 13);
    assert.deepEqual(visibleAnnotations(items, { rules: true, tertiaryAbc: false }), []);
});

test("ABC replay does not reveal a later breakout or invalidation", () => {
    const prefix = buildAnnotations({ ...view, asof: bars[9].time }, { events });
    assert.equal(prefix.length, 1);
    assert.doesNotMatch(prefix[0].description, /已严格突破/);
    const failed = buildAnnotations(view, { events: [{ ...events[1], event: "tertiary_c_invalidated" }] });
    assert.match(failed[0].description, /候选失效/);
    const price = buildAnnotations(view, {
        events: [{ ...events[0], price_path: true, time_path: false, b_low_price: 8 }],
    });
    assert.match(price[0].description, /≥ 2\/3 回撤价/);
    assert.doesNotMatch(price[0].description, /时间条件/);
});
