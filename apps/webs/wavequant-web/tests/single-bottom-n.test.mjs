import assert from "node:assert/strict";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import {
    isBottomNTargetSource,
    isSelectedStructuralNShape,
    isStructuralNTargetSource,
    matchesNTargetSourceId,
    STRUCTURAL_N_POLICY,
} from "../public/bottom-n-targets.js";
import { buyNTargetLevels } from "../public/buy-n-targets.js";
import { isEligibilityNTargetSource, withEligibilityNTargets } from "../public/eligibility-n-targets.js";
import { nStructureBounds } from "../public/n-structure-overlay.js";
import { nTargetAt, nTargetGroup, nTargetLevels, nTargetObservations } from "../public/n-target-focus.js";

function sample() {
    const bars = [
        ["2024-03-05", 5.4, 5.6, 5.251, 5.5],
        ["2024-03-06", 5.5, 6.0, 5.4, 5.9],
        ["2024-03-07", 5.8, 5.9, 5.5, 5.7],
        ["2024-03-11", 5.8, 6.1, 5.7, 6.0],
        ["2024-03-12", 5.68, 5.81, 5.64, 5.81],
    ].map(([time, open, high, low, close]) => ({ time, open, high, low, close, volume: 100 }));
    const selected = {
        event: "n_completed", direction: "up", id: "n:bottom", n_id: "bottom", price: 6,
        time: "2024-03-11", available_at: "2024-03-11", bar_index: 3,
        structural_target: true, structural_target_selected: true,
        structural_target_owner: "bottom", structural_trend_id: "trend:1",
        target_eligible: false, box_anchor: 6.1,
        shape: [
            { time: "2024-03-05", value: 5.251 },
            { time: "2024-03-06", value: 6 },
            { time: "2024-03-07", value: 5.5 },
            { time: "2024-03-11", value: 6.1 },
        ],
        levels: [
            { stage: "one_p", name: "一饱", price: 6.949, anchor_at: "2024-03-07", available_at: "2024-03-11" },
            { stage: "two_t", name: "二吐", price: 7.798, anchor_at: "2024-03-07", available_at: "2024-03-11" },
        ],
    };
    const rejected = Array.from({ length: 3 }, (_, index) => ({
        ...structuredClone(selected), id: `n:internal:${index}`, n_id: `internal:${index}`,
        structural_target_selected: false, target_eligible: true, display_target_eligible: true,
    }));
    const view = {
        bars, asof: "2024-03-12", timeframe: "1d", markers: [],
        audit: [
            { event: "trading_interval_opened", trend_level: 2, source_path: "p", bar_index: 3,
                timestamp: "2024-03-11", start_low_time: "2024-03-05" },
            ...rejected.map((event) => ({ ...event, timestamp: event.time, origin: 0, neckline: 1, pullback: 2,
                known_at: 3, one_p: 6.949, two_t: 7.798 })),
        ],
    };
    return { view, selected, rejected, theory: {
        asof: view.asof, structural_n_policy: STRUCTURAL_N_POLICY,
        n_target_policy: "level_one_floor_n_exclusive_v3", events: [...rejected, selected],
    } };
}

