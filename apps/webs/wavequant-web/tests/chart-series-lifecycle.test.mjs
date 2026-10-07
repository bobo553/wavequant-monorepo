import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { JSDOM } from "jsdom";
import * as LightweightCharts from "lightweight-charts";

import { reuseCompletedBacktest } from "../public/completed-backtest-result.js";
import { StockBacktestTasks } from "../public/stock-backtest-tasks.js";

const dom = new JSDOM("<!doctype html><html><body></body></html>", { pretendToBeVisual: true });
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.HTMLCanvasElement = dom.window.HTMLCanvasElement;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
window.LightweightCharts = LightweightCharts;
window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
globalThis.requestAnimationFrame = window.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
globalThis.ResizeObserver = window.ResizeObserver = class {
    observe() {}
    disconnect() {}
};
// Canvas drawing is outside this model-level SDK regression; measurements are deterministic.
window.HTMLCanvasElement.prototype.getContext = function () {
    return new Proxy(
        {
            canvas: this,
            measureText: (text) => ({
                width: String(text).length * 6,
                actualBoundingBoxAscent: 8,
                actualBoundingBoxDescent: 2,
            }),
            createLinearGradient: () => ({ addColorStop() {} }),
        },
        { get: (context, key) => (key in context ? context[key] : () => {}) },
    );
};

const { PriceChart } = await import("../public/charts.js");
const initial = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave.json", import.meta.url)));
const history = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave_history.json", import.meta.url)));
const bars = [...initial.bars, ...history.bars].map(([time, open, high, low, close, volume]) => ({
    time,
    open,
    high,
    low,
    close,
    volume,
}));
const data = { symbol: "sz.300163", bars, markers: [], orders: [], asof: history.asof };
const theory = { ...initial.theory, asof: history.asof, shapes: [], secondary_trends: history.secondary_trends };

function chartHarness(onKeyboardNavigate = () => {}) {
    const container = document.createElement("div");
    document.body.append(container);
    const chart = new PriceChart(
        container,
        () => {},
        () => {},
        () => {},
        () => {},
        () => {},
        onKeyboardNavigate,
    );
    chart.chart.applyOptions({ autoSize: false, width: 800, height: 500 });
    chart.setData(data);
    chart.setTheory(theory);
    chart.chart.resize(800, 500, true);
    return chart;
}

const collections = {
    levelLines: "clearLevels",
    lastFallHighLines: "clearLastFallHighGuides",
    bullishTurnGuideLines: "clearBullishTurnGuides",
    tertiaryRetracementLines: "clearTertiaryRetracementGuides",
    combinedAWaveLines: "clearCombinedAWavePath",
    combinedARetracementLines: "clearCombinedARetracementGuides",
    polylineLines: "clearPolyline",
    lines: "clearTheory",
    waveAbLines: "clearWaveAbPath",
};

test("PriceChart arrow navigation updates the real SDK window and stops after destruction", () => {
    let pauses = 0;
    const chart = chartHarness(() => pauses++);
    const container = chart.container;
    let width = 800;
    const press = (key, repeat = false) => {
        const event = new window.KeyboardEvent("keydown", { key, repeat, bubbles: true, cancelable: true });
        document.activeElement.dispatchEvent(event);
        chart.chart.resize(++width, 500, true);
        return event.defaultPrevented;
    };
    try {
        chart.chart.timeScale().setVisibleLogicalRange({ from: 10, to: 50 });
        chart.chart.resize(++width, 500, true);
        const canvas = container.querySelectorAll("canvas")[1];
        for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) {
            canvas.dispatchEvent(
                new window.MouseEvent(type, { bubbles: true, cancelable: true, clientX: 300, clientY: 200 }),
            );
        }
        assert.equal(document.activeElement, container);
        assert.equal(press("ArrowUp"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 14, to: 46 });
        assert.equal(press("ArrowLeft"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 8, to: 40 });
        assert.equal(press("ArrowRight"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 14, to: 46 });
        assert.equal(press("ArrowDown"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 10, to: 50 });
        assert.equal(pauses, 4);
        for (const type of ["pointerdown", "mousedown", "pointermove", "mousemove", "pointerup", "mouseup"]) {
            const moving = type.includes("move") || type.includes("up");
            canvas.dispatchEvent(
                new window.MouseEvent(type, {
                    bubbles: true,
                    cancelable: true,
                    clientX: moving ? 380 : 300,
                    clientY: 200,
                    buttons: type.includes("up") ? 0 : 1,
                }),
            );
            if (type === "mousedown") assert.equal(document.activeElement, document.body, "SDK removes focus on press");
        }
        assert.equal(document.activeElement, container, "ending a drag restores focus even without click");
        chart.chart.timeScale().setVisibleLogicalRange({ from: 10, to: 50 });
        chart.chart.resize(++width, 500, true);
        for (let repeat = 0; repeat < 5; repeat++) assert.equal(press("ArrowUp", true), true);
        const enlarged = chart.chart.timeScale().getVisibleLogicalRange();
        assert.ok(Math.abs(enlarged.to - enlarged.from - 20) < 1e-6);
        for (let repeat = 0; repeat < 2; repeat++) assert.equal(press("ArrowRight", true), true);
        const moved = chart.chart.timeScale().getVisibleLogicalRange();
        assert.ok(Math.abs(moved.from - enlarged.from - 8) < 1e-6, "held arrows pan the actual SDK window");
        assert.ok(Math.abs(moved.to - moved.from - 20) < 1e-6);
        assert.equal(pauses, 11);
        assertOwnedSeries(chart);
    } finally {
        chart.destroy();
    }
    const released = new window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true });
    container.dispatchEvent(released);
    assert.equal(released.defaultPrevented, false);
    assert.equal(pauses, 11);
    assert.equal(container.hasAttribute("tabindex"), false);
});

