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
        },
    ]).join("\n");
    assert.match(text, /普通 A 浪反弹买点/);
    assert.match(text, /达到一饱/);
    assert.doesNotMatch(text, /已到二吐|大 C 浪扩展目标/);
});
