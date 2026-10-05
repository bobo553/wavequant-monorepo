import assert from "node:assert/strict";
import test from "node:test";

import { waveEntryEvidence } from "../public/wave-entry-evidence.js";

const proof = {
    wave_entry_path: "two_t_held_defense_gap_attack",
    wave_gap_trigger: "volume",
    wave_entry_n_date: "2020-06-01",
    wave_entry_two_t_date: "2020-07-03",
    wave_entry_two_t: 3.25,
    wave_b_low_date: "2020-07-17",
    wave_b_low: 3.01,
    wave_defense: 2.7,
    wave_gap_low: 3.15,
    wave_gap_high: 3.31,
    wave_gap_previous_high: 3.1,
    wave_gap_volume: 21_000_000,
    wave_gap_previous_volume: 20_000_000,
    wave_breakout_high: 3.44,
    wave_breakout_date: "2020-07-14",
    wave_a_high_date: "2020-07-06",
    wave_a_high: 3.56,
    wave_a_origin_date: "2020-05-19",
    wave_a_origin: 2.58,
    wave_equal_target: 3.99,
};

test("volume branch identifies observed cumulative volume, not future closing volume", () => {
    const text = waveEntryEvidence([proof]).join("\n");
    assert.match(text, /确认时累计成交量/);
    assert.match(text, /前日全天/);
    assert.doesNotMatch(text, /跳空阳线|> 回调折线高点/);
});

test("breakout-only branch never claims volume was above yesterday", () => {
    const text = waveEntryEvidence([
        { ...proof, wave_gap_trigger: "breakout", wave_gap_high: 3.46, wave_gap_volume: 100 },
    ]).join("\n");
    assert.match(text, /跳空突破/);
    assert.match(text, /2020-07-14 3.4400/);
    assert.doesNotMatch(text, /确认时累计成交量|放量跳空/);
});

test("legacy volume evidence remains readable", () => {
    assert.ok(waveEntryEvidence([{ ...proof, wave_entry_path: "two_t_held_defense_volume_gap" }]).length);
    assert.deepEqual(waveEntryEvidence([]), []);
});

test("non-gap volume body breakout does not claim a gap", () => {
    const text = waveEntryEvidence([
        { ...proof, wave_gap_trigger: "volume_body_breakout", wave_breakout_close: 3.5 },
    ]).join("\n");
    assert.match(text, /放量中大阳线突破/);
    assert.match(text, /无需跳空/);
    assert.doesNotMatch(text, /放量跳空|观察时最低/);
});

test("ordinary one-p rebound never claims two-t or strong extensions", () => {
    const text = waveEntryEvidence([
        {
            ...proof,
            wave_entry_path: "one_p_held_defense_rebound",
            wave_a_class: "ordinary",
            wave_gap_trigger: "rebound_close_breakout",
            wave_entry_one_p: 4.8,
            wave_entry_milestone_date: "2020-06-04",
            wave_entry_two_t: 5,
            wave_breakout_close: 4.99,
            wave_c_0618_target: 3.61572,
        },
    ]).join("\n");
    assert.match(text, /普通 A 浪反弹买点/);
    assert.match(text, /达到一饱/);
    assert.match(text, /2020-05-19 起点 2\.5800/);
    assert.match(text, /0\.618×A = 3\.6157 元.*1×A = 3\.9900 元/);
    assert.doesNotMatch(text, /已到二吐|大 C 浪扩展目标/);
});

test("strong A evidence names the five top goal and waits to compute ten full", () => {
    const lines = waveEntryEvidence([
        {
            ...proof,
            wave_entry_path: "two_t_strong_a_resistance_rebreak",
            wave_a_class: "strong",
            wave_a_origin_date: "2023-06-26",
            wave_a_origin: 4.27,
            wave_a_high_date: "2023-07-21",
            wave_a_high: 5.7,
            wave_b_low_date: "2023-07-24",
            wave_b_low: 4.98,
            wave_two_t_break_date: "2023-07-20",
            wave_resistance_date: "2023-07-21",
            wave_resistance_high: 5.7,
            wave_two_t_body_midpoint: 5.14,
            wave_breakout_date: "2023-07-21",
            wave_breakout_high: 5.7,
            wave_breakout_close: 5.76,
            wave_c_0618_target: 5.86374,
            wave_equal_target: 6.41,
            wave_five_top_target: 5.85,
        },
    ]).join("\n");
    assert.match(lines, /五顶观察位.*5\.8500 元/);
    assert.match(lines, /十满需五顶达成后/);
});

test("published B defense loss and actual duration explain a surviving ordinary A", () => {
    const evidence = {
        ...proof,
        wave_entry_path: "one_p_held_defense_rebound",
        wave_a_class: "ordinary",
        wave_b_broke_squeeze_low: 1,
        wave_b_squeeze_break_date: "2020-06-10",
        wave_b_duration: 25,
        wave_b_consolidation_duration: 9,
        wave_b_elapsed_duration: 34,
        wave_duration_unit: "trading_bars",
    };
    const before = structuredClone(evidence);
    const text = waveEntryEvidence([evidence]).join("\n");
    assert.match(text, /2020-06-10.*跌破轧空低.*原 A 起点保持有效/);
    assert.match(text, /最低价严格跌破 A 起点.*相等仍有效/);
    assert.match(text, /25.*9.*34.*交易日/);
    assert.doesNotMatch(text, /守住轧空低/);
    assert.deepEqual(evidence, before);
});
