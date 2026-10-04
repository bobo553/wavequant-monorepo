import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { combinedAAnnotations, combinedAObservations } from "../public/combined-a-wave.js";
import { waveCProjectionsFromStructure } from "../public/wave-c-projection.js";

function candle(time, open, high, low, close, volume = 100) {
    return { time, open, high, low, close, volume };
}

function sample() {
    const bars = [
        candle("2024-01-02", 100, 120, 95, 110),
        candle("2024-01-03", 60, 70, 50, 60),
        candle("2024-01-04", 60, 72, 58, 65),
        candle("2024-02-01", 45, 48, 40, 44),
        candle("2024-02-05", 50, 56, 49, 54),
        candle("2024-03-01", 75, 80, 73, 76),
        candle("2024-04-01", 55, 58, 50, 52),
        candle("2024-06-03", 94, 100, 92, 95),
        candle("2024-06-04", 84, 85, 75, 78),
        candle("2024-06-05", 82, 90, 78, 84),
        candle("2024-06-06", 84, 86, 80, 82),
        candle("2024-06-07", 82, 86, 80, 83),
        candle("2024-06-10", 84, 86, 80, 85),
    ];
    const point = (time, kind, available_at) => {
        const index = bars.findIndex((bar) => bar.time === time);
        return {
            index,
            time,
            kind,
            value: bars[index][kind === "H" ? "high" : "low"],
            available_at,
            state: "reversal",
        };
    };
    const before = [point("2024-01-02", "H", "2024-01-03"), point("2024-01-03", "L", "2024-01-04")];
    const primary = {
        id: "primary",
        kind: "reversal",
        points: [
            ...structuredClone(before),
            point("2024-01-04", "H", "2024-01-05"),
            point("2024-02-01", "L", "2024-02-02"),
            point("2024-03-01", "H", "2024-03-04"),
            point("2024-04-01", "L", "2024-04-02"),
            point("2024-06-03", "H", "2024-06-07"),
            point("2024-06-04", "L", "2024-06-05"),
            point("2024-06-05", "H", "2024-06-07"),
        ],
    };
    const secondary = { id: "secondary-primary", source_path: "primary", trend_level: 2, points: before };
    const tertiary = {
        id: "tertiary-secondary-primary",
        source_path: "secondary-primary",
        trend_level: 3,
        points: structuredClone(before),
    };
    const projection = {
        nTime: "2024-02-05",
        originTime: "2024-02-01",
        origin: 40,
        aTime: "2024-03-01",
        aHigh: 80,
        aKnownAt: "2024-03-04",
        bTime: "2024-04-01",
        bLow: 50,
        bKnownAt: "2024-04-02",
        confirmedAt: "2024-04-02",
        cTime: "2024-06-03",
        cHigh: 100,
        cKnownAt: "2024-06-07",
        aIsValid: true,
        projectionSource: "n_origin_local_structure",
        sourcePath: "primary",
        sourceTrendLevel: 1,
        trendLevel: 2,
    };
    return {
        bars,
        projections: [projection],
        theory: {
            asof: "2024-06-10",
            events: [
                {
                    event: "n_completed",
                    direction: "up",
                    time: projection.nTime,
                    available_at: projection.nTime,
                    shape: [{ time: projection.originTime, value: projection.origin }],
                },
            ],
            reversal_trends: { strokes: [primary] },
            secondary_trends: { strokes: [secondary] },
            tertiary_trends: { strokes: [tertiary] },
        },
    };
}

function observe(input, asof = input.theory.asof, bars = input.bars) {
    return combinedAObservations(bars, { ...input.theory, asof }, input.projections);
}

function changeBar(input, time, changes) {
    const index = input.bars.findIndex((bar) => bar.time === time);
    input.bars[index] = { ...input.bars[index], ...changes };
}

function addBar(input, bar) {
    input.bars.push(bar);
    input.theory.asof = bar.time;
}

