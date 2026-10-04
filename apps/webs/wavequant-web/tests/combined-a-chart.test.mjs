import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

globalThis.window = {
    LightweightCharts: { LineSeries: "line" },
    matchMedia: () => ({ matches: false }),
};
globalThis.document = { documentElement: {} };
globalThis.requestAnimationFrame = () => 1;
globalThis.cancelAnimationFrame = () => {};
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.MutationObserver = class {
    observe() {}
};
const { PriceChart } = await import("../public/charts.js");
const { WaveEndpointOverlay } = await import("../public/wave-endpoint-overlay.js");
const { combinedAAnnotations } = await import("../public/combined-a-wave.js");
const { markerGroups } = await import("../public/annotations.js");

const dates = ["2025-01-03", "2025-01-21", "2025-01-22", "2025-01-23", "2025-02-03", "2025-02-04"];
const sampleBars = dates.map((time) => ({ time, open: 7, high: 8, low: 6, close: 7, volume: 100 }));
const observation = {
    id: "combined-a-sample",
    title: "组合大 A 50%",
    price: 6.75,
    start: "2025-01-22",
    end: "2025-02-04",
    available_at: "2025-01-22",
    invalidatedAt: null,
    halfHeld: true,
    firstCloseBelowHalf: null,
    preconditions: [],
    originTime: "2024-02-08",
    origin: 4.2,
    cTime: "2025-01-03",
    cHigh: 9.3,
    strengthObservations: [],
};

function chartHarness(bars = sampleBars) {
    const chart = Object.create(PriceChart.prototype);
    const rendered = { range: { from: 0, to: bars.length - 1 }, added: [], removed: [], guides: [] };
    chart.container = { dataset: {} };
    chart.data = { bars, markers: [], orders: [], asof: bars.at(-1).time };
    chart.theory = { asof: bars.at(-1).time, events: [], shapes: [], lecture_drawing: { strokes: [] } };
    chart.options = { tertiaryAbc: true, levels: true, rules: false, fills: true };
    chart.drawingMode = "lecture";
    for (const field of [
        "lines",
        "polylineLines",
        "levelLines",
        "waveAbLines",
        "lastFallHighLines",
        "bullishTurnGuideLines",
        "tertiaryRetracementLines",
        "combinedARetracementLines",
        "autoWaveProjections",
        "autoWaveEvidence",
        "autoCombinedAObservations",
        "annotations",
        "windowAnnotations",
    ])
        chart[field] = [];
    chart.chart = {
        addSeries: (_, options) => {
            const series = {
                options,
                setData(points) {
                    assert.ok(points.every((point, index) => !index || points[index - 1].time < point.time));
                    this.points = points;
                },
            };
            rendered.added.push(series);
            return series;
        },
        removeSeries: (series) => rendered.removed.push(series),
        timeScale: () => ({
            getVisibleLogicalRange: () => rendered.range,
            setVisibleLogicalRange: (range) => {
                rendered.range = range;
            },
            timeToCoordinate: (time) => (bars.findIndex((bar) => bar.time === time) - rendered.range.from) * 20,
        }),
    };
    chart.markers = {
        setMarkers(markers) {
            for (const marker of markers)
                if (marker.position.startsWith("atPrice"))
                    assert.ok(Number.isFinite(marker.price), "SDK price-position marker requires a finite price");
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
        series: { priceToCoordinate: () => 100 },
        requestUpdate() {},
    });
    chart.focusFlashOverlay = { clear() {} };
    chart.tooltip = { hidden: false };
    chart.candles = { setData() {} };
    chart.volume = { setData() {} };
    chart.onSelect = () => {};
    chart.onVisible = () => {};
    chart.onViewport = () => {};
    return { chart, rendered };
}

function withObservation(value = observation) {
    const harness = chartHarness();
    harness.chart.autoCombinedAObservations = [value];
    harness.chart.refreshMarkers();
    return harness;
}

