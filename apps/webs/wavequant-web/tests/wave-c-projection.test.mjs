import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { avoidLabelCollisions, markerGroups } from "../public/annotations.js";
import { targetLevelGuide } from "../public/target-level-guides.js";
import {
    waveCProjection,
    waveCProjectionAnnotation,
    waveCProjectionEvidenceAnnotations,
    waveCProjectionForSelection,
    waveCProjectionFromStructure,
    waveCProjectionLegs,
    waveCProjectionLevels,
    waveCProjectionsFromStructure,
} from "../public/wave-c-projection.js";

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
        ["2024-03-25", 5.3, 4.95, 4.99],
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

test("projection needs to reach one-P and rejects a B low below the N origin", () => {
    assert.equal(waveCProjection(bars, [n], "2026-01-02"), null);
    assert.equal(waveCProjection(bars.slice(0, 3), [n], "2026-01-03"), null);
    const broken = bars.map((bar) => ({ ...bar }));
    broken[3].low = 8.5;
    assert.equal(waveCProjection(broken, [n], "2026-01-03")?.bLow, 8.5);
    broken[3].low = 7.9;
    broken[3].close = 7.9;
    assert.equal(waveCProjection(broken, [n], "2026-01-03"), null);
});

test("Xianfeng 2020 A/B wave keeps the April 28 B wick and projects C from the February 4 N origin", () => {
    const history = [
        ["2020-02-04", 3.27, 2.9, 3.23],
        ["2020-02-10", 3.46, 3.21, 3.42],
        ["2020-02-17", 3.53, 3.29, 3.45],
        ["2020-03-02", 4.39, 3.88, 3.89],
        ["2020-04-28", 3.45, 3.13, 3.41],
    ].map(([time, high, low, close]) => ({ time, high, low, close }));
    const attack = {
        ...n,
        time: "2020-02-10",
        available_at: "2020-02-10",
        defense: 3.21,
        shape: [{ time: "2020-02-04", value: 2.9 }],
        levels: [{ name: "1P 投影", price: 4.02 }],
    };
    const projection = waveCProjection(history, [attack], "2020-03-02");
    assert.deepEqual(
        { ...projection, target: Number(projection.target.toFixed(2)) },
        {
            nTime: "2020-02-10",
            origin: 2.9,
            oneP: 4.02,
            aTime: "2020-03-02",
            aHigh: 4.39,
            bTime: "2020-04-28",
            bLow: 3.13,
            target0618: 4.05082,
            target: 4.62,
        },
    );
    const levels = waveCProjectionLevels(projection);
    assert.deepEqual(
        levels.map((level) => [level.stage, level.anchor_at, level.available_at]),
        [
            ["c_0618", "2020-04-28", "2020-04-28"],
            ["c_equal", "2020-04-28", "2020-04-28"],
        ],
    );
    assert.deepEqual(
        levels.map((level) => targetLevelGuide({ time: "2020-04-28" }, level, history, "2020-04-28")?.start),
        ["2020-04-28", "2020-04-28"],
    );
    assert.deepEqual(
        levels.map((level) => targetLevelGuide({ time: "2020-04-28" }, level, history, "2020-04-28")?.end),
        [null, null],
    );
    assert.equal(waveCProjection(history.slice(0, 4), [attack], "2020-03-02"), null);
    const lostOrigin = history.map((bar) => ({ ...bar }));
    lostOrigin[4].low = 2.89;
    lostOrigin[4].close = 2.89;
    assert.equal(waveCProjection(lostOrigin, [attack], "2020-03-02"), null);
    lostOrigin.splice(4, 0, { time: "2020-04-27", high: 3.6, low: 3.2, close: 3.3 });
    lostOrigin[5].high = 4.4;
    assert.equal(waveCProjection(lostOrigin, [attack], "2020-03-02"), null);
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

const xianfeng = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave.json", import.meta.url)));
const xianfengBars = xianfeng.bars.map(([time, open, high, low, close, volume]) => ({
    time,
    open,
    high,
    low,
    close,
    volume,
}));
const xianfengTheory = xianfeng.theory;
const history = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave_history.json", import.meta.url)));
const historyBars = [
    ...xianfengBars,
    ...history.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    })),
];
const historyTheory = { ...xianfengTheory, asof: history.asof, secondary_trends: history.secondary_trends };