function formalSample() {
    const input = sample();
    const projection = input.projections[0];
    delete projection.sourcePath;
    delete projection.projectionSource;
    projection.sourceTrendLevel = 2;
    for (const [time, kind, available_at] of [
        [projection.aTime, "H", projection.aKnownAt],
        [projection.bTime, "L", projection.bKnownAt],
        [projection.cTime, "H", projection.cKnownAt],
    ]) {
        const index = input.bars.findIndex((bar) => bar.time === time);
        input.theory.secondary_trends.strokes[0].points.push({
            index,
            time,
            kind,
            value: input.bars[index][kind === "H" ? "high" : "low"],
            available_at,
            state: "reversal",
            flip: kind === "H" ? "翻多为空" : "翻空为多",
        });
    }
    return input;
}

test("confirmed overlapping ABC retains its half guide without requiring the preceding key to be broken", () => {
    const input = sample();
    const before = structuredClone(input);
    const [observation] = observe(input);
    assert.ok(observation);
    assert.equal(observation.price, 70);
    assert.equal(observation.start, "2024-06-07");
    assert.equal(observation.available_at, "2024-06-07");
    assert.equal(observation.end, "2024-06-10");
    assert.equal(observation.halfHeld, true);
    assert.equal(observation.firstCloseBelowHalf, null);
    assert.equal(observation.preconditions.length, 2);
    assert.ok(observation.preconditions.every((item) => item.scope === "source_confirmed_display_context"));
    assert.deepEqual(
        observation.preconditions.map((item) => [item.level, item.sourceLevel, item.key.time, item.key.value]),
        [
            [2, 1, "2024-01-04", 72],
            [3, 2, "2024-01-02", 120],
        ],
    );
    assert.deepEqual(observation.strengthObservations, []);
    assert.deepEqual(input, before);
});

test("either confirmed source level suffices but missing or disconnected ancestry does not", () => {
    for (const level of ["reversal_trends", "secondary_trends"]) {
        const input = sample();
        input.theory[level].strokes[0].points = input.theory[level].strokes[0].points.filter(
            (point) => point.time >= input.projections[0].originTime,
        );
        assert.equal(observe(input).length, 1);
        assert.equal(observe(input)[0].preconditions.length, 1);
    }
    const missing = sample();
    for (const level of ["reversal_trends", "secondary_trends"])
        missing.theory[level].strokes[0].points = missing.theory[level].strokes[0].points.filter(
            (point) => point.time >= missing.projections[0].originTime,
        );
    assert.deepEqual(observe(missing), []);
    const unrelated = sample();
    unrelated.theory.reversal_trends.strokes[0].id = "another-primary";
    unrelated.theory.secondary_trends.strokes[0].source_path = "another-primary";
    unrelated.theory.tertiary_trends.strokes[0].source_path = "another-secondary";
    assert.deepEqual(observe(unrelated), []);
});

test("future and mismatched source predecessor prices cannot create a frozen prerequisite", () => {
    for (const mutation of [(point) => (point.available_at = "2024-02-06"), (point) => (point.value += 0.01)]) {
        const input = sample();
        for (const level of ["reversal_trends", "secondary_trends"]) {
            for (const point of input.theory[level].strokes[0].points)
                if (point.kind === "H" && point.time < input.projections[0].originTime) mutation(point);
        }
        assert.deepEqual(observe(input), []);
    }
});

test("later source points do not replace the prerequisite frozen at the internal N", () => {
    const input = sample();
    const frozen = observe(input)[0].preconditions;
    for (const level of ["reversal_trends", "secondary_trends"]) {
        input.theory[level].strokes[0].points.push({
            index: 2,
            time: "2024-01-04",
            kind: "H",
            value: 72,
            available_at: "2024-06-10",
            state: "reversal",
        });
    }
    assert.deepEqual(observe(input)[0].preconditions, frozen);
});

test("display source evidence does not depend on future formal target-level membership", () => {
    const input = sample();
    input.theory.secondary_trends.strokes[0].points.push({
        index: 2,
        time: "2024-01-04",
        kind: "H",
        value: 72,
        available_at: "2024-06-10",
        state: "reversal",
    });
    const keys = (observation) =>
        observation.preconditions.map(({ level, sourceLevel, sourcePath, key, knownAt }) => ({
            level,
            sourceLevel,
            sourcePath,
            key,
            knownAt,
        }));
    const withFutureFormal = keys(observe(input)[0]);
    input.theory.secondary_trends.strokes[0].points = input.theory.secondary_trends.strokes[0].points.filter(
        (point) => point.available_at <= input.projections[0].nTime,
    );
    input.theory.tertiary_trends.strokes = [];
    assert.deepEqual(keys(observe(input)[0]), withFutureFormal);
});