test("the combined A half reference draws as an independent dashed line without scaling or requiring trend geometry", () => {
    const { chart, rendered } = withObservation();
    assert.equal(chart.combinedARetracementLines.length, 1);
    const series = chart.combinedARetracementLines[0];
    assert.equal(series.options.title, "组合大 A 50%");
    assert.equal(series.options.lineStyle, 2);
    assert.equal(series.options.autoscaleInfoProvider(), null);
    assert.equal(series.options.priceLineVisible, false);
    assert.equal(series.options.pointMarkersVisible, false);
    assert.equal(series.options.crosshairMarkerVisible, false);
    assert.deepEqual(series.points, [
        { time: "2025-01-22", value: 6.75 },
        { time: "2025-02-04", value: 6.75 },
    ]);
    assert.equal(chart.container.dataset.combinedARetracementGuides, "1");
    const originalRange = { ...rendered.range };
    chart.drawLevels();
    chart.refreshMarkers();
    assert.equal(chart.combinedARetracementLines[0], series);
    assert.deepEqual(rendered.range, originalRange);
});

test("ABC and target switches clear the half guide and preserve its annotation through repeated option changes", () => {
    const { chart } = withObservation();
    const expected = combinedAAnnotations([observation]).map(({ id }) => id);
    assert.ok(expected.length > 0);
    chart.setAnnotationOptions({ tertiaryAbc: false });
    assert.equal(chart.combinedARetracementLines.length, 0);
    chart.setAnnotationOptions({ tertiaryAbc: true });
    assert.equal(chart.combinedARetracementLines.length, 1);
    chart.setAnnotationOptions({ levels: false });
    assert.equal(chart.combinedARetracementLines.length, 0);
    chart.setAnnotationOptions({ levels: true });
    assert.equal(chart.combinedARetracementLines.length, 1);
    assert.deepEqual(
        chart.annotations.filter(({ id }) => expected.includes(id)).map(({ id }) => id),
        expected,
    );
});

test("unavailable or one-candle observations cannot draw a half reference", () => {
    const { chart } = withObservation({ ...observation, available_at: "2025-02-05" });
    assert.equal(chart.combinedARetracementLines.length, 0);
    chart.autoCombinedAObservations = [{ ...observation, start: observation.end }];
    chart.refreshMarkers();
    assert.equal(chart.combinedARetracementLines.length, 0);
    chart.autoCombinedAObservations = [];
    chart.refreshMarkers();
    assert.equal(chart.combinedARetracementLines.length, 0);
});

test("a failed half keeps the historical dashed line while panning only changes visibility", () => {
    const { chart, rendered } = withObservation({
        ...observation,
        end: "2025-01-23",
        invalidatedAt: "2025-01-23",
        halfHeld: false,
        firstCloseBelowHalf: { time: "2025-01-23", close: 6.5 },
    });
    const series = chart.combinedARetracementLines[0];
    assert.match(series.options.title, /50%.*半幅失守/);
    assert.equal(series.options.lineStyle, 2);
    assert.equal(series.points.at(-1).time, "2025-01-23");
    rendered.range = { from: 4, to: 5 };
    chart.refreshMarkers();
    assert.equal(chart.combinedARetracementLines.length, 0);
    assert.ok(rendered.removed.includes(series));
    rendered.range = { from: 0, to: 3 };
    chart.refreshMarkers();
    assert.equal(chart.combinedARetracementLines.length, 1);
    assert.equal(chart.combinedARetracementLines[0].points.at(-1).time, "2025-01-23");
});

test("changing theory or candle data clears previous combined A observations and line series", () => {
    const { chart, rendered } = withObservation();
    const originalSeries = chart.combinedARetracementLines[0];
    chart.setTheory(null);
    assert.deepEqual(chart.autoCombinedAObservations, []);
    assert.deepEqual(chart.combinedARetracementLines, []);
    assert.deepEqual(chart.combinedAWaveLines, []);
    assert.ok(rendered.removed.includes(originalSeries));
    chart.autoCombinedAObservations = [observation];
    chart.refreshMarkers();
    chart.setData({ bars: sampleBars.slice(-2), markers: [], orders: [], asof: "2025-02-04" });
    assert.deepEqual(chart.autoCombinedAObservations, []);
    assert.deepEqual(chart.combinedARetracementLines, []);
    assert.equal(chart.container.dataset.combinedARetracementGuides, "0");
    assert.equal(chart.container.dataset.combinedAWaveLegs, "0");
});

