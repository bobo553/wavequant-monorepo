import assert from "node:assert/strict";
import test from "node:test";
import { isEligibilityNTargetSource, withEligibilityNTargets } from "../public/eligibility-n-targets.js";
import { buildAnnotations } from "../public/annotations.js";
import { nTargetAt, nTargetGuide, nTargetLevels, nTargetObservations } from "../public/n-target-focus.js";

function sample() {
    const bars = [
        ["2019-08-06", 4.12, 4.15, 4, 4.09],
        ["2019-08-09", 4.10, 4.12, 4.01, 4.02],
        ["2019-08-12", 4.04, 4.15, 4.04, 4.15],
        ["2019-08-14", 4.18, 4.21, 4.16, 4.20],
        ["2019-08-15", 4.10, 4.21, 4.06, 4.21],
        ["2019-08-16", 4.19, 4.25, 4.19, 4.22],
        ["2019-08-19", 4.25, 4.64, 4.22, 4.64],
        ["2019-08-20", 4.65, 4.80, 4.61, 4.75],
        ["2019-08-21", 4.70, 4.80, 4.60, 4.70],
    ].map(([time, open, high, low, close]) => ({ time, open, high, low, close, volume: 100 }));
    return { symbol: "sh.600812", timeframe: "1d", asof: bars.at(-1).time, bars, markers: [],
        backtest: { strategy: { buy_interval_level: 2 } }, audit: [
            { event: "trading_interval_opened", trend_level: 2, source_path: "p", bar_index: 1007,
                timestamp: "2019-08-20", start_low_time: "2019-08-06" },
            { event: "n_completed", direction: "up", n_id: "up:1001:1003:1004:1005",
                timestamp: "2019-08-16", bar_index: 1005, origin: 1001, neckline: 1003,
                pullback: 1004, known_at: 1007, target_eligible: true, one_p: 4.49, two_t: 4.73 },
        ] };
}

test("confirmed floor N restores four hover targets in cropped running snapshots", () => {
    const view = sample(), theory = { asof: view.asof, events: [], shapes: [] };
    const original = structuredClone({ view, theory });
    const result = withEligibilityNTargets(view, theory);
    const event = result.events[0];
    assert.equal(event.available_at, "2019-08-20");
    assert.equal(event.target_eligible, true);
    assert.equal(event.one_p, 4.49);
    assert.equal(event.shape[0].time, "2019-08-09");
    assert.deepEqual(event.levels.map((level) => level.stage), ["one_p", "two_t", "five_top", "ten_full"]);
    assert.ok(Math.abs(event.levels[0].price - 4.49) < 1e-10);
    assert.ok(event.levels.slice(2).every((level) => level.estimated && level.display_only));
    const items = buildAnnotations(view, result);
    const observations = nTargetObservations(items, view.bars, view.asof);
    const focused = nTargetAt(observations, "2019-08-12");
    assert.equal(focused.raw.n_id, event.n_id);
    assert.equal(nTargetLevels(focused, view.asof).length, 4);
    assert.equal(nTargetGuide(focused, focused.levels[2], view.bars, view.asof).targetState, "预估叠箱");
    assert.equal(observations[0].to, "2019-08-20", "estimates do not extend the original observation range");
    assert.deepEqual({ view, theory }, original);
    assert.deepEqual(withEligibilityNTargets(view, result), result, "repeated projection is idempotent");
});

test("future confirmation, wrong level and an origin outside the structural interval do not publish targets", () => {
    for (const mutate of [
        (view) => { view.asof = "2019-08-19"; },
        (view) => { view.backtest.strategy.buy_interval_level = 3; },
        (view) => { view.audit[0].start_low_time = "2019-08-12"; },
    ]) {
        const view = sample(); mutate(view);
        assert.equal(withEligibilityNTargets(view, { events: [] }).events.length, 0);
    }
});

test("closing or invalidating a source bounds historical display instead of hiding its entire past", () => {
    const view = sample();
    view.audit.push({ event: "trading_interval_closed", trend_level: 2, source_path: "p", start_index: 1007,
        bar_index: 1008, timestamp: "2019-08-21" });
    view.audit.push({ event: "n_invalidated", n_id: view.audit[1].n_id, bar_index: 1008, timestamp: "2019-08-21" });
    const result = withEligibilityNTargets(view, { events: [] });
    assert.ok(result.events[0].levels.every((level) => level.valid_until === "2019-08-20"));
    const observations = nTargetObservations(buildAnnotations(view, result), view.bars, view.asof);
    assert.ok(nTargetAt(observations, "2019-08-12"));
    assert.equal(nTargetAt(observations, "2019-08-21"), null);
});

test("published extensions replace estimates only when their own evidence is known", () => {
    const view = sample();
    view.audit.push({ event: "wave_projection_target", timestamp: "2019-08-21", bar_index: 1008,
        attack: 1005, target_stage: "five_top", target: 5.9, state: "stacking" });
    const event = withEligibilityNTargets(view, { events: [] }).events[0];
    assert.equal(event.levels[2].price, 5.9);
    assert.equal(event.levels[2].estimated, undefined);
    view.asof = "2019-08-20";
    assert.equal(withEligibilityNTargets(view, { events: [] }).events[0].levels[2].estimated, true);
});


test("interval membership never promotes rejected or unknown internal Ns to measurement sources", () => {
    for (const eligible of [false, undefined]) {
        const view = sample();
        view.audit[1].target_eligible = eligible;
        assert.deepEqual(withEligibilityNTargets(view, { events: [] }).events, []);
    }
});

test("published rejection overrides an eligible audit copy without removing N geometry", () => {
    const view = sample();
    const internal = { ...view.audit[1], target_eligible: false, shape: [{ time: "2019-08-09", value: 4.01 }] };
    const theory = { events: [internal] };
    assert.deepEqual(withEligibilityNTargets(view, theory), theory);
});

test("many internal and reformed Ns cannot add targets beside the single floor source", () => {
    const view = sample();
    for (let index = 0; index < 20; index++) {
        view.audit.push({ ...view.audit[1], n_id: `internal:${index}`, target_eligible: false,
            reformed_from: 1003, target_primary: false });
    }
    const result = withEligibilityNTargets(view, { events: [] });
    assert.deepEqual(result.events.map((event) => event.n_id), [view.audit[1].n_id]);
    assert.equal(result.events[0].levels.length, 4);
});


test("a stale display flag cannot bypass an explicit Core rejection", () => {
    assert.equal(isEligibilityNTargetSource({ event: "n_completed", direction: "up",
        target_eligible: false, display_target_eligible: true }), false);
    assert.equal(isEligibilityNTargetSource({ event: "n_completed", direction: "up",
        display_target_eligible: true }), false);
});
