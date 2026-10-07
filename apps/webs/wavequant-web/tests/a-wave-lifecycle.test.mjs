import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { classifyAAttack, isAOriginBroken } from "../public/a-wave-rules.js";
import { confirmedCWaveProjection } from "../public/confirmed-c-wave.js";
import { structuralCWaveProjections } from "../public/structural-c-wave.js";
import { waveCProjection, waveCProjectionAnnotation } from "../public/wave-c-projection.js";

const boundaries = JSON.parse(
    readFileSync(
        new URL("../../../../packages/wavequant-core/tests/fixtures/a_wave_rule_boundaries.json", import.meta.url),
    ),
);
const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_parent_abc.json", import.meta.url)));
const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
const aHigh = bars.find((bar) => bar.time === "2024-03-25").high;
const origin = bars.find((bar) => bar.time === "2024-02-08").low;
const findParent = (data, theory, asof = "2024-09-03") =>
    structuralCWaveProjections(data, { ...theory, asof }).find(
        (item) => item.originTime === "2024-02-08" && item.aTime === "2024-03-25",
    );
const theoryForClass = (ordinary) => {
    const theory = structuredClone(fixture.theory);
    theory.events = theory.events.filter((event) => event.time === "2024-03-05");
    theory.events[0].levels.find((level) => level.stage === "two_t").price = aHigh + (ordinary ? 0.01 : 0);
    return theory;
};

test("Core and Web share strict one-P and inclusive two-T classification boundaries", () => {
    for (const entry of boundaries) assert.equal(classifyAAttack(entry.high, entry.one_p, entry.two_t), entry.class);
    assert.equal(classifyAAttack(NaN, 12, 14), null);
    assert.equal(classifyAAttack(true, 12, 14), null);
    assert.equal(isAOriginBroken({ low: origin }, origin), false);
    assert.equal(isAOriginBroken({ low: origin - 0.01, close: 10 }, origin), true);
});

for (const ordinary of [false, true]) {
    test(`${ordinary ? "ordinary" : "strong"} parent A survives the long B below squeeze defense`, () => {
        const theory = theoryForClass(ordinary);
        theory.events[0].n_invalidated_at = "2024-04-16";
        const projection = findParent(bars, theory);
        assert.equal(projection.aAttackClass, ordinary ? "non_strong" : "strong");
        assert.equal(projection.bTime, "2024-08-28");
        assert.equal(projection.bBrokeASqueezeLow, true);
        assert.equal(projection.bSqueezeBreakAt, "2024-04-16");
        assert.deepEqual(
            [projection.bDuration, projection.bConsolidationDuration, projection.bFormationDuration],
            [106, 4, 110],
        );
        assert.equal(projection.aIsValid, true);
        const annotation = waveCProjectionAnnotation(projection, bars, "2024-09-03");
        assert.equal(annotation.levels.length, ordinary ? 3 : 2);
        assert.match(annotation.description, /A 起点最低价严格跌破.*相等仍有效/);
        assert.match(annotation.description, /106 个交易日.*110 个交易日.*4 日/);
        assert.doesNotMatch(annotation.description, /双破|undefined|NaN/);
        if (ordinary) assert.doesNotMatch(annotation.description, /已满足等浪/);
        assert.deepEqual(
            projection,
            findParent(
                bars.filter((bar) => bar.time <= "2024-09-03"),
                theory,
            ),
        );
    });

    test(`${ordinary ? "ordinary" : "strong"} A equality remains valid; a wick loss permanently cancels later C`, () => {
        const theory = theoryForClass(ordinary);
        const touch = bars.map((bar) => (bar.time === "2024-08-28" ? { ...bar, low: origin } : bar));
        // Formal endpoints must carry the same exact OHLC price.
        for (const stroke of theory.reversal_trends.strokes)
            for (const point of stroke.points)
                if (point.time === "2024-08-28" && point.kind === "L") point.value = origin;
        assert.equal(findParent(touch, theory)?.aIsValid, true);
        const broken = bars.map((bar) => (bar.time === "2024-09-04" ? { ...bar, low: origin - 0.01 } : bar));
        assert.ok(broken.find((bar) => bar.time === "2024-09-04").close > origin);
        const invalid = findParent(broken, theoryForClass(ordinary), fixture.asof);
        assert.equal(invalid.invalidatedAt, "2024-09-04");
        assert.equal(invalid.aIsValid, false);
        assert.equal(invalid.cEligible, false);
        assert.equal(invalid.cTime, undefined);
        assert.deepEqual(waveCProjectionAnnotation(invalid, broken, fixture.asof).levels, []);
    });
}

