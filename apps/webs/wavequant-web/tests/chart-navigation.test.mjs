import assert from "node:assert/strict";
import test from "node:test";

import {
    chartNavigationKeyPosition,
    chartNavigationState,
    panChartRange,
    seekChartRange,
    zoomChartRange,
} from "../public/chart-navigation.js";

test("chart navigation clamps panning and seeking to the loaded data", () => {
    const start = chartNavigationState({ from: 0, to: 106 }, 1000);
    assert.equal(start.canPanLeft, false);
    assert.deepEqual(panChartRange(start, -1), { from: 0, to: 106 });
    assert.deepEqual(seekChartRange(start, 9999), { from: 899, to: 1005 });

    const end = chartNavigationState({ from: 899, to: 1005 }, 1000);
    assert.equal(end.canPanRight, false);
    assert.equal(end.firstIndex, 899);
    assert.equal(end.lastIndex, 999);
    assert.deepEqual(panChartRange(end, 1), { from: 899, to: 1005 });
});

test("zoom keeps the latest edge visible and respects the minimum and maximum window", () => {
    const end = chartNavigationState({ from: 859, to: 1005 }, 1000);
    const enlarged = zoomChartRange(end, 1000, "in");
    assert.equal(enlarged.to, 1005);
    assert.ok(enlarged.from > end.from);

    const minimum = chartNavigationState({ from: 985, to: 1005 }, 1000);
    assert.equal(minimum.canZoomIn, false);
    assert.deepEqual(zoomChartRange(minimum, 1000, "in"), { from: 985, to: 1005 });

    const maximum = chartNavigationState({ from: 0, to: 1005 }, 1000);
    assert.equal(maximum.canZoomOut, false);
    assert.deepEqual(zoomChartRange(maximum, 1000, "out"), { from: 0, to: 1005 });
});

test("empty and short histories have stable disabled navigation state", () => {
    assert.equal(chartNavigationState(null, 0), null);
    const short = chartNavigationState({ from: 0, to: 10 }, 5);
    assert.equal(short.maxStart, 0);
    assert.equal(short.firstIndex, 0);
    assert.equal(short.lastIndex, 4);
    assert.equal(short.canZoomIn, false);
    assert.equal(short.canZoomOut, false);
});

test("dates describe the actual chart window when native dragging passes a data edge", () => {
    const overscrolled = chartNavigationState({ from: -50, to: 50 }, 1000);
    assert.equal(overscrolled.from, 0);
    assert.equal(overscrolled.firstIndex, 0);
    assert.equal(overscrolled.lastIndex, 50);
});

test("range keyboard navigation uses one-bar arrows and larger page steps", () => {
    const state = chartNavigationState({ from: 400, to: 500 }, 1000);
    assert.equal(chartNavigationKeyPosition(state, "ArrowLeft"), 399);
    assert.equal(chartNavigationKeyPosition(state, "ArrowRight"), 401);
    assert.equal(chartNavigationKeyPosition(state, "PageDown"), 380);
    assert.equal(chartNavigationKeyPosition(state, "PageUp"), 420);
    assert.equal(chartNavigationKeyPosition(state, "Home"), 0);
    assert.equal(chartNavigationKeyPosition(state, "End"), state.maxStart);
    assert.equal(chartNavigationKeyPosition(state, "Escape"), null);
});
