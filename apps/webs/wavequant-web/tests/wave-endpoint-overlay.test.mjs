import assert from "node:assert/strict";
import test from "node:test";

import { WaveEndpointOverlay, selectedWaveEndpoints } from "../public/wave-endpoint-overlay.js";

const bars = ["2021-04-20", "2021-05-07", "2021-05-21"].map((time) => ({ time }));
const buy = {
    kind: "fill",
    side: "BUY",
    signal_time: "2021-05-21",
    decision_evidence: [
        {
            wave_entry_path: "two_t_held_defense_gap_attack",
            wave_a_high_date: "2021-04-20",
            wave_a_high: 8.5655,
            wave_b_low_date: "2021-05-07",
            wave_b_low: 5.9814,
            wave_breakout_close: 6.7021,
        },
    ],
};

test("selected executed wave entry marks known A, B and C confirmation prices", () => {
    assert.deepEqual(selectedWaveEndpoints(buy, bars), [
        { label: "A", time: "2021-04-20", price: 8.5655, position: "above" },
        { label: "B", time: "2021-05-07", price: 5.9814, position: "below" },
        { label: "C 确认", time: "2021-05-21", price: 6.7021, position: "above" },
    ]);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, side: "SELL" }, bars), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, kind: "signal" }, bars), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, decision_evidence: [] }, bars), []);
    assert.deepEqual(selectedWaveEndpoints(buy, bars.slice(0, 2)), []);
    assert.deepEqual(selectedWaveEndpoints({ ...buy, signal_time: "2021-04-20" }, bars), []);
});

test("wave endpoint overlay projects and draws the three selected labels, then clears them", () => {
    const container = { dataset: {} };
    const overlay = new WaveEndpointOverlay(container);
    let updates = 0;
    overlay.attached({
        chart: {
            timeScale: () => ({
                timeToCoordinate: (time) => ({ "2021-04-20": 45, "2021-05-07": 100, "2021-05-21": 155 })[time],
            }),
        },
        series: { priceToCoordinate: (price) => 180 - price * 10 },
        requestUpdate: () => updates++,
    });
    overlay.setPoints(selectedWaveEndpoints(buy, bars));
    assert.equal(container.dataset.waveEndpointCount, "3");
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
        fillText(label) {
            labels.push(label);
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 220, height: 180 } }) });
    assert.deepEqual(labels, ["A 8.5655", "B 5.9814", "C 确认 6.7021"]);
    overlay.setPoints([]);
    assert.equal(container.dataset.waveEndpointCount, "0");
    assert.equal(updates, 2);
});
