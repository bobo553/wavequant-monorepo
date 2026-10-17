import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { aWaveObservations } from "../public/a-wave-observations.js";
import { targetLevelGuide } from "../public/target-level-guides.js";
import {
    waveCProjection,
    waveCProjectionAnnotation,
    waveCProjectionForSelection,
    waveCProjectionLevels,
    waveCProjectionsFromStructure,
} from "../public/wave-c-projection.js";

const bars = [
    { time: "2026-01-01", high: 10, low: 8, close: 9 },
    { time: "2026-01-02", high: 12, low: 10, close: 11 },
    { time: "2026-01-03", high: 14, low: 11, close: 13 },
    { time: "2026-01-04", high: 13, low: 11, close: 12 },
    { time: "2026-01-05", high: 30, low: 9, close: 25 },
    { time: "2026-01-06", high: 15, low: 7, close: 10 },
    { time: "2026-01-07", high: 16, low: 12, close: 14 },
    { time: "2026-01-08", high: 18, low: 14, close: 17.5 },
];
const n = {
    event: "n_completed", direction: "up", time: "2026-01-02", available_at: "2026-01-02",
    defense: 9, shape: [{ time: "2026-01-01", value: 8 }], levels: [{ stage: "one_p", price: 12 }],
};
const core = {
    event: "b_wave_updated", source_id: "n:old-window", window_start: 1,
    time: "2026-01-04", available_at: "2026-01-04", phase: "b", a_class: "strong",
    attack_date: "2026-01-02", origin_date: "2026-01-01", origin_price: 8,
    a_high_date: "2026-01-03", a_high_price: 14, confirmed_date: "2026-01-03",
    a_top_known_at_date: "2026-01-04", b_low_date: "2026-01-04", b_low_price: 11,
    b_known_at_date: "2026-01-04", one_p: 12, two_t: 14,
};
const closure = {
    event: "n_target_source_retired", n_id: core.source_id, window_start: 1,
    time: "2026-01-05", available_at: "2026-01-05", reason: "trading_eligibility_closed",
};
const theory = (events, overrides = {}) => ({
    asof: "2026-01-08", a_wave_policy: "one_p_break_confirmed_a_origin_lifetime_v1",
    trading_observation_policy: "qualified_windows_v1", events, ...overrides,
});

test("windowed selection never invents B/C from later market bars when Core has no target", () => {
    const legacyBars = bars.slice(0, 4);
    assert.ok(waveCProjection(legacyBars, [n], "2026-01-03"));
    assert.equal(waveCProjection(legacyBars, [{ ...n, window_start: 1 }], "2026-01-03"), null);
    const projection = waveCProjection(legacyBars, [n], "2026-01-03");
    assert.equal(waveCProjectionForSelection(legacyBars, [n], "2026-01-03", projection, theory([n])), null);
    assert.deepEqual(waveCProjectionsFromStructure(legacyBars, theory([])), []);
});

test("window policy blocks unscoped synthetic Ns from structural drawing fallback", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave.json", import.meta.url)));
    const historicalBars = fixture.bars.map(([time, open, high, low, close, volume]) =>
        ({ time, open, high, low, close, volume }));
    assert.ok(waveCProjectionsFromStructure(historicalBars, fixture.theory).length);
    assert.deepEqual(waveCProjectionsFromStructure(historicalBars, {
        ...fixture.theory, trading_observation_policy: "qualified_windows_v1", events: [],
    }), []);
});

test("Core A/B facts survive closing but target touches and extensions stop before the gap", () => {
    const input = theory([core, closure]);
    const [projection] = waveCProjectionsFromStructure(bars, input);
    assert.equal(projection.sourceNId, core.source_id);
    assert.equal(projection.targetValidUntil, "2026-01-04");
    assert.equal(projection.invalidatedAt, undefined);
    assert.equal(projection.cEligible, false);
    assert.equal(projection.bTime, core.b_low_date);
    const levels = waveCProjectionLevels(projection, bars, input.asof);
    assert.deepEqual(levels.map((level) => level.stage), ["c_0618", "c_equal"]);
    assert.ok(levels.every((level) => level.valid_until === "2026-01-04"));
    assert.ok(levels.every((level) => targetLevelGuide({ time: projection.bTime }, level, bars, input.asof).end === null));
    assert.match(waveCProjectionAnnotation(projection, bars, input.asof).description, /交易资格结束/);
    assert.equal(waveCProjectionForSelection(bars, input.events, projection.aTime, undefined, input).sourceNId, core.source_id);
    const [before] = waveCProjectionsFromStructure(bars, { ...input, asof: "2026-01-04" });
    assert.equal(before.targetValidUntil, undefined);
});

test("ordinary Core targets keep the same cutoff and reopened identities do not inherit it", () => {
    const old = { ...core, a_class: "ordinary", two_t: 16 };
    const reopened = {
        ...old, source_id: "n:new-window", window_start: 6,
        time: "2026-01-07", available_at: "2026-01-07", confirmed_date: "2026-01-07",
        a_top_known_at_date: "2026-01-07", b_known_at_date: "2026-01-07",
    };
    const staleGapUpdate = { ...old, time: "2026-01-06", available_at: "2026-01-06", b_low_date: "2026-01-06", b_low_price: 7 };
    const input = theory([old, closure, staleGapUpdate, reopened]);
    const observations = aWaveObservations(bars, input);
    assert.equal(observations.find((item) => item.sourceNId === old.source_id).bTime, "2026-01-04");
    const projections = waveCProjectionsFromStructure(bars, input);
    const oldLevels = waveCProjectionLevels(projections.find((item) => item.sourceNId === old.source_id), bars, input.asof);
    assert.equal(oldLevels.length, 3);
    assert.ok(oldLevels.every((level) => level.valid_until === "2026-01-04"));
    const current = projections.find((item) => item.sourceNId === reopened.source_id);
    assert.equal(current.targetValidUntil, undefined);
    assert.equal(waveCProjectionForSelection(bars, input.events, current.aTime, undefined, input).sourceNId, reopened.source_id);
    const historical = projections.find((item) => item.sourceNId === old.source_id);
    assert.equal(waveCProjectionForSelection(bars, input.events, current.aTime, historical, input).sourceNId, old.source_id);
    const levels = waveCProjectionLevels(current, bars, input.asof);
    assert.ok(levels.every((level) => level.available_at === "2026-01-07" && !level.valid_until));
    const equal = levels.find((level) => level.stage === "c_equal");
    assert.equal(targetLevelGuide({ time: current.bTime }, equal, bars, input.asof).end, "2026-01-08");
});
