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

test("C target stops at its first touch after confirmation, starting above B", () => {
    assert.deepEqual(targetLevelGuide(item, level, bars, "2026-05-21"), {
        start: "2026-05-15",
        end: "2026-05-19",
        price: 6,
        name: level.name,
        targetState: "已触及",
        firstTouchedAt: "2026-05-19",
    });
});

test("entry shadows and future bars cannot extend a target during replay", () => {
    assert.equal(targetLevelGuide(item, level, bars, "2026-05-18").end, null);
    assert.equal(targetLevelGuide(item, level, bars, "2026-05-19").end, "2026-05-19");
    assert.equal(targetLevelGuide(item, level, bars, "2026-05-15"), null);
    assert.equal(targetLevelGuide(item, { ...level, anchor_at: "2026-05-14" }, bars), null);
    assert.equal(targetLevelGuide(item, { ...level, price: NaN }, bars), null);
    assert.equal(targetLevelGuide(item, { ...level, stage: undefined }, bars), null);
});

test("a close beyond a target at confirmation can end the line on that same session", () => {
    const history = bars.map((bar) => (bar.time === item.time ? { ...bar, close: 6.1 } : bar));
    assert.equal(targetLevelGuide(item, level, history, item.time).end, item.time);
});

test("C touches use equality while existing N targets still require a strict break", () => {
    const oneP = { ...level, stage: "one_p", name: "一饱" };
    assert.equal(targetLevelGuide(item, oneP, bars, "2026-05-19").end, null);
    assert.equal(targetLevelGuide(item, oneP, bars, "2026-05-20").end, "2026-05-20");
    const closed = bars.map((bar) => (bar.time === item.time ? { ...bar, close: level.price } : bar));
    assert.equal(targetLevelGuide(item, level, closed, item.time).targetState, "已触及");
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

function renderGuides(
    guides,
    { width = 600, height = 200, x = 400, endX = 520, y = () => 80, background = "#fff" } = {},
) {
    const overlay = new TargetGuideOverlay();
    overlay.attached({
        chart: {
            timeScale: () => ({ timeToCoordinate: (time) => (time === item.time ? x : endX) }),
            options: () => ({ layout: { background: { color: background } } }),
        },
        series: { priceToCoordinate: y },
        requestUpdate() {},
    });
    overlay.setGuides(guides);
    const labels = [],
        segments = [],
        backgrounds = [];
    let dash = [],
        start;
    const context = {
        save() {},
        restore() {},
        beginPath() {},
        fillRect(...bounds) {
            backgrounds.push({ color: this.fillStyle, bounds });
        },
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
    return { labels, segments, backgrounds };
}

test("fixed N labels center over the N candle and stay on the edge without false lines when it is offscreen", () => {
    const guide = {
        start: item.time,
        end: null,
        price: 8.48,
        name: "一饱",
        stage: "one_p",
        color: "#a29ce0",
        fixedAnchor: true,
        labelPosition: "center",
    };
    const centered = renderGuides([guide], { x: 300 });
    assert.equal(centered.labels[0].x + centered.labels[0].width / 2, 300);
    assert.equal(centered.segments.length, 1);
    for (const x of [-200, 800]) {
        const edge = renderGuides([guide], { x });
        assert.equal(edge.labels.length, 1);
        assert.ok(edge.labels[0].x >= 4 && edge.labels[0].x + edge.labels[0].width <= 596);
        assert.equal(edge.segments.length, 0);
    }
});

test("fixed C labels center above B and disappear when B leaves the pane without relocating", () => {
    const guide = {
        start: item.time,
        end: "2026-05-20",
        price: 11.4,
        name: "C 浪目标 1×A",
        stage: "c_equal",
        color: "#a29ce0",
        fixedAnchor: true,
        labelPosition: "center",
        hideWhenAnchorOffscreen: true,
    };
    for (const x of [240, 300, 400]) {
        const centered = renderGuides([guide], { x });
        assert.equal(centered.labels.length, 1);
        assert.equal(centered.labels[0].x + centered.labels[0].width / 2, x);
    }
    for (const x of [-200, -0.1, 600.1, 800]) {
        const hidden = renderGuides([guide], { x, endX: 400 });
        assert.equal(hidden.labels.length, 0);
        assert.equal(hidden.segments.length, 0);
    }
});

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

test("distant offscreen targets keep separate edge labels without drawing false price lines", () => {
    const guides = [
        { start: item.time, end: null, stage: "five_top", name: "五顶（预估）", price: 10.45 },
        { start: item.time, end: null, stage: "ten_full", name: "十满（预估）", price: 16.75 },
    ];
    const { labels, segments } = renderGuides(guides, { y: (price) => -price * 100 });
    assert.equal(labels.length, 2);
    assert.ok(labels.every(({ text, y }) => /↑.*预估.*图外/.test(text) && y >= 16));
    assert.ok(labels[1].y - labels[0].y >= 18);
    assert.equal(segments.length, 0);
    assert.match(labels.map(({ text }) => text).join(" "), /10\.4500.*16\.7500/);
    const below = renderGuides([guides[0]], { width: 160, x: 20, y: () => 240 });
    assert.match(below.labels[0].text, /↓.*五顶.*10\.4500/);
    assert.ok(below.labels[0].y <= 196);
    assert.equal(below.segments.length, 0);
    assert.equal(renderGuides(guides, { x: -30, y: () => -100 }).labels.length, 0);
    const onscreen = renderGuides([guides[0]], { y: () => 80 });
    assert.match(onscreen.labels[0].text, /10\.4500.*未突破/);
    assert.ok(onscreen.segments.some(({ dash }) => dash.length));
});

test("a C extension outside the price axis still shows its frozen state without a false line", () => {
    const { labels, segments } = renderGuides(
        [
            {
                start: item.time,
                end: null,
                stage: "c_1618",
                name: "C 浪目标 1.618×A",
                price: 10.54188,
                targetState: "本段结束未达成",
            },
        ],
        { width: 800, y: () => -100 },
    );
    assert.equal(labels.length, 1);
    assert.match(labels[0].text, /C 1\.618×A.*10\.5419.*本段结束未达成.*图外/);
    assert.equal(segments.length, 0);
});

test("all three C targets retain distinct edge labels when the hovered A candle has a lower price axis", () => {
    const guides = [
        { stage: "c_0618", name: "C 浪目标 0.618×A", price: 6.877680927981544, targetState: "已触及" },
        { stage: "c_equal", name: "C 浪目标 1×A（等浪）", price: 8.274794803857262, targetState: "已触及" },
        { stage: "c_1618", name: "C 浪目标 1.618×A", price: 10.535047095195464, targetState: "本段结束未达成" },
    ].map((guide) => ({ ...guide, start: item.time, end: null }));
    const { labels, segments } = renderGuides(guides, { width: 1000, y: () => -100 });
    assert.equal(labels.length, 3);
    assert.ok(labels.every(({ text }) => /图外/.test(text)));
    assert.ok(labels.some(({ text }) => /等浪.*已触及/.test(text)));
    assert.ok(labels.some(({ text }) => /1\.618.*本段结束未达成/.test(text)));
    assert.ok(labels.slice(1).every((label, index) => label.y - labels[index].y >= 18));
    assert.equal(segments.length, 0);
});

test("touched and pending C references both use dashed horizontal segments", () => {
    const guide = { start: item.time, end: null, stage: "c_equal", name: "C 浪目标 1×A（等浪）", price: 8.28 };
    const touched = renderGuides([{ ...guide, targetState: "已触及" }]);
    const pending = renderGuides([{ ...guide, targetState: "待达成" }]);
    for (const result of [touched, pending]) {
        const horizontal = result.segments.filter(({ start, end }) => start[1] === end[1]);
        assert.equal(horizontal.length, 1);
        assert.deepEqual(horizontal[0].dash, [4, 3]);
    }
    assert.match(touched.labels[0].text, /已触及/);
    assert.match(pending.labels[0].text, /待达成/);
});

test("C, N and combined retracement labels leave the chart background transparent in both themes", () => {
    const guides = [
        { stage: "c_equal", name: "C 浪目标 1×A（等浪）", price: 11.4, targetState: "已触及" },
        { stage: "one_p", name: "一饱", price: 11.15, targetState: "已满足" },
        { stage: "combined_a_half", name: "组合 A 50%", price: 10.51, targetState: "半幅失守" },
    ].map((guide) => ({ ...guide, start: item.time, end: null, color: "#a29ce0" }));
    for (const background of ["#fff", "#101722"]) {
        const result = renderGuides(guides, { background, y: (price) => price * 10 });
        assert.equal(result.labels.length, 3);
        assert.deepEqual(result.backgrounds, []);
        assert.ok(result.labels.some(({ text }) => /等浪.*已触及/.test(text)));
        assert.ok(result.labels.some(({ text }) => /一饱.*已满足/.test(text)));
        assert.ok(result.labels.some(({ text }) => /50%.*半幅失守/.test(text)));
    }
});

test("combined retracement labels render above their lines with independent states and clear offscreen", () => {
    const guides = [
        { name: "组合 A 50%", price: 7.5, targetState: "半幅失守" },
        { name: "组合 A 2/3", price: 6, targetState: "低点突破，收盘没有跌破" },
    ].map((guide) => ({ ...guide, start: item.time, end: "2026-05-21", labelPosition: "line" }));
    const { labels, segments } = renderGuides(guides, { width: 800, x: 100, y: (price) => (price === 7.5 ? 80 : 130) });
    assert.equal(labels.length, 2);
    assert.ok(labels.every((label) => label.x === 108));
    assert.deepEqual(
        labels.map((label) => label.y),
        [77, 127],
    );
    assert.match(labels[0].text, /50%.*7\.5000.*半幅失守/);
    assert.match(labels[1].text, /2\/3.*6\.0000.*低点突破，收盘没有跌破/);
    assert.ok(
        segments.every((segment) => segment.start[1] !== segment.end[1]),
        "SDK owns the horizontal dashed lines",
    );
    assert.deepEqual(renderGuides(guides, { x: -300, endX: -100 }).labels, []);
    assert.deepEqual(renderGuides(guides, { y: () => -50 }).labels, []);
});
