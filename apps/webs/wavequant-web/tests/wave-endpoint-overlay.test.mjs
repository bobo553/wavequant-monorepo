import assert from "node:assert/strict";
import test from "node:test";

import {
    WaveEndpointOverlay,
    projectionWaveEndpoints,
    selectedWaveEndpoints,
} from "../public/wave-endpoint-overlay.js";

const bars = ["2021-04-01", "2021-04-20", "2021-05-07", "2021-05-21"].map((time) => ({ time }));
const buy = {
    kind: "fill",
    side: "BUY",
    signal_time: "2021-05-21",
    decision_evidence: [
        {
            wave_entry_path: "two_t_held_defense_gap_attack",
            wave_a_origin_date: "2021-04-01",
            wave_a_origin: 4.5123,
            wave_a_high_date: "2021-04-20",
            wave_a_high: 8.5655,
            wave_b_low_date: "2021-05-07",
            wave_b_low: 5.9814,
            wave_breakout_close: 6.7021,
        },
    ],
};

const projection = {
    originTime: "2021-04-01",
    origin: 4.5123,
    aTime: "2021-04-20",
    aHigh: 8.5655,
    aKnownAt: "2021-04-21",
    bTime: "2021-05-07",
    bLow: 5.9814,
    bKnownAt: "2021-05-10",
    confirmedAt: "2021-05-10",
    cTime: "2021-05-21",
    cHigh: 9.5,
    cKnownAt: "2021-05-24",
};

test("selected executed wave entry marks A origin, A high, B low and C confirmation", () => {
    assert.deepEqual(selectedWaveEndpoints(buy, bars), [
        { label: "A 起点", time: "2021-04-01", price: 4.5123, position: "below" },
        { label: "A 高", time: "2021-04-20", price: 8.5655, position: "above" },
        { label: "B", time: "2021-05-07", price: 5.9814, position: "below" },
        { label: "C 确认", time: "2021-05-21", price: 6.7021, position: "above" },
    ]);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, side: "SELL" }, bars), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, kind: "signal" }, bars), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, decision_evidence: [] }, bars), []);
    assert.deepEqual(selectedWaveEndpoints(buy, bars.slice(0, 3)), []);
    assert.deepEqual(selectedWaveEndpoints(buy, bars.slice(1)), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, signal_time: "2021-04-20" }, bars), []);
    assert.deepEqual(
        selectedWaveEndpoints(
            { ...buy, decision_evidence: [{ ...buy.decision_evidence[0], wave_a_origin_date: "2021-05-07" }] },
            bars,
        ),
        [],
    );
    assert.deepEqual(
        selectedWaveEndpoints(
            { ...buy, decision_evidence: [{ ...buy.decision_evidence[0], wave_a_origin_date: undefined }] },
            bars,
        ),
        [
            { label: "A 高", time: "2021-04-20", price: 8.5655, position: "above" },
            { label: "B", time: "2021-05-07", price: 5.9814, position: "below" },
            { label: "C 确认", time: "2021-05-21", price: 6.7021, position: "above" },
        ],
    );
});

test("ABC observations reuse A/B endpoint geometry and reveal the completed C only when known", () => {
    const abcPoints = [
        { label: "A 起点", time: "2021-04-01", price: 4.5123, position: "below" },
        { label: "A 终点", time: "2021-04-20", price: 8.5655, position: "above" },
        { label: "B", time: "2021-05-07", price: 5.9814, position: "below" },
    ];
    assert.deepEqual(projectionWaveEndpoints(projection, bars, "2021-05-09"), []);
    assert.deepEqual(projectionWaveEndpoints(projection, bars, "2021-05-10"), abcPoints);
    assert.deepEqual(projectionWaveEndpoints(projection, bars, "2021-05-23"), abcPoints);
    assert.deepEqual(projectionWaveEndpoints(projection, bars, "2021-05-24"), [
        ...abcPoints,
        { label: "C 终点", time: "2021-05-21", price: 9.5, position: "above" },
    ]);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, cTime: undefined }, bars, "2021-05-24"), abcPoints);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, cKnownAt: undefined }, bars, "2021-05-24"), abcPoints);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, cKnownAt: "2021-05-20" }, bars, "2021-05-24"), abcPoints);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, aIsValid: false }, bars, "2021-05-24"), abcPoints);
    assert.deepEqual(selectedWaveEndpoints({ kind: "wave-projection", raw: projection }, bars), []);
});

