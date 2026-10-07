import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { avoidLabelCollisions, buildAnnotations, markerGroups } from "../public/annotations.js";
import { isBottomNTargetSource } from "../public/bottom-n-targets.js";
import { buyNTargetLevels } from "../public/buy-n-targets.js";
import { NStructureOverlay, nStructureBounds } from "../public/n-structure-overlay.js";
import { nTargetAt, nTargetGroup, nTargetLevels, nTargetObservations } from "../public/n-target-focus.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xiangyang_2024_reformed_ns.json", import.meta.url)));
const { view, theory } = fixture;
const items = buildAnnotations(view, theory);

test("same-candle positive Ns have separate marker colors stable across order, future events and invalidation", () => {
    const options = { rules: true };
    const groups = markerGroups(items, options, 1400);
    assert.equal(groups.length, 2);
    assert.equal(new Set(groups.map(({ marker }) => marker.color)).size, 2);
    assert.ok(groups.every(({ items, marker }) => items.length === 1 && marker.text.includes("正 N")));
    avoidLabelCollisions(groups, () => 100);
    assert.ok(groups.every(({ marker }) => marker.text.includes("正 N")));
    const original = new Map(items.map((item) => [item.id, item.color]));
    assert.ok(items.every((item) => item.color));
    const reordered = buildAnnotations(view, { ...theory, events: [...theory.events].reverse() });
    assert.ok(reordered.every((item) => item.color === original.get(item.id)));
    const future = buildAnnotations(view, {
        ...theory,
        events: [
            ...theory.events,
            {
                ...theory.events.find((event) => event.id === items[0].id),
                id: "future-n",
                n_id: "future-n",
                available_at: "2024-08-21",
            },
        ],
    });
    assert.equal(future.length, 2);
    assert.ok(future.every((item) => item.color === original.get(item.id)));
    const oneFailed = buildAnnotations(view, {
        ...theory,
        events: theory.events.map((event) =>
            event.id === items[0].id ? { ...event, n_invalidated_at: view.asof } : event,
        ),
    });
    assert.equal(oneFailed.length, 1);
    assert.equal(oneFailed[0].color, original.get(oneFailed[0].id));
    const withRegime = markerGroups(
        [
            ...oneFailed,
            { id: "other-rule", time: view.asof, kind: "rule", category: "rules", title: "盘坚", priority: 1000 },
        ],
        options,
    );
    assert.equal(withRegime.find((group) => group.id === oneFailed[0].id).marker.color, oneFailed[0].color);
});

test("actual failed July31/August15 Ns disappear while August20 retains two independent markers", () => {
    assert.equal(items.length, 2);
    assert.equal(new Set(items.map((item) => item.id)).size, 2);
    assert.ok(items.every((item) => item.time === "2024-08-20"));
    const auditOnly = ["n_invalidated", "n_target_source_retired"].map((event) => ({
        event,
        id: event,
        time: view.asof,
        available_at: view.asof,
        direction: "up",
        levels: [],
    }));
    assert.equal(buildAnnotations(view, { ...theory, events: [...theory.events, ...auditOnly] }).length, 2);
    assert.deepEqual(
        items.map((item) => item.title),
        ["正 N · 重算自 2024-07-31", "正 N · 重算自 2024-08-15"],
    );
    assert.deepEqual(
        items.map((item) => nTargetLevels(item, view.asof).map((level) => level.price)),
        [
            [4.83, 5.52],
            [4.63, 5.12],
        ],
    );
    for (const old of theory.events.filter((event) => event.n_invalidated_at)) {
        assert.equal(isBottomNTargetSource(old, theory), false);
        const stale = { id: old.id, time: old.time, raw: old, levels: old.levels };
        assert.deepEqual(nTargetLevels(stale, view.asof), []);
        assert.equal(nStructureBounds(stale, view.asof), null);
    }
});

test("hovering either August20 N shows both target groups without deduplication by price", () => {
    const observations = nTargetObservations(items, view.bars, view.asof);
    assert.equal(observations.length, 2);
    for (const item of items) {
        const focus = nTargetAt(observations, view.asof, item.id);
        assert.equal(focus, item);
        assert.equal(nTargetGroup(observations, focus, view.asof).length, 2);
    }
    const equalPrice = { ...items[1], levels: items[0].levels };
    assert.equal(nTargetGroup([{ item: items[0] }, { item: equalPrice }], items[0], view.asof).length, 2);
});

test("historical asof precedes failure and never exposes either future reformation", () => {
    const historical = { ...view, asof: "2024-08-19" };
    const before = buildAnnotations(historical, { ...theory, asof: historical.asof });
    assert.equal(before.length, 1);
    assert.equal(before[0].raw.time, "2024-08-15");
    assert.equal(isBottomNTargetSource(before[0].raw, theory, historical.asof), true);
    assert.equal(nTargetObservations(before, view.bars, historical.asof).length, 1);
    assert.ok(!before.some((item) => item.raw.reformed_from_date));
});

