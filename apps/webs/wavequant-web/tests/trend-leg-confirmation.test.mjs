import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { LectureOverlay, projectStroke } from "../public/lecture-overlay.js";

const fixture = JSON.parse(
    readFileSync(
        new URL("../../../../packages/wavequant-core/tests/fixtures/xiangyang_2019_trend_legs.json", import.meta.url),
        "utf8",
    ),
);

function drawnLegs(stroke) {
    const overlay = new LectureOverlay({ dataset: {} });
    const dates = new Map(fixture.bars.map((bar, index) => [bar.time, index * 20]));
    const projected = projectStroke(
        stroke,
        (day) => dates.get(day),
        (price) => 100 - price,
    );
    overlay.projected = [{ stroke, points: projected }];
    const lines = [];
    let start, end;
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
    return lines.map(([start, end]) => [
        projected.find(({ x, y }) => x === start[0] && y === start[1]).point.time,
        projected.find(({ x, y }) => x === end[0] && y === end[1]).point.time,
    ]);
}

test("the March downward leg cannot borrow April's upward proof", () => {
    const before = structuredClone(fixture.secondary_stroke);
    assert.deepEqual(drawnLegs(fixture.secondary_stroke), fixture.expected_legs);
    assert.deepEqual(fixture.secondary_stroke, before);
});

for (const kind of ["reversal", "secondary", "tertiary"]) {
    test(`${kind} keeps the whole rising wave through an unqualified pullback`, () => {
        const stroke = { ...structuredClone(fixture.secondary_stroke), kind };
        assert.deepEqual(drawnLegs(stroke), fixture.expected_legs);
    });
}

for (const failure of [
    "missing_legs",
    "missing_proof",
    "wrong_direction",
    "wrong_origin",
    "wrong_ordinal",
    "future_proof",
    "future_key",
    "premature_endpoint",
    "incomplete_cycle",
]) {
    test(`incomplete formal evidence fails closed: ${failure}`, () => {
        const stroke = structuredClone(fixture.secondary_stroke),
            leg = stroke.confirmed_legs[0];
        if (failure === "missing_legs") delete stroke.confirmed_legs;
        else if (failure === "missing_proof") delete leg.confirmation;
        else if (failure === "wrong_direction") leg.direction = "down";
        else if (failure === "wrong_origin") leg.confirmation.origin.value += 1;
        else if (failure === "wrong_ordinal") leg.confirmation.origin.ordinal += 1;
        else if (failure === "future_proof") leg.confirmation.available_at = "2099-01-01";
        else if (failure === "future_key") leg.confirmation.broken_key.available_at = "2099-01-01";
        else if (failure === "premature_endpoint") leg.confirmation.confirmed_by.index = leg.points[1].index + 1;
        else delete leg.confirmation.alternation_low;
        assert.deepEqual(drawnLegs(stroke), []);
    });
}

function mirror(value) {
    if (Array.isArray(value)) return value.map(mirror);
    if (value && typeof value === "object")
        return Object.fromEntries(
            Object.entries(value).map(([key, item]) => {
                if (key === "kind") return [key, item === "H" ? "L" : item === "L" ? "H" : item];
                if (key === "direction") return [key, item === "up" ? "down" : "up"];
                if (key === "value") return [key, 30 - item];
                const name = { flip_high: "flip_low", alternation_low: "alternation_high" }[key] ?? key;
                return [name, mirror(item)];
            }),
        );
    return value;
}

for (const kind of ["reversal", "secondary", "tertiary"]) {
    for (const route of ["cycle", "own_key"]) {
        test(`${kind} draws an independently confirmed downward ${route} route`, () => {
            const stroke = mirror(fixture.secondary_stroke);
            stroke.kind = kind;
            if (route === "own_key") {
                const proof = stroke.confirmed_legs[0].confirmation;
                proof.confirmation_rule = "strict_same_level_market_key_break";
                proof.confirmed_by.kind = "L";
                delete proof.flip_low;
                delete proof.alternation_high;
            }
            assert.deepEqual(drawnLegs(stroke), fixture.expected_legs);
        });
    }
}

test("legacy data keeps its historical drawing behavior", () => {
    const stroke = structuredClone(fixture.secondary_stroke);
    delete stroke.leg_confirmation_policy;
    assert.deepEqual(drawnLegs(stroke), [
        ["2018-10-19", "2019-03-21"],
        ["2019-03-21", "2019-03-29"],
        ["2019-03-29", "2019-04-18"],
    ]);
});

test("the interior low is explained as a retracement reference, not a confirmed secondary reversal", () => {
    const overlay = new LectureOverlay({ dataset: {} }),
        stroke = fixture.secondary_stroke;
    overlay.strokes = [stroke];
    const p = stroke.points.find((p) => p.time === "2019-03-29");
    const annotation = overlay.annotation(`drawing:${stroke.id}:${stroke.points.indexOf(p)}`);
    assert.match(annotation.title, /二级上涨内/);
    assert.match(annotation.description, /未成立时保持原上涨波段/);
    assert.equal(annotation.raw.scope, "lecture_level2_reference_not_confirmed_reversal");
});
