import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { nTargetAt, nTargetGuide, nTargetLevels, nTargetObservations } from "../public/n-target-focus.js";

const bars = [
    { time: "2024-03-01", high: 4.5, close: 4.3 },
    { time: "2024-03-04", high: 4.6, close: 4.4 },
    { time: "2024-03-05", high: 7, close: 4.6 },
    { time: "2024-03-06", high: 5.5, close: 5.1 },
    { time: "2024-03-07", high: 6, close: 5.5 },
    { time: "2024-03-08", high: 6.01, close: 5.5 },
    { time: "2024-03-11", high: 6.5, close: 6.3 },
];
const item = {
    id: "older-n",
    time: "2024-03-05",
    raw: { event: "n_completed", direction: "up", shape: [{ time: "2024-03-01" }] },
    levels: [
        { stage: "one_p", price: 5, anchor_at: "2024-03-04", available_at: "2024-03-05" },
        { stage: "two_t", price: 6, anchor_at: "2024-03-04", available_at: "2024-03-05" },
    ],
};

test("N focus covers the structure through the first strict two-t break without changing its prices", () => {
    const original = structuredClone(item);
    const observations = nTargetObservations([item], bars, "2024-03-11");
    assert.deepEqual(observations, [{ item, from: "2024-03-01", to: "2024-03-08" }]);
    for (const bar of bars.slice(0, -1)) assert.equal(nTargetAt(observations, bar.time), item);
    assert.equal(nTargetAt(observations, "2024-03-11"), null);
    assert.equal(nTargetAt(observations, null), null);
    assert.equal(nTargetAt(observations, "2024-02-29"), null);
    assert.deepEqual(item, original);
});

test("confirmation-day high and equality do not close N focus; replay cannot reveal a future N or break", () => {
    assert.deepEqual(nTargetObservations([item], bars, "2024-03-04"), []);
    for (const asof of ["2024-03-05", "2024-03-07"]) assert.equal(nTargetObservations([item], bars, asof)[0].to, asof);
    const later = { ...item, levels: item.levels.map((level) => ({ ...level, available_at: "2024-03-06" })) };
    assert.deepEqual(nTargetObservations([later], bars, "2024-03-05"), []);
    const sameDayBreak = bars.map((bar) => (bar.time === item.time ? { ...bar, close: 6.01 } : bar));
    assert.equal(nTargetObservations([item], sameDayBreak, "2024-03-11")[0].to, item.time);
});

test("overlapping N ranges choose an explicit hit, then selection, then the latest known structure", () => {
    const newer = { ...item, id: "newer-n", time: "2024-03-06" };
    const observations = nTargetObservations([item, newer], bars, "2024-03-11");
    assert.equal(nTargetAt(observations, "2024-03-07"), newer);
    assert.equal(nTargetAt(observations, "2024-03-07", null, item.id), item);
    assert.equal(nTargetAt(observations, "2024-03-07", newer.id, item.id), newer);
    assert.equal(nTargetAt(observations, "2024-03-11", item.id, newer.id), null);
    assert.equal(nTargetAt(observations, "2024-03-07", "unknown", "unknown"), newer);
});

test("expired, inverse, missing-anchor and incomplete target structures cannot leak into focus", () => {
    const expired = { ...item, levels: item.levels.map((level) => ({ ...level, valid_until: "2024-03-06" })) };
    const observations = nTargetObservations([expired], bars, "2024-03-11");
    assert.equal(nTargetAt(observations, "2024-03-06"), expired);
    assert.equal(nTargetAt(observations, "2024-03-07"), null);
    for (const invalid of [
        { ...item, raw: { ...item.raw, direction: "down" } },
        { ...item, levels: item.levels.slice(0, 1) },
        { ...item, levels: item.levels.map((level) => ({ ...level, price: NaN })) },
        { ...item, levels: item.levels.map((level) => ({ ...level, anchor_at: "2024-02-29" })) },
        { ...item, raw: { ...item.raw, shape: [{ time: "2024-02-29" }] } },
    ])
        assert.deepEqual(nTargetObservations([invalid], bars, "2024-03-11"), []);
});

