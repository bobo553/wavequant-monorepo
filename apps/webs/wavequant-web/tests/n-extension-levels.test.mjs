import assert from "node:assert/strict";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";

test("N extension levels only appear from their own availability date without changing the N date", () => {
    const view = {
        asof: "2026-08-07",
        bars: [{ time: "2026-07-27" }, { time: "2026-08-07" }],
        markers: [],
    };
    const event = {
        id: "n-july-27",
        event: "n_completed",
        direction: "up",
        time: "2026-07-27",
        available_at: "2026-07-27",
        price: 10,
        levels: [
            { name: "2T 投影", price: 12 },
            { name: "五顶投影", price: 15, available_at: "2026-08-07" },
            { name: "十满投影", price: 20, available_at: "2026-08-07" },
        ],
    };
    const theory = { asof: "2026-07-27", events: [event] };
    assert.deepEqual(
        buildAnnotations(view, theory)[0].levels.map((level) => level.name),
        ["2T 投影"],
    );
    const current = buildAnnotations(view, { ...theory, asof: "2026-08-07" })[0];
    assert.equal(current.time, "2026-07-27");
    assert.equal(current.levels.length, 3);
    assert.equal(event.levels.length, 3);
    assert.equal(
        buildAnnotations({ ...view, asof: "2026-07-27" }, { ...theory, asof: "2026-08-07" })[0].levels.length,
        1,
    );
});

test("strong A buy displays five top immediately and ten full only when known", () => {
    const proof = {
        event: "long_signal",
        wave_entry_path: "two_t_strong_a_resistance_rebreak",
        wave_entry_n_date: "2023-07-03",
        wave_five_top_target: 5.85,
        wave_c_0618_target: 5.86374,
        wave_equal_target: 6.41,
    };
    const marker = {
        id: "buy-july-26",
        kind: "fill",
        side: "BUY",
        status: "filled",
        time: "2023-07-26",
        price: 5.76,
        target: 5.86374,
        signal_time: "2023-07-26",
        decision_evidence: [proof],
    };
    const view = {
        asof: "2023-07-26",
        markers: [marker],
        bars: [{ time: "2023-07-03" }, { time: "2023-07-26" }],
    };
    const n = {
        event: "n_completed",
        direction: "up",
        time: "2023-07-03",
        available_at: "2023-07-03",
        levels: [
            { name: "五顶（强A再攻击 · 观察）", price: 5.85, stage: "five_top", available_at: "2023-07-26" },
            { name: "十满（叠箱 · 推演中）", price: 8.41, stage: "ten_full", available_at: "2023-07-27" },
        ],
    };
    const early = buildAnnotations(view, { asof: "2023-07-26", events: [n] }).find((item) => item.id === marker.id);
    assert.deepEqual(
        early.levels.map((level) => level.name),
        ["成交价", "C 浪目标 0.618×A", "C 浪目标 1×A", "五顶（强A再攻击 · 观察）"],
    );
    assert.deepEqual(
        early.levels
            .filter((level) => level.stage?.startsWith("c_"))
            .map(({ stage, price, available_at }) => ({ stage, price, available_at })),
        [
            { stage: "c_0618", price: 5.86374, available_at: "2023-07-26" },
            { stage: "c_equal", price: 6.41, available_at: "2023-07-26" },
        ],
    );
    const later = buildAnnotations(
        { ...view, asof: "2023-07-27", bars: [...view.bars, { time: "2023-07-27" }] },
        { asof: "2023-07-27", events: [n] },
    ).find((item) => item.id === marker.id);
    assert.equal(later.levels.find((level) => level.stage === "ten_full")?.price, 8.41);
    assert.equal(later.levels.find((level) => level.stage === "ten_full")?.available_at, "2023-07-27");
    const fallback = buildAnnotations(view, null).find((item) => item.id === marker.id);
    assert.equal(fallback.levels.find((level) => level.stage === "five_top")?.price, 5.85);
});

