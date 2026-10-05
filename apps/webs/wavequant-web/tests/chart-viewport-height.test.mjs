import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { bindChartViewportHeight, chartViewportHeight } from "../public/chart-viewport-height.js";

test("desktop charts add 100px to the prior viewport heights", () => {
    for (const [viewport, expectedHeight] of [
        [720, 424],
        [768, 472],
        [900, 604],
        [1080, 784],
    ]) {
        const chartTop = 320;
        const footerHeight = 60;
        const height = chartViewportHeight(viewport, chartTop, footerHeight);
        assert.equal(height, expectedHeight);
        assert.ok(chartTop + height + footerHeight <= viewport + 84);
        assert.ok(chartTop + height + footerHeight + 17 >= viewport);
    }
});

test("short layouts remain readable and tall screens extend the drawing area beyond the old cap", () => {
    assert.equal(chartViewportHeight(600, 400, 160), 300);
    assert.equal(chartViewportHeight(844, 900, 140), 300);
    assert.equal(chartViewportHeight(1600, 320, 60), 1304);
});

test("fractional measurements retain the same 100px increase", () => {
    const height = chartViewportHeight(768.25, 320.5, 60.5);
    assert.equal(height, 471);
    assert.ok(320.5 + height + 60.5 <= 768.25 + 84);
});

test("measurement follows viewport and footer changes, survives scrolling and hidden views, and cleans up", () => {
    const dom = new JSDOM(
        '<main><article class="chart-card"><div id="chart"></div><div id="position"></div><div id="replay"></div></article><aside class="insight"></aside></main>',
    );
    const taskWindow = dom.window;
    const doc = taskWindow.document;
    const chart = doc.getElementById("chart");
    const footers = [doc.getElementById("position")];
    let chartWidth = 900;
    let footerHeights = [50];
    chart.getBoundingClientRect = () => new taskWindow.DOMRect(0, 320 - taskWindow.scrollY, chartWidth, 200);
    footers.forEach((footer, index) => {
        footer.getBoundingClientRect = () => new taskWindow.DOMRect(0, 0, 900, footerHeights[index]);
    });
    taskWindow.innerHeight = 720;
    const frames = new Map();
    let frameId = 0;
    taskWindow.requestAnimationFrame = (callback) => {
        frames.set(++frameId, callback);
        return frameId;
    };
    taskWindow.cancelAnimationFrame = (id) => frames.delete(id);
    const flush = () => {
        const callbacks = [...frames.values()];
        frames.clear();
        callbacks.forEach((callback) => callback());
    };
    let observer;
    class TestResizeObserver {
        elements = [];
        disconnected = false;
        constructor(callback) {
            this.callback = callback;
            observer = this;
        }
        observe(element) {
            this.elements.push(element);
        }
        disconnect() {
            this.disconnected = true;
        }
    }
    const previousWindow = globalThis.window;
    const previousObserver = globalThis.ResizeObserver;
    globalThis.window = taskWindow;
    globalThis.ResizeObserver = TestResizeObserver;
    let dispose;
    try {
        dispose = bindChartViewportHeight(chart, footers, doc.querySelector("main"));
        assert.equal(observer.elements.length, 3);
        observer.callback();
        taskWindow.dispatchEvent(new taskWindow.Event("resize"));
        assert.equal(frames.size, 1);
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "434px");
        taskWindow.scrollY = 120;
        observer.callback();
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "434px");
        taskWindow.innerHeight = 900;
        taskWindow.dispatchEvent(new taskWindow.Event("resize"));
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "614px");
        footerHeights = [100];
        observer.callback();
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "564px");
        chartWidth = 0;
        taskWindow.innerHeight = 1080;
        observer.callback();
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "564px");
        chartWidth = 900;
        observer.callback();
        flush();
        assert.equal(chart.style.getPropertyValue("--chart-viewport-height"), "744px");
        observer.callback();
        dispose();
        assert.equal(observer.disconnected, true);
        assert.equal(frames.size, 0);
        taskWindow.dispatchEvent(new taskWindow.Event("resize"));
        assert.equal(frames.size, 0);
    } finally {
        dispose?.();
        globalThis.window = previousWindow;
        globalThis.ResizeObserver = previousObserver;
        taskWindow.close();
    }
});
