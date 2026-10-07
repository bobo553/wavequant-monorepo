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
    const key = (key, options = {}, target = document.activeElement) => {
        const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options });
        target.dispatchEvent(event);
        return event.defaultPrevented;
    };
    const pointer = (target, button = 0, drag = false) => {
        const events = drag
            ? ["pointerdown", "mousedown", "pointermove", "mousemove", "pointerup", "mouseup"]
            : ["pointerdown", "mousedown", "pointerup", "mouseup", "click"];
        for (const type of events) target.dispatchEvent(new MouseEvent(type, { bubbles: true, button }));
    };
    container.focus();
    return { document, container, chart, key, pointer, unbind, range: () => range, pauses: () => pauses };
}

test("canvas activation focuses the chart; arrows zoom and pan using the existing viewport steps", () => {
    const h = harness();
    const canvas = h.container.querySelector("canvas");
    h.document.getElementById("outside").focus();
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

test("focused SDK descendants receive all four commands even when the SDK stops bubbling", () => {
    const h = harness();
    const canvas = h.container.querySelector("canvas");
    canvas.tabIndex = 0;
    canvas.addEventListener("keydown", (event) => event.stopPropagation());
    canvas.focus();
    assert.equal(h.key("ArrowUp"), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowLeft"), true);
    assert.deepEqual(h.range(), { from: 394, to: 474 });
    assert.equal(h.key("ArrowRight"), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowDown"), true);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    h.unbind();
});

test("hovering the chart activates arrows with page focus, while leaving it restores page keys", () => {
    const h = harness();
    h.container.blur();
    h.container.dispatchEvent(new h.document.defaultView.MouseEvent("pointerenter"));
    assert.equal(h.document.activeElement, h.document.body);
    assert.equal(h.key("ArrowUp"), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowLeft", { repeat: true }), true);
    assert.deepEqual(h.range(), { from: 394, to: 474 });
    assert.equal(h.key("ArrowRight", { repeat: true }), true);
    assert.deepEqual(h.range(), { from: 410, to: 490 });
    assert.equal(h.key("ArrowDown"), true);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    h.container.dispatchEvent(new h.document.defaultView.MouseEvent("pointerleave"));
    assert.equal(h.key("ArrowUp"), false);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    h.unbind();
});

test("ending a drag restores keyboard focus after the SDK removes it; mouse-only clicks also activate", () => {
    const h = harness();
    const canvas = h.container.querySelector("canvas");
    canvas.addEventListener("mousedown", () => h.document.activeElement.blur());
    h.pointer(canvas, 0, true);
    assert.equal(h.document.activeElement, h.container);
    assert.equal(h.key("ArrowRight"), true);
    assert.deepEqual(h.range(), { from: 420, to: 520 });
    h.document.getElementById("outside").focus();
    canvas.dispatchEvent(new canvas.ownerDocument.defaultView.MouseEvent("click", { bubbles: true }));
    assert.equal(h.document.activeElement, h.container);
    assert.equal(h.key("ArrowUp"), true);
    assert.deepEqual(h.range(), { from: 430, to: 510 });
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
    h.container.focus();
    for (const flag of ["ctrlKey", "metaKey", "altKey", "shiftKey", "isComposing"]) {
        assert.equal(h.key("ArrowDown", { [flag]: true }), false);
    }
    assert.equal(h.key("Tab"), false);
    assert.equal(h.key("Home"), false);
    h.document.defaultView.addEventListener("keydown", (event) => event.preventDefault(), {
        capture: true,
        once: true,
    });
    assert.equal(h.key("ArrowRight"), true);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    assert.equal(h.pauses(), 0);
    h.unbind();
});

test("hover does not steal keys from input controls; hidden, detached and destroyed charts remain inactive", () => {
    const h = harness();
    h.container.dispatchEvent(new h.document.defaultView.MouseEvent("pointerenter"));
    const range = h.document.createElement("input");
    range.type = "range";
    h.container.append(range);
    for (const target of [
        h.document.getElementById("outside"),
        range,
        ...h.container.querySelectorAll("button,select,[contenteditable]"),
    ]) {
        target.focus();
        for (const arrow of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"]) assert.equal(h.key(arrow), false);
    }
    h.container.focus();
    h.container.hidden = true;
    assert.equal(h.key("ArrowRight"), false);
    h.container.hidden = false;
    h.container.remove();
    assert.equal(h.key("ArrowRight"), false);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    h.document.body.append(h.container);
    h.unbind();
    h.container.focus();
    assert.equal(h.key("ArrowRight"), false);
    assert.equal(h.pauses(), 0);
});

test("a hovered chart cannot take arrows from another focused chart", () => {
    const h = harness();
    h.container.dispatchEvent(new h.document.defaultView.MouseEvent("pointerenter"));
    const other = h.document.createElement("div");
    other.tabIndex = 0;
    h.document.body.append(other);
    let otherPans = 0;
    const releaseOther = bindChartKeyboardNavigation(other, {
        navigationState: () => chartNavigationState({ from: 20, to: 120 }, 1000),
        pan: () => otherPans++,
        zoom: () => {},
    });
    other.focus();
    assert.equal(h.key("ArrowRight"), true);
    assert.equal(otherPans, 1);
    assert.deepEqual(h.range(), { from: 400, to: 500 });
    releaseOther();
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
        h.key("ArrowDown", {}, h.container);
        assert.equal(h.pauses(), 0);
    }
});