test("historical ABC survives removal of its A high from the active landmark list", () => {
    assert.equal(
        historyTheory.secondary_trends.bear_to_bull_highs.some((high) => high.time === "2020-03-02"),
        false,
    );
    const projection = waveCProjectionFromStructure(historyBars, historyTheory);
    assert.ok(projection);
    assert.equal(projection.aTime, "2020-03-02");
    assert.equal(projection.bTime, "2020-04-28");
    assert.equal(projection.bKnownAt, "2020-05-06");
    assert.ok(Math.abs(projection.target - 4.62) < 1e-10);
    assert.equal(waveCProjectionsFromStructure(historyBars, historyTheory).length, 1);
});

test("later ABC observations do not replace the earlier group", () => {
    const shifted = JSON.parse(
        JSON.stringify({ bars: xianfengBars, theory: xianfengTheory }).replaceAll("2020-", "2021-"),
    );
    const theory = {
        asof: shifted.theory.asof,
        lecture_drawing: {
            strokes: [...xianfengTheory.lecture_drawing.strokes, ...shifted.theory.lecture_drawing.strokes],
        },
        secondary_trends: {
            ...historyTheory.secondary_trends,
            bear_to_bull_highs: shifted.theory.secondary_trends.bear_to_bull_highs,
        },
    };
    const projections = waveCProjectionsFromStructure([...historyBars, ...shifted.bars], theory);
    assert.deepEqual(
        projections.map((p) => [p.aTime, p.bTime]),
        [
            ["2020-03-02", "2020-04-28"],
            ["2021-03-02", "2021-04-28"],
        ],
    );
});

test("historical evidence rejects unavailable, equal-key and mismatched B confirmation", () => {
    assert.equal(waveCProjectionFromStructure(historyBars, { ...historyTheory, asof: "2020-04-01" }), null);
    const equalKey = structuredClone(historyTheory);
    equalKey.secondary_trends.strokes[0].points[0].broken_key.value = 4.39;
    assert.equal(waveCProjectionFromStructure(historyBars, equalKey), null);
    const mismatched = structuredClone(historyTheory);
    mismatched.secondary_trends.strokes[0].points[1].confirmed_by.value = 3.12;
    assert.equal(waveCProjectionFromStructure(historyBars, mismatched), null);
});

test("confirmed historical B stays fixed after a later origin failure and targets stop before that failure", () => {
    const later = [
        ...historyBars,
        { time: "2020-06-01", open: 2.88, high: 2.89, low: 2.8, close: 2.85, volume: 1 },
        { time: "2020-06-02", open: 4.9, high: 5, low: 4.8, close: 4.9, volume: 1 },
    ];
    const projection = waveCProjectionFromStructure(later, { ...historyTheory, asof: "2020-06-02" });
    assert.ok(projection);
    assert.equal(projection.bTime, "2020-04-28");
    assert.equal(projection.invalidatedAt, "2020-06-01");
    assert.deepEqual(waveCProjectionAnnotation(projection, later, "2020-06-02").levels, []);
    const annotation = waveCProjectionAnnotation(projection, later, "2020-05-29");
    assert.equal(annotation.levels[1].valid_until, undefined);
    assert.equal(targetLevelGuide(annotation, annotation.levels[1], later, "2020-05-29").end, null);
});

test("real Xianfeng daily bars reveal the February 10 N, February 17 squeeze and April 28 B", () => {
    const projection = waveCProjectionFromStructure(xianfengBars, xianfengTheory);
    assert.ok(projection);
    assert.deepEqual(
        [projection.originTime, projection.nTime, projection.squeezeTime, projection.aTime, projection.bTime],
        ["2020-02-04", "2020-02-10", "2020-02-17", "2020-03-02", "2020-04-28"],
    );
    assert.equal(projection.origin, 2.9);
    assert.equal(projection.oneP, 4.02);
    assert.ok(Math.abs(projection.target0618 - 4.05082) < 1e-10);
    assert.ok(Math.abs(projection.target - 4.62) < 1e-10);
    const annotation = waveCProjectionAnnotation(projection);
    assert.equal(annotation.time, "2020-04-28");
    assert.equal(annotation.sourceTime, "2020-03-02");
    assert.equal(annotation.title, "B / C");
    assert.deepEqual(
        annotation.levels.map((level) => level.price),
        [projection.target0618, projection.target, projection.target1618],
    );
    const laterFormalN = {
        ...n,
        time: "2020-02-17",
        available_at: "2020-02-17",
        defense: 3.28,
        shape: [{ time: "2020-02-07", value: 3.15 }],
        levels: [{ name: "1P 投影", price: 3.91 }],
    };
    assert.equal(waveCProjectionForSelection(xianfengBars, [laterFormalN], "2020-03-02", projection), projection);
});

