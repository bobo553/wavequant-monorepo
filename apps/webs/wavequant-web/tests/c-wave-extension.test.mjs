import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { targetLevelGuide } from "../public/target-level-guides.js";
import { waveCProjectionAnnotation, waveCProjectionLevels } from "../public/wave-c-projection.js";

const projection = {
    nTime: "2026-01-02",
    origin: 8,
    oneP: 12,
    aTime: "2026-01-03",
    aHigh: 14,
    bTime: "2026-01-05",
    bLow: 11.5,
    target0618: 15.208,
    target: 17.5,
};
const bars = [
    { time: "2026-01-05", high: 19, low: 11.5, close: 12.2 },
    { time: "2026-01-06", high: 17.49, low: 12, close: 17.4 },
    { time: "2026-01-07", high: 17.5, low: 16, close: 17.45 },
    { time: "2026-01-08", high: 22, low: 17, close: 20 },
];
const extension = (value, history = bars, asof = history.at(-1)?.time) =>
    waveCProjectionLevels(value, history, asof).find((level) => level.stage === "c_1618");

test("the first equal-target touch reveals 1.618 from that date without borrowing a future bar", () => {
    assert.equal(extension(projection, bars, "2026-01-06"), undefined);
    const level = extension(projection, bars, "2026-01-07");
    assert.equal(level.name, "C 浪目标 1.618×A");
    assert.ok(Math.abs(level.price - 21.208) < 1e-10);
    assert.equal(level.available_at, "2026-01-07");
    assert.equal(level.anchor_at, "2026-01-07");
    assert.deepEqual(level, extension(projection, bars.slice(0, 3), "2026-01-07"));
    assert.equal(targetLevelGuide({ time: projection.bTime }, level, bars, "2026-01-07").end, null);
    assert.equal(targetLevelGuide({ time: projection.bTime }, level, bars, "2026-01-08").end, "2026-01-08");
    assert.match(waveCProjectionAnnotation(projection, bars, "2026-01-07").description, /2026-01-07.*1\.618/);
});

test("a confirmation-day shadow cannot unlock the extension; a confirmed close can", () => {
    assert.equal(extension(projection, bars.slice(0, 1)), undefined);
    const closed = [{ ...bars[0], close: 17.5 }];
    assert.equal(extension(projection, closed).available_at, projection.bTime);
    const pendingB = { ...projection, bKnownAt: "2026-01-07" };
    assert.equal(extension(pendingB, bars, "2026-01-07"), undefined);
    assert.equal(extension(pendingB, bars).available_at, "2026-01-08");
});

test("a failed origin or expired observation cannot unlock a new extension", () => {
    const failed = bars.map((bar) => (bar.time === "2026-01-06" ? { ...bar, low: 7.9, close: 7.9 } : bar));
    assert.equal(extension(projection, failed), undefined);
    assert.equal(extension({ ...projection, targetValidUntil: "2026-01-06" }), undefined);
    const ambiguous = [{ ...bars[0], high: 22, low: 7.9, close: 7.9 }];
    assert.equal(extension(projection, ambiguous), undefined);
    assert.equal(extension({ ...projection, aHigh: NaN }), undefined);
    assert.equal(extension({ ...projection, target: 18 }), undefined);
    assert.equal(extension(projection, []), undefined);
});

test("later failure keeps the earlier target but stops its breakout observations", () => {
    const history = [
        ...bars.slice(0, 3),
        { ...bars[3], low: 7.9, close: 7.9 },
        { time: "2026-01-09", high: 30, low: 8, close: 25 },
    ];
    const level = extension(projection, history);
    assert.equal(level.valid_until, "2026-01-07");
    assert.equal(targetLevelGuide({ time: projection.bTime }, level, history).end, null);
});

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_c_extension.json", import.meta.url)));
const dailyBars = (rows) =>
    rows.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));

test("May 18 real prices do not pre-label a 6.22 equal target; the same wave unlocks 7.49926 only after reaching it", () => {
    const history = dailyBars(fixture.may2026.bars);
    const proof = fixture.may2026.proof;
    const marker = {
        id: "may18",
        time: "2026-05-18",
        kind: "signal",
        side: "LONG",
        price: 5.1,
        reason: "system_wave_push_gap",
        decision_evidence: [proof],
    };
    const view = { asof: marker.time, bars: history, markers: [marker] };
    const original = buildAnnotations(view, null)[0];
    assert.equal(history.at(-1).high, 5.2);
    assert.equal(original.levels.find((level) => level.stage === "c_equal").price, 6.22);
    assert.equal(
        original.levels.find((level) => level.stage === "c_1618"),
        undefined,
    );
    const future = { time: "2026-05-19", open: 5.1, high: 6.22, low: 5, close: 6, volume: 1 };
    const reached = buildAnnotations({ ...view, asof: future.time, bars: [...history, future] }, null)[0];
    const level = reached.levels.find((value) => value.stage === "c_1618");
    assert.ok(Math.abs(level.price - 7.49926) < 1e-10);
    assert.equal(level.available_at, future.time);
    assert.equal(
        buildAnnotations({ ...view, bars: [...history, future] }, null)[0].levels.length,
        original.levels.length,
    );
});

test("real 2020 C wave reaches 4.62 on June 16 and reveals 5.54082 from that day", () => {
    const value = {
        ...projection,
        origin: 2.9,
        aHigh: 4.39,
        bTime: "2020-04-28",
        bKnownAt: "2020-05-06",
        bLow: 3.13,
        target0618: 4.05082,
        target: 4.62,
    };
    const history = dailyBars(fixture.june2020.bars);
    assert.equal(extension(value, history, "2020-06-15"), undefined);
    const level = extension(value, history, "2020-06-16");
    assert.ok(Math.abs(level.price - 5.54082) < 1e-10);
    assert.equal(level.available_at, "2020-06-16");
});

test("filled buys share the extension while an earlier theory snapshot still hides a future touch", () => {
    const proof = {
        event: "long_signal",
        wave_entry_path: "one_p_held_defense_rebound",
        wave_a_origin: 8,
        wave_a_high: 14,
        wave_b_low_date: projection.bTime,
        wave_b_low: 11.5,
        wave_c_0618_target: 15.208,
        wave_equal_target: 17.5,
    };
    const marker = {
        id: "fill",
        time: "2026-01-06",
        signal_time: "2026-01-05",
        kind: "fill",
        side: "BUY",
        price: 17.4,
        reason: "system_wave_push_gap",
        decision_evidence: [proof],
    };
    const view = { asof: "2026-01-08", bars, markers: [marker] };
    assert.equal(
        buildAnnotations(view, { asof: "2026-01-06", events: [] })[0].levels.some((level) => level.stage === "c_1618"),
        false,
    );
    const level = buildAnnotations(view, { asof: "2026-01-08", events: [] })[0].levels.find(
        (value) => value.stage === "c_1618",
    );
    assert.equal(level.available_at, "2026-01-07");
    assert.ok(Math.abs(level.price - 21.208) < 1e-10);
});
