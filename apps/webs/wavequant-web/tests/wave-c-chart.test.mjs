import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";

let reducedMotion = false;
globalThis.window = {
    LightweightCharts: { LineSeries: "line" },
    matchMedia: () => ({ matches: reducedMotion }),
};
globalThis.document = { documentElement: {} };
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
globalThis.getComputedStyle = () => ({
    getPropertyValue: (name) => ({ "--cyan": "#60cfc3", "--amber": "#ebbc70", "--panel": "#111d2d" })[name] || "",
});
globalThis.MutationObserver = class {
    observe() {}
};
const { PriceChart } = await import("../public/charts.js");
const { FocusFlashOverlay } = await import("../public/focus-flash-overlay.js");
const { TargetGuideOverlay } = await import("../public/target-level-guides.js");
const { WaveEndpointOverlay } = await import("../public/wave-endpoint-overlay.js");

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
            timeToCoordinate: (time) => (data.findIndex((bar) => bar.time === time) - rendered.range.from) * 20,
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
    chart.waveEndpointOverlay = new WaveEndpointOverlay(chart.container);
    chart.waveEndpointOverlay.attached({
        chart: chart.chart,
        series: { priceToCoordinate: (price) => 200 - price * 10 },
        requestUpdate() {},
    });
    chart.focusFlashOverlay = new FocusFlashOverlay(chart.container);
    chart.focusFlashOverlay.attached({
        chart: chart.chart,
        series: { priceToCoordinate: (price) => price * 10 },
        requestUpdate() {},
    });
    chart.onSelect = () => {};
    chart.onVisible = () => {};
    return { chart, rendered };
}

function ordinaryChart(asof = "2025-01-22") {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
    const data = fixture.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const harness = chartHarness(data);
    harness.rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-02-08"),
        to: data.findIndex((bar) => bar.time === "2025-01-22"),
    };
    harness.chart.setTheory({ ...fixture.theory, asof, shapes: [], lecture_drawing: { strokes: [] } });
    return { ...harness, data };
}

test("Xinhua parent ABC renders the full A, deep B, C and separate internal N targets", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_parent_abc.json", import.meta.url)));
    const data = fixture.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const { chart, rendered } = chartHarness(data);
    const originalRange = { ...rendered.range };
    chart.setTheory({ ...fixture.theory, lecture_drawing: { strokes: [] } });
    const group = chart.autoWaveProjections.find(
        (item) => item.raw.originTime === "2024-02-08" && item.raw.aTime === "2024-03-25",
    );
    assert.ok(group);
    assert.equal(group.raw.bTime, "2024-08-28");
    for (const [start, end] of [
        ["2024-02-08", "2024-03-25"],
        ["2024-03-25", "2024-08-28"],
        ["2024-08-28", "2024-10-31"],
    ])
        assert.ok(chart.waveAbLines.some(({ points }) => points[0].time === start && points.at(-1).time === end));
    assert.ok(chart.annotations.some((item) => item.title === "A 起" && item.time === "2024-02-08"));
    assert.ok(chart.annotations.some((item) => item.title === "A 顶" && item.time === "2024-03-25"));
    assert.ok(chart.annotations.some((item) => item.title === "B / C" && item.time === "2024-08-28"));
    assert.ok(chart.annotations.some((item) => item.title === "C 顶" && item.time === "2024-10-31"));
    chart.updateWaveProjectionHover("2024-08-28");
    assert.equal(chart.hoveredWaveProjection.id, group.id);
    assert.deepEqual(endpointDrawing(chart).labels, ["A 起点 3.5328", "A 终点 6.3893", "B 3.6057", "C 终点 7.6469"]);
    assert.ok(targetLabels(chart, rendered.guides).some((label) => /本段结束未达成/.test(label)));
    assert.deepEqual(rendered.range, originalRange);
    chart.updateWaveProjectionHover("2024-03-05");
    assert.equal(chart.hoveredWaveProjection.id, group.id);
    assert.ok(rendered.guides.some((guide) => guide.price === 5.289057414759589));
    assert.ok(rendered.guides.some((guide) => guide.price === 5.783644844765735));
    chart.setTheory({ ...fixture.theory, asof: "2024-09-02", lecture_drawing: { strokes: [] } }, false);
    const previous = chart.autoWaveProjections.find(
        (item) => item.raw.originTime === "2024-02-08" && item.raw.aTime === "2024-03-25",
    );
    assert.equal(previous.raw.bTime, "2024-07-09");
    assert.equal(previous.raw.cTime, undefined);
});

function targetLabels(chart, guides, priceToCoordinate = () => 100) {
    const overlay = new TargetGuideOverlay();
    const range = chart.chart.timeScale().getVisibleLogicalRange();
    const overlayChart = {
        timeScale: () => ({
            timeToCoordinate: (time) =>
                ((chart.data.bars.findIndex((bar) => bar.time === time) - range.from) * 2000) /
                Math.max(1, range.to - range.from),
        }),
    };
    overlay.attached({ chart: overlayChart, series: { priceToCoordinate }, requestUpdate() {} });
    overlay.setGuides(guides);
    const labels = [];
    const context = Object.fromEntries(
        ["save", "restore", "fillRect", "beginPath", "stroke", "moveTo", "lineTo", "setLineDash"].map((name) => [
            name,
            () => {},
        ]),
    );
    context.measureText = (text) => ({ width: text.length * 6 });
    context.fillText = (text) => labels.push(text);
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 2000, height: 200 } }) });
    return labels;
}

