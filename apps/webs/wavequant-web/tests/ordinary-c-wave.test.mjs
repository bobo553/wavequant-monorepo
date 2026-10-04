import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ordinaryCWaveLevels, ordinaryCWaveProjections } from "../public/ordinary-c-wave.js";
import { targetLevelGuide } from "../public/target-level-guides.js";
import {
    waveCProjectionAnnotation,
    waveCProjectionEvidenceAnnotations,
    waveCProjectionFromStructure,
} from "../public/wave-c-projection.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2024_ordinary_c_wave.json", import.meta.url)));
const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
const observe = (asof, input = bars, theory = fixture.theory) =>
    waveCProjectionFromStructure(input, { ...theory, asof });
const guides = (projection, asof, input = bars) => {
    const annotation = waveCProjectionAnnotation(projection, input, asof);
    return annotation.levels.map((level) => targetLevelGuide(annotation, level, input, asof));
};

test("real ordinary A keeps February 8 as origin, its formal February 23 N and the long deep B", () => {
    const projection = observe("2025-01-22");
    assert.deepEqual(
        [projection.originTime, projection.nTime, projection.aTime, projection.bTime, projection.cTime],
        ["2024-02-08", "2024-02-23", "2024-04-12", "2024-07-25", "2025-01-03"],
    );
    assert.deepEqual(
        [projection.aKnownAt, projection.bKnownAt, projection.cKnownAt],
        ["2024-04-30", "2024-08-14", "2025-01-22"],
    );
    assert.equal(projection.aAttackClass, "non_strong");
    assert.ok(projection.aHigh > projection.oneP && projection.aHigh < projection.twoT);
    assert.equal(projection.bBrokeASqueezeLow, true);
    assert.equal(projection.aIsValid, true);
    assert.equal(projection.aDuration, 38);
    assert.equal(projection.bDuration, 70);
    assert.ok(Math.abs(projection.bRetracementRatio - 0.8910703757548409) < 1e-12);
    const levels = ordinaryCWaveLevels(projection);
    assert.deepEqual(
        levels.map((level) => level.stage),
        ["c_0618", "c_equal", "c_1618"],
    );
    for (const [index, expected] of [6.877680927981544, 8.274794803857262, 10.535047095195464].entries())
        assert.ok(Math.abs(levels[index].price - expected) < 1e-12);
    assert.ok(levels.every((level) => level.available_at === "2024-08-14" && level.anchor_at === "2024-07-25"));
    assert.match(levels[1].name, /等浪/);
    const annotation = waveCProjectionAnnotation(projection, bars, "2025-01-22");
    assert.match(annotation.description, /未达既有二吐.*非强攻击/);
    assert.match(annotation.description, /2025-01-22.*确认结束/);
    assert.deepEqual(
        waveCProjectionEvidenceAnnotations(projection).map(({ title }) => title),
        ["A 起", "正 N", "A 顶", "C 顶"],
    );
    assert.equal(
        waveCProjectionEvidenceAnnotations(projection, bars, "2025-01-21").some(({ title }) => title === "C 顶"),
        false,
    );
});

test("B upgrades only when the new low is confirmed and retains the earlier anchor version", () => {
    assert.equal(observe("2024-07-09"), null);
    const early = observe("2024-07-10");
    const pending = observe("2024-08-13");
    const confirmed = observe("2024-08-14");
    assert.equal(early.bTime, "2024-06-24");
    assert.equal(pending.bTime, early.bTime);
    assert.equal(confirmed.bTime, "2024-07-25");
    assert.notEqual(waveCProjectionAnnotation(early).id, waveCProjectionAnnotation(confirmed).id);
    const { supersededAt, validUntil, ...retained } = confirmed.anchorVersions[0];
    assert.deepEqual(retained, early.anchorVersions[0]);
    assert.equal(supersededAt, "2024-08-14");
    assert.equal(validUntil, "2024-08-13");
    assert.equal(confirmed.anchorVersions.length, 2);
    assert.equal(confirmed.anchorVersions[1].id, confirmed.anchorVersion);
});

test("historical prefixes cannot borrow B or C confirmation from the future", () => {
    for (const asof of ["2024-07-10", "2024-08-13", "2024-08-14", "2025-01-03", "2025-01-21", "2025-01-22"]) {
        assert.deepEqual(
            observe(asof),
            observe(
                asof,
                bars.filter((bar) => bar.time <= asof),
            ),
        );
    }
    for (const asof of ["2025-01-03", "2025-01-06", "2025-01-21"]) assert.equal(observe(asof).cTime, undefined);
    const completed = observe("2025-01-22");
    assert.equal(completed.cTime, "2025-01-03");
    assert.equal(ordinaryCWaveLevels(completed, "2024-08-13").length, 0);
    assert.equal(ordinaryCWaveLevels(completed, "2025-01-21")[2].valid_until, undefined);
});

