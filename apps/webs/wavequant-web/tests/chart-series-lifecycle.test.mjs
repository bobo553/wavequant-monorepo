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

test("running N-confirmed snapshots draw polyline and fills together and preserve the viewport", () => {
    const chart = chartHarness();
    const dates = ["2019-01-04", "2019-04-11", "2019-08-06", "2019-08-20", "2019-08-21"];
    const sample = dates.map((time) => ({ time, open: 5, high: 6, low: 4, close: 5, volume: 100 }));
    const event = { event: "trend_lifecycle_state", timestamp: dates[3], bar_index: 3,
        available_index: 3, symbol: "sh.600812", trend_level: 2, source_path: "secondary-reversal-lecture-0",
        state: "空多交替", direction: "up", cycle_started_index: 1, origin_index: 0, origin_price: 4,
        flip_index: 1, flip_known_index: 1, breakout_level: 6, counter_index: 2, counter_price: 4.5,
        alternation_index: 3, special_bull_recovery: true,
        confirmation_rule: "positive_n_and_squeeze_after_qualified_b",
        confirmation_evidence: { regime: "轧空", time_path: true }, counter_source_level: 0 };
    const points = sample.map((bar, index) => ({ time: bar.time, index, ordinal: 0,
        kind: index % 2 ? "H" : "L", value: index % 2 ? 6 : 4.5, available_at: bar.time, state: "confirmed" }));
    try {
        for (const count of [4, 5]) {
            const current = { symbol: event.symbol, timeframe: "1d", preview_job: "running", asof: dates[count - 1],
                bars: sample.slice(0, count), market_bars: sample, market_asof: dates.at(-1), orders: [], audit: [event],
                markers: [{ id: "buy", time: dates[3], kind: "fill", side: "BUY", price: 5 }],
                backtest: { strategy: { buy_interval_level: 2 } } };
            chart.setData(current, { preserveViewport: count > 4 });
            if (count === 4) chart.chart.timeScale().setVisibleLogicalRange({ from: 0, to: 4 });
            const range = chart.chart.timeScale().getVisibleLogicalRange();
            chart.setTheory({ asof: current.asof, shapes: [], events: [],
                lecture_drawing: { strokes: [{ id: "lecture-0", points: points.slice(0, count) }] } }, true, true);
            assert.equal(chart.candles.data().length, sample.length);
            assert.equal(chart.volume.data().length, sample.length);
            assert.equal(chart.data.bars.length, count);
            assert.equal(chart.waveProjectionAsOf(), dates[count - 1]);
            assert.ok(Number(chart.container.dataset.polylinePoints) >= count);
            assert.equal(chart.tradeMarkerOverlay.markers[0].time, dates[3]);
            assert.equal(chart.tradeMarkerOverlay.markers[0].side, "BUY");
            assert.equal(chart.groups.some((group) => group.items.some((item) => item.raw?.lifecycle)), true);
            assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range);
        }
    } finally {
        chart.destroy();
    }
});

test("progress leaves the viewport on unprocessed market dates and completion keeps the same axis", () => {
    const chart = chartHarness();
    try {
        const market = data.bars;
        const range = { from: market.length - 20, to: market.length + 2 };
        chart.chart.timeScale().setVisibleLogicalRange(range);
        for (const processed of [2, 3]) {
            const view = { ...data, bars: market.slice(0, processed), market_bars: market,
                market_asof: market.at(-1).time, asof: market[processed - 1].time,
                preview_job: "same-job", audit: [], signals: [], trades: [] };
            chart.setData(view, { preserveViewport: true });
            chart.setTheory({ asof: view.asof, shapes: [], events: [] }, true, true);
            assert.equal(chart.candles.data().length, market.length);
            assert.equal(chart.volume.data().length, market.length);
            assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range);
            assert.equal(chart.waveProjectionAsOf(), view.asof);
        }
        chart.setData(data, { preserveViewport: true });
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range);
        assert.equal(chart.candles.data().length, market.length);
    } finally {
        chart.destroy();
    }
});

test("annual history prepends and final complete installation preserve the real SDK viewport", () => {
    const chart = chartHarness();
    try {
        const original = { from: 10.25, to: 50.75 };
        chart.chart.timeScale().setVisibleLogicalRange(original);
        chart.chart.resize(801, 500, true);
        const firstYear = Number(data.bars[0].time.slice(0, 4));
        const previous = [1, 2, 3].map((day) => ({ ...data.bars[0], time: `${firstYear - 1}-01-0${day}` }));
        const merged = { ...data, bars: [...previous, ...data.bars] };
        chart.setData(merged, { preserveViewport: true });
        chart.chart.resize(802, 500, true);
        const shifted = { from: original.from + previous.length, to: original.to + previous.length };
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), shifted);
        chart.setData({ ...merged, markers: [] }, { preserveViewport: true });
        chart.chart.resize(803, 500, true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), shifted);
    } finally {
        chart.destroy();
    }
});