test("confirmed Xinhua N uses the shared one-p and two-t target guides before any buy", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const view = {
        ...fixture.view,
        asof: "2024-03-05",
        bars: fixture.view.bars.filter((bar) => bar.time <= "2024-03-05"),
    };
    const { chart, rendered } = chartHarness(view.bars);
    const lines = [];
    chart.chart.addSeries = (_type, options) => {
        const line = {
            options,
            setData(points) {
                this.points = points;
            },
        };
        lines.push(line);
        return line;
    };
    chart.data = view;
    chart.theory = { ...fixture.theory, asof: view.asof };
    chart.options.rules = true;
    chart.selected = buildAnnotations(view, chart.theory)[0];
    assert.equal(chart.selected.raw.event, "n_completed");
    assert.deepEqual(
        chart.selected.levels.filter((level) => level.stage).map((level) => level.name),
        ["一饱（正 N）", "二吐（正 N）"],
    );
    assert.deepEqual(
        fixture.theory.events[0].levels.filter((level) => level.stage).map((level) => level.name),
        ["1P 投影", "2T 投影"],
    );
    assert.equal(view.markers.length, 0);
    chart.drawLevels();
    assert.deepEqual(
        rendered.guides.map(({ stage, name, price, start, end }) => ({ stage, name, price, start, end })),
        [
            { stage: "one_p", name: "一饱（正 N）", price: 5.289057414759589, start: "2024-03-04", end: null },
            { stage: "two_t", name: "二吐（正 N）", price: 5.783644844765735, start: "2024-03-04", end: null },
        ],
    );
    const targets = lines.filter((line) => /一饱|二吐/.test(line.options.title));
    assert.equal(targets.length, 2);
    assert.ok(
        targets.every(
            (line) => line.points.length === 1 && !line.options.lastValueVisible && !line.options.priceLineVisible,
        ),
    );
    assert.deepEqual(targetLabels(chart, rendered.guides), ["一饱 5.2891 · 未突破", "二吐 5.7836 · 未突破"]);
    chart.data = fixture.view;
    chart.theory = fixture.theory;
    chart.drawLevels();
    assert.ok(rendered.guides.every((guide) => guide.end === null && guide.start === "2024-03-04"));
    chart.options.rules = false;
    chart.drawLevels();
    assert.equal(rendered.guides.length, 2);
    chart.options.rules = true;
    chart.options.levels = false;
    chart.drawLevels();
    assert.equal(rendered.guides.length, 0);
    chart.options.levels = true;
    chart.selected = null;
    chart.drawLevels();
    assert.equal(rendered.guides.length, 0);
    assert.equal(chart.levelLines.length, 0);
});

test("Xinhua N range focus shows its targets with rule markers disabled and preserves selection", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } });
    const selected = { id: "kept-buy", kind: "fill", category: "fills", time: "2024-03-05", levels: [] };
    chart.selected = selected;
    chart.options.fills = true;
    chart.setAnnotationOptions({ tertiaryAbc: false });
    const range = { ...rendered.range };
    for (const bar of fixture.view.bars) {
        chart.updateWaveProjectionHover(bar.time);
        assert.deepEqual(
            rendered.guides.map((guide) => guide.stage),
            ["one_p", "two_t"],
            bar.time,
        );
        assert.deepEqual(targetLabels(chart, rendered.guides), ["一饱 5.2891 · 未突破", "二吐 5.7836 · 未突破"]);
        assert.equal(chart.selected, selected);
        assert.deepEqual(rendered.range, range);
    }
    chart.updateWaveProjectionHover(null);
    assert.equal(rendered.guides.length, 0);
    chart.selected = null;
    chart.focusTrade("2024-03-05");
    assert.deepEqual(
        rendered.guides.map((guide) => guide.stage),
        ["one_p", "two_t"],
    );
    chart.options.levels = false;
    chart.drawLevels();
    assert.equal(rendered.guides.length, 0);
});

test("an explicitly selected N keeps both target labels alongside hovered ABC targets", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } });
    chart.selectAnnotation(fixture.theory.events[0].id, false);
    chart.hoveredWaveProjection = {
        id: "overlapping-abc",
        kind: "wave-projection",
        category: "wave-projection",
        time: "2024-03-04",
        raw: { originTime: "2024-02-29", origin: 4.3, aTime: "2024-03-01", aHigh: 4.7, bTime: "2024-03-04", bLow: 4.4 },
        levels: [
            { name: "C 浪目标 1×A", stage: "c_equal", price: 5.5, anchor_at: "2024-03-04", available_at: "2024-03-04" },
        ],
    };
    rendered.range = { from: 8, to: fixture.view.bars.length - 1 };
    chart.drawLevels();
    assert.deepEqual(
        rendered.guides.map((guide) => guide.stage),
        ["c_equal", "one_p", "two_t"],
    );
    const labels = targetLabels(chart, rendered.guides, () => -100);
    assert.ok(labels.some((label) => /一饱.*5\.2891.*图外/.test(label)));
    assert.ok(labels.some((label) => /二吐.*5\.7836.*图外/.test(label)));
    const nGuides = rendered.guides.filter((guide) => guide.stage === "one_p" || guide.stage === "two_t");
    assert.ok(nGuides.every((guide) => guide.start === "2024-03-04" && guide.end === null));
});

