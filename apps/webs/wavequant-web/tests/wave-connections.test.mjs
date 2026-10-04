import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { combinedAWaveConnections } from "../public/combined-a-wave.js";
import { waveCProjectionLegs } from "../public/wave-c-projection.js";
import { selectWaveConnections } from "../public/wave-connections.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_wave_connections.json", import.meta.url)));
const point = (time, value = 6.19) => ({ time, value });
const edge = (id, start, end, group = "abc:2") => ({ id, group, points: [start, end] });

function assertDegree(connections) {
    const incoming = new Set();
    const outgoing = new Set();
    for (const {
        group,
        points: [start, end],
    } of connections) {
        const startKey = JSON.stringify([group, start.time, start.value]);
        const endKey = JSON.stringify([group, end.time, end.value]);
        assert.ok(!outgoing.has(startKey), `multiple outgoing lines at ${startKey}`);
        assert.ok(!incoming.has(endKey), `multiple incoming lines at ${endKey}`);
        outgoing.add(startKey);
        incoming.add(endKey);
    }
}

test("June 8 can be an end and a start while duplicates and same-direction forks are removed", () => {
    const pivot = point("2026-06-08");
    const connections = [
        edge("older-in", point("2026-06-01"), pivot),
        edge("in", point("2026-06-05", 6.57), pivot),
        edge("out", pivot, point("2026-06-09", 7.3)),
        edge("duplicate-out", pivot, point("2026-06-09", 7.3)),
        edge("long-out", pivot, point("2026-07-06", 8.56)),
    ];
    const result = selectWaveConnections(connections);
    assertDegree(result);
    assert.equal(result.length, 2);
    assert.ok(result.some((line) => line.points[0] === pivot));
    assert.ok(result.some((line) => line.points[1] === pivot));
});

test("different levels, line types and prices retain their own endpoint slots", () => {
    const start = point("2026-06-08");
    const end = point("2026-07-06", 8.56);
    const connections = [
        edge("level2", start, end),
        edge("level3", start, end, "abc:3"),
        edge("combined", start, end, "combined-a:2"),
        edge("other-price", point(start.time, 6.56), point(end.time, 8.55)),
    ];
    assert.equal(selectWaveConnections(connections).length, connections.length);
    assertDegree(selectWaveConnections(connections));
});

test("real Xianfeng history keeps one ABC arrival and one combined A arrival at July 6", () => {
    const connections = fixture.projections.flatMap((projection, index) =>
        waveCProjectionLegs(projection).map((leg) => ({
            ...leg,
            id: `${index}:${leg.title}`,
            group: `abc:${projection.trendLevel || 2}:${leg.title}`,
        })),
    );
    assert.equal(connections.filter((line) => line.points[1].time === "2026-07-06").length, 4);
    const selected = selectWaveConnections(connections);
    assertDegree(selected);
    assert.equal(selected.filter((line) => line.points[1].time === "2026-07-06").length, 1);
    assert.equal(selected.filter((line) => line.title === "A 浪" && line.points[1].time === "2026-06-09").length, 1);
    assertDegree(combinedAWaveConnections(fixture.combined));
    assert.equal(
        combinedAWaveConnections(fixture.combined).filter((line) => line.points[1].time === "2026-07-06").length,
        1,
    );
});

test("selection is independent of source order and never mutates historical observations", () => {
    const snapshot = JSON.stringify(fixture.combined);
    const expected = combinedAWaveConnections(fixture.combined).map((line) => line.id);
    assert.deepEqual(
        combinedAWaveConnections([...fixture.combined].reverse()).map((line) => line.id),
        expected,
    );
    assert.deepEqual(
        combinedAWaveConnections([...fixture.combined.slice(7), ...fixture.combined.slice(0, 7)]).map(
            (line) => line.id,
        ),
        expected,
    );
    assert.equal(JSON.stringify(fixture.combined), snapshot);
});

test("empty, incomplete, nonfinite and unordered SDK endpoints cannot create connections", () => {
    assert.deepEqual(selectWaveConnections([]), []);
    assert.deepEqual(
        selectWaveConnections([
            { points: [] },
            { points: [point("2026-06-08")] },
            edge("bad-date", point("invalid"), point("2026-07-06")),
            edge("bad-price", point("2026-06-08", NaN), point("2026-07-06")),
            edge("infinite", point("2026-06-08"), point("2026-07-06", Infinity)),
            edge("same-day", point("2026-06-08"), point("2026-06-08", 6.56)),
            edge("reverse", point("2026-07-06"), point("2026-06-08")),
        ]),
        [],
    );
});

test("dense alternatives still satisfy one incoming and one outgoing slot per endpoint", () => {
    const points = Array.from({ length: 20 }, (_, index) =>
        point(`2026-06-${String(index + 1).padStart(2, "0")}`, index),
    );
    const connections = points.flatMap((start, index) =>
        points.slice(index + 1).map((end) => edge(`${index}:${end.time}`, start, end)),
    );
    const selected = selectWaveConnections(connections);
    assertDegree(selected);
    assert.equal(selected.length, points.length - 1);
    assert.deepEqual(selectWaveConnections([...connections].reverse()), selected);
});
