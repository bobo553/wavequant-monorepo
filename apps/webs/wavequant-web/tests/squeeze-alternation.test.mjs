import assert from "node:assert/strict";
import test from "node:test";

import { bearBullAlternationLowAnnotations } from "../public/annotations.js";

const point = (time, value, kind) => ({ time, value, kind, label: kind });
const low = {
    ...point("2026-07-21", 9, "L"),
    id: "squeeze-b",
    available_at: "2026-08-07",
    invalidated_at: "2026-08-20",
    trend_level: 3,
    source_level: 3,
    confirmation_rule: "positive_n_and_squeeze_after_qualified_b",
    confirmed_flip_high: point("2025-03-20", 16.8, "H"),
    confirmed_bear_low: point("2024-02-06", 4.84, "L"),
    retracement_origin: point("2024-02-06", 4.84, "L"),
    broken_key: point("2020-11-05", 10.35, "H"),
    retracement_ratio: 0.65217,
    confirmation_evidence: {
        regime: "轧空",
        price_path: true,
        time_path: true,
        b_low_price: 9,
        two_thirds_price: 8.83,
        half_price: 10.82,
        b_minimum_close: 9.63,
        a_duration: 266,
        b_duration: 324,
    },
};

test("N squeeze marks B only after confirmation and dates failure without repainting history", () => {
    const render = (asof) =>
        bearBullAlternationLowAnnotations([{ level: 3, landmarks: [low] }], "2026-07-01", "2026-07-31", asof);
    assert.deepEqual(render("2026-08-06"), []);
    const confirmed = render("2026-08-07")[0];
    assert.equal(confirmed.time, "2026-07-21");
    assert.match(confirmed.description, /正 N \+ 轧空于 2026-08-07/);
    assert.match(confirmed.description, /最低收盘 .* < 1\/2/);
    assert.doesNotMatch(confirmed.title, /已失效/);
    assert.equal(confirmed.raw.invalidated_at, undefined);
    const failed = render("2026-08-20")[0];
    assert.match(failed.title, /已失效/);
    assert.equal(failed.color, "#8292a9");
    assert.match(failed.description, /2026-08-20 尚未收盘突破/);
});

test("later breakout becomes visible only at its date", () => {
    const landmark = { ...low, invalidated_at: undefined, breakout_at: "2026-09-02" };
    const render = (date) =>
        bearBullAlternationLowAnnotations([{ level: 3, landmarks: [landmark] }], "2026-07-01", "2026-07-31", date)[0];
    assert.doesNotMatch(render("2026-08-07").description, /收盘已严格突破/);
    assert.match(render("2026-09-02").description, /收盘已严格突破/);
});