test("N focus stops after two-t, preserves the first break and clears after data or selection changes", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const data = [
        ...fixture.view.bars,
        { time: "2024-03-21", open: 5.3, high: 5.9, low: 5.2, close: 5.7, volume: 1 },
        { time: "2024-03-22", open: 5.7, high: 6, low: 5.7, close: 5.9, volume: 1 },
    ];
    const { chart, rendered } = chartHarness(data);
    chart.setTheory({ ...fixture.theory, asof: "2024-03-22", shapes: [], lecture_drawing: { strokes: [] } });
    chart.updateWaveProjectionHover("2024-03-21");
    assert.ok(rendered.guides.every((guide) => guide.end === "2024-03-21" && guide.targetState === "已突破"));
    chart.updateWaveProjectionHover("2024-03-22");
    assert.equal(rendered.guides.length, 0);
    chart.focusNTargets("2024-03-05");
    assert.equal(rendered.guides.length, 2);
    chart.focusNTargets("2024-03-22");
    assert.equal(rendered.guides.length, 0);
    chart.selectAnnotation(fixture.theory.events[0].id, false);
    assert.equal(rendered.guides.length, 2);
    chart.setTheory(null);
    assert.equal(chart.selected, null);
    assert.equal(chart.focusedNTarget, null);
    assert.equal(rendered.guides.length, 0);
    chart.updateWaveProjectionHover("2024-03-05");
    assert.equal(rendered.guides.length, 0);
});

test("selected buy targets and focused N targets are shown once per price and stage", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } });
    const n = chart.nTargetObservations[0].item;
    chart.selected = {
        ...n,
        id: "buy-with-same-targets",
        kind: "fill",
        category: "fills",
        raw: {},
        time: "2024-03-20",
    };
    chart.options.fills = true;
    chart.updateWaveProjectionHover("2024-03-05");
    assert.deepEqual(
        rendered.guides.map((guide) => guide.stage),
        ["one_p", "two_t"],
    );
    assert.equal(targetLabels(chart, rendered.guides).length, 2);
});

test("a candidate without target levels keeps its reference while N focus adds the two labels", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } });
    chart.selected = { id: "candidate", kind: "candidate", time: "2024-03-05", price: 4.6 };
    chart.updateWaveProjectionHover("2024-03-05");
    assert.deepEqual(
        rendered.guides.map((guide) => guide.stage),
        ["one_p", "two_t"],
    );
    assert.equal(chart.levelLines.length, 3);
    assert.equal(chart.selected.id, "candidate");
});

test("a selected N retains labels when C is clipped at either side or at a fractional viewport boundary", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_targets.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } });
    chart.selectAnnotation(fixture.theory.events[0].id, false);
    for (const range of [
        { from: 0, to: 1 },
        { from: 8.5, to: 13.5 },
    ]) {
        rendered.range = range;
        chart.refreshMarkers();
        const labels = targetLabels(chart, rendered.guides, () => -100);
        assert.equal(labels.length, 2, JSON.stringify(range));
        assert.ok(labels.some((label) => /一饱.*图外/.test(label)));
        assert.ok(labels.some((label) => /二吐.*图外/.test(label)));
        assert.ok(rendered.guides.every((guide) => guide.start === "2024-03-04" && guide.end === null));
    }
    chart.selected = null;
    chart.focusNTargets(null);
    rendered.range = { from: 0, to: fixture.view.bars.length - 1 };
    chart.updateWaveProjectionHover("2024-03-05");
    rendered.range = { from: 8.5, to: 13.5 };
    chart.refreshMarkers();
    assert.equal(chart.hoveredNTarget, null);
    assert.equal(rendered.guides.length, 0);
});

test("mixed Xinhua N history renders original March 5 measurements for range focus and its March 20 buy", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_target_sources.json", import.meta.url)));
    const { chart, rendered } = chartHarness(fixture.view.bars);
    chart.data = fixture.view;
    chart.setTheory(fixture.theory, false);
    chart.setAnnotationOptions({ tertiaryAbc: false, fills: true, signals: true });
    const prices = [5.289057414759589, 5.783644844765735];
    const nGuides = () => rendered.guides.filter((guide) => ["one_p", "two_t"].includes(guide.stage));
    const original = chart.nTargetObservations.find(({ item }) => item.time === "2024-03-05").item;
    for (const bar of fixture.view.bars.filter((bar) => bar.time >= "2024-02-29" && bar.time <= "2024-03-18")) {
        chart.updateWaveProjectionHover(bar.time);
        assert.equal(chart.hoveredNTarget.id, original.id, bar.time);
        assert.deepEqual(
            nGuides().map((guide) => guide.price),
            prices,
        );
    }
    chart.selectAnnotation(fixture.view.markers.find((marker) => marker.kind === "fill").id, false);
    assert.equal(chart.selectedNTargetId(), original.id);
    chart.updateWaveProjectionHover("2024-03-20");
    assert.equal(chart.hoveredNTarget.id, original.id);
    assert.deepEqual(
        nGuides().map((guide) => guide.price),
        prices,
    );
    const labels = targetLabels(chart, nGuides());
    assert.equal(labels.length, 2);
    assert.ok(labels.some((label) => label.includes("一饱 5.2891")));
    assert.ok(labels.some((label) => label.includes("二吐 5.7836")));
});

function endpointDrawing(chart) {
    const labels = [],
        circles = [],
        textXs = [];
    const context = Object.fromEntries(
        ["save", "restore", "beginPath", "fill", "moveTo", "lineTo", "stroke", "strokeText"].map((name) => [
            name,
            () => {},
        ]),
    );
    context.measureText = (text) => ({ width: text.length * 6 });
    context.arc = (x, y) => circles.push({ x, y });
    context.fillText = (text, x) => {
        labels.push(text);
        textXs.push(x);
    };
    const range = chart.chart.timeScale().getVisibleLogicalRange();
    const width = Math.max(100, (range.to - range.from) * 20);
    chart.waveEndpointOverlay.updateAllViews();
    chart.waveEndpointOverlay.draw({
        useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width, height: 250 } }),
    });
    return { labels, circles, textXs, width };
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

