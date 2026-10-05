import assert from "node:assert/strict";
import test from "node:test";

import { nTargetAt, nTargetObservations } from "../public/n-target-focus.js";

const bars = [
    { time: "2024-03-01", high: 4.5, close: 4.3 },
    { time: "2024-03-04", high: 4.6, close: 4.4 },
    { time: "2024-03-05", high: 7, close: 4.6 },
    { time: "2024-03-06", high: 5.5, close: 5.1 },
    { time: "2024-03-07", high: 6, close: 5.5 },
    { time: "2024-03-08", high: 6.01, close: 5.5 },
    { time: "2024-03-11", high: 6.5, close: 6.3 },
];
const item = {
    id: "older-n",
    time: "2024-03-05",
    raw: { event: "n_completed", direction: "up", shape: [{ time: "2024-03-01" }] },
    levels: [
        { stage: "one_p", price: 5, anchor_at: "2024-03-04", available_at: "2024-03-05" },
        { stage: "two_t", price: 6, anchor_at: "2024-03-04", available_at: "2024-03-05" },
    ],
};

test("N focus covers the structure through the first strict two-t break without changing its prices", () => {
    const original = structuredClone(item);
    const observations = nTargetObservations([item], bars, "2024-03-11");
    assert.deepEqual(observations, [{ item, from: "2024-03-01", to: "2024-03-08" }]);
    for (const bar of bars.slice(0, -1)) assert.equal(nTargetAt(observations, bar.time), item);
    assert.equal(nTargetAt(observations, "2024-03-11"), null);
    assert.equal(nTargetAt(observations, null), null);
    assert.equal(nTargetAt(observations, "2024-02-29"), null);
    assert.deepEqual(item, original);
});

test("confirmation-day high and equality do not close N focus; replay cannot reveal a future N or break", () => {
    assert.deepEqual(nTargetObservations([item], bars, "2024-03-04"), []);
    for (const asof of ["2024-03-05", "2024-03-07"]) assert.equal(nTargetObservations([item], bars, asof)[0].to, asof);
    const later = { ...item, levels: item.levels.map((level) => ({ ...level, available_at: "2024-03-06" })) };
    assert.deepEqual(nTargetObservations([later], bars, "2024-03-05"), []);
    const sameDayBreak = bars.map((bar) => (bar.time === item.time ? { ...bar, close: 6.01 } : bar));
    assert.equal(nTargetObservations([item], sameDayBreak, "2024-03-11")[0].to, item.time);
});

test("overlapping N ranges choose an explicit hit, then selection, then the latest known structure", () => {
    const newer = { ...item, id: "newer-n", time: "2024-03-06" };
    const observations = nTargetObservations([item, newer], bars, "2024-03-11");
    assert.equal(nTargetAt(observations, "2024-03-07"), newer);
    assert.equal(nTargetAt(observations, "2024-03-07", null, item.id), item);
    assert.equal(nTargetAt(observations, "2024-03-07", newer.id, item.id), newer);
    assert.equal(nTargetAt(observations, "2024-03-11", item.id, newer.id), null);
    assert.equal(nTargetAt(observations, "2024-03-07", "unknown", "unknown"), newer);
});

test("expired, inverse, missing-anchor and incomplete target structures cannot leak into focus", () => {
    const expired = { ...item, levels: item.levels.map((level) => ({ ...level, valid_until: "2024-03-06" })) };
    const observations = nTargetObservations([expired], bars, "2024-03-11");
    assert.equal(nTargetAt(observations, "2024-03-06"), expired);
    assert.equal(nTargetAt(observations, "2024-03-07"), null);
    for (const invalid of [
        { ...item, raw: { ...item.raw, direction: "down" } },
        { ...item, levels: item.levels.slice(0, 1) },
        { ...item, levels: item.levels.map((level) => ({ ...level, price: NaN })) },
        { ...item, levels: item.levels.map((level) => ({ ...level, anchor_at: "2024-02-29" })) },
        { ...item, raw: { ...item.raw, shape: [{ time: "2024-02-29" }] } },
    ])
        assert.deepEqual(nTargetObservations([invalid], bars, "2024-03-11"), []);
});