test("formal ABC without an explicit source path infers its unique known secondary parent", () => {
    const input = formalSample();
    const before = structuredClone(input.projections);
    const [observation] = observe(input);
    assert.ok(observation);
    assert.equal(observation.id, observe(sample())[0].id);
    assert.equal(observation.preconditions[0].sourcePath, "primary");
    assert.deepEqual(input.projections, before);
});

test("formal ABC with two distinct matching source paths is ambiguous and cannot guess a parent", () => {
    const input = formalSample();
    input.theory.reversal_trends.strokes.push({
        ...structuredClone(input.theory.reversal_trends.strokes[0]),
        id: "another-primary",
    });
    input.theory.secondary_trends.strokes.push({
        ...structuredClone(input.theory.secondary_trends.strokes[0]),
        id: "secondary-another-primary",
        source_path: "another-primary",
    });
    assert.deepEqual(observe(input), []);
});

test("formal ABC cannot infer a source whose primary path is missing or whose endpoint is still future", () => {
    const missing = formalSample();
    missing.theory.secondary_trends.strokes[0].source_path = "missing-primary";
    assert.deepEqual(observe(missing), []);
    const future = formalSample();
    future.theory.secondary_trends.strokes[0].points.find(
        (point) => point.time === future.projections[0].cTime,
    ).available_at = "2024-06-11";
    assert.deepEqual(observe(future), []);
});

test("C confirmation and strict post-C overlap must both be visible", () => {
    const input = sample();
    assert.deepEqual(observe(input, "2024-06-06"), []);
    assert.equal(observe(input, "2024-06-07").length, 1);
    for (const bar of input.bars.filter((bar) => bar.time > "2024-06-03")) {
        bar.low = Math.max(80, bar.low);
        bar.open = Math.max(bar.open, bar.low);
        bar.close = Math.max(bar.close, bar.low);
    }
    assert.deepEqual(observe(input), []);
    changeBar(input, "2024-06-10", { low: 79.99 });
    assert.equal(observe(input)[0].start, "2024-06-10");
});

test("a new post-C high before or on the first overlap starts another phase and cannot form the old combination", () => {
    const laterOverlap = sample();
    for (const bar of laterOverlap.bars.filter((bar) => bar.time > "2024-06-03")) {
        bar.low = Math.max(80, bar.low);
        bar.open = Math.max(bar.open, bar.low);
        bar.close = Math.max(bar.close, bar.low);
    }
    changeBar(laterOverlap, "2024-06-07", { high: 100.01 });
    changeBar(laterOverlap, "2024-06-10", { low: 79.99 });
    assert.deepEqual(observe(laterOverlap), []);
    const simultaneous = sample();
    changeBar(simultaneous, "2024-06-04", { high: 100.01 });
    assert.deepEqual(observe(simultaneous), []);
    changeBar(laterOverlap, "2024-06-07", { high: 100 });
    assert.equal(observe(laterOverlap).length, 1);
});

test("unfinished C and invalid ABC never qualify as a combined observation", () => {
    for (const change of [
        { cKnownAt: "2024-06-11" },
        { cKnownAt: undefined },
        { cTime: undefined },
        { aIsValid: false },
        { cTime: "2024-03-01" },
        { cHigh: 99.99 },
    ]) {
        const input = sample();
        Object.assign(input.projections[0], change);
        assert.deepEqual(observe(input), []);
    }
});

test("a wick below half holds while close equality holds and a strictly lower close fails", () => {
    const input = sample();
    changeBar(input, "2024-06-04", { low: 69, close: 70 });
    assert.equal(observe(input)[0].halfHeld, true);
    changeBar(input, "2024-06-04", { close: 69.99 });
    const observation = observe(input)[0];
    assert.equal(observation.halfHeld, false);
    assert.deepEqual(observation.firstCloseBelowHalf, { time: "2024-06-04", close: 69.99 });
    assert.equal(observation.price, 70);
});

