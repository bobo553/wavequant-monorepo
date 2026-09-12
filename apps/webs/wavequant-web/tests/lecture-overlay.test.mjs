import assert from "node:assert/strict";
import test from "node:test";

import { LectureOverlay, projectStroke, reversalConnections, secondaryConnections } from "../public/lecture-overlay.js";

test("level-two links use confirmed level-one extremes, not raw or display-only points", () => {
    const p = (i, kind, value, known = i) => ({
        index: i,
        ordinal: 0,
        time: `2026-01-${String(i).padStart(2, "0")}`,
        available_at: `2026-01-${String(known).padStart(2, "0")}`,
        kind,
        value,
        state: "reversal",
    });
    const a = p(1, "L", 10, 2),
        b = p(8, "L", 9, 10),
        c = p(12, "H", 22, 13);
    const second = [
        { id: "a", kind: "secondary", points: [a] },
        { id: "b", kind: "secondary", points: [b] },
        { id: "c", kind: "secondary", points: [c] },
    ];
    const first = [
        {
            id: "f",
            kind: "reversal",
            points: [
                p(2, "H", 15),
                p(3, "H", 20),
                p(4, "H", 20),
                p(5, "H", 99, 11),
                { ...p(6, "H", 88), state: "developing" },
            ],
        },
        { id: "raw", kind: "ordinary", points: [p(3, "H", 1000)] },
        { id: "bridge", kind: "reversal-connection", display_only: true, points: [p(4, "H", 2000)] },
    ];
    const before = JSON.stringify({ first, second }),
        links = secondaryConnections(second, first);
    assert.equal(links.length, 2);
    assert.equal(before, JSON.stringify({ first, second }));
    assert.deepEqual(
        links[0].points.map((p) => [p.index, p.kind, p.value]),
        [
            [1, "L", 10],
            [3, "H", 20],
            [8, "L", 9],
        ],
    );
    assert.equal(links[0].points[0], a);
    assert.equal(links[0].points.at(-1), links[1].points[0]);
    assert.equal(links[1].points.at(-1), c);
    assert.equal(links[0].available_at, b.available_at);
    assert.ok(
        links.every(
            (s) => s.kind === "secondary-connection" && s.trend_level === 2 && s.source_level === 1 && s.display_only,
        ),
    );
});

test("secondary high-high joins via lowest level-one low; no fabricated or unconfirmed shortcut", () => {
    const p = (index, kind, value) => ({
        index,
        ordinal: 0,
        kind,
        value,
        time: "a" + index,
        available_at: "z",
        state: "reversal",
    });
    const high = [
        { id: "a", kind: "secondary", points: [p(1, "H", 20)] },
        { id: "b", kind: "secondary", points: [p(8, "H", 21)] },
    ];
    const first = [{ id: "first", kind: "reversal", points: [p(2, "L", 15), p(3, "L", 10), p(4, "L", 10)] }];
    assert.deepEqual(
        secondaryConnections(high, first)[0].points.map((p) => p.value),
        [20, 10, 21],
    );
    assert.deepEqual(secondaryConnections(high, []), []);
    assert.deepEqual(secondaryConnections(high, [{ id: "f", kind: "reversal", points: [p(2, "L", 25)] }]), []);
    assert.deepEqual(secondaryConnections([high[0]]), []);
});

