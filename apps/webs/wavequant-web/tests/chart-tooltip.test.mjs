import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { JSDOM } from "jsdom";
import * as LightweightCharts from "lightweight-charts";

const dom = new JSDOM('<div data-wavequant-react-workbench><article class="chart-card"></article></div>', {
    pretendToBeVisual: true,
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.location = dom.window.location;
globalThis.HTMLCanvasElement = dom.window.HTMLCanvasElement;
globalThis.MutationObserver = dom.window.MutationObserver;
globalThis.getComputedStyle = dom.window.getComputedStyle.bind(dom.window);
globalThis.requestAnimationFrame = window.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = window.cancelAnimationFrame = () => {};
globalThis.ResizeObserver = window.ResizeObserver = class {
    observe() {}
    disconnect() {}
};
window.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {} });
window.HTMLCanvasElement.prototype.getContext = function () {
    return new Proxy(
        {
            canvas: this,
            measureText: (text) => ({ width: String(text).length * 6 }),
            createLinearGradient: () => ({ addColorStop() {} }),
        },
        { get: (context, key) => (key in context ? context[key] : () => {}) },
    );
};
const crosshairCallbacks = new WeakMap();
window.LightweightCharts = {
    ...LightweightCharts,
    createChart(...args) {
        const chart = LightweightCharts.createChart(...args);
        const subscribe = chart.subscribeCrosshairMove.bind(chart);
        chart.subscribeCrosshairMove = (callback) => {
            crosshairCallbacks.set(chart, callback);
            subscribe(callback);
        };
        return chart;
    },
};
const styles = readFileSync(new URL("../public/styles.css", import.meta.url), "utf8");
const style = document.createElement("style");
style.textContent = styles.match(/\.chart-tooltip \{[^}]+\}/)[0];
document.head.append(style);
const { PriceChart } = await import("../public/charts.js");
const bars = [
    { time: "2026-09-29", open: 11, high: 12, low: 10, close: 11.72, volume: 100 },
    { time: "2026-09-30", open: 10.69, high: 12.89, low: 10.69, close: 12.89, raw_close: 12.89, volume: 90317836 },
];

function harness({ viewportWidth = 1600, viewportHeight = 900, left = 250, top = 160, width = 700 } = {}) {
    window.innerWidth = viewportWidth;
    window.innerHeight = viewportHeight;
    const container = document.createElement("div");
    document.querySelector(".chart-card").append(container);
    Object.defineProperty(container, "clientWidth", { value: width });
    Object.defineProperty(container, "clientHeight", { value: 500 });
    container.getBoundingClientRect = () => ({ left, top, width, height: 500, right: left + width, bottom: top + 500 });
    const copied = [];
    const chart = new PriceChart(
        container,
        () => {},
        () => {},
        () => {},
        (bar) => copied.push(bar),
    );
    chart.chart.applyOptions({ autoSize: false, width, height: 500 });
    chart.setData({ bars, markers: [], orders: [], asof: "2026-09-30" });
    Object.defineProperty(chart.tooltip, "offsetWidth", { value: 285, configurable: true });
    Object.defineProperty(chart.tooltip, "offsetHeight", { value: 460, configurable: true });
    const hover = (x = width - 20, y = 100, time = bars[1].time) =>
        crosshairCallbacks.get(chart.chart)({ time, point: time ? { x, y } : undefined });
    return {
        chart,
        container,
        copied,
        hover,
        dispose() {
            chart.destroy();
            container.remove();
        },
    };
}

test("rightmost candle tooltip extends past the chart instead of covering its candle", () => {
    const h = harness();
    try {
        h.hover();
        const left = Number.parseFloat(h.chart.tooltip.style.left);
        const top = Number.parseFloat(h.chart.tooltip.style.top);
        assert.ok(left > 250 + 680, "card stays to the right of the selected candle");
        assert.ok(left + 285 > 950, "card is allowed past the chart's right edge");
        assert.ok(left + 285 <= 1600, "card stays inside the page viewport");
        assert.equal(top, 272, "chart offset is included in viewport coordinates");
        assert.equal(getComputedStyle(h.chart.tooltip).position, "fixed");
    } finally {
        h.dispose();
    }
});

test("only the page edge flips the card left and a short chart does not constrain its height", () => {
    const h = harness({ viewportWidth: 1200, left: 400 });
    try {
        h.hover(680, 450);
        const left = Number.parseFloat(h.chart.tooltip.style.left);
        const top = Number.parseFloat(h.chart.tooltip.style.top);
        assert.ok(left + 285 < 1080, "flipped card does not cover the selected candle");
        assert.ok(top + 460 <= 892);
        assert.ok(top + 460 > 660, "card can extend below the chart");
    } finally {
        h.dispose();
    }
});

test("narrow viewport clamps a long tooltip to the page, including chart offsets", () => {
    const h = harness({ viewportWidth: 390, viewportHeight: 600, left: 20, top: 50, width: 350 });
    try {
        h.hover(175, 450);
        const left = Number.parseFloat(h.chart.tooltip.style.left);
        const top = Number.parseFloat(h.chart.tooltip.style.top);
        assert.ok(left >= 8 && left + 285 <= 382);
        assert.ok(top >= 8 && top + 460 <= 592);
    } finally {
        h.dispose();
    }
});