test("a new backtest prefix cannot retain a viewport beyond its last completed day", () => {
    const chart = chartHarness();
    try {
        chart.chart.timeScale().setVisibleLogicalRange({ from: bars.length - 40, to: bars.length + 6 });
        const prefix = { ...data, bars: bars.slice(0, 10), asof: bars[9].time };
        chart.setData(prefix, { preserveViewport: true });
        chart.chart.resize(801, 500, true);
        const range = chart.chart.timeScale().getVisibleLogicalRange();
        assert.ok(range.from <= 9 && range.to >= 0, "the first snapshot must be visible");
        chart.setData({ ...data, bars: bars.slice(0, 20), asof: bars[19].time }, { preserveViewport: true });
        chart.chart.resize(802, 500, true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range, "later updates keep the viewport");
    } finally {
        chart.destroy();
    }
});

test("appended and revised candles preserve the current right-edge window and selection", () => {
    const chart = chartHarness();
    try {
        const range = { from: bars.length - 40.5, to: bars.length + 5.5 };
        chart.chart.timeScale().setVisibleLogicalRange(range);
        chart.chart.resize(800, 500, true);
        const selected = { id: "retained-selection", time: bars.at(-1).time };
        chart.selected = selected;
        const lastTime = new Date(`${bars.at(-1).time}T00:00:00Z`);
        lastTime.setUTCDate(lastTime.getUTCDate() + 1);
        const revised = bars.map((bar, index) => index === 2 ? { ...bar, volume: bar.volume + 1 } : bar);
        const appended = { ...bars.at(-1), time: lastTime.toISOString().slice(0, 10) };
        let replacements = 0;
        const setData = chart.candles.setData.bind(chart.candles);
        chart.candles.setData = (...args) => { replacements++; return setData(...args); };
        chart.setData({ ...data, bars: [...revised, appended] }, { preserveViewport: true });
        chart.chart.resize(800, 500, true);
        assert.equal(replacements, 0, "a new trading day must not reinstall the candle series");
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range);
        assert.equal(chart.selected, selected);
        assert.equal(chart.candles.data().at(-1).time, appended.time);
        assert.equal(chart.volume.data()[2].value, revised[2].volume);
    } finally {
        chart.destroy();
    }
});

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
        chart.chart.timeScale().setVisibleLogicalRange({ from: 10, to: 50 });
        chart.chart.resize(++width, 500, true);
        canvas.tabIndex = 0;
        const stopBubbling = (event) => event.stopPropagation();
        canvas.addEventListener("keydown", stopBubbling);
        canvas.focus();
        assert.equal(press("ArrowUp"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 14, to: 46 });
        assert.equal(press("ArrowLeft"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 8, to: 40 });
        assert.equal(press("ArrowRight"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 14, to: 46 });
        assert.equal(press("ArrowDown"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 10, to: 50 });
        canvas.removeEventListener("keydown", stopBubbling);
        canvas.blur();
        container.dispatchEvent(new window.MouseEvent("pointermove", { bubbles: true }));
        assert.equal(document.activeElement, document.body);
        assert.equal(press("ArrowUp"), true);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 14, to: 46 });
        assert.equal(press("ArrowDown"), true);
        container.dispatchEvent(new window.MouseEvent("pointerleave"));
        assert.equal(press("ArrowUp"), false);
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), { from: 10, to: 50 });
        assert.equal(pauses, 17);
        assertOwnedSeries(chart);
    } finally {
        chart.destroy();
    }
    const released = new window.KeyboardEvent("keydown", { key: "ArrowUp", bubbles: true, cancelable: true });
    container.dispatchEvent(released);
    assert.equal(released.defaultPrevented, false);
    assert.equal(pauses, 17);
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

test("all four N targets leave the candle price scale and visible time range unchanged", () => {
    const chart = chartHarness();
    try {
        const time = bars[1].time;
        const levels = ["one_p", "two_t", "five_top", "ten_full"].map((stage, index) => ({
            stage, name: stage, price: 100 + index * 100, anchor_at: time, available_at: time,
            ...(index > 1 ? { estimated: true, display_only: true } : {}),
        }));
        const selected = { id: "display-n", time, kind: "rule", levels,
            raw: { event: "n_completed", direction: "up", display_target_eligible: true,
                display_target_level: 2, time, available_at: time, shape: [{ time: bars[0].time }] } };
        chart.selected = selected;
        chart.focusedNTarget = selected;
        chart.nTargetObservations = [{ item: selected, from: bars[0].time, to: data.asof }];
        chart.options.levels = true;
        const range = chart.chart.timeScale().getVisibleLogicalRange();
        chart.drawLevels();
        assert.equal(chart.levelLines.length, 4);
        for (const series of chart.levelLines) assert.equal(series.options().autoscaleInfoProvider(() => ({})), null);
        assert.ok(chart.targetGuideOverlay.guides.every((guide) => guide.hideWhenPriceOffscreen));
        assert.deepEqual(chart.chart.timeScale().getVisibleLogicalRange(), range);
    } finally { chart.destroy(); }
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
