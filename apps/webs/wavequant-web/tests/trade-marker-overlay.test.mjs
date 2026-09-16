import assert from "node:assert/strict";
import test from "node:test";

import { TradeMarkerOverlay } from "../public/trade-marker-overlay.js";

test("only executed BUY and SELL produce standalone top-layer B/S letters at price coordinates", () => {
    const container = { dataset: {} };
    const overlay = new TradeMarkerOverlay(container);
    let updates = 0;
    overlay.attached({
        chart: { timeScale: () => ({ timeToCoordinate: (time) => ({ buy: 40, sell: 80 })[time] ?? null }) },
        series: { priceToCoordinate: (price) => 160 - price },
        requestUpdate: () => updates++,
    });
    overlay.setMarkers([
        { id: "buy-fill", time: "buy", side: "BUY", price: 80 },
        { id: "sell-fill", time: "sell", side: "SELL", price: 70 },
        { id: "signal", time: "buy", side: "LONG", price: 80 },
    ]);

    assert.equal(overlay.zOrder(), "top");
    assert.equal(container.dataset.tradeLabelCount, "2");
    assert.equal(updates, 1);
    assert.deepEqual(
        overlay.projected.map(({ id, x, priceY, y }) => ({ id, x, priceY, y })),
        [
            { id: "buy-fill", x: 40, priceY: 80, y: 99 },
            { id: "sell-fill", x: 80, priceY: 90, y: 71 },
        ],
    );

    const letters = [];
    const halos = [];
    let circles = 0;
    const context = {
        save() {},
        restore() {},
        beginPath() {},
        moveTo() {},
        lineTo() {},
        stroke() {},
        arc() {
            circles++;
        },
        fill() {},
        strokeText(letter) {
            halos.push(letter);
        },
        fillText(letter) {
            letters.push({ letter, color: this.fillStyle, font: this.font });
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 200, height: 160 } }) });
    assert.equal(circles, 0);
    assert.deepEqual(halos, ["B", "S"]);
    assert.deepEqual(letters, [
        { letter: "B", color: "#ef7180", font: "800 18px ui-sans-serif, system-ui, sans-serif" },
        { letter: "S", color: "#3fba97", font: "800 18px ui-sans-serif, system-ui, sans-serif" },
    ]);
    assert.equal(overlay.hitTest(40, 99).externalId, "buy-fill");
    assert.equal(overlay.hitTest(80, 71).externalId, "sell-fill");
    assert.equal(overlay.hitTest(5, 5), null);
});
