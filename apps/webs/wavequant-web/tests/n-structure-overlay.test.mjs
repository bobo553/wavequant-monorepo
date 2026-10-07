import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { NStructureOverlay, nStructureBounds } from "../public/n-structure-overlay.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xiangyang_2026_bottom_n_guides.json", import.meta.url)));
const root = buildAnnotations(fixture.view, fixture.theory).find((item) => item.raw?.target_eligible === true);

test("the N box uses its completion high and original low instead of the mother high or squeeze defense", () => {
    assert.deepEqual(nStructureBounds(root, "2026-07-23"), {
        start: "2026-07-21",
        end: "2026-07-23",
        top: 7.9,
        bottom: 7.32,
    });
    assert.notEqual(root.raw.defense, 7.32);
    assert.equal(nStructureBounds(root, "2026-07-22"), null);
    for (const raw of [
        { ...root.raw, target_eligible: false },
        { ...root.raw, direction: "down" },
        { ...root.raw, available_at: "2026-07-24" },
        { ...root.raw, shape: [] },
        { ...root.raw, shape: null },
    ])
        assert.equal(nStructureBounds({ ...root, raw }, "2026-07-23"), null);
});

function harness() {
    const container = { dataset: {} },
        overlay = new NStructureOverlay(container);
    let zoom = 1,
        pan = 0;
    overlay.attached({
        chart: {
            timeScale: () => ({
                timeToCoordinate: (date) => ({ "2026-07-21": 100, "2026-07-23": 140 })[date] * zoom - pan,
            }),
        },
        series: { priceToCoordinate: (price) => 200 - price * 10 },
        requestUpdate() {},
    });
    const segments = [],
        labels = [];
    let from;
    const context = {
        save() {},
        restore() {},
        setLineDash() {},
        beginPath() {},
        stroke() {},
        moveTo(x, y) {
            from = [x, y];
        },
        lineTo(x, y) {
            segments.push([from, [x, y]]);
        },
        measureText(text) {
            return { width: text.length * 6 };
        },
        fillText(text, x, y) {
            labels.push({ text, x, y });
        },
    };
    return {
        overlay,
        container,
        segments,
        labels,
        draw() {
            overlay.draw({
                useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 600, height: 240 } }),
            });
        },
        viewport(nextZoom, nextPan) {
            zoom = nextZoom;
            pan = nextPan;
            overlay.updateAllViews();
        },
    };
}

test("two solid horizontal boundaries stay at their real prices and follow candle coordinates on zoom and pan", () => {
    const h = harness();
    h.overlay.setStructure(root, "2026-07-23");
    h.draw();
    assert.deepEqual(h.segments, [
        [
            [100, 121],
            [140, 121],
        ],
        [
            [100, 126.8],
            [140, 126.8],
        ],
    ]);
    assert.deepEqual(
        h.labels.map(({ text }) => text),
        ["正 N 顶 7.9000", "正 N 底 7.3200"],
    );
    h.viewport(2, 40);
    h.draw();
    assert.deepEqual(h.segments.slice(2), [
        [
            [160, 121],
            [240, 121],
        ],
        [
            [160, 126.8],
            [240, 126.8],
        ],
    ]);
    assert.equal(JSON.parse(h.container.dataset.nStructureBounds).top, 7.9);
    h.viewport(1, 700);
    const count = h.segments.length;
    h.draw();
    assert.equal(h.segments.length, count);
    h.overlay.setStructure(null);
    h.viewport(1, 0);
    h.draw();
    assert.equal(h.overlay.projected, null);
    assert.equal(h.container.dataset.nStructureBounds, "null");
    assert.equal(h.segments.length, count);
});
