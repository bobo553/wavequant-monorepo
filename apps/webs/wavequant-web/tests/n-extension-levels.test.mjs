import assert from "node:assert/strict";
import test from "node:test";

import { buildAnnotations } from "../public/annotations.js";

test("N extension levels only appear from their own availability date without changing the N date", () => {
    const view = {
        asof: "2026-08-07",
        bars: [{ time: "2026-07-27" }, { time: "2026-08-07" }],
        markers: [],
    };
    const event = {
        id: "n-july-27",
        event: "n_completed",
        direction: "up",
        time: "2026-07-27",
        available_at: "2026-07-27",
        price: 10,
        levels: [
            { name: "2T 投影", price: 12 },
            { name: "五顶投影", price: 15, available_at: "2026-08-07" },
            { name: "十满投影", price: 20, available_at: "2026-08-07" },
        ],
    };
    const theory = { asof: "2026-07-27", events: [event] };
    assert.deepEqual(
        buildAnnotations(view, theory)[0].levels.map((level) => level.name),
        ["2T 投影"],
    );
    const current = buildAnnotations(view, { ...theory, asof: "2026-08-07" })[0];
    assert.equal(current.time, "2026-07-27");
    assert.equal(current.levels.length, 3);
    assert.equal(event.levels.length, 3);
    assert.equal(
        buildAnnotations({ ...view, asof: "2026-07-27" }, { ...theory, asof: "2026-08-07" })[0].levels.length,
        1,
    );
});

test("strong A buy displays five top immediately and ten full only when known", () => {
    const proof = {
        event: "long_signal",
        wave_entry_path: "two_t_strong_a_resistance_rebreak",
        wave_entry_n_date: "2023-07-03",
        wave_five_top_target: 5.85,
        wave_equal_target: 6.41,
    };
    const marker = {
        id: "buy-july-26", kind: "fill", side: "BUY", status: "filled",
        time: "2023-07-26", price: 5.76, target: 5.86374,
        signal_time: "2023-07-26", decision_evidence: [proof],
    };
    const view = {
        asof: "2023-07-26", markers: [marker],
        bars: [{ time: "2023-07-03" }, { time: "2023-07-26" }],
    };
    const n = {
        event: "n_completed", direction: "up", time: "2023-07-03", available_at: "2023-07-03",
        levels: [
            { name: "五顶（强A再攻击 · 观察）", price: 5.85, stage: "five_top", available_at: "2023-07-26" },
            { name: "十满（叠箱 · 推演中）", price: 8.41, stage: "ten_full", available_at: "2023-07-27" },
        ],
    };
    const early = buildAnnotations(view, { asof: "2023-07-26", events: [n] })
        .find((item) => item.id === marker.id);
    assert.deepEqual(early.levels.map((level) => level.name), [
        "成交价", "原始目标投影", "五顶（强A再攻击 · 观察）", "B+1×A 观察",
    ]);
    const later = buildAnnotations({ ...view, asof: "2023-07-27", bars: [...view.bars, { time: "2023-07-27" }] },
        { asof: "2023-07-27", events: [n] }).find((item) => item.id === marker.id);
    assert.equal(later.levels.find((level) => level.stage === "ten_full")?.price, 8.41);
    assert.equal(later.levels.find((level) => level.stage === "ten_full")?.available_at, "2023-07-27");
    const fallback = buildAnnotations(view, null).find((item) => item.id === marker.id);
    assert.equal(fallback.levels.find((level) => level.stage === "five_top")?.price, 5.85);
});
