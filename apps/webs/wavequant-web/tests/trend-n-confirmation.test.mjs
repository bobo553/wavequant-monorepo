import assert from "node:assert/strict";
import test from "node:test";

import {
    LectureOverlay,
    connectedTrendStrokes,
    reversalConnections,
    secondaryConnections,
    trendConfirmationPresentation,
    usesCausalTrendConfirmation,
} from "../public/lecture-overlay.js";

function nConfirmation(rising = true) {
    const ref = (time, value, kind, available_at = time) => ({ time, value, kind, available_at });
    const origin = ref("2018-09-11", rising ? 4.94 : 6.06, rising ? "L" : "H", "2018-09-12");
    const neckline = ref("2018-09-12", rising ? 5.2 : 5.8, rising ? "H" : "L", "2018-09-14");
    return {
        confirmation_rule: "source_n_strict_one_p_target",
        direction: rising ? "up" : "down",
        available_at: "2018-09-19",
        origin,
        broken_key: neckline,
        n_neckline: neckline,
        n_pullback: ref("2018-09-13", rising ? 5.0 : 6.0, rising ? "L" : "H", "2018-09-14"),
        n_completion: ref("2018-09-14", rising ? 5.4 : 5.6, rising ? "H" : "L"),
        box_anchor: rising ? 5.4 : 5.6,
        one_p_target: rising ? 5.86 : 5.14,
        confirmed_by: ref("2018-09-19", rising ? 5.9 : 5.1, rising ? "H" : "L"),
    };
}

function developingStroke(level, rising = true) {
    const confirmation = nConfirmation(rising);
    return {
        id: `developing-n-${level}`,
        kind: ["", "reversal-developing", "secondary-developing", "tertiary-developing"][level],
        trend_level: level,
        state: "confirmed",
        display_only: true,
        confirmation_policy: "trend_routes_v108",
        confirmation,
        wave_direction: confirmation.direction,
        points: [
            { ...confirmation.origin, label: rising ? "L1" : "H1", index: 0, state: "confirmed" },
            {
                ...confirmation.confirmed_by,
                label: rising ? "H发展" : "L发展",
                index: 1,
                state: "confirmed",
                edge_state: "confirmed",
            },
        ],
    };
}

test("all three levels name source N and actual attack target without changing its confirmation route", () => {
    for (const level of [1, 2, 3]) {
        for (const rising of [true, false]) {
            const proof = nConfirmation(rising),
                before = structuredClone(proof),
                presentation = trendConfirmationPresentation(proof, level);
            assert.match(presentation.description, new RegExp(`下级${["原折线", "一级", "二级"][level - 1]}`));
            assert.match(presentation.description, /2018-09-11.*2018-09-12 可知/);
            assert.match(presentation.description, /颈线.*2018-09-14 可知.*回档.*2018-09-14 可知/);
            assert.match(presentation.description, /2018-09-14.*完成攻击.*实际攻击箱体锚点 X.*2×X−A/);
            assert.match(
                presentation.description,
                rising ? /一饱目标 5\.86.*最高价 5\.9 严格超过/ : /一饱目标 5\.14.*最低价 5\.1 严格低于/,
            );
            assert.match(presentation.summary, rising ? /正 N.*确认上涨/ : /倒 N.*确认下跌/);
            assert.match(presentation.description, /2018-09-19 确认.*当时可知.*相等触及不确认/);
            assert.doesNotMatch(
                presentation.description + presentation.sourceLabel,
                /末跌高|末升低|本级关键位|收盘转多|收盘转空/,
            );
            assert.deepEqual(proof, before);
        }
    }
});

test("formal endpoints and all developing levels preserve N confirmation and its later knowledge date", () => {
    for (const level of [1, 2, 3]) {
        for (const rising of [true, false]) {
            const confirmation = nConfirmation(rising),
                overlay = new LectureOverlay({ dataset: {} }),
                endpoint = {
                    ...confirmation.origin,
                    label: rising ? "L1" : "H1",
                    available_at: confirmation.available_at,
                    observations: [],
                    levels: [],
                    [rising ? "trend_confirmation" : "incoming_trend_confirmation"]: confirmation,
                };
            overlay.strokes = [
                {
                    id: "formal",
                    kind: ["", "reversal", "secondary", "tertiary"][level],
                    confirmation_policy: "trend_routes_v108",
                    points: [endpoint],
                },
            ];
            const formal = overlay.annotation("drawing:formal:0");
            assert.equal(formal.time, confirmation.available_at);
            assert.equal(formal.sourceTime, confirmation.origin.time);
            assert.match(formal.sourceLabel, /下级.*N.*一饱/);
            assert.doesNotMatch(formal.description, /已确认下降压力|本级.*末跌高|收盘.*转多/);
            const stroke = developingStroke(level, rising);
            overlay.setStrokes([stroke]);
            const developing = overlay.annotation(`drawing:${stroke.id}:1`);
            assert.equal(developing.raw.trend_level, level);
            assert.equal(developing.time, confirmation.available_at);
            assert.match(developing.description, /一饱.*当前端点.*独立完整确认依据/);
            assert.match(developing.sourceLabel, /下级.*N.*一饱/);
        }
    }
});