test("one selected bottom source rejects repeated 7.798 targets despite stale structural, eligibility and audit flags", () => {
    const { view, selected, rejected, theory } = sample();
    const original = structuredClone({ view, theory });
    const projected = withEligibilityNTargets(view, theory);
    assert.equal(projected, theory);
    const items = buildAnnotations(view, projected);
    assert.deepEqual(items.map((item) => item.raw.n_id), [selected.n_id]);
    const observations = nTargetObservations(items, view.bars, view.asof);
    const focus = nTargetAt(observations, "2024-03-12");
    assert.equal(focus.raw.n_id, selected.n_id);
    assert.deepEqual(nTargetGroup(observations, focus, view.asof), [focus]);
    assert.deepEqual(nTargetLevels(focus, view.asof).map((level) => level.price), [6.949, 7.798]);
    for (const raw of rejected) {
        const item = { id: raw.id, time: raw.time, raw, levels: raw.levels };
        assert.equal(isBottomNTargetSource(raw, theory), false);
        assert.equal(isStructuralNTargetSource(raw, theory), false);
        assert.equal(isEligibilityNTargetSource(raw), false);
        assert.deepEqual(nTargetLevels(item, view.asof), []);
        assert.deepEqual(nTargetGroup([{ item }], item, view.asof), []);
        assert.equal(nStructureBounds(item, view.asof), null);
        assert.equal(isSelectedStructuralNShape({ n_id: raw.n_id, direction: "up", points: raw.shape }, theory), false);
    }
    assert.equal(isSelectedStructuralNShape({ n_id: selected.n_id, direction: "up", points: selected.shape }, theory), true);
    assert.equal(isSelectedStructuralNShape({ direction: "down", points: selected.shape }, theory), true);
    assert.equal(isSelectedStructuralNShape({ direction: "up", points: selected.shape }, theory), false);
    assert.deepEqual({ view, theory }, original);
});

test("a new-policy snapshot without explicit selection cannot restore an N from eligible audit rows", () => {
    const { view, selected, theory } = sample();
    const { structural_target_selected: _selection, ...unknown } = selected;
    unknown.target_eligible = true;
    for (const events of [[], [unknown]]) {
        const incomplete = { ...theory, events };
        assert.equal(withEligibilityNTargets(view, incomplete), incomplete);
        assert.deepEqual(buildAnnotations(view, incomplete), []);
    }
    assert.equal(isStructuralNTargetSource(unknown, theory), false);
    assert.equal(isBottomNTargetSource(unknown, theory), false);
    const { structural_n_policy: _policy, ...legacy } = theory;
    assert.equal(buildAnnotations(view, { ...legacy, events: [unknown] }).length, 1);
    assert.equal(isStructuralNTargetSource(unknown, legacy), true);
});

test("legacy audit projection still respects an explicit structural rejection", () => {
    const { view, rejected } = sample();
    for (const events of [[], rejected]) {
        const theory = { events };
        assert.deepEqual(withEligibilityNTargets(view, theory), theory);
    }
});

test("same-day owners remain independently focusable while duplicates of one owner never expand the focus", () => {
    const { view, selected, theory } = sample();
    const other = { ...structuredClone(selected), id: "n:next-trend", n_id: "next-trend",
        structural_target_owner: "next-trend", structural_trend_id: "trend:2" };
    const items = buildAnnotations(view, { ...theory, events: [other, selected] });
    const observations = nTargetObservations(items, view.bars, view.asof);
    assert.equal(observations.length, 2);
    for (const item of items) {
        const focus = nTargetAt(observations, view.asof, item.id);
        assert.equal(focus, item);
        assert.deepEqual(nTargetGroup(observations, focus, view.asof), [item]);
        const duplicate = { ...item, id: `${item.id}:transport-copy` };
        assert.deepEqual(nTargetGroup([...observations, { item: duplicate }], focus, view.asof), [item]);
    }
    const before = { ...view, asof: "2024-03-07" };
    assert.deepEqual(buildAnnotations(before, theory), []);
});