test("secondary same-bar bridge preserves level-one intrabar projection and solid purple click targets", () => {
    const points = ["L", "H", "L"].map((kind, ordinal) => ({
        index: 2,
        ordinal,
        time: "2026-01-02",
        available_at: "2026-01-03",
        kind,
        value: [10, 20, 9][ordinal],
        state: "reversal",
        projection_count: 5,
        projection_rank: ordinal + 1,
    }));
    const second = [
        { id: "a", kind: "secondary", points: [points[0]] },
        { id: "b", kind: "secondary", points: [points[2]] },
    ];
    const first = [{ id: "f", kind: "reversal", points }],
        links = secondaryConnections(second, first);
    const projected = projectStroke(
        links[0],
        () => 100,
        (v) => 200 - v * 5,
        10,
    );
    assert.deepEqual(
        projected.map((p) => [p.x, p.y]),
        projectStroke(
            first[0],
            () => 100,
            (v) => 200 - v * 5,
            10,
        ).map((p) => [p.x, p.y]),
    );
    const overlay = new LectureOverlay({ dataset: {} });
    overlay.strokes = links;
    overlay.projected = [{ stroke: links[0], points: projected }];
    const edges = [];
    let dash;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(d) {
            dash = d;
        },
        beginPath() {},
        moveTo() {},
        lineTo() {},
        stroke() {
            edges.push({ color: this.strokeStyle, width: this.lineWidth, dash: [...dash] });
        },
        arc() {
            assert.fail("no endpoint circles");
        },
        fillText() {
            assert.fail("no H/L labels");
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.equal(edges.length, 2);
    assert.ok(edges.every((e) => e.color === "#d6a3ff" && e.width === 3 && !e.dash.length));
    for (let i = 1; i < projected.length; i++) {
        const a = projected[i - 1],
            b = projected[i],
            hit = overlay.hitTest((a.x + b.x) / 2, (a.y + b.y) / 2);
        assert.ok(hit);
        const info = overlay.annotation(hit.externalId);
        assert.equal(info.raw.trend_level, 2);
        assert.equal(info.raw.source_level, 1);
        assert.equal(info.raw.scope, "display_only_connection");
        assert.match(info.description, /经一级趋势线/);
    }
});

test("level-one display links connect split paths without mutating confirmation evidence", () => {
    const point = (i, kind) => ({ index: i, ordinal: 0, time: `d${i}`, available_at: `d${i + 1}`, value: i, kind });
    const paths = [
        { id: "a", kind: "reversal", points: [point(1, "L"), point(2, "H")] },
        { id: "b", kind: "reversal", points: [point(5, "H"), point(6, "L")] },
        { id: "empty", kind: "reversal", points: [] },
        { id: "c", kind: "reversal", points: [point(8, "H")] },
    ];
    const base = [{ id: "base", points: [{ ...point(3, "L"), value: 0, state: "confirmed" }] }];
    const before = structuredClone(paths),
        baseBefore = structuredClone(base),
        links = reversalConnections(paths, base);
    assert.deepEqual(paths, before);
    assert.equal(links.length, 2);
    assert.deepEqual(base, baseBefore);
    assert.equal(links[0].points[0], paths[0].points.at(-1));
    assert.equal(links[0].points[2], paths[1].points[0]);
    assert.equal(links[1].points[1], paths[3].points[0]);
    assert.ok(links.every((s) => s.display_only && s.kind !== "reversal"));
    assert.equal(links[0].available_at, "d6");
    assert.deepEqual(
        links[0].points.map((p) => p.kind),
        ["H", "L", "H"],
    );
    assert.deepEqual(
        links[0].points.map((p) => p.value),
        [2, 0, 5],
    );
    assert.deepEqual(reversalConnections([]), []);
    assert.deepEqual(reversalConnections([paths[0], { ...paths[0], id: "overlap" }]), []);
});

test("low-low bridge uses the highest known intermediate high, with earliest equal extreme", () => {
    const p = (i, kind, value, state = "confirmed", known = i) => ({
        index: i,
        ordinal: 0,
        time: `2026-01-${String(i).padStart(2, "0")}`,
        available_at: `2026-01-${String(known).padStart(2, "0")}`,
        kind,
        value,
        state,
    });
    const a = p(1, "L", 10),
        b = p(8, "L", 9, "confirmed", 10);
    const paths = [
        { id: "a", kind: "reversal", points: [a] },
        { id: "b", kind: "reversal", points: [b] },
    ];
    const base = [
        {
            id: "base",
            points: [
                a,
                p(2, "H", 13),
                p(3, "H", 17),
                p(4, "H", 17),
                p(5, "H", 99, "developing"),
                p(6, "H", 98, "seed"),
                p(7, "H", 97, "confirmed", 11),
                b,
            ],
        },
    ];
    const result = reversalConnections(paths, base);
    assert.equal(result.length, 1);
    assert.deepEqual(
        result[0].points.map((p) => [p.index, p.kind, p.value]),
        [
            [1, "L", 10],
            [3, "H", 17],
            [8, "L", 9],
        ],
    );
    assert.equal(result[0].available_at, b.available_at);
    assert.deepEqual(reversalConnections(paths), []);
    assert.deepEqual(reversalConnections(paths, [{ id: "bad", points: [p(3, "H", 8)] }]), []);
});

test("same-bar intermediate point retains original ordinal and projected position", () => {
    const p = (ordinal, kind, value) => ({
        index: 2,
        ordinal,
        time: "2026-01-02",
        available_at: "2026-01-03",
        state: "teaching",
        kind,
        value,
    });
    const a = p(0, "L", 10),
        h = p(1, "H", 15),
        b = p(2, "L", 9);
    const base = [{ id: "base", points: [a, h, b] }];
    const source = projectStroke(
        base[0],
        () => 100,
        (v) => v,
        10,
    );
    const paths = [
        { id: "a", kind: "reversal", points: [{ ...a, projection_count: 3, projection_rank: 0 }] },
        { id: "b", kind: "reversal", points: [{ ...b, projection_count: 3, projection_rank: 2 }] },
    ];
    const link = reversalConnections(paths, base)[0];
    assert.deepEqual(
        projectStroke(
            link,
            () => 100,
            (v) => v,
            10,
        ).map((p) => [p.x, p.y]),
        source.map((p) => [p.x, p.y]),
    );
});

test("both legs of a three-point bridge are drawable and clickable; no low-low shortcut", () => {
    const points = [
        { index: 1, ordinal: 0, time: "a", kind: "L", value: 10, available_at: "z" },
        { index: 2, ordinal: 0, time: "b", kind: "H", value: 20, available_at: "z", state: "confirmed" },
        { index: 3, ordinal: 0, time: "c", kind: "L", value: 8, available_at: "z" },
    ];
    const links = reversalConnections(
        [
            { id: "a", kind: "reversal", points: [points[0]] },
            { id: "b", kind: "reversal", points: [points[2]] },
        ],
        [{ id: "base", points }],
    );
    const o = new LectureOverlay({ dataset: {} });
    o.strokes = links;
    o.chart = { timeScale: () => ({ options: () => ({ barSpacing: 20 }) }) };
    o.projected = links.map((stroke) => ({
        stroke,
        points: projectStroke(
            stroke,
            (t) => ({ a: 0, b: 100, c: 200 })[t],
            (v) => 100 - v,
        ),
    }));
    const lines = [];
    let start, end;
    const ctx = {
        save() {},
        restore() {},
        beginPath() {},
        setLineDash() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            lines.push([start, end]);
        },
        fillText() {
            assert.fail("no H/L labels");
        },
    };
    o.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.deepEqual(lines, [
        [
            [0, 90],
            [100, 80],
        ],
        [
            [100, 80],
            [200, 92],
        ],
    ]);
    assert.ok(o.hitTest(50, 85));
    const hit = o.hitTest(150, 86);
    assert.ok(hit);
    assert.match(o.annotation(hit.externalId).description, /最高/);
});

