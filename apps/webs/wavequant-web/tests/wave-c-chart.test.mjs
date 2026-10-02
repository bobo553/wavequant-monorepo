import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

globalThis.window = { LightweightCharts: { LineSeries: "line" } };
globalThis.document = { documentElement: {} };
globalThis.requestAnimationFrame = () => 1;
globalThis.MutationObserver = class {
    observe() {}
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
const theory = { ...initial.theory, asof: history.asof, shapes: [], secondary_trends: history.secondary_trends };

// Exercise chart state and SDK inputs without opening a browser or claiming a pixel-level check.
function chartHarness(data = bars) {
    const chart = Object.create(PriceChart.prototype);
    const rendered = { markers: [], guides: [], range: { from: 0, to: data.length - 1 } };
    chart.container = { dataset: {} };
    chart.data = { bars: data, markers: [], orders: [], asof: data.at(-1).time };
    chart.options = { tertiaryAbc: true, levels: true, rules: false };
    chart.drawingMode = "lecture";
    for (const field of [
        "lines",
        "polylineLines",
        "levelLines",
        "waveAbLines",
        "lastFallHighLines",
        "bullishTurnGuideLines",
        "tertiaryRetracementLines",
        "autoWaveProjections",
    ])
        chart[field] = [];
    chart.chart = {
        addSeries: () => ({
            setData(points) {
                assert.ok(points.every((point, index) => !index || points[index - 1].time < point.time));
                this.points = points;
            },
        }),
        removeSeries() {},
        timeScale: () => ({
            getVisibleLogicalRange: () => rendered.range,
            setVisibleLogicalRange: (range) => {
                rendered.range = range;
            },
            timeToCoordinate: (time) => data.findIndex((bar) => bar.time === time) * 20,
        }),
    };
    chart.markers = {
        setMarkers: (markers) => {
            rendered.markers = markers;
        },
    };
    chart.lectureOverlay = { setStrokes() {} };
    chart.tradeMarkerOverlay = { setMarkers() {} };
    chart.targetGuideOverlay = {
        setGuides: (guides) => {
            rendered.guides = guides;
        },
    };
    chart.waveEndpointOverlay = { setPoints() {} };
    chart.onSelect = () => {};
    chart.onVisible = () => {};
    return { chart, rendered };
}

test("May 29 theory renders the earlier ABC labels, A/B legs and C targets with ordinary rules off", () => {
    const { chart, rendered } = chartHarness();
    chart.setTheory(theory);
    assert.deepEqual(
        rendered.markers.map((marker) => [marker.time, marker.text]),
        [
            ["2020-02-04", "A 起"],
            ["2020-02-10", "正 N"],
            ["2020-02-17", "轧空"],
            ["2020-03-02", "A 顶"],
            ["2020-04-28", "B / C"],
        ],
    );
    assert.equal(chart.container.dataset.waveAbcCount, "1");
    assert.equal(chart.waveAbLines.length, 2);
    assert.equal(rendered.guides.length, 2);
    assert.ok(rendered.guides.every((guide) => guide.start === "2020-04-28"));
    assert.ok(Math.abs(rendered.guides[1].price - 4.62) < 1e-10);

    chart.setAnnotationOptions({ tertiaryAbc: false });
    assert.equal(rendered.markers.length, 0);
    assert.equal(chart.container.dataset.waveAbcCount, "0");
    assert.equal(chart.waveAbLines.length, 0);
    assert.equal(rendered.guides.length, 0);
    chart.setAnnotationOptions({ tertiaryAbc: true });
    assert.equal(rendered.markers.length, 5);
    assert.equal(chart.waveAbLines.length, 2);
    assert.equal(rendered.guides.length, 2);
});

test("panning back to an earlier ABC selects that group's C targets", () => {
    const next = JSON.parse(JSON.stringify(initial).replaceAll("2020-", "2021-"));
    const nextBars = next.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const allBars = [...bars, ...nextBars];
    const { chart, rendered } = chartHarness(allBars);
    chart.setTheory({
        ...theory,
        asof: next.theory.asof,
        lecture_drawing: {
            strokes: [...initial.theory.lecture_drawing.strokes, ...next.theory.lecture_drawing.strokes],
        },
        secondary_trends: {
            ...theory.secondary_trends,
            bear_to_bull_highs: next.theory.secondary_trends.bear_to_bull_highs,
        },
    });
    assert.equal(chart.autoWaveProjections.length, 2);
    assert.equal(chart.waveAbLines.length, 4);
    assert.equal(rendered.guides[0].start, "2021-04-28");
    rendered.range = { from: 0, to: bars.length - 1 };
    chart.refreshMarkers();
    assert.equal(chart.container.dataset.waveAbcCount, "1");
    assert.equal(rendered.markers.length, 5);
    assert.equal(rendered.guides[0].start, "2020-04-28");
    rendered.range = { from: bars.length, to: allBars.length - 1 };
    chart.refreshMarkers();
    assert.equal(rendered.markers.length, 5);
    assert.equal(rendered.guides[0].start, "2021-04-28");
});

test("ABC labels and targets remain available when trend geometry is hidden", () => {
    const { chart, rendered } = chartHarness();
    chart.setTheory(theory, false);
    assert.equal(rendered.markers.length, 5);
    assert.equal(rendered.guides.length, 2);
    assert.equal(chart.waveAbLines.length, 0);
});

test("locating ABC brings an offscreen historical group and its targets into view", () => {
    const later = [...bars, { time: "2026-09-30", open: 5, high: 5, low: 5, close: 5, volume: 1 }];
    const { chart, rendered } = chartHarness(later);
    rendered.range = { from: bars.length, to: bars.length };
    chart.setTheory({ ...theory, asof: "2026-09-30" });
    assert.equal(rendered.markers.length, 0);
    assert.equal(rendered.guides.length, 0);
    assert.equal(chart.focusWaveProjection(), true);
    assert.equal(rendered.markers.length, 5);
    assert.equal(rendered.guides.length, 2);
    assert.equal(chart.selected.time, "2020-04-28");
    assert.ok(rendered.range.from <= bars.findIndex((bar) => bar.time === "2020-02-04"));
    assert.ok(rendered.range.to >= bars.findIndex((bar) => bar.time === "2020-04-28"));
    chart.setTheory(null);
    assert.equal(chart.focusWaveProjection(), false);
});