test("crossing to an outside card preserves the candle, selectable details and copy button", async () => {
    const h = harness();
    try {
        h.hover();
        const left = h.chart.tooltip.style.left;
        const top = h.chart.tooltip.style.top;
        h.hover(679, 180);
        assert.equal(h.chart.tooltip.style.left, left);
        assert.equal(h.chart.tooltip.style.top, top, "the copy button does not chase the pointer");
        h.hover(0, 0, null);
        h.chart.tooltip.dispatchEvent(new window.MouseEvent("pointerenter"));
        await new Promise((resolve) => setTimeout(resolve, 180));
        assert.equal(h.chart.tooltip.hidden, false);
        const button = h.chart.tooltip.querySelector("button");
        button.focus();
        h.hover(1, 1, bars[0].time);
        button.click();
        assert.deepEqual(h.copied, [bars[1]]);
        assert.match(h.chart.tooltip.textContent, /2026-09-30 · K 线/);
        assert.match(h.chart.tooltip.textContent, /90,317,836 股/);
        assert.equal(getComputedStyle(h.chart.tooltip).userSelect, "text");
        button.blur();
        h.chart.tooltip.dispatchEvent(new window.MouseEvent("pointerleave"));
        assert.equal(h.chart.tooltip.hidden, true);
    } finally {
        h.dispose();
    }
});

test("fallback layer leaves chart containment while retaining its workbench theme scope", () => {
    const h = harness();
    try {
        h.hover();
        assert.equal(h.chart.tooltip.parentElement, h.container.closest("[data-wavequant-react-workbench]"));
        assert.equal(h.chart.tooltip.closest(".chart-card"), null);
    } finally {
        h.dispose();
    }
});

test("scrolling card details keeps it open, while page scroll, resize and Escape close stale cards", () => {
    const h = harness();
    try {
        h.hover();
        h.chart.tooltip.dispatchEvent(new window.MouseEvent("pointerenter"));
        h.chart.tooltip.dispatchEvent(new window.Event("scroll"));
        assert.equal(h.chart.tooltip.hidden, false);
        for (const event of [
            new window.Event("scroll"),
            new window.Event("resize"),
            new window.KeyboardEvent("keydown", { key: "Escape" }),
        ]) {
            window.dispatchEvent(event);
            assert.equal(h.chart.tooltip.hidden, true);
            assert.equal(h.chart.tooltipHovered, false);
            h.hover();
            assert.equal(h.chart.tooltip.hidden, false, "next hover can reopen after layout changed");
        }
        h.chart.setData({ bars: bars.slice(0, 1), markers: [], orders: [], asof: bars[0].time });
        assert.equal(h.chart.tooltip.hidden, true);
        assert.equal(h.chart.tooltipBarTime, null);
    } finally {
        h.dispose();
    }
});

test("native popover uses the top layer without changing DOM theme ancestry and closes before destruction", () => {
    const prototype = window.HTMLElement.prototype;
    const originalMatches = prototype.matches;
    const states = new WeakMap();
    prototype.matches = function (selector) {
        return selector === ":popover-open" ? (states.get(this)?.open ?? false) : originalMatches.call(this, selector);
    };
    prototype.showPopover = function () {
        const previous = states.get(this) || { shows: 0, hides: 0 };
        states.set(this, { ...previous, open: true, shows: previous.shows + 1 });
    };
    prototype.hidePopover = function () {
        const previous = states.get(this);
        states.set(this, { ...previous, open: false, hides: previous.hides + 1 });
    };
    const h = harness();
    const tooltip = h.chart.tooltip;
    try {
        h.hover();
        assert.equal(tooltip.getAttribute("popover"), "manual");
        assert.equal(tooltip.parentElement, h.container);
        assert.equal(states.get(tooltip).open, true);
        h.hover(680, 110);
        assert.equal(states.get(tooltip).shows, 1, "same card is not reinserted into the top layer");
        window.dispatchEvent(new window.Event("resize"));
        assert.equal(states.get(tooltip).open, false);
        h.hover();
        assert.equal(states.get(tooltip).open, true);
    } finally {
        h.dispose();
        prototype.matches = originalMatches;
        delete prototype.showPopover;
        delete prototype.hidePopover;
    }
    assert.equal(states.get(tooltip).open, false);
    assert.equal(states.get(tooltip).hides, 2);
    assert.equal(tooltip.isConnected, false);
    let writesAfterDestroy = 0;
    Object.defineProperty(tooltip, "hidden", { get: () => true, set: () => writesAfterDestroy++ });
    window.dispatchEvent(new window.Event("scroll"));
    window.dispatchEvent(new window.Event("resize"));
    window.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Escape" }));
    assert.equal(writesAfterDestroy, 0, "teardown removes all viewport listeners");
});

test("visual viewport size and offsets constrain a zoomed mobile tooltip", () => {
    const viewport = new window.EventTarget();
    Object.assign(viewport, { offsetLeft: 100, offsetTop: 80, width: 390, height: 600 });
    Object.defineProperty(window, "visualViewport", { value: viewport, configurable: true });
    const h = harness({ viewportWidth: 800, viewportHeight: 900, left: 200, top: 100 });
    try {
        h.hover(280, 550);
        const tooltip = h.chart.tooltip;
        const left = Number.parseFloat(tooltip.style.left);
        const top = Number.parseFloat(tooltip.style.top);
        assert.ok(left >= 108 && left + 285 <= 482);
        assert.ok(top >= 88 && top + 460 <= 672);
        assert.equal(tooltip.style.getPropertyValue("--chart-tooltip-max-width"), "374px");
        assert.equal(tooltip.style.getPropertyValue("--chart-tooltip-max-height"), "584px");
        viewport.dispatchEvent(new window.Event("resize"));
        assert.equal(tooltip.hidden, true);
        h.hover();
        viewport.dispatchEvent(new window.Event("scroll"));
        assert.equal(tooltip.hidden, true);
    } finally {
        h.dispose();
        delete window.visualViewport;
    }
});
