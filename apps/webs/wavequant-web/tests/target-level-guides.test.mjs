import assert from "node:assert/strict";
import test from "node:test";

import { TargetGuideOverlay, targetLevelGuide } from "../public/target-level-guides.js";

const item = { time: "2026-05-18", signal_time: "2026-05-18" };
const level = { name: "C 浪目标 1×A", stage: "c_equal", price: 6, anchor_at: "2026-05-15", available_at: "2026-05-18" };
const bars = [
    { time: "2026-05-15", high: 6.5, close: 5.5 },
    { time: "2026-05-18", high: 6.2, close: 5.9 },
    { time: "2026-05-19", high: 6, close: 5.8 },
    { time: "2026-05-20", high: 6.1, close: 5.9 },
    { time: "2026-05-21", high: 6.5, close: 6.2 },
];

test("C target stops at its first strict break after confirmation, starting above B", () => {
    assert.deepEqual(targetLevelGuide(item, level, bars, "2026-05-21"), {
        start: "2026-05-15",
        end: "2026-05-20",
        price: 6,
        name: level.name,
    });
});

test("entry shadow, equal high and future bars cannot extend a target during replay", () => {
    for (const asof of ["2026-05-18", "2026-05-19"]) assert.equal(targetLevelGuide(item, level, bars, asof).end, null);
    assert.equal(targetLevelGuide(item, level, bars, "2026-05-15"), null);
    assert.equal(targetLevelGuide(item, { ...level, anchor_at: "2026-05-14" }, bars), null);
    assert.equal(targetLevelGuide(item, { ...level, price: NaN }, bars), null);
    assert.equal(targetLevelGuide(item, { ...level, stage: undefined }, bars), null);
});

test("a close beyond a target at confirmation can end the line on that same session", () => {
    const history = bars.map((bar) => (bar.time === item.time ? { ...bar, close: 6.1 } : bar));
    assert.equal(targetLevelGuide(item, level, history, item.time).end, item.time);
});

test("unbroken targets remain short dashed segments on a one-candle chart and clear on deselection", () => {
    const overlay = new TargetGuideOverlay();
    let updates = 0;
    overlay.attached({
        chart: { timeScale: () => ({ timeToCoordinate: () => 100 }) },
        series: { priceToCoordinate: () => 80 },
        requestUpdate: () => updates++,
    });
    const guide = { start: "2026-05-18", end: null, name: "五顶（预估）", price: 10.45, color: "#a29ce0" };
    overlay.setGuides([guide]);
    const operations = [];
    const context = {
        save() {},
        restore() {},
        fillRect() {},
        beginPath() {},
        stroke() {},
        setLineDash(dash) {
            operations.push(["dash", dash]);
        },
        moveTo(x, y) {
            operations.push(["start", x, y]);
        },
        lineTo(x, y) {
            operations.push(["end", x, y]);
        },
        measureText() {
            return { width: 140 };
        },
        fillText(text, x, y) {
            operations.push(["label", text, x, y]);
        },
    };
    overlay.draw({
        useMediaCoordinateSpace(callback) {
            callback({ context, mediaSize: { width: 300, height: 200 } });
        },
    });
    assert.deepEqual(operations.slice(0, 3), [
        ["dash", [4, 3]],
        ["start", 82, 80],
        ["end", 118, 80],
    ]);
    assert.match(operations.find(([kind]) => kind === "label")[1], /预估.*10\.4500.*未突破/);
    overlay.setGuides([{ ...guide, end: guide.start }]);
    overlay.draw({
        useMediaCoordinateSpace(callback) {
            callback({ context, mediaSize: { width: 300, height: 200 } });
        },
    });
    assert.match(operations.at(-1)[1], /已突破/);
    overlay.setGuides([]);
    assert.equal(overlay.projected.length, 0);
    assert.equal(updates, 3);
});

test("nearby C targets keep their actual price lines while their labels remain readable", () => {
    const overlay = new TargetGuideOverlay();
    overlay.attached({
        chart: { timeScale: () => ({ timeToCoordinate: () => 100 }) },
        series: { priceToCoordinate: (price) => (price === 5.08 ? 80 : 86) },
        requestUpdate() {},
    });
    overlay.setGuides([
        { start: item.time, end: null, name: "C 浪目标 1×A", price: 5.08 },
        { start: item.time, end: null, name: "C 浪目标 0.618×A", price: 4.8126 },
    ]);
    const prices = [],
        labels = [];
    let dashed = false;
    const context = {
        save() {},
        restore() {},
        setLineDash(dash) {
            dashed = dash.length > 0;
        },
        fillRect() {},
        beginPath() {},
        lineTo() {},
        stroke() {},
        moveTo(_x, y) {
            if (dashed) prices.push(y);
        },
        measureText() {
            return { width: 150 };
        },
        fillText(text, _x, y) {
            labels.push({ text, y });
        },
    };
    overlay.draw({
        useMediaCoordinateSpace(callback) {
            callback({ context, mediaSize: { width: 300, height: 200 } });
        },
    });
    assert.deepEqual(prices, [80, 86]);
    assert.ok(labels[1].y - labels[0].y >= 12);
    assert.match(labels[0].text, /C 1×A 5\.0800/);
    assert.match(labels[1].text, /C 0\.618×A 4\.8126/);
});