test("observed endpoint anchors must exist on their actual candles with finite prices and ordered dates", () => {
    assert.deepEqual(projectionWaveEndpoints(projection, bars.slice(1), "2021-05-24"), []);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, aHigh: NaN }, bars, "2021-05-24"), []);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, bLow: undefined }, bars, "2021-05-24"), []);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, originTime: "2021-04-20" }, bars, "2021-05-24"), []);
    assert.deepEqual(projectionWaveEndpoints({ ...projection, bTime: "2021-04-20" }, bars, "2021-05-24"), []);
});

test("wave endpoint overlay projects and draws the four selected labels, then clears them", () => {
    const container = { dataset: {} };
    const overlay = new WaveEndpointOverlay(container);
    let updates = 0;
    overlay.attached({
        chart: {
            timeScale: () => ({
                timeToCoordinate: (time) =>
                    ({ "2021-04-01": 25, "2021-04-20": 65, "2021-05-07": 120, "2021-05-21": 175 })[time],
            }),
        },
        series: { priceToCoordinate: (price) => 180 - price * 10 },
        requestUpdate: () => updates++,
    });
    overlay.setPoints(selectedWaveEndpoints(buy, bars));
    assert.equal(container.dataset.waveEndpointCount, "4");
    assert.equal(overlay.zOrder(), "top");
    const labels = [];
    const context = {
        save() {},
        restore() {},
        beginPath() {},
        arc() {},
        fill() {},
        moveTo() {},
        lineTo() {},
        stroke() {},
        strokeText() {},
        measureText(text) {
            return { width: text.length * 6 };
        },
        fillText(label) {
            labels.push(label);
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 220, height: 180 } }) });
    assert.deepEqual(labels, ["A 起点 4.5123", "A 高 8.5655", "B 5.9814", "C 确认 6.7021"]);
    overlay.setPoints([]);
    assert.equal(container.dataset.waveEndpointCount, "0");
    assert.equal(updates, 2);
});

test("visible boundary dots keep their exact anchors while endpoint text moves inside the pane", () => {
    const overlay = new WaveEndpointOverlay({ dataset: {} });
    overlay.attached({
        chart: {
            timeScale: () => ({
                timeToCoordinate: (time) =>
                    ({ "2021-04-01": 0, "2021-04-20": 100, "2021-05-07": 220, "2021-05-21": 250 })[time],
            }),
        },
        series: { priceToCoordinate: () => 100 },
        requestUpdate() {},
    });
    overlay.setPoints(projectionWaveEndpoints(projection, bars, "2021-05-24"));
    const labels = [],
        dots = [];
    const context = Object.fromEntries(
        ["save", "restore", "beginPath", "fill", "moveTo", "lineTo", "stroke", "strokeText"].map((name) => [
            name,
            () => {},
        ]),
    );
    context.arc = (x) => dots.push(x);
    context.measureText = (text) => ({ width: text.length * 6 });
    context.fillText = (text, x) => labels.push({ text, x });
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 220, height: 180 } }) });
    assert.deepEqual(dots, [0, 100, 220]);
    assert.deepEqual(
        labels.map(({ text }) => text),
        ["A 起点 4.5123", "A 终点 8.5655", "B 5.9814"],
    );
    assert.ok(labels[0].x > 0);
    assert.ok(labels.at(-1).x < 220);
    assert.equal(overlay.projected.at(-1).x, 250);
});
