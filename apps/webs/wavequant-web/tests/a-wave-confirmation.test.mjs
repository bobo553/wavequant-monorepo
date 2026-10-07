import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { aWaveAnnotations, aWaveCProjections, aWaveLegs, aWaveObservations } from "../public/a-wave-observations.js";
import { waveCProjectionLevels, waveCProjectionsFromStructure } from "../public/wave-c-projection.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xiangyang_a_wave_2026.json", import.meta.url)));
const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
const observe = (asof) => aWaveObservations(bars, { ...fixture.theory, asof });

test("actual July23 N confirms A on July31 without B/C or second-level turns", () => {
    assert.deepEqual(observe("2026-07-30"), []);
    const [a] = observe("2026-07-31");
    assert.deepEqual(
        [a.originTime, a.origin, a.aConfirmedAt, a.aTime, a.aHigh, a.aAttackClass, a.phase],
        ["2026-07-21", 7.32, "2026-07-31", "2026-07-31", 8.5, "non_strong", "a"],
    );
    assert.equal(a.bTime, null);
    assert.deepEqual(aWaveCProjections(bars, { ...fixture.theory, asof: "2026-07-31" }), []);
    assert.ok(aWaveAnnotations([a], bars).some((marker) => marker.title === "A 确认"));
    assert.equal(aWaveLegs([a])[0].lineStyle, 0);
});

test("two-T equality upgrades A and August17/18 extend the same source without future B", () => {
    const august5 = observe("2026-08-05")[0];
    assert.equal(august5.strongAt, "2026-08-05");
    assert.equal(august5.aAttackClass, "strong");
    const august17 = observe("2026-08-17")[0];
    const august18 = observe("2026-08-18")[0];
    assert.equal(august17.aHigh, 9.83);
    assert.equal(august18.aHigh, 10.51);
    assert.equal(august17.sourceNId, august18.sourceNId);
    assert.equal(august18.aConfirmedAt, "2026-07-31");
    assert.equal(august18.bTime, null);
    assert.equal(august17.phase, "a");
});

test("actual valid A supplies independent C observations after first-level A top and B", () => {
    const [projection] = waveCProjectionsFromStructure(bars, fixture.theory);
    assert.equal(projection.projectionSource, "one_p_confirmed_a");
    assert.deepEqual([projection.aTime, projection.bTime, projection.bLow], ["2026-08-18", "2026-08-25", 8.21]);
    assert.ok(Math.abs(projection.target0618 - 10.18142) < 1e-10);
    assert.equal(projection.target, 11.4);
    assert.equal(projection.aIsValid, true);
    assert.equal(waveCProjectionLevels(projection, bars, "2026-09-07").length, 2);
});

test("origin failure cancels C targets permanently and same-day source IDs stay separate", () => {
    const theory = structuredClone(fixture.theory);
    const last = theory.events.filter((event) => event.event === "b_wave_updated").at(-1);
    const failure = {
        ...last,
        event: "a_wave_invalidated",
        phase: "invalidated",
        time: "2026-09-04",
        available_at: "2026-09-04",
    };
    const fakeRecovery = { ...last, time: "2026-09-07", available_at: "2026-09-07" };
    theory.events.push(failure, fakeRecovery);
    const invalid = aWaveCProjections(bars, theory)[0];
    assert.equal(invalid.aIsValid, false);
    assert.equal(invalid.invalidatedAt, "2026-09-04");
    assert.deepEqual(waveCProjectionLevels(invalid, bars, theory.asof), []);
    assert.equal(invalid.cTime, undefined);
    const valid = aWaveCProjections(bars, { ...theory, asof: "2026-09-03" })[0];
    assert.equal(valid.aIsValid, true);
    assert.ok(aWaveAnnotations(observe("2026-08-17"), bars).some((marker) => marker.title === "强势 A"));
    const second = theory.events
        .filter((event) => event.source_id === last.source_id)
        .map((event) => ({ ...event, source_id: "independent-second-n" }));
    theory.events.push(...second);
    assert.equal(aWaveObservations(bars, theory).length, 2);
    assert.equal(new Set(aWaveAnnotations(aWaveObservations(bars, theory), bars).map((item) => item.id)).size, 8);
});

test("completed Core C freezes its target interval before a later leg reaches equal-wave", () => {
    const theory = structuredClone(fixture.theory);
    const last = theory.events.filter((event) => event.event === "b_wave_updated").at(-1);
    const cBar = bars.find((bar) => bar.time === "2026-09-02");
    theory.events.push({
        ...last,
        event: "c_wave_completed",
        phase: "completed",
        time: "2026-09-03",
        available_at: "2026-09-03",
        c_high_date: cBar.time,
        c_high_price: cBar.high,
        c_known_at_date: "2026-09-03",
    });
    const later = bars.map((bar) => (bar.time === "2026-09-07" ? { ...bar, high: 20 } : bar));
    const [projection] = aWaveCProjections(later, theory);
    assert.equal(projection.targetValidUntil, "2026-09-02");
    const levels = waveCProjectionLevels(projection, later, theory.asof);
    assert.equal(levels.length, 2);
    assert.ok(levels.every((level) => level.c_ended_at === "2026-09-02"));
});

test("full event stream replay matches separately clipped prefixes and legacy data stays versioned", () => {
    for (const asof of ["2026-07-31", "2026-08-05", "2026-08-17", "2026-08-18", "2026-08-24", "2026-08-25"]) {
        const clippedBars = bars.filter((bar) => bar.time <= asof);
        const clippedTheory = {
            ...fixture.theory,
            asof,
            events: fixture.theory.events.filter((event) => event.available_at <= asof),
        };
        assert.deepEqual(observe(asof), aWaveObservations(clippedBars, clippedTheory));
    }
    assert.deepEqual(aWaveObservations(bars, { ...fixture.theory, a_wave_policy: undefined }), []);
});
