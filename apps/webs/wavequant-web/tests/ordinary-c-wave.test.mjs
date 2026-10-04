import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { ordinaryCWaveLevels, ordinaryCWaveProjections } from "../public/ordinary-c-wave.js";
import { ordinaryLocalWavePoints } from "../public/ordinary-local-wave.js";
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

const fullFixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
const fullBars = fullFixture.bars.map(([time, open, high, low, close, volume]) => ({
    time,
    open,
    high,
    low,
    close,
    volume,
}));
const observeFull = (asof, input = fullBars, theory = fullFixture.theory) =>
    ordinaryCWaveProjections(input, { ...theory, asof }).find(
        (projection) => projection.nTime === "2024-02-23" && projection.aTime === "2024-04-12",
    );

test("the actual 2018 full-history response observes the local ABC without changing formal level two", () => {
    const before = structuredClone(fullFixture.theory);
    assert.equal(fullBars[0].time, "2018-01-02");
    assert.equal(fullBars.length, 2123);
    assert.equal(fullFixture.theory.events.length, 192);
    assert.ok(fullFixture.theory.events.every((event) => event.event === "n_completed" && event.direction === "up"));
    assert.equal(ordinaryCWaveProjections(fullBars, fullFixture.theory).length, 7);
    assert.equal(
        fullFixture.theory.secondary_trends.strokes.some((stroke) =>
            stroke.points.some((point) => point.time === "2024-04-12"),
        ),
        false,
    );
    const projection = observeFull("2025-01-22");
    assert.ok(projection, "the real full-history response must preserve the February N's ordinary A");
    assert.deepEqual(
        [projection.originTime, projection.nTime, projection.aTime, projection.bTime, projection.cTime],
        ["2024-02-08", "2024-02-23", "2024-04-12", "2024-07-25", "2025-01-03"],
    );
    assert.deepEqual(
        [projection.aKnownAt, projection.bKnownAt, projection.cKnownAt],
        ["2024-04-30", "2024-08-14", "2025-01-22"],
    );
    assert.equal(projection.projectionSource, "n_origin_local_structure");
    assert.equal(projection.formalTrend, false);
    assert.equal(projection.sourceTrendLevel, 1);
    assert.equal(projection.trendLevel, 2);
    assert.equal(projection.bBrokeASqueezeLow, true);
    assert.deepEqual(fullFixture.theory, before);
});

test("the local source reducer requires strict key breaks and upgrades its running extreme", () => {
    const source = [1, 4, 2, 5, 2, 6, 1.9, 6, 1.8, 6.1].map((value, index) => ({
        time: `2024-01-${String(index + 1).padStart(2, "0")}`,
        available_at: `2024-01-${String(index + 2).padStart(2, "0")}`,
        kind: index % 2 ? "H" : "L",
        value,
    }));
    assert.equal(ordinaryLocalWavePoints(source.slice(0, 5)).length, 0);
    const highsOnly = ordinaryLocalWavePoints(source.slice(0, 8));
    assert.deepEqual(
        highsOnly.map(({ time, available_at }) => ({ time, available_at })),
        [{ time: "2024-01-06", available_at: "2024-01-08" }],
    );
    const completed = ordinaryLocalWavePoints(source);
    assert.deepEqual(
        completed.map(({ time, available_at, local_wave_turn }) => ({
            time,
            available_at,
            local_wave_turn,
        })),
        [
            { time: "2024-01-06", available_at: "2024-01-08", local_wave_turn: "up_to_down" },
            { time: "2024-01-09", available_at: "2024-01-11", local_wave_turn: "down_to_up" },
        ],
    );
    assert.ok(completed.every((point) => point.flip === undefined));
});

test("full-history local observations preserve B versions and reject future endpoint confirmation", () => {
    assert.equal(observeFull("2024-07-09"), undefined);
    assert.equal(observeFull("2024-07-10").bTime, "2024-06-24");
    assert.equal(observeFull("2024-08-13").bTime, "2024-06-24");
    assert.equal(observeFull("2024-08-14").bTime, "2024-07-25");
    for (const asof of ["2024-07-10", "2024-08-14", "2024-12-09", "2025-01-06", "2025-01-21", "2025-01-22"]) {
        const prefix = fullBars.filter((bar) => bar.time <= asof);
        assert.deepEqual(observeFull(asof), observeFull(asof, prefix));
    }
    // 一级反弹高只是 C 内部一段，不能单独冻结整段终点。
    for (const asof of ["2024-12-09", "2025-01-06", "2025-01-21"]) assert.equal(observeFull(asof).cTime, undefined);
    assert.equal(observeFull("2025-01-22").cTime, "2025-01-03");
});

test("full-history local targets freeze at the completed C and never use a later group's rally", () => {
    const projection = observeFull(fullFixture.theory.asof);
    assert.deepEqual(
        guides(projection, fullFixture.theory.asof, fullBars).map(({ firstTouchedAt, targetState }) => ({
            firstTouchedAt,
            targetState,
        })),
        [
            { firstTouchedAt: "2024-10-11", targetState: "已触及" },
            { firstTouchedAt: "2024-12-03", targetState: "已触及" },
            { firstTouchedAt: null, targetState: "本段结束未达成" },
        ],
    );
    assert.ok(fullBars.some((bar) => bar.time > projection.cKnownAt && bar.high > projection.target1618));
    const later = [...fullBars, { time: "2026-10-01", open: 4, high: 4.3, low: 4, close: 4, volume: 1 }];
    assert.equal(observeFull("2026-10-01", later).aIsValid, true);
});

test("full-history local observations terminate on origin failure before B or before C confirmation", () => {
    const failedB = fullBars.map((bar) => (bar.time === "2024-07-24" ? { ...bar, low: 4.2, close: 4.2 } : bar));
    assert.equal(observeFull("2024-08-14", failedB), undefined);
    assert.equal(observeFull(fullFixture.theory.asof, failedB), undefined);
    const failedC = fullBars.map((bar) => (bar.time === "2025-01-15" ? { ...bar, low: 4, close: 4.1 } : bar));
    for (const asof of ["2025-01-15", "2025-01-22", fullFixture.theory.asof]) {
        const projection = observeFull(asof, failedC);
        assert.equal(projection.invalidatedAt, "2025-01-15");
        assert.equal(projection.cTime, undefined);
        assert.equal(ordinaryCWaveLevels(projection, asof).length, 0);
    }
});

test("formal secondary observations take precedence over the separate local fallback", () => {
    const theory = { ...fullFixture.theory, secondary_trends: fixture.theory.secondary_trends };
    const projection = observeFull("2025-01-22", fullBars, theory);
    assert.equal(projection.projectionSource, undefined);
    assert.equal(projection.bTime, "2024-07-25");
    assert.equal(projection.cTime, "2025-01-03");
    const disconnected = structuredClone(fullFixture.theory);
    for (const stroke of disconnected.reversal_trends.strokes)
        stroke.points = stroke.points.filter((point) => point.time !== "2024-02-08");
    assert.equal(observeFull("2025-01-22", fullBars, disconnected), undefined);
});

test("removing the target positive N rejects only its local group and retains other real observations", () => {
    const theory = {
        ...fullFixture.theory,
        events: fullFixture.theory.events.filter((event) => event.time !== "2024-02-23"),
    };
    assert.equal(observeFull(fullFixture.theory.asof, fullBars, theory), undefined);
    assert.ok(ordinaryCWaveProjections(fullBars, theory).length > 0);
});

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
