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
        target0618: 15.208,
        target: 17.5,
    });
});

test("Guofang April 1 chart projects both levels from the March 22 A high and March 27 B low", () => {
    const history = [
        ["2024-02-29", 4.28, 4.02, 4.25],
        ["2024-03-14", 4.67, 4.42, 4.48],
        ["2024-03-18", 5.42, 5.18, 5.42],
        ["2024-03-22", 5.61, 5.01, 5.34],
        ["2024-03-25", 5.30, 4.95, 4.99],
        ["2024-03-26", 5.03, 4.75, 4.85],
        ["2024-03-27", 4.98, 4.72, 4.82],
        ["2024-03-28", 4.92, 4.73, 4.92],
        ["2024-03-29", 4.91, 4.77, 4.86],
        ["2024-04-01", 4.99, 4.88, 4.97],
    ].map(([time, high, low, close]) => ({ time, high, low, close }));
    const attack = {
        ...n,
        time: "2024-03-14",
        available_at: "2024-03-14",
        defense: 4.29,
        shape: [{ time: "2024-02-29", value: 4.02 }],
        levels: [{ name: "1P 投影", price: 5.32 }],
    };
    const projection = waveCProjection(history, [attack], "2024-03-22");
    assert.equal(projection?.bTime, "2024-03-27");
    assert.ok(Math.abs(projection.target0618 - 5.70262) < 1e-10);
    assert.ok(Math.abs(projection.target - 6.31) < 1e-10);
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