test("three targets record highest-price touches and freeze the unachieved extension at C end", () => {
    const projection = observe("2025-01-22");
    const states = guides(projection, "2025-01-22");
    assert.deepEqual(
        states.map(({ firstTouchedAt }) => firstTouchedAt),
        ["2024-10-11", "2024-12-03", null],
    );
    assert.deepEqual(
        states.map(({ targetState }) => targetState),
        ["已触及", "已触及", "本段结束未达成"],
    );
    assert.ok(bars.find((bar) => bar.time === "2025-01-03").close < projection.target);
    const latest = observe(fixture.theory.asof);
    assert.deepEqual(
        guides(latest, fixture.theory.asof).map(({ firstTouchedAt, targetState }) => ({ firstTouchedAt, targetState })),
        states.map(({ firstTouchedAt, targetState }) => ({ firstTouchedAt, targetState })),
    );
    assert.ok(bars.some((bar) => bar.time > projection.cKnownAt && bar.high > projection.target1618));
});

test("an archived C cannot be erased by a later new group's origin break", () => {
    const archived = observe("2025-01-22");
    const later = [...bars, { time: "2026-10-01", open: 4, high: 4.3, low: 4, close: 4, volume: 1 }];
    const projection = observe("2026-10-01", later);
    assert.equal(projection.invalidatedAt, undefined);
    assert.equal(projection.cTime, archived.cTime);
    assert.equal(projection.aIsValid, true);
    assert.deepEqual(
        guides(projection, "2026-10-01", later).map(({ targetState }) => targetState),
        ["已触及", "已触及", "本段结束未达成"],
    );
});

test("an origin failure before C confirmation cannot be erased by the later endpoint confirmation", () => {
    const failed = bars.map((bar) => (bar.time === "2025-01-15" ? { ...bar, low: 4, close: 4.1 } : bar));
    for (const asof of ["2025-01-15", "2025-01-22", fixture.theory.asof]) {
        const projection = observe(asof, failed);
        assert.equal(projection.invalidatedAt, "2025-01-15");
        assert.equal(projection.cTime, undefined);
        assert.equal(ordinaryCWaveLevels(projection, asof).length, 0);
    }
    const failedOnConfirmation = bars.map((bar) => (bar.time === "2025-01-22" ? { ...bar, low: 4, close: 4.1 } : bar));
    assert.equal(observe("2025-01-22", failedOnConfirmation).invalidatedAt, "2025-01-22");
});

test("a joint origin break invalidates the original group permanently and hides its targets", () => {
    const failedB = bars.map((bar) => (bar.time === "2024-07-24" ? { ...bar, low: 4.2, close: 4.2 } : bar));
    assert.equal(observe("2024-08-14", failedB), null);
    assert.equal(observe(fixture.theory.asof, failedB), null);
    const failedC = bars.map((bar) => (bar.time === "2024-09-02" ? { ...bar, low: 4.2, close: 4.2 } : bar));
    const projection = observe(fixture.theory.asof, failedC);
    assert.equal(projection.invalidatedAt, "2024-09-02");
    assert.equal(projection.invalidationReason, "B_BROKE_A_START");
    assert.equal(projection.cTime, undefined);
    assert.equal(projection.cEligible, false);
    assert.equal(ordinaryCWaveLevels(projection).length, 0);
    assert.equal(ordinaryCWaveLevels(projection, "2024-08-30").length, 3);
});

test("missing second target, malformed dates and invalid anchors cannot classify an ordinary A", () => {
    for (const mutate of [
        (event) => {
            delete event.two_t;
            event.levels = event.levels.filter((level) => level.name !== "2T 投影");
        },
        (event) => {
            event.available_at = "2024-02-30";
        },
        (event) => {
            event.shape[0].value = NaN;
        },
        (event) => {
            event.two_t = 7;
        },
    ]) {
        const theory = structuredClone(fixture.theory);
        mutate(theory.events[0]);
        assert.equal(ordinaryCWaveProjections(bars, theory).length, 0);
    }
});

test("the user's rounded anchors produce the supplied exact arithmetic without altering real adjusted bars", () => {
    const projection = {
        origin: 4.22,
        aHigh: 7.88,
        bTime: "2024-07-25",
        bLow: 4.62,
        target0618: 4.62 + 0.618 * (7.88 - 4.22),
        target: 4.62 + 7.88 - 4.22,
        target1618: 4.62 + 1.618 * (7.88 - 4.22),
    };
    const levels = ordinaryCWaveLevels(projection);
    for (const [index, value] of [6.88188, 8.28, 10.54188].entries())
        assert.ok(Math.abs(levels[index].price - value) < 1e-12);
    const history = [
        { time: projection.bTime, high: 4.74, close: 4.71 },
        { time: "2025-01-03", high: 9.32, close: 7.83 },
    ];
    assert.deepEqual(
        levels.map((level) => targetLevelGuide({}, level, history).targetState),
        ["已触及", "已触及", "待达成"],
    );
});