test("an N already invalid at the A high cannot supply a new parent measurement", () => {
    for (const ordinary of [false, true]) {
        const theory = theoryForClass(ordinary);
        theory.events[0].n_invalidated_at = "2024-03-25";
        assert.equal(findParent(bars, theory), undefined);
    }
});

test("parent one-P equality is unclassified and a strict break is ordinary", () => {
    const theory = theoryForClass(true);
    const oneP = theory.events[0].levels.find((level) => level.stage === "one_p");
    theory.events[0].levels.find((level) => level.stage === "two_t").price = aHigh + 1;
    oneP.price = aHigh;
    assert.equal(findParent(bars, theory), undefined);
    oneP.price -= 0.01;
    assert.equal(findParent(bars, theory)?.aAttackClass, "non_strong");
    oneP.price += 0.01;
    oneP.price += 0.01;
    assert.equal(findParent(bars, theory), undefined);
});

test("manual selection does not revive an old A after an origin loss during C", () => {
    const data = [
        { time: "2024-01-01", high: 9, low: 8, close: 8.5 },
        { time: "2024-01-02", high: 10, low: 9, close: 9.8 },
        { time: "2024-01-03", high: 12, low: 10, close: 11.8 },
        { time: "2024-01-04", high: 11, low: 8.5, close: 9.5 },
        { time: "2024-01-05", high: 13, low: 9, close: 12.5 },
        { time: "2024-01-06", high: 14, low: 7.99, close: 13 },
    ];
    const event = {
        event: "n_completed",
        direction: "up",
        time: "2024-01-02",
        available_at: "2024-01-02",
        defense: 9,
        shape: [{ time: "2024-01-01", value: 8 }],
        levels: [{ stage: "one_p", price: 11.99 }],
    };
    event.n_invalidated_at = "2024-01-04";
    assert.equal(waveCProjection(data.slice(0, 5), [event], "2024-01-03")?.bLow, 8.5);
    assert.equal(waveCProjection(data.slice(0, 5), [{ ...event, n_invalidated_at: "2024-01-03" }], "2024-01-03"), null);
    assert.equal(waveCProjection(data, [event], "2024-01-03"), null);
    data.push({ time: "2024-01-07", high: 20, low: 10, close: 19 });
    assert.equal(waveCProjection(data, [event], "2024-01-03"), null);
});

test("a later new A has its own lifetime after the old A fails", () => {
    const data = [
        { time: "2024-01-01", high: 9, low: 8, close: 8.5 },
        { time: "2024-01-02", high: 12, low: 9, close: 11.5 },
        { time: "2024-01-03", high: 10, low: 7.5, close: 9 },
        { time: "2024-01-04", high: 11, low: 8, close: 10.5 },
        { time: "2024-01-05", high: 13, low: 10, close: 12.5 },
        { time: "2024-01-06", high: 12, low: 9, close: 10 },
    ];
    const point = (index, kind) => ({
        time: data[index].time,
        available_at: data[index].time,
        kind,
        value: data[index][kind === "H" ? "high" : "low"],
    });
    const observe = (start, high, eventDate) =>
        confirmedCWaveProjection({
            bars: data,
            asof: data.at(-1).time,
            event: { time: eventDate, available_at: eventDate, defense: 9 },
            origin: point(start, "L"),
            a: point(high, "H"),
            points: [point(2, "L"), point(5, "L")],
            isHigh: () => true,
        });
    assert.equal(observe(0, 1, "2024-01-02"), null);
    assert.equal(observe(2, 4, "2024-01-04")?.originTime, "2024-01-03");
});