test("level-one connection is solid, continuous and explicitly display-only on hit", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        segments = [],
        labels = [];
    const paths = [
        {
            id: "a",
            kind: "reversal",
            points: [{ index: 1, ordinal: 0, time: "a", available_at: "b", value: 10, kind: "H" }],
        },
        {
            id: "b",
            kind: "reversal",
            points: [{ index: 3, ordinal: 0, time: "c", available_at: "d", value: 5, kind: "L" }],
        },
    ];
    overlay.strokes = reversalConnections(paths);
    overlay.projected = overlay.strokes.map((stroke) => ({
        stroke,
        points: projectStroke(
            stroke,
            (t) => (t === "a" ? 0 : 100),
            (v) => v * 10,
        ),
    }));
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 20 }) }) };
    let start, end, dash;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(v) {
            dash = v;
        },
        beginPath() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            segments.push({ start, end, dash: [...dash], color: this.strokeStyle });
        },
        fillText(t) {
            labels.push(t);
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.deepEqual(segments, [{ start: [0, 100], end: [100, 50], dash: [], color: "#b7cdf4" }]);
    assert.deepEqual(labels, []);
    const hit = overlay.hitTest(50, 75),
        info = overlay.annotation(hit.externalId);
    assert.equal(info.time, "d");
    assert.equal(info.raw.scope, "display_only_connection");
    assert.deepEqual(info.levels, []);
    assert.match(info.description, /不新增反转/);
    assert.equal(overlay.hitTest(50, 100), null);
});
test("same-day low/high points remain separate and ordered", () => {
    const stroke = {
        points: [
            { time: "d1", ordinal: 0, value: 11 },
            { time: "d2", ordinal: 0, value: 9 },
            { time: "d2", ordinal: 1, value: 12 },
        ],
    };
    const projected = projectStroke(
        stroke,
        (t) => (t === "d1" ? 100 : 110),
        (p) => 200 - p * 10,
        10,
    );
    assert.equal(projected.length, 3);
    assert.ok(projected[1].x < projected[2].x);
    assert.equal(projected[1].y, 110);
    assert.equal(projected[2].y, 80);
    assert.equal(projected[1].point.time, projected[2].point.time);
});
test("normal points stay on their true bar coordinates; unavailable coordinates stay null", () => {
    const stroke = {
        points: [
            { time: "a", ordinal: 0, value: 12 },
            { time: "b", ordinal: 0, value: 10 },
        ],
    };
    const p = projectStroke(
        stroke,
        (t) => (t === "a" ? 100 : null),
        (v) => v,
        10,
    );
    assert.equal(p[0].x, 100);
    assert.equal(p[1].x, null);
});