test("touches, missing N references and wrong proof kinds cannot display a confirmed new-policy trend", () => {
    for (const level of [1, 2, 3]) {
        for (const rising of [true, false]) {
            const confirmation = nConfirmation(rising);
            for (const missing of [
                "origin",
                "n_neckline",
                "n_pullback",
                "n_completion",
                "one_p_target",
                "box_anchor",
            ]) {
                const incomplete = { ...confirmation, [missing]: null };
                assert.equal(trendConfirmationPresentation(incomplete, level), null, `${level} ${missing}`);
                const overlay = new LectureOverlay({ dataset: {} }),
                    stroke = { ...developingStroke(level, rising), confirmation: incomplete };
                overlay.setStrokes([stroke]);
                assert.deepEqual(overlay.strokes, []);
                overlay.strokes = [stroke];
                assert.equal(overlay.annotation(`drawing:${stroke.id}:1`), null);
            }
            for (const value of [
                confirmation.one_p_target,
                rising ? confirmation.one_p_target - 0.01 : confirmation.one_p_target + 0.01,
            ])
                assert.equal(
                    trendConfirmationPresentation(
                        { ...confirmation, confirmed_by: { ...confirmation.confirmed_by, value } },
                        level,
                    ),
                    null,
                );
            assert.equal(
                trendConfirmationPresentation(
                    { ...confirmation, confirmed_by: { ...confirmation.confirmed_by, kind: "K" } },
                    level,
                ),
                null,
            );
            assert.equal(
                trendConfirmationPresentation(
                    { ...confirmation, broken_key: { ...confirmation.broken_key, value: 100 } },
                    level,
                ),
                null,
            );
            for (const reference of ["origin", "n_neckline", "n_pullback", "n_completion", "confirmed_by"])
                assert.equal(
                    trendConfirmationPresentation(
                        { ...confirmation, [reference]: { ...confirmation[reference], available_at: "2018-09-20" } },
                        level,
                    ),
                    null,
                );
            assert.equal(
                trendConfirmationPresentation(
                    { ...confirmation, n_completion: { ...confirmation.n_completion, time: "2018-09-20" } },
                    level,
                ),
                null,
            );
        }
    }
});

test("new and legacy causal policies both reject geometry that reconnects separate server paths", () => {
    const point = (index, kind, value) => ({
        index,
        time: `d${index}`,
        kind,
        value,
        available_at: "z",
        state: "confirmed",
    });
    for (const policy of ["two_routes_v106", "trend_routes_v108", "trend_routes_v109"]) {
        assert.equal(usesCausalTrendConfirmation({ confirmation_policy: policy }), true);
        for (const [kind, connect] of [
            ["reversal", reversalConnections],
            ["secondary", secondaryConnections],
        ]) {
            const legacyPaths = [
                { id: "left", kind, points: [point(1, "L", 4.4)] },
                { id: "right", kind, points: [point(8, "L", 4.82)] },
            ];
            const source = [
                { id: "source", kind: kind === "secondary" ? "reversal" : "ordinary", points: [point(4, "H", 5.85)] },
            ];
            const links = connect(legacyPaths, source),
                causalPaths = legacyPaths.map((stroke) => ({ ...stroke, confirmation_policy: policy }));
            assert.equal(links.length, 1);
            assert.deepEqual(connect(causalPaths, source), []);
            assert.equal(connectedTrendStrokes(causalPaths, links).length, 2);
        }
    }
    assert.equal(usesCausalTrendConfirmation({}), false);
});

test("the authoritative primary developing path is rendered as a solid level-one direction and can be hidden", async () => {
    globalThis.window = { LightweightCharts: {} };
    globalThis.document = { documentElement: { classList: { contains: () => false } } };
    globalThis.MutationObserver = class {
        observe() {}
    };
    const { PriceChart } = await import("../public/charts.js");
    const stroke = developingStroke(1),
        overlay = new LectureOverlay({ dataset: {} }),
        owner = {
            polylineEnabled: true,
            drawingMode: "lecture",
            showTrend: true,
            container: { dataset: {} },
            lectureOverlay: overlay,
            theory: {
                lecture_drawing: { strokes: [] },
                reversal_trends: { strokes: [], developing_strokes: [stroke] },
            },
            clearPolyline() {
                this.polylineKey = "";
            },
        };
    PriceChart.prototype.renderPolyline.call(owner, "2018-09-11", "2018-09-25");
    assert.deepEqual(overlay.strokes, [stroke]);
    overlay.projected = [
        { stroke, points: stroke.points.map((point, index) => ({ point, index, x: index * 10, y: point.value })) },
    ];
    const drawn = [];
    let dash;
    const context = {
        save() {},
        restore() {},
        beginPath() {},
        moveTo() {},
        lineTo() {},
        setLineDash(value) {
            dash = value;
        },
        stroke() {
            drawn.push({ color: this.strokeStyle, dash });
        },
    };
    overlay.draw({ useMediaCoordinateSpace: (callback) => callback({ context }) });
    assert.deepEqual(drawn, [{ color: "#b7cdf4", dash: [] }]);
    assert.equal(overlay.annotation(`drawing:${stroke.id}:1`).raw.trend_level, 1);
    owner.showTrend = false;
    PriceChart.prototype.renderPolyline.call(owner, "2018-09-11", "2018-09-25");
    assert.deepEqual(overlay.strokes, []);
    delete globalThis.window;
    delete globalThis.document;
    delete globalThis.MutationObserver;
});