test("half lost before C confirmation remains lost after recovery and suppresses strength", () => {
    const input = sample();
    changeBar(input, "2024-06-04", { low: 68, close: 69 });
    addBar(input, candle("2024-06-11", 91, 99, 91, 98, 200));
    const observation = observe(input)[0];
    assert.equal(observation.start, "2024-06-07");
    assert.equal(observation.halfHeld, false);
    assert.deepEqual(observation.firstCloseBelowHalf, { time: "2024-06-04", close: 69 });
    assert.deepEqual(observation.strengthObservations, []);
});

test("strict low-only origin failure ends the guide permanently despite a recovered close", () => {
    const input = sample();
    addBar(input, candle("2024-06-11", 50, 105, 39.99, 101, 200));
    addBar(input, candle("2024-06-12", 106, 115, 106, 114, 300));
    const observation = observe(input)[0];
    assert.equal(observation.invalidatedAt, "2024-06-11");
    assert.equal(observation.end, "2024-06-11");
    assert.deepEqual(observation.strengthObservations, []);
});

test("origin equality is held but a prior origin loss cannot revive at C confirmation", () => {
    const equal = sample();
    changeBar(equal, "2024-06-04", { low: 40 });
    assert.equal(observe(equal)[0].invalidatedAt, null);
    const broken = sample();
    changeBar(broken, "2024-06-04", { low: 39.99 });
    assert.deepEqual(observe(broken), []);
});

test("the combination-confirmation candle cannot also supply a strength observation", () => {
    const input = sample();
    changeBar(input, "2024-06-07", { open: 91, high: 100, low: 91, close: 99, volume: 200 });
    assert.deepEqual(observe(input)[0].strengthObservations, []);
});

test("an unfilled volume gap may qualify independently of a known post-C high", () => {
    const input = sample();
    input.theory.reversal_trends.strokes[0].points = input.theory.reversal_trends.strokes[0].points.filter(
        (point) => point.time <= "2024-06-03",
    );
    addBar(input, candle("2024-06-11", 87, 89, 87, 88, 200));
    assert.equal(observe(input)[0].strengthObservations.length, 1);
});

test("gap equality and a filled gap are rejected without another qualifying breakout", () => {
    for (const [open, low] of [
        [86, 86],
        [87, 86],
        [86, 85.99],
    ]) {
        const input = sample();
        addBar(input, candle("2024-06-11", open, 88, low, 87.5, 200));
        assert.deepEqual(observe(input)[0].strengthObservations, []);
    }
});

test("gap observation requires strict volume growth and a bullish body", () => {
    for (const [open, close, volume, expected] of [
        [87, 88, 101, 1],
        [87, 88, 100, 0],
        [87, 87, 101, 0],
        [88, 87, 101, 0],
    ]) {
        const input = sample();
        addBar(input, candle("2024-06-11", open, 89, 87, close, volume));
        assert.equal(observe(input)[0].strengthObservations.length, expected);
    }
});

test("body breakout requires strict volume growth, a bullish body and a strict known-high close break", () => {
    for (const [open, high, low, close, volume, expected] of [
        [90, 96, 89, 95, 101, 1],
        [90, 96, 89, 95, 100, 0],
        [95, 96, 94, 95, 101, 0],
        [96, 97, 94, 95, 101, 0],
        [86, 91, 85.5, 90, 101, 0],
    ]) {
        const input = sample();
        changeBar(input, "2024-06-10", { high: 102 });
        addBar(input, candle("2024-06-11", open, high, low, close, volume));
        assert.equal(observe(input)[0].strengthObservations.length, expected);
    }
});

