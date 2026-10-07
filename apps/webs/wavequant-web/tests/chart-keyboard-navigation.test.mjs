import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { bindChartKeyboardNavigation } from "../public/chart-keyboard-navigation.js";
import { chartNavigationState, panChartRange, zoomChartRange } from "../public/chart-navigation.js";

function harness(tabIndex = null) {
    const dom = new JSDOM(
        '<div id="chart"><canvas></canvas><button>复制</button><input><select></select><div contenteditable="true">价格</div></div><input id="outside">',
    );
    const { document, KeyboardEvent, MouseEvent } = dom.window;
    const container = document.getElementById("chart");
    if (tabIndex !== null) container.setAttribute("tabindex", tabIndex);
    let range = { from: 400, to: 500 };
    let pauses = 0;
    const chart = {
        navigationState: () => chartNavigationState(range, 1000),
        pan: (direction) => (range = panChartRange(chart.navigationState(), direction)),
        zoom: (direction) => (range = zoomChartRange(chart.navigationState(), 1000, direction)),
    };
    const unbind = bindChartKeyboardNavigation(container, chart, () => pauses++);
    const key = (key, options = {}, target = container) => {
        const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });
        target.dispatchEvent(event);
        return event.defaultPrevented;
    };
    const pointer = (target, button = 0) =>
        target.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true, button }));
    return { document, container, chart, key, pointer, unbind, range: () => range, pauses: () => pauses };
}

test("canvas activation focuses the chart; arrows zoom and pan using the existing viewport steps", () => {
    const h = harness();
    const canvas = h.container.querySelector("canvas");
    h.pointer(canvas);
    assert.equal(h.document.activeElement, h.container);
    assert.equal(h.key("ArrowUp"), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowLeft"), true);
    assert.deepEqual(h.range(), { from: 394, to: 474 });
    assert.equal(h.key("ArrowRight"), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowDown"), true);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    assert.equal(h.pauses(), 4);
    h.unbind();
});

test("held arrows respect the zoom and data edges and keep page scrolling suppressed at the boundary", () => {
    const h = harness();
    for (let repeat = 0; repeat < 50; repeat++) h.key("ArrowUp", { repeat: true });
    assert.equal(h.range().to - h.range().from, 20);
    for (let repeat = 0; repeat < 150; repeat++) h.key("ArrowLeft", { repeat: true });
    assert.deepEqual(h.range(), { from: 0, to: 20 });
    assert.equal(h.key("ArrowLeft"), true);
    for (let repeat = 0; repeat < 300; repeat++) h.key("ArrowRight", { repeat: true });
    assert.deepEqual(h.range(), { from: 985, to: 1005 });
    for (let repeat = 0; repeat < 50; repeat++) h.key("ArrowDown", { repeat: true });
    assert.deepEqual(h.range(), { from: 0, to: 1005 });
    h.unbind();
});

test("editors, buttons, outside focus, modifiers, composition and already handled events retain their keys", () => {
    const h = harness();
    for (const target of h.container.querySelectorAll("button,input,select,[contenteditable]")) {
        target.focus();
        h.pointer(target);
        assert.equal(h.document.activeElement, target);
        assert.equal(h.key("ArrowUp", {}, target), false);
    }
    const outside = h.document.getElementById("outside");
    outside.focus();
    h.pointer(h.container.querySelector("canvas"), 2);
    assert.equal(h.document.activeElement, outside);
    assert.equal(h.key("ArrowLeft", {}, outside), false);
    for (const flag of ["ctrlKey", "metaKey", "altKey", "shiftKey", "isComposing"]) {
        assert.equal(h.key("ArrowDown", { [flag]: true }), false);
    }
    assert.equal(h.key("Tab"), false);
    assert.equal(h.key("Home"), false);
    h.container.addEventListener("keydown", (event) => event.preventDefault(), { capture: true, once: true });
    assert.equal(h.key("ArrowRight"), true);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    assert.equal(h.pauses(), 0);
    h.unbind();
});

test("empty data does not claim keys; teardown removes listeners and preserves existing tabindex", () => {
    for (const tabIndex of [null, "0", "-1"]) {
        const h = harness(tabIndex);
        h.chart.navigationState = () => null;
        assert.equal(h.key("ArrowUp"), false);
        assert.equal(h.pauses(), 0);
        h.unbind();
        assert.equal(h.container.getAttribute("tabindex"), tabIndex);
        assert.equal(h.key("ArrowRight"), false);
        h.document.getElementById("outside").focus();
        h.pointer(h.container.querySelector("canvas"));
        assert.equal(h.document.activeElement.id, "outside");
    }
});