function assertOwnedSeries(chart) {
    const expected = [chart.candles, chart.volume, ...Object.keys(collections).flatMap((field) => chart[field])];
    const actual = chart.chart.panes().flatMap((pane) => pane.getSeries());
    assert.equal(new Set(expected).size, expected.length, "each series has exactly one owner");
    assert.deepEqual(new Set(actual), new Set(expected), "no removed or orphaned SDK series remain");
}

test("target cleanup while the SDK crosshair is active does not reenter redraw or lose series ownership", () => {
    const chart = chartHarness();
    try {
        for (let repeat = 0; repeat < 3; repeat++) {
            chart.focusTrade("2020-05-29");
            chart.chart.resize(801 + repeat, 500, true);
            chart.chart.setCrosshairPosition(4.1, "2020-05-29", chart.candles);
            assert.ok(chart.levelLines.length > 0);
            chart.clearLevels();
            assert.equal(chart.levelLines.length, 0);
            assertOwnedSeries(chart);
            chart.setTheory(theory);
            chart.setData(data);
            chart.setTheory(theory);
            chart.focusTrade("2020-04-28");
        }
        assert.equal(chart.data, data);
        assert.equal(chart.candles.data().length, bars.length);
        assert.equal(chart.volume.data().length, bars.length);
        assertOwnedSeries(chart);
        chart.candles.setData(chart.candles.data());
        assert.ok(chart.hoveredWaveProjection, "normal SDK crosshair callbacks resume after cleanup");
        assertOwnedSeries(chart);
    } finally {
        chart.destroy();
    }
});

test("locating and retrying a completed result uses its cached job without recalculation", async () => {
    const chart = chartHarness();
    let calculations = 0;
    let reads = 0;
    const tasks = new StockBacktestTasks({ run: async () => (++calculations, {}) });
    const result = { ...data, result_scope: "stock", backtest: { status: "complete" } };
    try {
        for (let repeat = 0; repeat < 3; repeat++) {
            const task = await reuseCompletedBacktest({
                tasks,
                path: "/api/akshare-backtest",
                params: { symbol: data.symbol, asof: data.asof },
                version: "same-engine",
                jobId: "completed-job",
                fetchJob: async () => (++reads, { status: "completed", result }),
            });
            chart.setData(await task.promise);
            chart.setTheory(theory);
            chart.chart.resize(802 + repeat, 500, true);
            assert.equal(chart.focus("2020-04-28"), true);
            chart.chart.resize(902 + repeat, 500, true);
            chart.chart.setCrosshairPosition(4.1, "2020-05-29", chart.candles);
            assertOwnedSeries(chart);
        }
        assert.equal(calculations, 0);
        assert.equal(reads, 1);
        assert.equal(chart.data, result);
    } finally {
        chart.destroy();
    }
});

for (const [field, clear] of Object.entries(collections)) {
    test(`${clear} releases temporary lines safely with an active SDK crosshair`, () => {
        const chart = chartHarness();
        try {
            const series = chart.chart.addSeries(LightweightCharts.LineSeries);
            series.setData([
                { time: bars[0].time, value: 4 },
                { time: bars.at(-1).time, value: 4 },
            ]);
            chart[field].push(series);
            chart.chart.resize(803, 500, true);
            chart.chart.setCrosshairPosition(4.1, "2020-05-29", chart.candles);
            chart[clear]();
            assert.equal(chart[field].length, 0);
            assertOwnedSeries(chart);
            chart[clear]();
            assertOwnedSeries(chart);
        } finally {
            chart.destroy();
        }
    });
}