test("A origin, N, squeeze, A high and B/C are visible at their historical prices", () => {
    const projection = waveCProjectionFromStructure(xianfengBars, xianfengTheory);
    const annotations = [...waveCProjectionEvidenceAnnotations(projection), waveCProjectionAnnotation(projection)];
    assert.deepEqual(
        annotations.map(({ time, title, price }) => [time, title, price]),
        [
            ["2020-02-04", "A 起", 2.9],
            ["2020-02-10", "正 N", 3.46],
            ["2020-02-17", "轧空", 3.53],
            ["2020-03-02", "A 顶", 4.39],
            ["2020-04-28", "B / C", 3.13],
        ],
    );
    const groups = markerGroups(annotations, { rules: false, tertiaryAbc: true }, 65);
    assert.equal(markerGroups(annotations, { rules: true, tertiaryAbc: false }, 65).length, 0);
    assert.deepEqual(
        groups.map(({ marker }) => [marker.time, marker.text, marker.position, marker.price]),
        [
            ["2020-02-04", "A 起", "atPriceBottom", 2.9],
            ["2020-02-10", "正 N", "aboveBar", undefined],
            ["2020-02-17", "轧空", "aboveBar", undefined],
            ["2020-03-02", "A 顶", "atPriceTop", 4.39],
            ["2020-04-28", "B / C", "atPriceBottom", 3.13],
        ],
    );
    assert.deepEqual(
        waveCProjectionLegs(projection).map(({ title, points }) => [title, points]),
        [
            [
                "A 浪",
                [
                    { time: "2020-02-04", value: 2.9 },
                    { time: "2020-03-02", value: 4.39 },
                ],
            ],
            [
                "B 浪",
                [
                    { time: "2020-03-02", value: 4.39 },
                    { time: "2020-04-28", value: 3.13 },
                ],
            ],
        ],
    );
    const crowded = markerGroups(
        [
            ...annotations,
            {
                id: "other-rule",
                time: projection.bTime,
                title: "其他规则",
                kind: "rule",
                category: "rules",
                priority: 100,
            },
        ],
        { rules: true, tertiaryAbc: true },
        65,
    );
    avoidLabelCollisions(crowded, (time) => xianfengBars.findIndex((bar) => bar.time === time) * 20);
    assert.equal(crowded.find((group) => group.id === annotations.at(-1).id)?.marker.text, "B / C");
});

test("structural projection uses only available A evidence and invalidates a joint origin break", () => {
    assert.equal(waveCProjectionFromStructure(xianfengBars, { ...xianfengTheory, asof: "2020-04-01" }), null);
    const lost = xianfengBars.map((bar) => ({ ...bar }));
    lost.at(-1).low = 2.89;
    lost.at(-1).close = 2.89;
    assert.equal(waveCProjectionFromStructure(lost, xianfengTheory), null);
    const noSqueeze = xianfengBars.map((bar) => ({ ...bar }));
    for (const bar of noSqueeze) {
        if (bar.time > "2020-02-10" && bar.time < "2020-03-02") bar.volume = 1;
    }
    assert.equal(waveCProjectionFromStructure(noSqueeze, xianfengTheory), null);
});

test("full-history Guofang local observation connects C only after its confirmation", () => {
    const actual = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
    const actualBars = actual.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const projection = waveCProjectionsFromStructure(actualBars, actual.theory).find(
        (item) => item.nTime === "2024-02-23" && item.aTime === "2024-04-12",
    );
    assert.ok(projection);
    const annotation = waveCProjectionAnnotation(projection, actualBars, actual.theory.asof);
    assert.match(annotation.sourceLabel, /局部波段 A\/B\/C/);
    assert.match(annotation.description, /按正 N 原点的局部波段观察确认/);
    assert.match(annotation.description, /来源为一级已确认端点/);
    const legs = waveCProjectionLegs(projection);
    assert.deepEqual(
        legs.map((leg) => leg.title),
        ["A 浪", "B 浪", "C 浪"],
    );
    assert.deepEqual(legs[2].points, [
        { time: "2024-07-25", value: projection.bLow },
        { time: "2025-01-03", value: projection.cHigh },
    ]);
    const asof = "2025-01-21";
    const prefix = waveCProjectionsFromStructure(
        actualBars.filter((bar) => bar.time <= asof),
        { ...actual.theory, asof },
    ).find((item) => item.nTime === "2024-02-23" && item.aTime === "2024-04-12");
    assert.ok(prefix);
    assert.equal(prefix.cTime, undefined);
    assert.deepEqual(
        waveCProjectionLegs(prefix).map((leg) => leg.title),
        ["A 浪", "B 浪"],
    );
});