test("buy targets resolve only canonical IDs and explicitly published aliases of the selected geometry", () => {
    const { view, selected, rejected, theory } = sample();
    const canonical = { ...selected, target_eligible: true, one_p: 6.949, two_t: 7.798,
        structural_target_aliases: ["trading:original"] };
    const unrelated = { ...canonical, id: "n:unrelated", n_id: "unrelated",
        structural_target_owner: "unrelated", structural_target_aliases: [] };
    const internal = { ...rejected[0], structural_target_aliases: ["unpublished:internal"] };
    assert.equal(matchesNTargetSourceId(canonical, canonical.n_id), true);
    assert.equal(matchesNTargetSourceId(canonical, "trading:original"), true);
    assert.equal(matchesNTargetSourceId(canonical, internal.n_id), false);
    assert.equal(matchesNTargetSourceId(internal, "unpublished:internal"), false);
    assert.equal(matchesNTargetSourceId(internal, internal.n_id), false);
    for (const events of [[unrelated, internal, canonical], [canonical, internal, unrelated]]) {
        for (const id of [canonical.n_id, "trading:original", internal.n_id, "unpublished:internal"]) {
            for (const date of [canonical.time, undefined]) {
                const marker = { time: view.asof, decision_evidence: [{ event: "long_signal",
                    ...(date ? { target_source_date: date } : {}), target_source_id: id }] };
                const levels = buyNTargetLevels(marker, null, view, { ...theory, events });
                if ([canonical.n_id, "trading:original"].includes(id)) {
                    const targets = levels.filter((level) => ["one_p", "two_t"].includes(level.stage));
                    assert.deepEqual(targets.map((level) => level.price), [6.949, 7.798]);
                    assert.ok(targets.every((level) => level.n_id === canonical.n_id));
                } else assert.deepEqual(levels, []);
            }
        }
    }
});

test("chart draws the single published owner once and ignores stale fill prices and unselected N levels", async () => {
    globalThis.window = { LightweightCharts: { LineSeries: Symbol("line") } };
    globalThis.MutationObserver = class { observe() {} };
    globalThis.document = { documentElement: {} };
    const { PriceChart } = await import("../public/charts.js");
    const { view, rejected, theory } = sample();
    const [source] = buildAnnotations(view, theory);
    const observations = nTargetObservations([source], view.bars, view.asof);
    const fake = {
        data: view, theory, focusedNTarget: source, nTargetObservations: observations,
        selected: { id: "fill", time: view.asof, kind: "fill", category: "fills", side: "BUY",
            levels: source.levels.map((level) => ({ ...level, n_id: source.raw.n_id, price: level.price + 1 })) },
        selectedNTargetId: PriceChart.prototype.selectedNTargetId,
        options: { levels: true, rules: true, fills: true }, levelLines: [], container: { dataset: {} },
        waveEndpointOverlay: { setPoints() {} },
        nStructureOverlay: { setStructure() {}, setStructures(items) { this.items = items; } },
        targetGuideOverlay: { setGuides(guides) { this.guides = guides; } },
        chart: {
            addSeries(_type, options) { return { options, setData(points) { this.points = points; } }; },
            removeSeries() {},
        },
        clearLevels: PriceChart.prototype.clearLevels,
        displayedWaveProjection: PriceChart.prototype.displayedWaveProjection,
    };
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(fake.nStructureOverlay.items, [source]);
    assert.deepEqual(fake.targetGuideOverlay.guides.map((guide) => guide.price), [6.949, 7.798]);
    assert.equal(fake.levelLines.length, 2);
    source.raw.structural_target_aliases = ["trading:original"];
    fake.selected.levels = fake.selected.levels.map((level) => ({ ...level, n_id: "trading:original" }));
    assert.equal(fake.selectedNTargetId(), source.id);
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(fake.targetGuideOverlay.guides.map((guide) => guide.price), [6.949, 7.798]);
    assert.equal(fake.levelLines.length, 2);
    fake.selected.levels = fake.selected.levels.map((level) => ({ ...level, n_id: rejected[0].n_id }));
    fake.focusedNTarget = null;
    assert.equal(fake.selectedNTargetId(), undefined);
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(fake.targetGuideOverlay.guides, []);
    fake.selected = { id: rejected[0].id, time: rejected[0].time, kind: "rule", category: "rules",
        side: "up", raw: rejected[0], levels: rejected[0].levels };
    fake.focusedNTarget = null;
    PriceChart.prototype.drawLevels.call(fake);
    assert.deepEqual(fake.targetGuideOverlay.guides, []);
    assert.deepEqual(fake.levelLines, []);
});