test("Guofang April 1 ordinary C-wave buy labels both projected levels once", () => {
    const marker = {
        id: "ordinary-c",
        kind: "fill",
        side: "BUY",
        status: "filled",
        time: "2024-04-01",
        price: 6.495070867219145,
        target: 8.242135908334124,
        decision_evidence: [
            {
                event: "long_signal",
                wave_entry_path: "one_p_held_defense_rebound",
                wave_c_0618_target: 7.448774813563287,
                wave_equal_target: 8.242135908334124,
            },
        ],
    };
    const item = buildAnnotations({ asof: "2024-04-01", bars: [{ time: "2024-04-01" }], markers: [marker] }, null)[0];
    assert.deepEqual(
        item.levels.map(({ name, stage }) => ({ name, stage })),
        [
            { name: "成交价", stage: undefined },
            { name: "C 浪目标 0.618×A", stage: "c_0618" },
            { name: "C 浪目标 1×A", stage: "c_equal" },
        ],
    );
    assert.deepEqual(
        item.levels.slice(1).map(({ price, available_at }) => [price, available_at]),
        [
            [7.448774813563287, "2024-04-01"],
            [8.242135908334124, "2024-04-01"],
        ],
    );
});

test("legacy C-wave buys without B dates use direct target labels and preserve existing line geometry", async () => {
    globalThis.window = { LightweightCharts: { LineSeries: Symbol("line") } };
    globalThis.MutationObserver = class {
        observe() {}
    };
    globalThis.document = { documentElement: {} };
    const { PriceChart } = await import("../public/charts.js");
    const view = {
        asof: "2023-08-01",
        bars: [{ time: "2023-07-26" }, { time: "2023-08-01" }],
        markers: [
            {
                id: "guofang-c-buy",
                kind: "fill",
                side: "BUY",
                status: "filled",
                time: "2023-07-26",
                signal_time: "2023-07-26",
                price: 7.523724696038756,
                target: 7.6592301126997056,
                decision_evidence: [
                    {
                        event: "long_signal",
                        wave_entry_path: "two_t_strong_a_resistance_rebreak",
                        wave_c_0618_target: 7.6592301126997056,
                        wave_equal_target: 8.372756128751465,
                    },
                ],
            },
        ],
    };
    const lines = [];
    const fake = {
        data: view,
        selected: buildAnnotations(view, null)[0],
        options: { levels: true, fills: true },
        levelLines: [],
        container: { dataset: {} },
        chart: {
            addSeries(_type, options) {
                const line = {
                    options,
                    setData(points) {
                        this.points = points;
                    },
                };
                lines.push(line);
                return line;
            },
            removeSeries() {},
        },
        targetGuideOverlay: {
            setGuides(guides) {
                this.guides = guides;
            },
        },
        waveEndpointOverlay: { setPoints() {} },
        clearLevels: PriceChart.prototype.clearLevels,
        displayedWaveProjection: PriceChart.prototype.displayedWaveProjection,
    };
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(
        lines
            .filter((line) => line.options.title.startsWith("C 浪目标"))
            .map((line) => [
                line.options.title,
                line.points[0].value,
                line.options.priceLineVisible,
                line.options.autoscaleInfoProvider,
            ]),
        [
            ["C 浪目标 0.618×A", 7.6592301126997056, false, undefined],
            ["C 浪目标 1×A", 8.372756128751465, false, undefined],
        ],
    );
    assert.ok(
        lines
            .filter((line) => line.options.title.startsWith("C 浪目标"))
            .every((line) => line.options.lastValueVisible === false),
    );
    assert.equal(fake.targetGuideOverlay.guides.length, 2);
    assert.ok(fake.targetGuideOverlay.guides.every((guide) => guide.start === "2023-07-26"));
    fake.selected.levels = fake.selected.levels.map((level) => ({ ...level, anchor_at: undefined }));
    PriceChart.prototype.drawLevels.call(fake);
    assert.ok(fake.targetGuideOverlay.guides.every((guide) => guide.statusKnown === false));
    fake.selected = null;
    PriceChart.prototype.drawLevels.call(fake);
    assert.equal(fake.container.dataset.levelCount, "0");
});
