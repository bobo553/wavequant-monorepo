import assert from "node:assert/strict";
import test from "node:test";

globalThis.window = { LightweightCharts: { LineSeries: Symbol("LineSeries") } };
globalThis.document = { documentElement: {} };
globalThis.MutationObserver = class {
    observe() {}
};
const { PriceChart } = await import("../public/charts.js");
delete globalThis.window;
delete globalThis.document;
delete globalThis.MutationObserver;

function renderLevels(kind, raw = {}) {
    const series = [],
        guides = [];
    const selected = {
        kind,
        category: "orders",
        raw,
        time: "2026-05-18",
        levels: [
            { name: "一饱", price: 6.25, stage: "one_p", anchor_at: "2026-05-18" },
            { name: "二吐", price: 7.3, stage: "two_t", anchor_at: "2026-05-18" },
            { name: "五顶（预估）", price: 10.45, stage: "five_top", anchor_at: "2026-05-18" },
            { name: "十满（预估）", price: 16.75, stage: "ten_full", anchor_at: "2026-05-18" },
        ].map((level) => (kind === "order" ? { ...level, name: level.name.replace("（预估）", "") } : level)),
    };
    const owner = {
        selected,
        options: { levels: true, fills: true, diagnostics: true },
        data: { bars: [{ time: selected.time, high: 5.2, close: 5.1 }], asof: selected.time },
        container: { dataset: {} },
        levelLines: [],
        clearLevels() {},
        targetGuideOverlay: {
            setGuides(items) {
                guides.push(...items);
            },
        },
        chart: {
            addSeries(_type, options) {
                const line = {
                    options,
                    setData(data) {
                        this.data = data;
                    },
                };
                series.push(line);
                return line;
            },
        },
    };
    PriceChart.prototype.drawLevels.call(owner);
    return { series, guides };
}

test("estimated and confirmed distant targets do not expand the candle price scale", () => {
    for (const [kind, raw] of [
        ["wave-projection", {}],
        ["order", { event: "n_completed" }],
    ]) {
        const { series, guides } = renderLevels(kind, raw);
        assert.equal(series.length, 4);
        assert.equal(series[0].options.autoscaleInfoProvider, undefined);
        assert.equal(series[1].options.autoscaleInfoProvider, undefined);
        for (const line of series.slice(2)) {
            assert.equal(line.options.autoscaleInfoProvider(), null);
            assert.equal(line.options.lastValueVisible, false);
        }
        assert.deepEqual(
            guides.slice(2).map(({ stage, price }) => ({ stage, price })),
            [
                { stage: "five_top", price: 10.45 },
                { stage: "ten_full", price: 16.75 },
            ],
        );
    }
});