test("same-day source IDs bind buy targets to the correct origin even if event order changes", () => {
    const current = theory.events.filter((event) => event.time === view.asof);
    for (const source of current) {
        const proof = { event: "long_signal", target_source_date: view.asof, target_source_id: source.n_id };
        const levels = buyNTargetLevels({ time: view.asof, decision_evidence: [proof] }, null, view, {
            ...theory,
            events: [...theory.events].reverse(),
        });
        assert.deepEqual(
            levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
            source.levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
        );
        assert.ok(
            levels
                .filter((level) => ["one_p", "two_t"].includes(level.stage))
                .every((level) => level.n_id === source.n_id),
        );
    }
});

test("Canvas draws both original bounds and labels their separate sources", () => {
    const labels = [];
    const labelColors = [];
    const lines = [];
    const context = {
        save() {},
        restore() {},
        setLineDash() {},
        beginPath() {},
        moveTo(x, y) {
            this.from = [x, y];
        },
        lineTo(x, y) {
            lines.push([this.from, [x, y]]);
        },
        stroke() {},
        measureText(text) {
            return { width: text.length * 6 };
        },
        fillText(text) {
            labels.push(text);
            labelColors.push(this.fillStyle);
        },
    };
    const overlay = new NStructureOverlay({ dataset: {} });
    overlay.attached({
        chart: {
            timeScale: () => ({ timeToCoordinate: (time) => view.bars.findIndex((bar) => bar.time === time) * 20 }),
        },
        series: { priceToCoordinate: (value) => 200 - (value - 3) * 100 },
        requestUpdate() {},
    });
    overlay.setStructures(items, view.asof);
    overlay.draw({ useMediaCoordinateSpace: (draw) => draw({ context, mediaSize: { width: 800, height: 400 } }) });
    assert.equal(overlay.container.dataset.nStructureCount, "2");
    assert.equal(labels.length, 4);
    assert.deepEqual(
        labelColors,
        items.flatMap((item) => [item.color, item.color]),
    );
    assert.equal(lines.filter(([from, to]) => from[0] === to[0] && from[1] !== to[1]).length, 1);
    assert.ok(labels.some((label) => label.includes("3.4500") && label.includes("2024-07-31")));
    assert.ok(labels.some((label) => label.includes("3.6500") && label.includes("2024-08-15")));
});

test("actual chart drawLevels displays four independent guides even when two prices match", async () => {
    globalThis.window = { LightweightCharts: { LineSeries: Symbol("line") } };
    globalThis.MutationObserver = class {
        observe() {}
    };
    globalThis.document = { documentElement: {} };
    const { PriceChart } = await import("../public/charts.js");
    const duplicatePrice = {
        ...items[1],
        levels: items[1].levels.map((level, index) => (level.stage === "one_p" ? { ...level, price: 4.83 } : level)),
    };
    for (const sources of [items, [...items].reverse(), [items[0], duplicatePrice]]) {
        const observations = nTargetObservations(sources, view.bars, view.asof);
        const fake = {
            data: view,
            theory,
            selected: sources[0],
            focusedNTarget: sources[0],
            nTargetObservations: observations,
            options: { levels: true, rules: true },
            levelLines: [],
            container: { dataset: {} },
            waveEndpointOverlay: { setPoints() {} },
            nStructureOverlay: {
                setStructure() {},
                setStructures(value) {
                    this.items = value;
                },
            },
            targetGuideOverlay: {
                setGuides(value) {
                    this.guides = value;
                },
            },
            chart: {
                addSeries(_type, options) {
                    return {
                        options,
                        setData(points) {
                            this.points = points;
                        },
                    };
                },
                removeSeries() {},
            },
            clearLevels: PriceChart.prototype.clearLevels,
            displayedWaveProjection: PriceChart.prototype.displayedWaveProjection,
        };
        PriceChart.prototype.drawLevels.call(fake);
        assert.equal(fake.targetGuideOverlay.guides.length, 4);
        assert.equal(fake.nStructureOverlay.items.length, 2);
        for (const guide of fake.targetGuideOverlay.guides) {
            const source = sources.find((source) => guide.name.includes(source.raw.reformed_from_date));
            assert.ok(source);
            assert.equal(guide.color, source.color);
            const lines = fake.levelLines.filter(
                ({ options, points }) => !options.lastValueVisible && points[0].value === guide.price,
            );
            assert.ok(lines.some(({ options }) => options.color === source.color && options.lineStyle === 2));
        }
        assert.deepEqual(
            fake.targetGuideOverlay.guides.map((guide) => guide.price).sort(),
            sources.flatMap((source) => nTargetLevels(source, view.asof).map((level) => level.price)).sort(),
        );
        for (const source of sources) {
            fake.focusedNTarget = null;
            fake.options.fills = true;
            fake.selectedNTargetId = () => null;
            fake.selected = {
                id: `fill:${source.id}`,
                time: view.asof,
                kind: "fill",
                category: "fills",
                side: "BUY",
                levels: nTargetLevels(source, view.asof).map((level) => ({ ...level, n_id: source.raw.n_id })),
            };
            PriceChart.prototype.drawLevels.call(fake);
            assert.equal(fake.targetGuideOverlay.guides.length, 2);
            assert.ok(fake.targetGuideOverlay.guides.every((guide) => guide.color === source.color));
            assert.ok(fake.levelLines.every(({ options }) => options.color === source.color));
        }
    }
});