test("the equal-target hit adds a dated 1.618 guide for automatic ABC and selected evidence without changing zoom", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_c_extension.json", import.meta.url)));
    const continuation = fixture.june2020.bars
        .filter(([time]) => time > theory.asof)
        .map(([time, open, high, low, close, volume]) => ({ time, open, high, low, close, volume }));
    const { chart, rendered } = chartHarness([...bars, ...continuation]);
    const seriesOptions = [];
    const addSeries = chart.chart.addSeries;
    chart.chart.addSeries = (type, options) => {
        seriesOptions.push(options);
        return addSeries(type, options);
    };
    const originalRange = { ...rendered.range };
    chart.setTheory({ ...theory, asof: "2020-06-15" });
    assert.equal(rendered.guides.length, 2);
    chart.setTheory({ ...theory, asof: "2020-06-16" });
    assert.equal(rendered.guides.length, 3);
    const level = rendered.guides.find((guide) => guide.stage === "c_1618");
    assert.ok(Math.abs(level.price - 5.54082) < 1e-10);
    assert.equal(level.start, "2020-06-16");
    assert.equal(level.end, null);
    assert.deepEqual(rendered.range, originalRange);
    const options = seriesOptions.find((item) => item.title === "C 浪目标 1.618×A");
    assert.equal(options.autoscaleInfoProvider(), null);
    chart.selectAnnotation(chart.autoWaveEvidence[0].id, false);
    assert.equal(rendered.guides.find((guide) => guide.stage === "c_1618").start, "2020-06-16");
    chart.setAnnotationOptions({ levels: false });
    assert.equal(rendered.guides.length, 0);
});

test("ordinary Guofang ABC draws all three targets and adds the C high only after its structural confirmation", () => {
    const ordinary = JSON.parse(readFileSync(new URL("./fixtures/guofang_2024_ordinary_c_wave.json", import.meta.url)));
    const data = ordinary.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const { chart, rendered } = chartHarness(data);
    const structure = { ...ordinary.theory, shapes: [], lecture_drawing: { strokes: [] } };
    const options = [];
    const addSeries = chart.chart.addSeries;
    chart.chart.addSeries = (type, value) => {
        options.push(value);
        return addSeries(type, value);
    };
    const originalRange = { ...rendered.range };
    chart.setTheory({ ...structure, asof: "2024-08-14" });
    assert.equal(rendered.guides.length, 3);
    assert.ok(rendered.guides.every(({ targetState }) => targetState === "待达成"));
    assert.equal(
        rendered.markers.some(({ text }) => text === "C 顶"),
        false,
    );
    const extension = options.find(({ title }) => title === "C 浪目标 1.618×A");
    assert.equal(extension.autoscaleInfoProvider(), null);
    assert.equal(options.find(({ title }) => title.includes("等浪")).lineWidth, 2);
    chart.setTheory({ ...structure, asof: "2025-01-21" });
    assert.equal(
        rendered.markers.some(({ text }) => text === "C 顶"),
        false,
    );
    chart.setTheory({ ...structure, asof: "2025-01-22" });
    assert.ok(
        rendered.markers.some(({ time, text, price }) => time === "2025-01-03" && text === "C 顶" && price > 9.31),
    );
    assert.deepEqual(
        rendered.guides.map(({ targetState }) => targetState),
        ["已触及", "已触及", "本段结束未达成"],
    );
    assert.deepEqual(rendered.range, originalRange);
});

test("a window containing only the C segment retains the corresponding ABC targets", () => {
    const { chart, rendered, data } = ordinaryChart();
    const originalProjection = chart.autoWaveProjection;
    assert.equal(targetLabels(chart, rendered.guides).length, 3);
    rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-11-01"),
        to: data.findIndex((bar) => bar.time === "2025-01-22"),
    };
    const originalRange = { ...rendered.range };
    chart.refreshMarkers();
    assert.equal(chart.autoWaveProjection, originalProjection);
    assert.equal(chart.autoWaveProjection.raw.originTime, "2024-02-08");
    assert.equal(chart.autoWaveProjection.raw.bTime, "2024-07-25");
    assert.equal(rendered.guides.length, 3);
    assert.equal(chart.autoWaveProjection.raw.projectionSource, "n_origin_local_structure");
    for (const [start, end] of [
        ["2024-02-08", "2024-04-12"],
        ["2024-04-12", "2024-07-25"],
        ["2024-07-25", "2025-01-03"],
    ])
        assert.ok(chart.waveAbLines.some(({ points }) => points[0].time === start && points[1].time === end));
    assert.equal(targetLabels(chart, rendered.guides).length, 3);
    // 已确认 C 顶也是组合 A 顶，同价同日标签优先展示组合身份。
    assert.ok(rendered.markers.some(({ text }) => text === "组合 A 顶"));
    assert.deepEqual(rendered.range, originalRange);
});