test("full-history Guofang observes the exact half reference while its original ABC hover and BUY selection remain intact", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
    const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const { chart, rendered } = chartHarness(bars);
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } }, false);
    const observed = chart.autoCombinedAObservations.find(
        ({ originTime, cTime }) => originTime === "2024-02-08" && cTime === "2025-01-03",
    );
    assert.ok(observed);
    assert.equal(chart.autoWaveProjections.length, 7);
    assert.equal(chart.container.dataset.waveAbcCount, "7");
    assert.equal(Number(chart.container.dataset.combinedAWaveCount), chart.autoCombinedAObservations.length);
    const annotation = chart.annotations.find((item) => item.kind === "combined-a-wave" && item.raw.id === observed.id);
    const marker = rendered.markers.find(({ id }) => id === annotation.id);
    assert.equal(marker.price, observed.price);
    assert.equal(marker.color, annotation.color);
    const path = chart.combinedAWaveLines.find(
        ({ points }) => points[0].time === observed.originTime && points[1].time === observed.cTime,
    );
    assert.ok(path);
    assert.deepEqual(path.points, [
        { time: observed.originTime, value: observed.origin },
        { time: observed.cTime, value: observed.cHigh },
    ]);
    for (const [suffix, time, price, text] of [
        ["origin", observed.originTime, observed.origin, "组合 A 起"],
        ["high", observed.cTime, observed.cHigh, "组合 A 顶"],
    ]) {
        const endpoint = rendered.markers.find(({ id }) => id === observed.id + ":" + suffix);
        assert.ok(endpoint);
        assert.equal(endpoint.time, time);
        assert.equal(endpoint.price, price);
        assert.equal(endpoint.text, text);
    }
    assert.ok(Math.abs(observed.price - 6.767928288212306) < 1e-12);
    assert.deepEqual(
        observed.preconditions.map(({ level, sourceLevel, scope, contextAsOf, key }) => ({
            level,
            sourceLevel,
            scope,
            contextAsOf,
            keyTime: key.time,
        })),
        [
            {
                level: 2,
                sourceLevel: 1,
                scope: "source_confirmed_display_context",
                contextAsOf: "2024-02-23",
                keyTime: "2024-01-12",
            },
            {
                level: 3,
                sourceLevel: 2,
                scope: "source_confirmed_display_context",
                contextAsOf: "2024-02-23",
                keyTime: "2023-08-01",
            },
        ],
    );
    assert.match(observed.preconditions[0].sourcePath, /^reversal-continuous-/);
    assert.match(observed.preconditions[1].sourcePath, /^secondary-reversal-continuous-/);
    assert.deepEqual(observed.strengthObservations, []);
    const series = chart.combinedARetracementLines.find(({ points }) => points[0].value === observed.price);
    assert.ok(series);
    assert.equal(series.options.lineStyle, 2);
    assert.equal(series.options.autoscaleInfoProvider(), null);
    const buy = {
        id: "retained-buy",
        time: "2025-01-22",
        kind: "fill",
        category: "fills",
        side: "BUY",
        price: 7,
        levels: [{ name: "成交价", price: 7 }],
    };
    chart.windowAnnotations.push(buy);
    let selections = 0;
    chart.onSelect = () => selections++;
    chart.selectAnnotation(buy.id, false);
    const originalRange = { ...rendered.range };
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(chart.hoveredWaveProjection.raw.nTime, "2024-02-23");
    assert.equal(chart.waveEndpointOverlay.points.length, 4);
    assert.equal(rendered.guides.length, 3);
    assert.equal(
        chart.combinedARetracementLines.find(({ points }) => points[0].value === observed.price),
        series,
    );
    chart.updateWaveProjectionHover(null);
    assert.equal(chart.selected, buy);
    assert.equal(selections, 1);
    assert.deepEqual(rendered.range, originalRange);
    assert.equal(
        chart.combinedARetracementLines.find(({ points }) => points[0].value === observed.price),
        series,
    );
});