test("known five-top and ten-full stages continue the original N focus after two-t", () => {
    const history = JSON.parse(
        readFileSync(
            new URL(
                "../../../../packages/wavequant-core/tests/fixtures/xiangyang_2023_shared_edge_n.json",
                import.meta.url,
            ),
        ),
    );
    const realBars = history.bars.map(([time, open, high, low, close]) => ({ time, open, high, low, close }));
    const original = {
        id: "xiangyang-june-12-n",
        time: "2023-06-12",
        raw: { event: "n_completed", direction: "up", shape: [{ time: "2023-06-07" }] },
        levels: [
            { stage: "one_p", price: 6.11, anchor_at: "2023-06-09", available_at: "2023-06-12" },
            { stage: "two_t", price: 6.51, anchor_at: "2023-06-09", available_at: "2023-06-12" },
            { stage: "five_top", price: 6.97, available_at: "2023-06-28", status: "已满足" },
            { stage: "ten_full", price: 9.37, available_at: "2023-06-29", status: "已满足" },
        ],
    };
    const snapshot = structuredClone(original);
    for (const [asof, end] of [
        ["2023-06-27", "2023-06-26"],
        ["2023-06-28", "2023-06-28"],
        ["2023-06-29", "2023-06-29"],
        ["2023-07-04", "2023-07-04"],
    ]) {
        const observations = nTargetObservations([original], realBars, asof);
        assert.deepEqual(observations, [{ item: original, from: "2023-06-07", to: end }], asof);
        assert.equal(nTargetAt(observations, end, original.id), original);
    }
    const complete = nTargetObservations([original], realBars, "2023-07-04");
    assert.equal(nTargetAt(complete, "2023-07-04"), original);
    assert.equal(nTargetAt(complete, "2023-07-05", original.id), null);
    assert.deepEqual(original, snapshot);
});

test("N extensions keep their own availability, inclusive reach and original drawing anchor", () => {
    const fiveTop = {
        stage: "five_top",
        price: 7.5,
        anchor_at: "2024-03-04",
        available_at: "2024-03-06",
        status: "已满足",
    };
    const extended = { ...item, levels: [...item.levels, fiveTop] };
    const prices = bars.map((bar) => (["2024-03-06", "2024-03-07"].includes(bar.time) ? { ...bar, high: 7.5 } : bar));
    assert.deepEqual(nTargetLevels(extended, "2024-03-05"), item.levels);
    const known = nTargetGuide(extended, fiveTop, prices, "2024-03-06");
    assert.equal(known.start, fiveTop.anchor_at);
    assert.equal(known.end, null);
    assert.equal(known.targetState, "推演中");
    const reached = nTargetGuide(extended, fiveTop, prices, "2024-03-07");
    assert.equal(reached.start, fiveTop.anchor_at);
    assert.equal(reached.end, "2024-03-07");
    assert.equal(reached.targetState, "已满足");
    assert.equal(nTargetObservations([extended], prices, "2024-03-11")[0].to, "2024-03-07");
});

test("another N, estimated targets and unknown events cannot prolong an original N", () => {
    const extension = { stage: "ten_full", price: 9, available_at: "2024-03-06" };
    const other = { ...item, id: "other-n", time: "2024-03-06", levels: [...item.levels, extension] };
    const observations = nTargetObservations([item, other], bars, "2024-03-11");
    assert.equal(observations.find(({ item: source }) => source.id === item.id).to, "2024-03-08");
    assert.equal(nTargetAt(observations, "2024-03-11", item.id), other);
    for (const invalidExtension of [
        { ...extension, estimated: true },
        { ...extension, available_at: undefined },
    ]) {
        const source = { ...item, levels: [...item.levels, invalidExtension] };
        assert.equal(nTargetObservations([source], bars, "2024-03-11")[0].to, "2024-03-08");
        assert.deepEqual(nTargetLevels(source, "2024-03-11"), item.levels);
    }
    for (const status of ["已失效", "回调暂停"]) {
        const reference = { ...extension, status, valid_until: "2024-03-06" };
        const source = { ...item, levels: [...item.levels, reference] };
        assert.equal(nTargetObservations([source], bars, "2024-03-11")[0].to, "2024-03-08");
        assert.equal(nTargetGuide(source, reference, bars, "2024-03-11").targetState, status);
    }
    const future = { ...other, raw: { ...other.raw, available_at: "2024-03-12" } };
    assert.deepEqual(nTargetObservations([future], bars, "2024-03-11"), []);
});