test("panning from hovered A candles to C replaces the offscreen display anchor without changing the ABC", () => {
    const { chart, rendered, data } = ordinaryChart();
    chart.updateWaveProjectionHover("2024-02-23");
    const originalProjection = chart.hoveredWaveProjection;
    assert.ok(rendered.guides.every(({ start }) => start === "2024-02-23"));
    assert.equal(targetLabels(chart, rendered.guides).length, 3);
    rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-11-01"),
        to: data.findIndex((bar) => bar.time === "2025-01-22"),
    };
    const originalRange = { ...rendered.range };
    chart.refreshMarkers();
    assert.equal(chart.hoveredWaveProjection, null);
    assert.equal(chart.autoWaveProjection, originalProjection);
    assert.ok(rendered.guides.every(({ start }) => start === "2024-11-01"));
    assert.equal(targetLabels(chart, rendered.guides).length, 3);
    assert.deepEqual(rendered.range, originalRange);
});

test("C-tail hover draws three real labels while keeping original target anchors and first-touch evidence", () => {
    const { chart, rendered, data } = ordinaryChart();
    rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-11-01"),
        to: data.findIndex((bar) => bar.time === "2025-01-03"),
    };
    chart.refreshMarkers();
    chart.updateWaveProjectionHover("2024-12-12");
    const labels = targetLabels(chart, rendered.guides, (price) => (price < 7 ? 240 : price > 10 ? -100 : 100));
    assert.equal(labels.length, 3);
    assert.ok(labels.some((label) => /C 0\.618.*已触及.*图外/.test(label)));
    assert.ok(labels.some((label) => /等浪.*已触及/.test(label)));
    assert.ok(labels.some((label) => /C 1\.618.*本段结束未达成.*图外/.test(label)));
    assert.ok(rendered.guides.every((guide) => guide.start === "2024-12-12"));
    assert.deepEqual(
        rendered.guides.map(({ firstTouchedAt }) => firstTouchedAt),
        ["2024-10-11", "2024-12-03", null],
    );
    assert.deepEqual(
        chart.levelLines[0].points.map(({ time }) => time),
        ["2024-07-25", "2024-10-11"],
    );
    assert.deepEqual(
        chart.levelLines[1].points.map(({ time }) => time),
        ["2024-07-25", "2024-12-03"],
    );
    assert.equal(chart.hoveredWaveProjection.levels[0].valid_until, "2025-01-03");
});

test("A-segment hover keeps all three target labels visible outside that candle's price axis", () => {
    const { chart, rendered, data } = ordinaryChart();
    rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-02-08"),
        to: data.findIndex((bar) => bar.time === "2024-02-29"),
    };
    chart.refreshMarkers();
    const originalRange = { ...rendered.range };
    chart.updateWaveProjectionHover("2024-02-23");
    const labels = targetLabels(chart, rendered.guides, () => -100);
    assert.equal(labels.length, 3);
    assert.ok(labels.every((label) => /图外/.test(label)));
    assert.ok(labels.some((label) => /等浪/.test(label)));
    assert.deepEqual(rendered.range, originalRange);
    assert.equal(chart.hoveredWaveProjection.levels[0].available_at, "2024-08-14");
    assert.equal(chart.hoveredWaveProjection.levels[0].anchor_at, "2024-07-25");
});

test("long B-correction hover draws three target labels with their later availability preserved", () => {
    const { chart, rendered, data } = ordinaryChart();
    rendered.range = {
        from: data.findIndex((bar) => bar.time === "2024-06-03"),
        to: data.findIndex((bar) => bar.time === "2024-07-01"),
    };
    chart.refreshMarkers();
    chart.updateWaveProjectionHover("2024-06-04");
    assert.equal(targetLabels(chart, rendered.guides, () => -100).length, 3);
    assert.match(chart.waveProjectionHoverLines()[1].text, /目标生效 2024-08-14/);
    assert.ok(chart.hoveredWaveProjection.levels.every(({ available_at }) => available_at === "2024-08-14"));
});

test("all 217 ABC candles in the full 2018 response draw this group's endpoints and three tooltip targets", () => {
    const { chart, rendered, data } = ordinaryChart("2026-09-30");
    assert.equal(data.length, 2123);
    assert.equal(chart.autoWaveProjections.length, 7);
    const originalRange = { ...rendered.range };
    const abcBars = data.filter(({ time }) => time >= "2024-02-08" && time <= "2025-01-03");
    assert.equal(abcBars.length, 217);
    for (const { time } of abcBars) {
        chart.updateWaveProjectionHover(time);
        assert.equal(chart.hoveredWaveProjection.raw.nTime, "2024-02-23", time);
        assert.equal(chart.hoveredWaveProjection.raw.aTime, "2024-04-12", time);
        const lines = chart.waveProjectionHoverLines();
        assert.match(lines[0].text, /A 起 2024-02-08.*A 顶 2024-04-12.*B 2024-07-25.*C 顶 2025-01-03/);
        assert.match(lines[1].text, /目标生效 2024-08-14/);
        assert.equal(lines.filter(({ tag }) => tag === "div").length, 3);
        assert.ok(lines.some(({ text }) => /0\.618.*6\.8777.*已触及/.test(text)));
        assert.ok(lines.some(({ text }) => /等浪.*8\.2748.*已触及/.test(text)));
        assert.ok(lines.some(({ text }) => /1\.618.*10\.535.*本段结束未达成/.test(text)));
        assert.deepEqual(endpointDrawing(chart).labels, [
            "A 起点 4.2190",
            "A 终点 7.8764",
            "B 4.6174",
            "C 终点 9.3168",
        ]);
        assert.deepEqual(
            chart.waveEndpointOverlay.points.map(({ time, price }) => ({ time, price })),
            [
                { time: "2024-02-08", price: 4.219033114321772 },
                { time: "2024-04-12", price: 7.876399281535692 },
                { time: "2024-07-25", price: 4.617428636643342 },
                { time: "2025-01-03", price: 9.316823462102839 },
            ],
        );
    }
    assert.deepEqual(rendered.range, originalRange);
    assert.equal(chart.selected, undefined);
});