test("filtered reversal keeps its original same-bar coordinate", () => {
    const stroke = { points: [{ time: "a", value: 12, projection_count: 2, projection_rank: 1 }] };
    const p = projectStroke(
        stroke,
        () => 100,
        (v) => v,
        10,
    );
    assert.equal(p[0].x, 101.5);
});

test("reversal overlay connects points with solid strokes and no circles", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        dashes = [];
    let dash;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(value) {
            dash = value;
        },
        beginPath() {},
        moveTo() {},
        lineTo() {},
        stroke() {
            dashes.push([...dash]);
        },
        fillText() {},
        arc() {
            assert.fail("no endpoint circles");
        },
    };
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 20 }) }) };
    overlay.projected = [
        {
            stroke: { kind: "reversal" },
            points: [9, 14, 10, 16].map((value, index) => ({
                x: index * 10,
                y: value,
                index,
                point: { kind: index % 2 ? "H" : "L", label: "turn" },
            })),
        },
    ];
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.deepEqual(dashes, [[], [], []]);
});

test("all lecture legs are dashed without endpoint circles", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        dashes = [];
    let dash = [],
        circles = 0;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(value) {
            dash = value;
        },
        beginPath() {},
        moveTo() {},
        lineTo() {},
        stroke() {
            dashes.push([...dash]);
        },
        arc() {
            circles++;
        },
        fill() {
            circles++;
        },
        fillText() {
            assert.fail("no line labels");
        },
    };
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 8 }) }) };
    overlay.projected = ["ordinary", "teaching"].map((kind) => ({
        stroke: { kind },
        points: ["seed", "confirmed", "developing"].map((state, index) => ({
            index,
            x: index * 10,
            y: 20 - index,
            point: { state, kind: "H", teaching_ordinal: index + 1 },
        })),
    }));
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.deepEqual(dashes, [
        [5, 3],
        [5, 3],
        [5, 3],
        [5, 3],
    ]);
    assert.equal(circles, 0);
});

test("secondary is a continuous purple solid line with distinct labels and evidence", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        segments = [],
        labels = [];
    let dash, start, end;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(v) {
            dash = v;
        },
        beginPath() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            segments.push({ dash: [...dash], start, end, color: this.strokeStyle, width: this.lineWidth });
        },
        fillText(v) {
            labels.push(v);
        },
        arc() {
            assert.fail("no endpoint circles");
        },
    };
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 20 }) }) };
    const points = [10, 45, 24].map((value, i) => ({
        time: `d${i}`,
        value,
        kind: i % 2 ? "H" : "L",
        label: `${i % 2 ? "H" : "L"}${i + 1}`,
        available_at: `d${i + 2}`,
        flip: i % 2 ? "翻多为空" : "翻空为多",
        broken_key: { label: "H0", value: 30 },
        confirmed_by: { label: "H4", value: 35 },
        levels: [],
    }));
    overlay.strokes = [{ id: "secondary-test", kind: "secondary", points }];
    overlay.projected = [
        {
            stroke: overlay.strokes[0],
            points: points.map((point, i) => ({ point, index: i, x: i * 100, y: point.value })),
        },
    ];
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.equal(segments.length, 2);
    assert.deepEqual(segments[0].end, segments[1].start);
    assert.ok(segments.every((s) => s.dash.length === 0 && s.color === "#d6a3ff" && s.width === 3));
    assert.deepEqual(labels, []);
    const item = overlay.annotation("drawing:secondary-test:0");
    assert.equal(item.raw.trend_level, 2);
    assert.equal(item.time, "d2");
    assert.equal(item.sourceTime, "d0");
    assert.match(item.description, /突破末跌高/);
});