function renderGuides(guides, { width = 600, height = 200, x = 400, endX = 520, y = () => 80 } = {}) {
    const overlay = new TargetGuideOverlay();
    overlay.attached({
        chart: {
            timeScale: () => ({ timeToCoordinate: (time) => (time === item.time ? x : endX) }),
            options: () => ({ layout: { background: { color: "#fff" } } }),
        },
        series: { priceToCoordinate: y },
        requestUpdate() {},
    });
    overlay.setGuides(guides);
    const labels = [],
        segments = [];
    let dash = [],
        start;
    const context = {
        save() {},
        restore() {},
        beginPath() {},
        fillRect() {},
        setLineDash(value) {
            dash = value;
        },
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            segments.push({ start, end: [x, y], dash });
        },
        stroke() {},
        measureText(text) {
            return { width: text.length * 8 };
        },
        fillText(text, x, y, maxWidth) {
            labels.push({ text, x, y, width: maxWidth });
        },
    };
    overlay.draw({
        useMediaCoordinateSpace(callback) {
            callback({ context, mediaSize: { width, height } });
        },
    });
    return { labels, segments };
}

test("reached and pending target labels sit to the left of their line starts without redrawing long lines", () => {
    const { labels, segments } = renderGuides(
        [
            { start: item.time, end: "2026-05-20", name: "C 浪目标 1×A", price: 5.08 },
            { start: item.time, end: null, name: "五顶（预估）", price: 10.45 },
        ],
        { y: (price) => (price === 5.08 ? 100 : 40) },
    );
    assert.equal(labels.length, 2);
    assert.ok(labels[0].x + labels[0].width <= 374);
    assert.ok(labels[1].x + labels[1].width <= 392);
    assert.match(labels[0].text, /预估.*未突破/);
    assert.match(labels[1].text, /5\.0800.*已突破/);
    assert.deepEqual(
        segments.filter((segment) => segment.dash.length).map(({ start, end }) => [start, end]),
        [
            [
                [382, 40],
                [418, 40],
            ],
        ],
    );
});

test("six close targets stay separated at top and bottom boundaries with leaders at their actual prices", () => {
    const guides = Array.from({ length: 6 }, (_, index) => ({
        start: item.time,
        end: null,
        name: `目标 ${index}`,
        price: index,
    }));
    for (const firstY of [6, 148]) {
        const { labels, segments } = renderGuides(guides, {
            width: 320,
            height: 180,
            x: 24,
            y: (price) => firstY + price,
        });
        assert.equal(labels.length, 6);
        assert.ok(
            labels.every((label) => label.x >= 4 && label.x + label.width <= 316 && label.y >= 16 && label.y <= 176),
        );
        assert.ok(labels.slice(1).every((label, index) => label.y - labels[index].y >= 18));
        assert.deepEqual(
            segments.filter((segment) => !segment.dash.length).map(({ end }) => end[1]),
            guides.map(({ price }) => firstY + price),
        );
    }
});

test("narrow panes retain estimated name and price, and panning preserves labels only for visible segments", () => {
    const guide = { start: item.time, end: null, name: "五顶（启动正 N · 预估叠箱）", price: 10.45 };
    const { labels } = renderGuides([guide], { width: 160, x: 10 });
    assert.equal(labels[0].text, "五顶（预估） 10.4500");
    assert.ok(labels[0].x + labels[0].width <= 156);
    assert.equal(renderGuides([guide], { x: -20 }).labels.length, 0);
    assert.equal(renderGuides([{ ...guide, end: "2026-05-20" }], { x: -20, endX: 200 }).labels.length, 1);
    assert.equal(renderGuides([{ ...guide, end: "2026-05-20" }], { x: -40, endX: -10 }).labels.length, 0);
    assert.equal(renderGuides([{ ...guide, end: "2026-05-20" }], { x: -40, endX: null }).labels.length, 0);
    assert.equal(renderGuides([guide], { x: 800 }).labels.length, 0);
});