test("hover endpoint labels survive exact viewport boundaries while their dots remain at the real dates", () => {
    const { chart, rendered, data } = ordinaryChart();
    rendered.range = {
        from: data.findIndex(({ time }) => time === "2024-02-08"),
        to: data.findIndex(({ time }) => time === "2025-01-03"),
    };
    chart.refreshMarkers();
    chart.updateWaveProjectionHover("2024-12-12");
    const drawing = endpointDrawing(chart);
    assert.equal(drawing.labels.length, 4);
    assert.equal(drawing.circles[0].x, 0);
    assert.equal(drawing.circles.at(-1).x, drawing.width);
    assert.ok(drawing.textXs[0] > 0);
    assert.ok(drawing.textXs.at(-1) < drawing.width);
    assert.equal(rendered.guides.length, 3);
});

test("hover shows only known endpoints and clears them when ABC or its theory is disabled", () => {
    const { chart, rendered } = ordinaryChart("2025-01-21");
    chart.updateWaveProjectionHover("2024-12-12");
    assert.deepEqual(endpointDrawing(chart).labels, ["A 起点 4.2190", "A 终点 7.8764", "B 4.6174"]);
    assert.equal(rendered.guides.length, 3);
    chart.setAnnotationOptions({ tertiaryAbc: false });
    assert.deepEqual(endpointDrawing(chart).labels, []);
    chart.setAnnotationOptions({ tertiaryAbc: true });
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(chart.waveEndpointOverlay.points.length, 3);
    const structure = chart.theory;
    chart.setTheory({ ...structure, asof: "2025-01-22" });
    assert.deepEqual(endpointDrawing(chart).labels, []);
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(chart.waveEndpointOverlay.points.at(-1).label, "C 终点");
    chart.setTheory(null);
    assert.deepEqual(endpointDrawing(chart).labels, []);
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(chart.hoveredWaveProjection, null);
});

test("changing candle data clears hovering ABC endpoints and previous selections", () => {
    const { chart } = ordinaryChart();
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(endpointDrawing(chart).labels.length, 4);
    chart.tooltip = { hidden: false };
    chart.candles = { setData() {} };
    chart.volume = { setData() {} };
    chart.onViewport = () => {};
    const replacement = {
        bars: [
            { time: "2026-09-29", open: 5, high: 6, low: 4, close: 5, volume: 100 },
            { time: "2026-09-30", open: 5, high: 6, low: 4, close: 5, volume: 100 },
        ],
        markers: [],
        orders: [],
        asof: "2026-09-30",
    };
    chart.setData(replacement);
    assert.equal(chart.hoveredWaveProjection, null);
    assert.equal(chart.selected, null);
    assert.equal(chart.theory, null);
    assert.deepEqual(chart.waveEndpointOverlay.points, []);
    assert.equal(chart.container.dataset.waveEndpointCount, "0");
});

test("leaving an ABC restores a selected BUY's original endpoints without another selection or zoom", () => {
    const { chart, rendered } = ordinaryChart();
    const buy = {
        id: "selected-wave-buy",
        time: "2024-08-16",
        signal_time: "2024-08-15",
        kind: "fill",
        category: "fills",
        side: "BUY",
        price: 5,
        levels: [{ name: "成交价", price: 5 }],
        decision_evidence: [
            {
                wave_entry_path: "two_t_held_defense_gap_attack",
                wave_a_origin_date: "2024-02-08",
                wave_a_origin: 4.219033114321772,
                wave_a_high_date: "2024-04-12",
                wave_a_high: 7.876399281535692,
                wave_b_low_date: "2024-07-25",
                wave_b_low: 4.617428636643342,
                wave_breakout_close: 5,
            },
        ],
    };
    chart.options.fills = true;
    chart.windowAnnotations.push(buy);
    let selections = 0;
    chart.onSelect = () => selections++;
    chart.selectAnnotation(buy.id, false);
    const selectedPoints = chart.waveEndpointOverlay.points;
    assert.deepEqual(endpointDrawing(chart).labels, ["A 起点 4.2190", "A 高 7.8764", "B 4.6174", "C 确认 5.0000"]);
    const originalRange = { ...rendered.range };
    chart.updateWaveProjectionHover("2024-12-12");
    assert.ok(endpointDrawing(chart).labels.includes("A 终点 7.8764"));
    assert.ok(endpointDrawing(chart).labels.includes("C 终点 9.3168"));
    assert.equal(rendered.guides.length, 3);
    chart.updateWaveProjectionHover(null);
    assert.deepEqual(chart.waveEndpointOverlay.points, selectedPoints);
    assert.ok(endpointDrawing(chart).labels.includes("C 确认 5.0000"));
    chart.updateWaveProjectionHover("2024-12-12");
    chart.setAnnotationOptions({ tertiaryAbc: false, levels: false });
    assert.deepEqual(chart.waveEndpointOverlay.points, selectedPoints);
    assert.ok(endpointDrawing(chart).labels.includes("A 高 7.8764"));
    assert.equal(chart.selected, buy);
    assert.equal(selections, 1);
    assert.deepEqual(rendered.range, originalRange);
});

