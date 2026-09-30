import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { buyNTargetLevels } from "../public/buy-n-targets.js";

const history = JSON.parse(
    readFileSync(
        new URL("../../../../packages/wavequant-core/tests/fixtures/xianfeng_2026_resistance.json", import.meta.url),
    ),
);
const bars = history.bars
    .filter(([day]) => day >= "2026-04-29" && day <= "2026-06-09")
    .map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
const marker = {
    id: "xianfeng-may18",
    kind: "fill",
    side: "BUY",
    status: "filled",
    time: "2026-05-18",
    signal_time: "2026-05-18",
    price: 5.1,
    target: 5.38,
    decision_evidence: [
        {
            event: "long_signal",
            channel: "mature_shallow_squeeze",
            attack: bars.findIndex((bar) => bar.time === "2026-05-11"),
        },
    ],
};
const oldN = {
    event: "n_completed",
    direction: "up",
    time: "2026-05-11",
    available_at: "2026-05-11",
    one_p: 4.97,
    two_t: 5.38,
    n_level: 0,
};
const launchN = {
    event: "n_completed",
    direction: "up",
    time: "2026-05-18",
    available_at: "2026-05-18",
    one_p: 6.25,
    two_t: 7.3,
    n_level: 1,
    levels: [],
    shape: [
        { time: "2026-04-29", value: 4.15 },
        { time: "2026-05-12", value: 4.85 },
        { time: "2026-05-18", value: 4.38 },
        { time: "2026-05-18", value: 5.2 },
    ],
};
const view = { asof: "2026-05-18", bars, markers: [marker] };
const theory = { asof: view.asof, events: [oldN, launchN] };

test("Xianfeng May 18 buy shows its new positive N, C targets and explicitly estimated distant targets", () => {
    const sample = bars.find((bar) => bar.time === marker.time);
    assert.deepEqual(
        [sample.open, sample.high, sample.low, sample.close, sample.volume],
        [4.38, 5.2, 4.38, 5.1, 79_253_633],
    );
    const item = buildAnnotations(view, theory).find((annotation) => annotation.id === marker.id);
    assert.ok(!item.levels.some((level) => level.price === 5.38));
    assert.deepEqual(
        item.levels.filter((level) => level.stage).map(({ stage, price }) => [stage, price]),
        [
            ["c_0618", 4.8126],
            ["c_equal", 5.08],
            ["one_p", 6.25],
            ["two_t", 7.3],
            ["five_top", 10.45],
            ["ten_full", 16.75],
        ],
    );
    assert.ok(item.levels.filter((level) => level.stage).every((level) => level.n_date === "2026-05-18"));
    const estimates = item.levels.filter((level) => level.estimated);
    assert.equal(estimates.length, 2);
    assert.ok(estimates.every((level) => level.name.includes("预估叠箱") && level.available_at === marker.time));
    assert.match(item.description, /预估.*后续以已确认/);
});

test("C-wave evidence retains frozen A targets while its launch N supplies all four N targets", () => {
    const wave = {
        event: "long_signal",
        wave_entry_path: "one_p_held_defense_rebound",
        wave_entry_n_date: oldN.time,
        wave_b_low_date: "2026-05-15",
        wave_c_0618_target: 5.8,
        wave_equal_target: 6.5,
    };
    const buy = { ...marker, decision_evidence: [wave] };
    const selected = buildAnnotations({ ...view, markers: [buy] }, theory).find((item) => item.id === buy.id);
    assert.deepEqual(
        selected.levels.filter((level) => level.stage).map(({ stage, price }) => [stage, price]),
        [
            ["c_0618", 5.8],
            ["c_equal", 6.5],
            ["one_p", 6.25],
            ["two_t", 7.3],
            ["five_top", 10.45],
            ["ten_full", 16.75],
        ],
    );
    assert.equal(selected.levels.find((level) => level.stage === "c_equal").anchor_at, "2026-05-15");
});

