import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";
import { isBottomNTargetSource } from "../public/bottom-n-targets.js";
import { buyNTargetLevels } from "../public/buy-n-targets.js";
import { nTargetAt, nTargetGuide, nTargetObservations } from "../public/n-target-focus.js";
import { waveCProjection, waveCProjectionsFromStructure } from "../public/wave-c-projection.js";

const fixture = JSON.parse(readFileSync(new URL("./fixtures/xinhua_2023_bottom_n_targets.json", import.meta.url)));
const annotations = buildAnnotations(fixture.view, fixture.theory);
const root = annotations.find((item) => item.raw?.time === "2023-05-15");
const folded = annotations.find((item) => item.raw?.time === "2023-05-16");

test("actual May 16 hover selects the May 10 bottom launch instead of the cross-cycle N", () => {
    const observations = nTargetObservations(annotations, fixture.view.bars, fixture.view.asof);
    assert.equal(nTargetAt(observations, "2023-05-16"), root);
    assert.equal(nTargetAt(observations, "2023-05-16", folded.id), root);
    assert.equal(nTargetAt(observations, "2023-05-16", null, folded.id), root);
    assert.deepEqual(
        root.levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
        [7.075628335394026, 7.953773364180446],
    );
    assert.ok(!folded.levels.some((level) => ["one_p", "two_t", "five_top", "ten_full"].includes(level.stage)));
    assert.match(folded.description, /不另算一饱、二吐、五顶、十满/);
    assert.match(folded.description, /2023-05-15/);
});

test("all named guide labels disclose the frozen bottom and launch dates", () => {
    for (const level of root.levels.filter((level) => ["one_p", "two_t"].includes(level.stage))) {
        const guide = nTargetGuide(root, level, fixture.view.bars, fixture.view.asof);
        assert.match(guide.name, /底部 2023-05-10 · 正 N 2023-05-15/);
    }
    assert.match(root.description, /已确认下跌段最低点的首个正 N/);
});

test("a strict May 18 origin break ends hover without erasing earlier historical ownership", () => {
    const observations = nTargetObservations(annotations, fixture.view.bars, fixture.view.asof);
    assert.equal(observations.find(({ item }) => item === root).to, "2023-05-17");
    assert.equal(nTargetAt(observations, "2023-05-17"), root);
    assert.equal(nTargetAt(observations, "2023-05-18", root.id), null);
});

test("buy markers resolve an internal attack to its declared bottom source, including a cropped global index", () => {
    const event = folded.raw;
    for (const proof of [{ attack: event.bar_index }, { attack: event.bar_index, attack_date: event.time }]) {
        const levels = buyNTargetLevels(
            {
                time: event.time,
                decision_evidence: [
                    { event: "long_signal", squeeze_confirmation: "resistance_attack_bar_break", ...proof },
                ],
            },
            null,
            fixture.view,
            fixture.theory,
        );
        assert.deepEqual(
            levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
            [7.075628335394026, 7.953773364180446],
        );
        assert.ok(levels.every((level) => level.n_date === "2023-05-15"));
        assert.match(levels.find((level) => level.stage === "one_p").display_name, /底部 2023-05-10/);
        for (const level of levels.filter((level) =>
            ["one_p", "two_t", "five_top", "ten_full"].includes(level.stage),
        )) {
            assert.match(level.display_name, /底部 2023-05-10 · 正 N 2023-05-15/);
            assert.equal(level.valid_until, "2023-05-17");
        }
    }
});

test("a missing, future or retired source cannot fall back to an unrelated N or stale prices", () => {
    const marker = {
        time: "2023-05-16",
        decision_evidence: [
            { event: "long_signal", attack_date: "2023-05-16", squeeze_confirmation: "resistance_attack_bar_break" },
        ],
    };
    for (const transform of [
        (events) => events.filter((event) => event.time !== root.time),
        (events) =>
            events.map((event) => (event.time === root.time ? { ...event, available_at: "2023-05-17" } : event)),
        (events) =>
            events.map((event) =>
                event.time === root.time
                    ? { ...event, levels: event.levels.map((level) => ({ ...level, valid_until: "2023-05-15" })) }
                    : event,
            ),
    ])
        assert.deepEqual(
            buyNTargetLevels(marker, null, fixture.view, {
                ...fixture.theory,
                events: transform(fixture.theory.events),
            }),
            [],
        );
});

test("the signal's live measurement source overrides the older attack's expired source", () => {
    const marker = {
        time: "2023-05-16",
        decision_evidence: [
            {
                event: "long_signal",
                attack_date: "2023-05-04",
                squeeze_confirmation: "resistance_attack_bar_break",
                target_source_date: "2023-05-15",
            },
        ],
    };
    const levels = buyNTargetLevels(marker, null, fixture.view, fixture.theory);
    assert.deepEqual(
        levels.filter((level) => ["one_p", "two_t"].includes(level.stage)).map((level) => level.price),
        [7.075628335394026, 7.953773364180446],
    );
    assert.ok(levels.every((level) => level.n_date === "2023-05-15"));
    marker.decision_evidence[0].target_source_date = null;
    assert.deepEqual(buyNTargetLevels(marker, null, fixture.view, fixture.theory), []);
});

test("explicit ineligible evidence wins over legacy one/two/five/ten prices on a stale marker", () => {
    const stale = { ...root, raw: { ...root.raw, target_eligible: false } };
    assert.deepEqual(nTargetObservations([stale], fixture.view.bars, fixture.view.asof), []);
    assert.equal(isBottomNTargetSource(stale.raw, fixture.theory), false);
    assert.equal(waveCProjection(fixture.view.bars, [stale.raw], "2023-05-16"), null);
});

test("the new policy requires explicit Core eligibility while sealed legacy contracts remain readable", () => {
    const { target_eligible: _eligibility, ...unqualified } = root.raw;
    assert.equal(isBottomNTargetSource(unqualified, fixture.theory), false);
    assert.equal(isBottomNTargetSource(unqualified), true);
    const items = buildAnnotations(fixture.view, { ...fixture.theory, events: [unqualified] });
    assert.deepEqual(nTargetObservations(items, fixture.view.bars, fixture.view.asof), []);
});

test("the current renderer cannot synthesize a measured N from an arbitrary lecture seed", () => {
    const old = JSON.parse(readFileSync(new URL("./fixtures/xianfeng_2020_c_wave.json", import.meta.url)));
    assert.ok(old.theory);
    const history = old.bars || old.view?.bars;
    const theory = { ...old.theory, n_target_policy: fixture.theory.n_target_policy, events: [] };
    assert.deepEqual(waveCProjectionsFromStructure(history, theory), []);
});