test("a medium-large bullish body uses inclusive 3 percent and 60 percent boundaries", () => {
    for (const [close, high, low, expected] of [
        [103, 104, 99, 1],
        [102.99, 104, 99, 0],
        [103, 104.01, 99, 0],
    ]) {
        const input = sample();
        changeBar(input, "2024-06-10", { open: 100, high: 102, low: 99, close: 101 });
        input.theory.reversal_trends.strokes[0].points.push({
            index: input.bars.findIndex((bar) => bar.time === "2024-06-10"),
            time: "2024-06-10",
            kind: "H",
            value: 102,
            available_at: "2024-06-10",
            state: "reversal",
        });
        addBar(input, candle("2024-06-11", 100, high, low, close, 200));
        assert.equal(observe(input)[0].strengthObservations.length, expected);
    }
});

test("body and volume observations cannot borrow a future high or a high from another path", () => {
    for (const mutation of [
        (input) => {
            for (const point of input.theory.reversal_trends.strokes[0].points)
                if (point.time > "2024-06-03") point.available_at = "2024-06-12";
        },
        (input) => {
            input.theory.reversal_trends.strokes.push({
                ...structuredClone(input.theory.reversal_trends.strokes[0]),
                id: "another-primary",
            });
            input.theory.reversal_trends.strokes[0].points = input.theory.reversal_trends.strokes[0].points.filter(
                (point) => point.time <= "2024-06-03",
            );
        },
        (input) => {
            input.theory.reversal_trends.strokes[0].points = input.theory.reversal_trends.strokes[0].points.filter(
                (point) => point.time <= "2024-06-03",
            );
        },
    ]) {
        const input = sample();
        changeBar(input, "2024-06-10", { high: 102 });
        mutation(input);
        addBar(input, candle("2024-06-11", 90, 96, 89, 95, 200));
        assert.deepEqual(observe(input)[0].strengthObservations, []);
    }
});

test("a high becoming known on the trigger candle cannot confirm that candle", () => {
    const input = sample();
    changeBar(input, "2024-06-10", { high: 102 });
    for (const point of input.theory.reversal_trends.strokes[0].points)
        if (point.time > "2024-06-03") point.available_at = "2024-06-11";
    addBar(input, candle("2024-06-11", 90, 96, 89, 95, 200));
    assert.deepEqual(observe(input)[0].strengthObservations, []);
});

test("a breakout is first crossing evidence and only the first qualifying strength observation is retained", () => {
    const input = sample();
    changeBar(input, "2024-06-10", { open: 90, high: 95, low: 89, close: 91 });
    addBar(input, candle("2024-06-11", 90, 95, 89, 94, 200));
    assert.deepEqual(observe(input)[0].strengthObservations, []);
    const first = sample();
    addBar(first, candle("2024-06-11", 87, 89, 87, 88, 200));
    addBar(first, candle("2024-06-12", 90, 94, 90, 93, 300));
    assert.equal(observe(first)[0].strengthObservations.length, 1);
});

test("later half failure keeps the earlier known strength observation while preventing revival", () => {
    const input = sample();
    addBar(input, candle("2024-06-11", 87, 89, 87, 88, 200));
    const recorded = observe(input)[0].strengthObservations;
    assert.equal(recorded.length, 1);
    addBar(input, candle("2024-06-12", 75, 80, 68, 69, 300));
    addBar(input, candle("2024-06-13", 81, 90, 81, 89, 400));
    const observation = observe(input)[0];
    assert.equal(observation.halfHeld, false);
    assert.deepEqual(observation.firstCloseBelowHalf, { time: "2024-06-12", close: 69 });
    assert.deepEqual(observation.strengthObservations, recorded);
});

test("failure on the attack candle takes precedence over a bullish body or volume breakout", () => {
    const input = sample();
    changeBar(input, "2024-06-10", { high: 102 });
    addBar(input, candle("2024-06-11", 65, 96, 64, 69, 200));
    const observation = observe(input)[0];
    assert.equal(observation.halfHeld, false);
    assert.deepEqual(observation.strengthObservations, []);
});

test("annotations remain observation categories and never become BUY or executed fills", () => {
    const input = sample();
    addBar(input, candle("2024-06-11", 87, 89, 87, 88, 200));
    const annotations = combinedAAnnotations(observe(input));
    assert.equal(annotations.length, 2);
    assert.ok(annotations.every((annotation) => annotation.category === "wave-projection"));
    assert.deepEqual(
        new Set(annotations.map((annotation) => annotation.kind)),
        new Set(["combined-a-wave", "combined-a-strength"]),
    );
    assert.ok(annotations.every((annotation) => annotation.side !== "BUY" && annotation.kind !== "fill"));
});