test("combined half and strength markers retain their explicit SDK price, color and shape", () => {
    const items = combinedAAnnotations([observation]);
    const base = items.find(({ kind }) => kind === "combined-a-wave");
    const strength = {
        ...base,
        id: "synthetic-strength",
        kind: "combined-a-strength",
        price: 8,
        markerPosition: "atPriceTop",
        markerShape: "arrowUp",
        color: "#a29ce0",
    };
    const groups = markerGroups([...items, strength], { tertiaryAbc: true });
    const baseMarker = groups.find(({ id }) => id === base.id).marker;
    assert.equal(baseMarker.position, "atPriceBottom");
    assert.equal(baseMarker.price, observation.price);
    assert.equal(baseMarker.color, base.color);
    const strengthMarker = groups.find(({ id }) => id === strength.id).marker;
    assert.equal(strengthMarker.price, 8);
    assert.equal(strengthMarker.color, strength.color);
    assert.equal(strengthMarker.shape, "arrowUp");
});

test("a shorter data asof cannot borrow a future theory's C completion, half status or strength observations", () => {
    const fixture = JSON.parse(readFileSync(new URL("./fixtures/guofang_2018_ordinary_c_wave.json", import.meta.url)));
    const bars = fixture.bars.map(([time, open, high, low, close, volume]) => ({
        time,
        open,
        high,
        low,
        close,
        volume,
    }));
    const { chart, rendered } = chartHarness(bars);
    chart.data.asof = "2025-01-09";
    chart.setTheory({ ...fixture.theory, shapes: [], lecture_drawing: { strokes: [] } }, false);
    assert.equal(chart.waveProjectionAsOf(), "2025-01-09");
    assert.equal(
        chart.autoCombinedAObservations.some(
            ({ originTime, cTime }) => originTime === "2024-02-08" && cTime === "2025-01-03",
        ),
        false,
    );
    assert.ok(
        chart.autoCombinedAObservations.every(
            ({ available_at, end }) => available_at <= chart.data.asof && end <= chart.data.asof,
        ),
    );
    assert.ok(
        chart.autoCombinedAObservations
            .flatMap(({ strengthObservations }) => strengthObservations)
            .every(({ time }) => time <= chart.data.asof),
    );
    chart.updateWaveProjectionHover("2024-12-12");
    assert.equal(chart.hoveredWaveProjection.raw.nTime, "2024-02-23");
    assert.equal(chart.waveEndpointOverlay.points.length, 3);
    assert.deepEqual(
        rendered.guides.map(({ targetState }) => targetState),
        ["已触及", "已触及", "待达成"],
    );
});

test("combined A connects confirmed anchors independently from C targets and removes lines when hidden or out of view", () => {
    const { chart, rendered } = withObservation();
    const series = chart.combinedAWaveLines[0];
    assert.ok(series);
    assert.equal(series.options.title, "组合 A 浪");
    assert.equal(series.options.lineStyle, 0);
    assert.equal(series.options.autoscaleInfoProvider(), null);
    assert.deepEqual(series.points, [
        { time: observation.originTime, value: observation.origin },
        { time: observation.cTime, value: observation.cHigh },
    ]);
    const originalRange = { ...rendered.range };
    chart.setAnnotationOptions({ levels: false });
    assert.equal(chart.combinedAWaveLines[0], series);
    assert.ok(rendered.markers.some(({ id, text }) => id === observation.id + ":high" && text === "组合 A 顶"));
    chart.setAnnotationOptions({ tertiaryAbc: false });
    assert.deepEqual(chart.combinedAWaveLines, []);
    assert.ok(rendered.removed.includes(series));
    assert.ok(rendered.markers.every(({ id }) => !id.startsWith(observation.id)));
    chart.setAnnotationOptions({ tertiaryAbc: true });
    assert.equal(chart.combinedAWaveLines.length, 1);
    rendered.range = { from: 4, to: 5 };
    chart.refreshMarkers();
    assert.deepEqual(chart.combinedAWaveLines, []);
    rendered.range = originalRange;
    chart.refreshMarkers();
    assert.equal(chart.combinedAWaveLines.length, 1);
    assert.deepEqual(rendered.range, originalRange);
});

test("future confirmation and invalid anchors cannot draw a combined A connection", () => {
    for (const changes of [
        { available_at: "2025-02-05" },
        { origin: NaN },
        { cHigh: Infinity },
        { originTime: observation.cTime },
    ]) {
        const { chart } = withObservation({ ...observation, ...changes });
        assert.deepEqual(chart.combinedAWaveLines, []);
    }
});