test("hovering any A, B or C candle shows that group's targets without changing a selected fill or zoom", () => {
    const { chart, rendered } = ordinaryChart();
    const fill = {
        id: "selected-buy",
        time: "2024-11-01",
        kind: "fill",
        category: "fills",
        side: "BUY",
        price: 6,
        levels: [{ name: "成交价", price: 6 }],
    };
    chart.options.fills = true;
    chart.windowAnnotations.push(fill);
    let selections = 0;
    chart.onSelect = () => selections++;
    chart.selectAnnotation(fill.id, false);
    const originalRange = { ...rendered.range };
    for (const time of [
        "2024-02-08",
        "2024-02-23",
        "2024-04-12",
        "2024-06-04",
        "2024-07-25",
        "2024-11-01",
        "2025-01-03",
    ]) {
        chart.updateWaveProjectionHover(time);
        assert.equal(chart.hoveredWaveProjection.raw.aTime, "2024-04-12");
        assert.equal(rendered.guides.length, 3);
        assert.equal(chart.selected, fill);
        assert.equal(chart.options.tertiaryAbc, true);
        assert.deepEqual(rendered.range, originalRange);
    }
    assert.equal(selections, 1);
    chart.updateWaveProjectionHover("2025-01-06");
    assert.equal(chart.hoveredWaveProjection, null);
    assert.equal(chart.selected, fill);
    assert.equal(rendered.guides.length, 0);
    assert.equal(chart.container.dataset.levelCount, "1");
    chart.updateWaveProjectionHover(null);
    assert.equal(chart.selected, fill);
});

test("overlapping ABCs prefer an explicit hit or selected group and otherwise choose consistently", () => {
    const { chart } = ordinaryChart();
    const current = chart.autoWaveProjections.find(({ raw }) => raw.nTime === "2024-02-23");
    const older = { ...current, id: "overlapping-older-abc", raw: { ...current.raw, aTime: "2024-04-10" } };
    chart.autoWaveProjections.unshift(older);
    chart.updateWaveProjectionHover("2024-06-04", older.id);
    assert.equal(chart.hoveredWaveProjection, older);
    chart.updateWaveProjectionHover("2024-06-04");
    assert.equal(chart.hoveredWaveProjection, current);
    chart.autoWaveProjections.reverse();
    chart.updateWaveProjectionHover("2024-06-04");
    assert.equal(chart.hoveredWaveProjection, current);
    chart.selected = older;
    chart.updateWaveProjectionHover("2024-06-04");
    assert.equal(chart.hoveredWaveProjection, older);
    chart.updateWaveProjectionHover("2024-06-04", current.id);
    assert.equal(chart.hoveredWaveProjection, current);
    assert.equal(chart.selected, older);
    chart.updateWaveProjectionHover(null);
    assert.equal(chart.selected, older);
});

test("repeated movement within one ABC avoids redraws and respects a disabled ABC layer", () => {
    const { chart, rendered } = ordinaryChart();
    const setGuides = chart.targetGuideOverlay.setGuides;
    let updates = 0;
    chart.targetGuideOverlay.setGuides = (guides) => {
        updates++;
        setGuides(guides);
    };
    chart.updateWaveProjectionHover("2024-06-04");
    const afterFirst = updates;
    chart.updateWaveProjectionHover("2024-06-05");
    assert.equal(updates, afterFirst);
    chart.setAnnotationOptions({ tertiaryAbc: false });
    assert.equal(chart.hoveredWaveProjection, null);
    chart.updateWaveProjectionHover("2024-06-04");
    assert.equal(chart.hoveredWaveProjection, null);
    assert.equal(chart.options.tertiaryAbc, false);
    assert.equal(rendered.guides.length, 0);
});