test("full history and an equally dated visible prefix produce the same combined observation", () => {
    const input = sample();
    addBar(input, candle("2024-06-11", 87, 89, 87, 88, 200));
    addBar(input, candle("2024-06-12", 75, 90, 39, 80, 200));
    for (const asof of ["2024-06-04", "2024-06-06", "2024-06-07", "2024-06-10", "2024-06-11", "2024-06-12"])
        assert.deepEqual(
            observe(input, asof),
            observe(
                input,
                asof,
                input.bars.filter((bar) => bar.time <= asof),
            ),
        );
});

const realFixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
const realBars = realFixture.bars.map(([time, open, high, low, close, volume]) => ({
    time,
    open,
    high,
    low,
    close,
    volume,
}));

test("actual full-history Guofang keeps the half guide and records the loss before C became known", () => {
    const theory = { ...realFixture.theory, asof: "2025-01-22" };
    const projections = waveCProjectionsFromStructure(realBars, theory);
    const observation = combinedAObservations(realBars, theory, projections).find(
        (item) => item.originTime === "2024-02-08" && item.cTime === "2025-01-03",
    );
    assert.ok(observation);
    assert.equal(observation.price, 6.767928288212305);
    assert.equal(observation.available_at, "2025-01-22");
    assert.equal(observation.halfHeld, false);
    assert.deepEqual(observation.firstCloseBelowHalf, { time: "2025-01-10", close: 6.27041344443578 });
    assert.deepEqual(observation.strengthObservations, []);
    assert.ok(observation.preconditions.length > 0);
    assert.deepEqual(
        observation.preconditions.map(({ level, sourceLevel, key, knownAt }) => ({
            level,
            sourceLevel,
            time: key.time,
            price: key.value,
            knownAt,
        })),
        [
            { level: 2, sourceLevel: 1, time: "2024-01-12", price: 9.535276078807719, knownAt: "2024-01-19" },
            { level: 3, sourceLevel: 2, time: "2023-08-01", price: 11.024346589744814, knownAt: "2023-12-15" },
        ],
    );
    const withoutFutureFormal = structuredClone(theory);
    withoutFutureFormal.secondary_trends.strokes = withoutFutureFormal.secondary_trends.strokes.map((stroke) => ({
        ...stroke,
        points: stroke.points.filter((point) => !(point.kind === "H" && point.available_at > "2024-02-23")),
    }));
    const independent = combinedAObservations(realBars, withoutFutureFormal, projections).find(
        (item) => item.originTime === observation.originTime && item.cTime === observation.cTime,
    );
    assert.deepEqual(
        independent.preconditions.map(({ level, sourceLevel, key, knownAt }) => ({ level, sourceLevel, key, knownAt })),
        observation.preconditions.map(({ level, sourceLevel, key, knownAt }) => ({ level, sourceLevel, key, knownAt })),
    );
    const future = { ...realFixture.theory, asof: realFixture.theory.asof };
    const final = combinedAObservations(realBars, future, waveCProjectionsFromStructure(realBars, future)).find(
        (item) => item.originTime === observation.originTime && item.cTime === observation.cTime,
    );
    assert.equal(final.halfHeld, false);
    assert.deepEqual(final.firstCloseBelowHalf, observation.firstCloseBelowHalf);
    assert.deepEqual(final.preconditions, observation.preconditions);
    assert.deepEqual(final.strengthObservations, []);
    for (const asof of ["2025-01-21", "2025-01-22", realFixture.theory.asof]) {
        const cutTheory = { ...realFixture.theory, asof };
        const cutProjections = waveCProjectionsFromStructure(realBars, cutTheory);
        assert.deepEqual(
            combinedAObservations(realBars, cutTheory, cutProjections),
            combinedAObservations(
                realBars.filter((bar) => bar.time <= asof),
                cutTheory,
                cutProjections,
            ),
        );
    }
});
