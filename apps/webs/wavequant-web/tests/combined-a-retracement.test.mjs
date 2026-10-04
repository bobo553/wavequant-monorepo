import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
    combinedAObservations,
    combinedARetracementGuides,
    latestCombinedAObservation,
} from "../public/combined-a-wave.js";
import { waveCProjectionsFromStructure } from "../public/wave-c-projection.js";

const observation = {
    id: "test-combined",
    origin: 3,
    originTime: "2025-01-01",
    cHigh: 12,
    cTime: "2025-01-02",
    available_at: "2025-01-04",
    end: "2025-01-08",
};
const bars = [
    { time: "2025-01-02", close: 12, low: 11 },
    { time: "2025-01-03", close: 7.5, low: 7 },
    { time: "2025-01-04", close: 7, low: 6.5 },
    { time: "2025-01-05", close: 6, low: 5.5 },
    { time: "2025-01-06", close: 5.9, low: 5.5 },
    { time: "2025-01-07", close: 9, low: 8 },
    { time: "2025-01-08", close: 8, low: 7.5 },
];

test("50% and two-thirds measure down from C and stop at their independent first strict closing breaks", () => {
    const original = structuredClone({ observation, bars });
    const [half, twoThirds] = combinedARetracementGuides([observation], bars, observation.end);
    assert.equal(half.price, 7.5);
    assert.equal(half.start, observation.cTime);
    assert.equal(half.end, "2025-01-04");
    assert.deepEqual(half.firstCloseBelow, { time: "2025-01-04", close: 7 });
    assert.equal(half.targetState, "半幅失守");
    assert.equal(twoThirds.price, 6);
    assert.equal(twoThirds.end, "2025-01-06");
    assert.deepEqual(twoThirds.firstCloseBelow, { time: "2025-01-06", close: 5.9 });
    assert.equal(twoThirds.targetState, "2/3失守");
    assert.deepEqual({ observation, bars }, original);
});

test("a held retracement extends only to the known data end and cannot borrow a future closing break", () => {
    const [half, twoThirds] = combinedARetracementGuides([observation], bars, "2025-01-05");
    assert.equal(half.end, "2025-01-04");
    assert.equal(twoThirds.end, "2025-01-05");
    assert.equal(twoThirds.targetState, "未跌破");
    assert.equal(twoThirds.firstCloseBelow, null);
    assert.deepEqual(
        combinedARetracementGuides([observation], bars, "2025-01-05"),
        combinedARetracementGuides(
            [observation],
            bars.filter((bar) => bar.time <= "2025-01-05"),
            "2025-01-05",
        ),
    );
    assert.equal(combinedARetracementGuides([observation], bars.slice(0, 4), observation.end)[1].end, "2025-01-05");
});

test("confirmation gates display but earlier closing breaks are back-marked from the now-confirmed C", () => {
    const later = { ...observation, available_at: "2025-01-07" };
    assert.deepEqual(combinedARetracementGuides([later], bars, "2025-01-06"), []);
    const [half, twoThirds] = combinedARetracementGuides([later], bars, "2025-01-07");
    assert.equal(half.end, "2025-01-04");
    assert.equal(twoThirds.end, "2025-01-06");
    assert.ok(half.end < later.available_at && twoThirds.end < later.available_at);
});

test("origin invalidation bounds each guide and cannot revive a failed half or borrow a later two-thirds break", () => {
    const bounded = { ...observation, invalidatedAt: "2025-01-05" };
    const [half, twoThirds] = combinedARetracementGuides([bounded], bars, observation.end);
    assert.equal(half.end, "2025-01-04");
    assert.equal(twoThirds.end, "2025-01-05");
    assert.equal(twoThirds.firstCloseBelow, null);
});

test("empty, unavailable, missing C and invalid amplitude inputs cannot draw retracement guides", () => {
    assert.deepEqual(combinedARetracementGuides([], bars), []);
    assert.deepEqual(combinedARetracementGuides([observation], []), []);
    assert.deepEqual(combinedARetracementGuides([observation], bars, "2025-01-03"), []);
    assert.deepEqual(combinedARetracementGuides([observation], bars.slice(1)), []);
    for (const input of [
        { ...observation, origin: NaN },
        { ...observation, cHigh: Infinity },
        { ...observation, origin: observation.cHigh },
        { ...observation, cTime: observation.end, available_at: observation.end },
        { ...observation, cTime: "2025-02-30" },
    ])
        assert.deepEqual(combinedARetracementGuides([input], bars), []);
});

test("real Guofang history ends the half at January 10 while the two-thirds reference stays held", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
    const history = fixture.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const observations = combinedAObservations(
        history,
        fixture.theory,
        waveCProjectionsFromStructure(history, fixture.theory),
    );
    const latest = latestCombinedAObservation(observations, fixture.theory.asof);
    const [half, twoThirds] = combinedARetracementGuides([latest], history, fixture.theory.asof);
    assert.equal(half.price, 6.767928288212305);
    assert.equal(half.start, "2025-01-03");
    assert.equal(half.end, "2025-01-10");
    assert.equal(twoThirds.price, 5.918296563582128);
    assert.equal(twoThirds.end, fixture.theory.asof);
    assert.equal(twoThirds.firstCloseBelow, null);
    const older = observations.find((item) => item.cTime === "2022-04-22");
    const oldGuides = combinedARetracementGuides([older], history, fixture.theory.asof);
    assert.equal(oldGuides[0].end, "2022-04-27");
    assert.equal(oldGuides[1].end, "2022-04-28");
});
