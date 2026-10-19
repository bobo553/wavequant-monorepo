import assert from "node:assert/strict";
import test from "node:test";
import { hoveredTrendThirds } from "../public/retracement-guides.js";

const bars = ["2019-08-06", "2019-08-19", "2019-09-23", "2019-09-24"].map((time) => ({ time }));
const low = { time: "2019-08-06", value: 4, kind: "L", available_at: "2019-08-19" };
const high = { time: "2019-09-23", value: 10.47, kind: "H", available_at: "2019-09-24" };
const theory = { asof: "2019-09-24", secondary_trends: { strokes: [{ id: "p", points: [low, high] }] } };

test("hover anywhere in the confirmed second-level segment shows its two thirds without window repricing", () => {
    const expected = hoveredTrendThirds(theory, bars, "2019-08-19");
    assert.equal(expected.length, 2);
    assert.equal(expected[0].price, high.value - (high.value - low.value) / 3);
    assert.equal(expected[1].price, high.value - (high.value - low.value) * 2 / 3);
    assert.equal(expected[0].start, low.time);
    assert.equal(expected[0].end, high.time);
    assert.deepEqual(hoveredTrendThirds(theory, bars, "2019-09-23"), expected);
    assert.equal(hoveredTrendThirds(theory, bars.slice(1), "2019-08-19")[0].price, expected[0].price);
    assert.deepEqual(hoveredTrendThirds(theory, bars, null), []);
    assert.deepEqual(hoveredTrendThirds(theory, bars, "2019-09-24"), []);
});

test("future, developing, display-only or unrelated level endpoints do not create confirmed thirds", () => {
    assert.deepEqual(hoveredTrendThirds({ ...theory, asof: "2019-09-23" }, bars, "2019-08-19"), []);
    assert.deepEqual(hoveredTrendThirds(theory, bars, "2019-08-19", 3), []);
    for (const change of [{ state: "developing" }, { display_only: true }]) {
        const changed = { ...theory, secondary_trends: { strokes: [{ points: [low, { ...high, ...change }] }] } };
        assert.deepEqual(hoveredTrendThirds(changed, bars, "2019-08-19"), []);
    }
});