test("tertiary solid line and annotation use level-two evidence", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        segments = [],
        labels = [];
    let dash, start, end;
    const ctx = {
        save() {},
        restore() {},
        setLineDash(v) {
            dash = v;
        },
        beginPath() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            segments.push({ dash: [...dash], start, end, color: this.strokeStyle });
        },
        fillText(v) {
            labels.push(v);
        },
        arc() {
            assert.fail("no endpoint circles");
        },
    };
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 20 }) }) };
    const points = [10, 45, 24].map((value, i) => ({
        time: `d${i}`,
        value,
        kind: i % 2 ? "H" : "L",
        label: `${i % 2 ? "H" : "L"}${i + 1}`,
        available_at: `d${i + 3}`,
        flip: i % 2 ? "翻多为空" : "翻空为多",
        broken_key: { label: "H1", value: 30 },
        confirmed_by: { label: "H4", value: 35 },
        levels: [],
    }));
    overlay.strokes = [{ id: "tertiary-test", kind: "tertiary", points }];
    overlay.projected = [
        {
            stroke: overlay.strokes[0],
            points: points.map((point, i) => ({ point, index: i, x: i * 100, y: point.value })),
        },
    ];
    overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    assert.equal(segments.length, 2);
    assert.deepEqual(segments[0].end, segments[1].start);
    assert.ok(segments.every((s) => s.dash.length === 0 && s.color === "#ffad72"));
    assert.deepEqual(labels, []);
    const item = overlay.annotation("drawing:tertiary-test:0");
    assert.equal(item.raw.trend_level, 3);
    assert.equal(item.time, "d3");
    assert.equal(item.sourceTime, "d0");
    assert.match(item.description, /基于二级趋势线/);
    assert.match(item.title, /三级低点/);
});

test("mixed main path shares junction coordinates and emphasis never removes legs", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        segments = [];
    let start, end;
    const ctx = {
        save() {},
        restore() {},
        setLineDash() {},
        beginPath() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            segments.push({ start, end, color: this.strokeStyle });
        },
        fillText() {},
    };
    overlay.chart = { timeScale: () => ({ options: () => ({ barSpacing: 8 }) }) };
    const stroke = {
        kind: "path",
        points: [
            { time: "a", value: 8, kind: "L" },
            { time: "b", value: 11, kind: "H", edge_kind: "ordinary", teaching_ordinal: 1 },
            { time: "c", value: 9, kind: "L", edge_kind: "teaching", teaching_ordinal: 2 },
            { time: "c", value: 12, kind: "H", edge_kind: "teaching", teaching_ordinal: 3 },
            { time: "d", value: 8, kind: "L", edge_kind: "ordinary" },
        ],
    };
    overlay.projected = [
        {
            stroke,
            points: projectStroke(
                stroke,
                (t) => ({ a: 10, b: 20, c: 30, d: 40 })[t],
                (v) => 100 - v,
            ),
        },
    ];
    const draw = () => overlay.draw({ useMediaCoordinateSpace: (fn) => fn({ context: ctx }) });
    draw();
    assert.equal(segments.length, 4);
    for (let i = 1; i < segments.length; i++) assert.deepEqual(segments[i - 1].end, segments[i].start);
    assert.deepEqual(
        segments.map((s) => s.color),
        ["#ffd36d", "#50dfd2", "#50dfd2", "#ffd36d"],
    );
    overlay.setTeachingHighlight(false);
    segments.length = 0;
    draw();
    assert.equal(segments.length, 4);
    assert.ok(segments.every((s) => s.color === "#ffd36d"));
});
