import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { buyNTargetLevels } from "../public/buy-n-targets.js";
import { nTargetAt, nTargetObservations } from "../public/n-target-focus.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2024_n_target_sources.json", import.meta.url)));
const annotations = buildAnnotations(fixture.view, fixture.theory);
const observations = nTargetObservations(annotations, fixture.view.bars, fixture.view.asof);
const original = observations.find(({ item }) => item.time === "2024-03-05").item;
const buy = annotations.find((item) => item.kind === "fill");
const expectedPrices = [5.289057414759589, 5.783644844765735];

function targets(levels) {
    return levels.filter((level) => ["one_p", "two_t"].includes(level.stage));
}

test("real overlapping N history keeps February 29 through March 5 focus on its own breakout N", () => {
    assert.equal(original.raw.mother_pullback_confirmed, true);
    assert.deepEqual(
        original.raw.shape.map((point) => point.time),
        ["2024-02-29", "2024-03-04", "2024-03-04", "2024-03-05"],
    );
    assert.equal(original.raw.shape[0].value, 4.299882554747297);
    assert.equal(original.raw.box_anchor, 4.794469984753443);
    assert.deepEqual(
        targets(original.levels).map((level) => level.price),
        expectedPrices,
    );
    for (const bar of fixture.view.bars.filter((bar) => bar.time >= "2024-02-29" && bar.time <= "2024-03-18"))
        assert.equal(nTargetAt(observations, bar.time)?.id, original.id, bar.time);
    for (const time of ["2024-03-19", "2024-03-20"]) assert.equal(nTargetAt(observations, time)?.time, time);
    assert.equal(nTargetAt(observations, "2024-03-20", original.id)?.id, original.id);
    assert.equal(nTargetAt(observations, "2024-03-20", null, original.id)?.id, original.id);
    assert.equal(nTargetAt(nTargetObservations(annotations, fixture.view.bars, "2024-03-04"), "2024-03-05"), null);
});

test("the actual March 20 signal and fill use March 5 attack measurements instead of the same-day N", () => {
    for (const marker of annotations.filter((item) => item.kind === "fill" || item.kind === "signal")) {
        const levels = targets(marker.levels);
        assert.deepEqual(
            levels.map((level) => level.price),
            expectedPrices,
        );
        assert.ok(levels.every((level) => level.n_date === "2024-03-05"));
        assert.equal(
            marker.decision_evidence.find((proof) => proof.event === "long_signal").attack,
            original.raw.bar_index,
        );
    }
});

test("a cropped view can resolve the original attack by its global event index when no attack date is present", () => {
    const marker = {
        ...buy,
        decision_evidence: buy.decision_evidence.map((proof) => {
            const { attack_date: _attackDate, ...remaining } = proof;
            return remaining;
        }),
    };
    assert.ok(
        marker.decision_evidence.find((proof) => proof.event === "long_signal").attack > fixture.view.bars.length,
    );
    assert.deepEqual(
        targets(buyNTargetLevels(marker, null, fixture.view, fixture.theory)).map((level) => level.price),
        expectedPrices,
    );
});

test("missing or future original-N evidence never falls back to unrelated same-day targets", () => {
    for (const events of [
        fixture.theory.events.filter((event) => event.time !== "2024-03-05"),
        fixture.theory.events.map((event) =>
            event.time === "2024-03-05" ? { ...event, available_at: "2024-03-21" } : event,
        ),
    ])
        assert.deepEqual(buyNTargetLevels(buy, null, fixture.view, { ...fixture.theory, events }), []);
});
