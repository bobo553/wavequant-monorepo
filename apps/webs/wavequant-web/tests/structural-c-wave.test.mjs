import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { structuralCWaveProjections } from "../public/structural-c-wave.js";
import { targetLevelGuide } from "../public/target-level-guides.js";
import {
    waveCProjectionAnnotation,
    waveCProjectionEvidenceAnnotations,
    waveCProjectionLegs,
    waveCProjectionsFromStructure,
} from "../public/wave-c-projection.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_parent_abc.json", import.meta.url)));
const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
const find = (items) => items.find((item) => item.originTime === "2024-02-08" && item.aTime === "2024-03-25");
const observe = (asof = fixture.asof, data = bars, theory = fixture.theory) =>
    find(waveCProjectionsFromStructure(data, { ...theory, asof }));
const parentPoints = (theory) => theory.secondary_trends.strokes.flatMap((stroke) => stroke.points);

test("genuine Xinhua parent A retains February 8 separately from the internal March 5 N", () => {
    const before = structuredClone(fixture);
    const projection = observe();
    assert.equal(fixture.source.original_bar_count, 1699);
    assert.equal(fixture.price_basis, "causal_adjusted_equivalent");
    assert.deepEqual(
        [projection.originTime, projection.nTime, projection.aTime, projection.bTime, projection.cTime],
        ["2024-02-08", "2024-03-05", "2024-03-25", "2024-08-28", "2024-10-31"],
    );
    assert.deepEqual(
        [projection.aKnownAt, projection.bKnownAt, projection.cKnownAt],
        ["2024-04-30", "2024-09-03", "2024-11-08"],
    );
    assert.equal(projection.projectionSource, "confirmed_parent_wave");
    assert.equal(projection.formalTrend, false);
    assert.equal(projection.aAttackClass, "strong");
    assert.equal(projection.origin, 3.532767357186747);
    assert.equal(projection.aHigh, 6.389262105997745);
    assert.equal(projection.bLow, 3.605696909119602);
    assert.equal(projection.nOriginTime, "2024-02-29");
    assert.equal(projection.nOrigin, 4.299882554747297);
    assert.equal(projection.bBrokeASqueezeLow, true);
    assert.equal(projection.aIsValid, true);
    const n = buildAnnotations({ bars, asof: fixture.asof, markers: [] }, fixture.theory).find(
        (item) => item.raw?.event === "n_completed" && item.time === "2024-03-05",
    );
    assert.equal(n.raw.shape[0].time, "2024-02-29");
    assert.deepEqual(
        n.levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
        [5.289057414759589, 5.783644844765735],
    );
    assert.equal(fixture.orders[0].side, "BUY");
    assert.equal(fixture.orders[0].status, "filled");
    assert.equal(fixture.orders[0].timestamp.slice(0, 10), "2024-03-20");
    assert.deepEqual(fixture, before);
});

test("the parent B progresses past April and July only on each endpoint confirmation", () => {
    assert.equal(observe("2024-04-29"), undefined);
    assert.equal(observe("2024-04-30").bTime, "2024-04-22");
    assert.equal(observe("2024-08-28").bTime, "2024-07-09");
    assert.equal(observe("2024-09-02").bTime, "2024-07-09");
    const projection = observe("2024-09-03");
    assert.equal(projection.bTime, "2024-08-28");
    assert.equal(projection.anchorVersions[0].bTime, "2024-04-22");
    assert.ok(
        projection.anchorVersions.every((version) => !version.validUntil || version.knownAt <= version.validUntil),
    );
    assert.equal(projection.anchorVersions.at(-1).knownAt, "2024-09-03");
    const july = projection.anchorVersions.find((version) => version.bTime === "2024-07-09");
    assert.equal(july.supersededAt, "2024-09-03");
    assert.equal(july.validUntil, "2024-09-02");
    assert.equal(projection.cTime, undefined);
    assert.equal(observe("2024-11-07").cTime, undefined);
    assert.equal(observe("2024-11-08").cTime, "2024-10-31");
});

test("full-history inputs and truncated historical prefixes publish identical parent observations", () => {
    for (const asof of [
        "2024-03-25",
        "2024-04-30",
        "2024-07-15",
        "2024-09-02",
        "2024-09-03",
        "2024-10-28",
        "2024-11-08",
    ])
        assert.deepEqual(
            observe(asof),
            observe(
                asof,
                bars.filter((bar) => bar.time <= asof),
            ),
        );
    const future = bars.map((bar) => (bar.time > "2024-09-02" ? { ...bar, low: 0.1, close: 0.2, high: 100 } : bar));
    assert.deepEqual(observe("2024-09-02", future), observe("2024-09-02"));
});

