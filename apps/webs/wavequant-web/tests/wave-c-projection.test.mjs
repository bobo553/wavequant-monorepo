import assert from "node:assert/strict";
import test from "node:test";

import { waveCProjection } from "../public/wave-c-projection.js";

const bars = [
    { time: "2026-01-01", high: 10, low: 8, close: 9 },
    { time: "2026-01-02", high: 12, low: 10, close: 11 },
    { time: "2026-01-03", high: 14, low: 11, close: 13 },
    { time: "2026-01-04", high: 13, low: 12, close: 12 },
    { time: "2026-01-05", high: 12.5, low: 11.5, close: 12.2 },
];
const n = {
    event: "n_completed",
    direction: "up",
    time: "2026-01-02",
    available_at: "2026-01-02",
    defense: 9,
    shape: [{ time: "2026-01-01", value: 8 }],
    levels: [{ name: "1P 投影", price: 12 }],
};

test("selecting A high projects the equal C wave from the later B low", () => {
    assert.deepEqual(waveCProjection(bars, [n], "2026-01-03"), {
        nTime: "2026-01-02",
        origin: 8,
        oneP: 12,
        aTime: "2026-01-03",
        aHigh: 14,
        bTime: "2026-01-05",
        bLow: 11.5,
        target: 17.5,
    });
});

test("projection needs a strict one-P break and a later defended B low", () => {
    assert.equal(waveCProjection(bars, [n], "2026-01-02"), null);
    assert.equal(waveCProjection(bars.slice(0, 3), [n], "2026-01-03"), null);
    const broken = bars.map((bar) => ({ ...bar }));
    broken[3].low = 8.5;
    assert.equal(waveCProjection(broken, [n], "2026-01-03"), null);
});

test("B low stops before a later A-high breakout", () => {
    const continuation = [...bars.slice(0, 4), { time: "2026-01-05", high: 15, low: 10, close: 14 }];
    assert.equal(waveCProjection(continuation, [n], "2026-01-03")?.bLow, 12);
});

test("an early B low remains the anchor when the later declining close makes the correction clear", () => {
    const correction = bars.map((bar) => ({ ...bar }));
    correction[3].high = 13.5;
    correction[3].close = 13.2;
    correction[3].low = 11;
    correction[4].close = 12;
    assert.equal(waveCProjection(correction, [n], "2026-01-03")?.bLow, 11);
    assert.equal(waveCProjection(correction.slice(0, 4), [n], "2026-01-03"), null);
});