test("later known stack or push targets replace estimates only in their own historical prefix", () => {
    const current = {
        ...launchN,
        levels: [
            { name: "五顶（堆箱 · 已满足）", stage: "five_top", price: 9.1, available_at: "2026-05-25" },
            { name: "十满（叠箱 · 推演中）", stage: "ten_full", price: 16.3, available_at: "2026-06-09" },
        ],
    };
    const all = { asof: "2026-06-09", events: [oldN, current] };
    const early = buyNTargetLevels(marker, null, view, all);
    assert.equal(early.find((level) => level.stage === "five_top").price, 10.45);
    const later = buyNTargetLevels(marker, null, { ...view, asof: "2026-05-25" }, all);
    const five = later.find((level) => level.stage === "five_top");
    assert.equal(five.price, 9.1);
    assert.equal(five.anchor_at, "2026-05-25");
    assert.equal(five.estimated, undefined);
    assert.equal(later.find((level) => level.stage === "ten_full").estimated, true);
    const latest = buyNTargetLevels(marker, null, { ...view, asof: "2026-06-09" }, all);
    assert.equal(latest.find((level) => level.stage === "ten_full").price, 16.3);
    const stale = buyNTargetLevels(marker, null, { ...view, asof: "2026-06-09" }, { ...all, asof: marker.time });
    assert.equal(stale.find((level) => level.stage === "five_top").estimated, true);
});

test("future N confirmation cannot be associated retroactively with the buy", () => {
    const delayed = { ...launchN, available_at: "2026-05-19" };
    const levels = buyNTargetLevels(marker, null, { ...view, asof: "2026-06-09" }, { events: [delayed] });
    assert.deepEqual(levels, []);
});

test("selecting an ordinary C buy draws the broken target to its first break and the unbroken target above B", async () => {
    globalThis.window = { LightweightCharts: { LineSeries: Symbol("line") } };
    globalThis.MutationObserver = class {
        observe() {}
    };
    globalThis.document = { documentElement: {} };
    const { PriceChart } = await import("../public/charts.js");
    const market = history.bars
        .filter(([day]) => day >= "2026-01-06" && day <= "2026-02-04")
        .map(([time, open, high, low, close]) => ({ time, open, high, low, close }));
    const buy = {
        id: "xianfeng-january",
        kind: "fill",
        side: "BUY",
        status: "filled",
        time: "2026-01-29",
        signal_time: "2026-01-29",
        price: 5.34,
        target: 6.03,
        decision_evidence: [
            {
                event: "long_signal",
                wave_entry_path: "one_p_held_defense_rebound",
                wave_b_low_date: "2026-01-27",
                wave_c_0618_target: 5.50666,
                wave_equal_target: 6.03,
            },
        ],
    };
    const data = { asof: "2026-02-04", bars: market, markers: [buy] };
    const selected = buildAnnotations(data, null)[0];
    const fake = {
        data,
        selected,
        options: { levels: true, fills: true },
        levelLines: [],
        container: { dataset: {} },
        targetGuideOverlay: {
            setGuides(guides) {
                this.guides = guides;
            },
        },
        chart: {
            addSeries(_type, options) {
                return {
                    options,
                    setData(points) {
                        this.points = points;
                    },
                };
            },
            removeSeries() {},
        },
        clearLevels: PriceChart.prototype.clearLevels,
    };
    PriceChart.prototype.drawLevels.call(fake);
    const targets = fake.levelLines.filter((line) => line.options.title.startsWith("C 浪目标"));
    assert.deepEqual(targets[0].points, [
        { time: "2026-01-27", value: 5.50666 },
        { time: "2026-01-30", value: 5.50666 },
    ]);
    assert.deepEqual(targets[1].points, [{ time: "2026-01-27", value: 6.03 }]);
    assert.ok(targets.every((line) => !line.options.priceLineVisible && !line.options.pointMarkersVisible));
    assert.deepEqual(
        fake.targetGuideOverlay.guides.map(({ start, end, price }) => [start, end, price]),
        [["2026-01-27", null, 6.03]],
    );
    fake.selected = null;
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(fake.targetGuideOverlay.guides, []);
    assert.equal(fake.container.dataset.levelCount, "0");
});