test("parent projection requires coherent confirmed formal A and same-path primary endpoints", () => {
    for (const mutate of [
        (theory) => {
            parentPoints(theory).find((point) => point.time === "2024-02-08").flip = undefined;
        },
        (theory) => {
            parentPoints(theory).find((point) => point.time === "2024-03-25").preceding_turn.time = "2024-02-29";
        },
        (theory) => {
            parentPoints(theory).find((point) => point.time === "2024-02-08").confirmed_by.available_at = "2025-01-01";
        },
        (theory) => {
            theory.reversal_trends.strokes = theory.reversal_trends.strokes.map((stroke) => ({
                ...stroke,
                points: stroke.points.filter((point) => point.time !== "2024-02-08"),
            }));
        },
    ]) {
        const theory = structuredClone(fixture.theory);
        mutate(theory);
        assert.equal(find(structuralCWaveProjections(bars, theory)), undefined);
    }
    const theory = structuredClone(fixture.theory);
    theory.events = [];
    assert.equal(find(structuralCWaveProjections(bars, theory)), undefined);
});

test("strong parent classification requires the internal N's valid two-T with equality allowed", () => {
    const theory = structuredClone(fixture.theory);
    theory.events = theory.events.filter((event) => event.time === "2024-03-05");
    const twoT = theory.events[0].levels.find((level) => level.stage === "two_t");
    twoT.price = 6.389262105997745 + 0.01;
    assert.equal(find(structuralCWaveProjections(bars, theory)), undefined);
    twoT.price = 6.389262105997745;
    assert.ok(find(structuralCWaveProjections(bars, theory)));
    twoT.price = NaN;
    assert.equal(find(structuralCWaveProjections(bars, theory)), undefined);
});

test("A origin failure before the internal N is fatal and a later recovery cannot revive it", () => {
    const broken = bars.map((bar) => (bar.time === "2024-02-20" ? { ...bar, low: 3, close: 3 } : bar));
    assert.equal(observe(fixture.asof, broken), undefined);
    const beforeB = bars.map((bar) => (bar.time === "2024-08-29" ? { ...bar, low: 3, close: 3 } : bar));
    assert.equal(observe(fixture.asof, beforeB), undefined);
    const beforeC = bars.map((bar) => (bar.time === "2024-11-04" ? { ...bar, low: 3, close: 3 } : bar));
    const invalid = observe(fixture.asof, beforeC);
    assert.equal(invalid.invalidatedAt, "2024-11-04");
    assert.equal(invalid.cTime, undefined);
    assert.deepEqual(waveCProjectionAnnotation(invalid, beforeC, fixture.asof).levels, []);
    const afterC = bars.map((bar) => (bar.time === "2024-11-18" ? { ...bar, low: 3, close: 3 } : bar));
    assert.deepEqual(observe(fixture.asof, afterC), observe());
});

test("parent labels, legs and targets use the full A amplitude and freeze at the original C", () => {
    const projection = observe();
    const annotation = waveCProjectionAnnotation(projection, bars, fixture.asof);
    assert.match(annotation.description, /内部正 N 不替换整段 A 起点/);
    assert.deepEqual(
        annotation.levels.map((level) => level.price),
        [5.371010663884799, 6.4621916579306, 8.227505412695798],
    );
    assert.deepEqual(
        annotation.levels.map((level) => level.available_at),
        ["2024-09-03", "2024-09-03", "2024-10-28"],
    );
    assert.ok(annotation.levels.every((level) => level.valid_until === "2024-10-31"));
    assert.equal(waveCProjectionAnnotation(projection, bars, "2024-09-02").levels.length, 0);
    assert.equal(waveCProjectionAnnotation(observe("2024-10-25"), bars, "2024-10-25").levels.length, 2);
    assert.equal(waveCProjectionAnnotation(observe("2024-10-28"), bars, "2024-10-28").levels.length, 3);
    const extension = targetLevelGuide(annotation, annotation.levels[2], bars, fixture.asof);
    assert.equal(extension.firstTouchedAt, null);
    assert.equal(extension.targetState, "本段结束未达成");
    assert.deepEqual(
        waveCProjectionEvidenceAnnotations(projection, bars, fixture.asof).map((item) => [item.title, item.time]),
        [
            ["A 起", "2024-02-08"],
            ["正 N", "2024-03-05"],
            ["A 顶", "2024-03-25"],
            ["C 顶", "2024-10-31"],
        ],
    );
    assert.deepEqual(
        waveCProjectionLegs(projection).map((leg) => leg.points.map((point) => point.time)),
        [
            ["2024-02-08", "2024-03-25"],
            ["2024-03-25", "2024-08-28"],
            ["2024-08-28", "2024-10-31"],
        ],
    );
});
