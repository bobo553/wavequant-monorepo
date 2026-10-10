import assert from "node:assert/strict";
import test from "node:test";

import {
    LectureOverlay,
    connectedTrendStrokes,
    publishedTrendSegments,
    usesCausalTrendConfirmation,
} from "../public/lecture-overlay.js";

const points = [
    {
        index: 2,
        ordinal: 0,
        time: "2026-01-03",
        available_at: "2026-01-08",
        kind: "L",
        value: 10,
        label: "L1",
        reversal: "正反转",
        levels: [],
        observations: [],
    },
    {
        index: 9,
        ordinal: 0,
        time: "2026-01-10",
        available_at: "2026-01-15",
        kind: "H",
        value: 45,
        label: "H1",
        reversal: "负反转",
        levels: [],
        observations: [],
    },
    {
        index: 14,
        ordinal: 0,
        time: "2026-01-15",
        available_at: "2026-01-19",
        kind: "L",
        value: 24,
        label: "L2",
        reversal: "正反转",
        levels: [],
        observations: [],
    },
];

const stroke = (level) => ({
    id: `structure-${level}`,
    kind: ["", "reversal", "secondary", "tertiary"][level],
    trend_level: level,
    confirmation_policy: "point_first_recursive_six_definitions_v113",
    leg_confirmation_policy: "high_low_point_geometry_v113",
    points: structuredClone(points),
    confirmed_legs: [],
});

for (const level of [1, 2, 3]) {
    test(`level ${level} draws judged geometry independently of strategy certificates`, () => {
        const geometry = stroke(level);
        const projected = geometry.points.map((point, index) => ({ point, index, x: index * 50, y: point.value }));
        const before = structuredClone(geometry);
        const segments = publishedTrendSegments(geometry, projected);
        assert.equal(segments.length, 2);
        assert.deepEqual(
            segments.map(([left, right]) => [left.point.value, right.point.value]),
            [
                [10, 45],
                [45, 24],
            ],
        );
        geometry.confirmed_legs = [
            { confirmation: { direction: "up", confirmation_rule: "source_n_strict_one_p_target" } },
        ];
        assert.deepEqual(publishedTrendSegments(geometry, projected), segments);
        assert.deepEqual(geometry.points, before.points);
    });

    test(`level ${level} keeps low reversal annotations visible without trading permission`, () => {
        const geometry = stroke(level);
        const overlay = new LectureOverlay({ dataset: {} });
        overlay.strokes = [geometry];
        const info = overlay.annotation(`drawing:${geometry.id}:0`);
        assert.ok(info);
        assert.equal(info.raw.trend_level, level);
        assert.match(info.title, /低点 · 正反转/);
        assert.match(info.description, /缺少策略证书.*删除/);
        assert.match(info.description, /另行决定策略资格/);
        assert.equal(info.time, "2026-01-08");
        assert.equal(info.sourceTime, "2026-01-03");
    });

    test(`level ${level} displays the structural tail without granting it formal status`, () => {
        const tail = {
            ...stroke(level),
            id: `tail-${level}`,
            kind: ["", "reversal-developing", "secondary-developing", "tertiary-developing"][level],
            state: "candidate",
            display_only: true,
            wave_direction: "down",
            points: [points[1], { ...points[2], state: "developing", display_only: true }],
        };
        const overlay = new LectureOverlay({ dataset: {} });
        overlay.setStrokes([stroke(level), tail]);
        assert.equal(overlay.strokes.length, 2);
        const info = overlay.annotation(`drawing:${tail.id}:1`);
        assert.ok(info);
        assert.equal(info.time, points[2].available_at);
        assert.equal(info.raw.scope, "display_only_developing_path");
        assert.equal(info.raw.stroke.display_only, true);
        assert.match(info.description, /不参与后续级别、策略或回测/);
    });
}

test("frontend display connections cannot join separate point-judged source paths", () => {
    const left = stroke(1);
    const right = { ...stroke(1), id: "right", points: [{ ...points[2], index: 20 }] };
    assert.equal(usesCausalTrendConfirmation(left), true);
    const result = connectedTrendStrokes(
        [left, right],
        [{ source_paths: [left.id, right.id], points: [...left.points, ...right.points] }],
    );
    assert.equal(result.length, 2);
    assert.deepEqual(result[0].points, left.points);
    assert.deepEqual(result[1].points, right.points);
});
