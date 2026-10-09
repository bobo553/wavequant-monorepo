import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { LectureOverlay, projectStroke, selectPublishedTrendSegments } from "../public/lecture-overlay.js";

const fixture = JSON.parse(
    readFileSync(
        new URL(
            "../../../../packages/wavequant-core/tests/fixtures/xiangyang_2024_wave_continuation.json",
            import.meta.url,
        ),
        "utf8",
    ),
);
function drawnConnections(strokes) {
    const days = [...new Set(strokes.flatMap((stroke) => stroke.points.map((point) => point.time)))].sort();
    const dates = new Map(days.map((day, index) => [day, index * 20]));
    const overlay = new LectureOverlay({ dataset: {} });
    overlay.projected = strokes.map((stroke) => ({
        stroke,
        points: projectStroke(
            stroke,
            (day) => dates.get(day),
            (price) => 100 - price,
        ),
    }));
    let start, end;
    const lines = [];
    const context = {
        save() {},
        restore() {},
        setLineDash() {},
        beginPath() {},
        fillText() {},
        moveTo(x, y) {
            start = [x, y];
        },
        lineTo(x, y) {
            end = [x, y];
        },
        stroke() {
            lines.push([start, end]);
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (callback) => callback({ context }) });
    const points = overlay.projected.flatMap((item) => item.points);
    return lines.map((line) => line.map(([x, y]) => points.find((p) => p.x === x && p.y === y).point.time));
}

test("the July low has one visible tertiary wave when its formal endpoint is extended", () => {
    const strokes = [fixture.formal_stroke, fixture.developing_stroke],
        before = structuredClone(strokes);
    assert.deepEqual(drawnConnections(strokes), [fixture.expected_connection]);
    assert.deepEqual(strokes, before);
});

function pair(level = 3) {
    const formal = structuredClone(fixture.formal_stroke),
        tail = structuredClone(fixture.developing_stroke);
    formal.trend_level = tail.trend_level = level;
    formal.kind = level === 2 ? "secondary" : "tertiary";
    tail.kind = `${formal.kind}-developing`;
    return [formal, tail];
}

for (const level of [2, 3]) {
    test(`level ${level} keeps only one rising extension in either input order`, () => {
        const [formal, tail] = pair(level);
        for (const strokes of [
            [formal, tail],
            [tail, formal],
        ])
            assert.deepEqual(drawnConnections(strokes), [fixture.expected_connection]);
    });
    test(`level ${level} retains the formal connection when no development path is visible`, () => {
        const [formal] = pair(level);
        assert.deepEqual(drawnConnections([formal]), [["2024-07-25", "2025-03-21"]]);
    });
    test(`level ${level} favors the formal point when the live endpoint is exactly the same`, () => {
        const [formal, tail] = pair(level);
        tail.points[1] = { ...formal.points[1], state: "developing", development_role: "active_endpoint" };
        for (const strokes of [
            [formal, tail],
            [tail, formal],
        ]) {
            const projected = strokes.map((stroke) => ({
                stroke,
                points: projectStroke(
                    stroke,
                    (day) => (day === "2024-07-25" ? 0 : 20),
                    (price) => 100 - price,
                ),
            }));
            const selected = selectPublishedTrendSegments(projected);
            assert.equal(selected.find((item) => item.stroke === formal).segments.length, 1);
            assert.equal(selected.find((item) => item.stroke === tail).segments.length, 0);
        }
    });
}

for (const change of ["other_wave", "level", "path", "origin_ordinal", "missing_identity"]) {
    test(`independent paths do not merge from a common date and price: ${change}`, () => {
        const [formal, tail] = pair();
        if (change === "other_wave") tail.wave_id += "-another-proof";
        else if (change === "level") {
            tail.trend_level = 2;
            tail.kind = "secondary-developing";
        } else if (change === "path") tail.source_path += "-other-source";
        else if (change === "origin_ordinal") tail.points[0].ordinal += 1;
        else delete tail.wave_id;
        assert.equal(drawnConnections([formal, tail]).length, 2);
    });
}

test("an incomplete developing proof cannot hide the valid formal wave", () => {
    const [formal, tail] = pair();
    delete tail.confirmation.broken_key;
    assert.deepEqual(drawnConnections([formal, tail]), [["2024-07-25", "2025-03-21"]]);
});

test("an unprojectable development endpoint cannot hide a visible formal line", () => {
    const [formal, tail] = pair();
    const projected = [formal, tail].map((stroke) => ({
        stroke,
        points: projectStroke(
            stroke,
            (day) => (day === "2025-05-15" ? null : day === "2024-07-25" ? 0 : 20),
            (price) => 100 - price,
        ),
    }));
    const selected = selectPublishedTrendSegments(projected);
    assert.equal(selected.find((item) => item.stroke === formal).segments.length, 1);
});

test("independent pending countertrend segments remain after the live rising leg wins", () => {
    const [formal, tail] = pair();
    tail.points.push({
        ...tail.points[1],
        index: tail.points[1].index + 1,
        time: "2025-05-16",
        kind: "L",
        value: 17,
        available_at: "2025-05-16",
        development_role: "pending_evidence",
        edge_state: "developing",
    });
    assert.deepEqual(drawnConnections([formal, tail]), [fixture.expected_connection, ["2025-05-15", "2025-05-16"]]);
});

function mirror(value) {
    if (Array.isArray(value)) return value.map(mirror);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(
        Object.entries(value).map(([key, item]) => {
            if (key === "kind" && ["H", "L"].includes(item)) return [key, item === "H" ? "L" : "H"];
            if (key === "value") return [key, 30 - item];
            if (key === "direction" || key === "wave_direction") return [key, item === "up" ? "down" : "up"];
            return [key, mirror(item)];
        }),
    );
}
for (const level of [2, 3]) {
    test(`level ${level} likewise keeps a single downward extension`, () => {
        const strokes = mirror(pair(level));
        assert.deepEqual(drawnConnections(strokes), [fixture.expected_connection]);
    });
}