test("panning away clears a stale hover and archived ABC targets without changing selection or the viewport", () => {
    const { chart, rendered, data } = ordinaryChart();
    chart.updateWaveProjectionHover("2024-06-04");
    rendered.range = { from: data.findIndex((bar) => bar.time === "2025-05-06"), to: data.length - 1 };
    const originalRange = { ...rendered.range };
    chart.refreshMarkers();
    assert.equal(chart.hoveredWaveProjection, null);
    assert.equal(chart.autoWaveProjection, null);
    assert.deepEqual(endpointDrawing(chart).labels, []);
    assert.equal(rendered.guides.length, 0);
    assert.deepEqual(rendered.range, originalRange);
    assert.equal(chart.selected, undefined);
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

test("locating an active ABC brings its B/C start into view while retaining its targets and zoom", () => {
    const later = [...bars, { time: "2026-09-30", open: 5, high: 5, low: 5, close: 5, volume: 1 }];
    const { chart, rendered } = chartHarness(later);
    rendered.range = { from: bars.length, to: bars.length + 20 };
    chart.setTheory({ ...theory, asof: "2026-09-30" });
    assert.equal(rendered.markers.length, 0);
    // 未提供结束确认的原观察仍活动到当前 asof；C 区间可保留目标。
    assert.equal(rendered.guides.length, 3);
    assert.equal(chart.focusWaveProjection(), true);
    assert.ok(rendered.markers.some((marker) => marker.text === "B / C"));
    assert.equal(rendered.guides.length, 3);
    assert.equal(rendered.guides.find((guide) => guide.stage === "c_1618").start, "2026-09-30");
    assert.equal(chart.selected.time, "2020-04-28");
    const target = bars.findIndex((bar) => bar.time === "2020-04-28");
    assert.equal(rendered.range.to - rendered.range.from, 20);
    assert.ok(rendered.range.from <= target && rendered.range.to >= target);
    assert.equal(chart.focusFlashOverlay.active.time, "2020-04-28");
    chart.setTheory(null);
    assert.equal(chart.focusWaveProjection(), false);
});

test("successful date and holding navigation pulse on the actual candle after panning", () => {
    const { chart, rendered } = chartHarness();
    rendered.range = { from: 30, to: 50 };
    assert.equal(chart.focus("2020-02-04"), true);
    assert.equal(chart.container.dataset.focusFlashActive, "true");
    assert.equal(chart.container.dataset.focusFlashMotion, "pulse");
    const focusedIndex = bars.findIndex((bar) => bar.time === "2020-02-04");
    assert.equal(chart.focusFlashOverlay.active.price, bars[focusedIndex].close);
    assert.equal(chart.focusFlashOverlay.projected.x, (focusedIndex - rendered.range.from) * 20);

    const from = "2020-02-04",
        to = "2020-05-29";
    const interval = bars.filter((bar) => bar.time >= from && bar.time <= to);
    const target = interval[Math.floor((interval.length - 1) / 2)];
    assert.equal(chart.focusRange(from, to), true);
    assert.equal(chart.focusFlashOverlay.active.time, target.time);
    assert.equal(chart.focusFlashOverlay.active.price, target.close);
    const index = bars.findIndex((bar) => bar.time === target.time);
    assert.ok(rendered.range.from <= index && rendered.range.to >= index);
    assert.equal(rendered.range.to - rendered.range.from, 20);
});

test("repeated navigation replaces the pulse, animates its rings and clears the feedback", () => {
    const { chart } = chartHarness();
    chart.focus("2020-02-04");
    chart.focus("2020-05-29");
    const overlay = chart.focusFlashOverlay;
    assert.equal(overlay.active.time, "2020-05-29");
    const radii = [];
    const context = Object.fromEntries(
        ["save", "restore", "beginPath", "stroke", "fill"].map((method) => [method, () => {}]),
    );
    context.arc = (x, y, radius) => {
        assert.equal(x, overlay.projected.x);
        assert.equal(y, overlay.projected.y);
        radii.push(radius);
    };
    const target = { useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 10000, height: 10000 } }) };
    overlay.draw(target);
    const firstRadius = radii[0];
    overlay.tick(overlay.active.startedAt + 200);
    radii.length = 0;
    overlay.draw(target);
    assert.ok(radii[0] > firstRadius);
    overlay.tick(overlay.active.startedAt + overlay.active.duration + 1);
    assert.equal(overlay.active, null);
    assert.equal(chart.container.dataset.focusFlashActive, "false");
    assert.equal(chart.container.dataset.focusFlashId, undefined);
});

test("reduced motion uses the existing static locator without changing zoom", () => {
    reducedMotion = true;
    const { chart, rendered } = chartHarness();
    try {
        rendered.range = { from: 30, to: 50 };
        chart.focus("2020-02-04");
        assert.equal(chart.container.dataset.focusFlashMotion, "static");
        assert.equal(rendered.range.to - rendered.range.from, 20);
        assert.ok(chart.focusFlashOverlay.timer !== null);
    } finally {
        chart.focusFlashOverlay.clear();
        reducedMotion = false;
    }
});

test("date, fill and holding-cycle chart navigation preserve the SDK viewport span", () => {
    const { chart, rendered } = chartHarness();
    for (const span of [20, 40.5, bars.length + 10]) {
        for (const locate of [
            () => chart.focus("2020-02-04"),
            () => chart.focusTrade("2020-05-29"),
            () => chart.focusRange("2020-02-04", "2020-05-29"),
        ]) {
            rendered.range = { from: 70, to: 70 + span };
            assert.equal(locate(), true);
            assert.equal(rendered.range.to - rendered.range.from, span);
        }
    }
    const before = { ...rendered.range };
    const pulse = chart.focusFlashOverlay.active;
    assert.equal(chart.focus("2099-01-01"), false);
    assert.equal(chart.focusRange("2099-01-01", "2099-12-31"), false);
    assert.deepEqual(rendered.range, before);
    assert.equal(chart.focusFlashOverlay.active, pulse);
});

test("locating a wave entry keeps zoom while retaining its four endpoint labels", () => {
    const { chart, rendered } = chartHarness();
    chart.setTheory(theory);
    const entry = {
        id: "wave-entry",
        kind: "fill",
        side: "BUY",
        time: "2020-05-29",
        price: 3.67,
        signal_time: "2020-05-29",
        levels: [],
        decision_evidence: [
            {
                wave_entry_path: "two_t_held_defense_gap_attack",
                wave_a_origin_date: "2020-02-04",
                wave_a_origin: 2.9,
                wave_a_high_date: "2020-03-02",
                wave_a_high: 4.39,
                wave_b_low_date: "2020-04-28",
                wave_b_low: 3.13,
                wave_breakout_close: 3.67,
            },
        ],
    };
    chart.windowAnnotations.push(entry);
    chart.waveEndpointOverlay = {
        setPoints: (points) => {
            rendered.endpoints = points;
        },
    };
    rendered.range = { from: 5, to: 25 };
    chart.selectAnnotation(entry.id);
    chart.flashSelectedAnnotation(entry.id, "execution");
    assert.equal(chart.selected.time, "2020-05-29");
    assert.equal(chart.focusFlashOverlay.active.id, entry.id);
    assert.equal(chart.focusFlashOverlay.active.color, "#ebbc70");
    assert.equal(rendered.range.to - rendered.range.from, 20);
    assert.deepEqual(
        rendered.endpoints.map((point) => point.label),
        ["A 起点", "A 高", "B", "C 确认"],
    );
});
