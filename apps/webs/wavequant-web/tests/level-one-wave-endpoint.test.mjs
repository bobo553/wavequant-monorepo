import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { connectedTrendStrokes, projectStroke, reversalConnections } from "../public/lecture-overlay.js";

const fixture = JSON.parse(
    readFileSync(
        new URL(
            "../../../../packages/wavequant-core/tests/fixtures/xiangyang_2025_level_one_low.json",
            import.meta.url,
        ),
        "utf8",
    ),
);

test("the shared Core snapshot projects December 30 directly to January 13", () => {
    const strokes = fixture.level_one_strokes;
    const before = structuredClone(strokes);
    const paths = connectedTrendStrokes(strokes, reversalConnections(strokes));
    const path = paths.find((stroke) => stroke.points.some((point) => point.time === "2024-12-30"));
    const indices = new Map(fixture.bars.map(([day], index) => [day, index]));
    const projected = projectStroke(
        path,
        (day) => indices.get(day) * 10,
        (price) => 100 - price,
    );
    const position = projected.findIndex(({ point }) => point.time === "2024-12-30");
    assert.deepEqual(
        projected.slice(position, position + 2).map(({ point, x, y }) => [point.time, point.value, x, y]),
        [
            ["2024-12-30", 7.29, indices.get("2024-12-30") * 10, 100 - 7.29],
            ["2025-01-13", 5.51, indices.get("2025-01-13") * 10, 100 - 5.51],
        ],
    );
    assert.ok(!projected.some(({ point }) => point.time === "2025-01-07"));
    assert.deepEqual(strokes, before);
});

test("the January low keeps its real date, price and later causal availability", () => {
    const low = fixture.level_one_strokes
        .flatMap((stroke) => stroke.points)
        .find((point) => point.time === "2025-01-13");
    assert.equal(low.kind, "L");
    assert.equal(low.value, 5.51);
    assert.equal(low.available_at, "2025-01-21");
    assert.deepEqual(fixture.bars.find(([day]) => day === "2025-01-13").slice(1, 5), [5.66, 6.1, 5.51, 5.85]);
    assert.deepEqual(fixture.bars.find(([day]) => day === "2025-01-07").slice(1, 5), [5.65, 5.88, 5.55, 5.83]);
});
