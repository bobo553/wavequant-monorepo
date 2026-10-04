import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { buildAnnotations, reasonText } from "../public/annotations.js";
import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import { label } from "../public/labels.js";
import { numberedTradeReasons } from "../public/trade-reasons.js";
import { appendTradeEvidence } from "../public/trade-review.js";
import { waveEntryEvidence } from "../public/wave-entry-evidence.js";

const evidence = {
    event: "long_transition_evidence",
    channel: "combined_a_pullback_breakout",
    buy_point_type: "combined_a_pullback_breakout",
    time: "2025-04-03",
    combined_a_origin_date: "2024-02-08",
    combined_a_origin_price: 4.219,
    combined_a_top_date: "2025-01-03",
    combined_a_top_price: 9.3168,
    combined_a_known_date: "2025-01-22",
    combined_a_internal_pullback_sessions: 70,
    combined_a_child_pullback_sessions: 9,
    combined_a_pullback_sessions: 58,
    combined_a_pullback_date: "2025-01-13",
    combined_a_pullback_low: 5.8879,
    combined_a_two_thirds_price: 5.9183,
    combined_a_minimum_close: 6.0242,
    combined_a_breakout_date: "2025-03-21",
    combined_a_breakout_price: 7.0511,
    combined_a_breakout_known_date: "2025-03-26",
    confirmation_close: 7.1604,
    breakout_volume: 20_135_200,
    previous_volume: 12_000_000,
    breakout_body_pct: 0.1102,
    breakout_body_ratio: 1,
    stop: 5.8879,
    target: 9.3168,
};

const marker = {
    id: "combined-a-buy",
    kind: "fill",
    side: "BUY",
    time: "2025-04-03",
    signal_time: "2025-04-03",
    reason: "system_combined_a_pullback_breakout",
    price: 7.1604,
    stop: evidence.stop,
    target: evidence.target,
    decision_evidence: [
        { event: "long_signal", channel: evidence.channel, timestamp: "2025-04-03T15:00:00+08:00" },
        evidence,
    ],
};

test("combined A trade reasons explain the OR duration, close defense, volume and known breakout", () => {
    assert.equal(reasonText(marker.reason), "组合A回调放量突破");
    assert.equal(label(evidence.channel), "组合A回调放量突破");
    assert.equal(label("combined_a_candidate"), "组合A回调候选");
    assert.equal(label("combined_a_invalidated"), "组合A回调候选失效");
    const text = waveEntryEvidence([evidence]).join("\n");
    assert.match(text, /2024-02-08.*4\.2190.*2025-01-03.*9\.3168.*2025-01-22.*可知/);
    assert.match(text, /回调及整理 58 个交易日.*组合内部回调 70.*或.*子级回调 9.*满足其一/);
    assert.match(text, /最低收盘 6\.0242.*≥.*2\/3.*5\.9183/);
    assert.match(text, /2025-04-03.*收盘 7\.1604.*严格突破.*2025-03-21.*7\.0511.*2025-03-26.*可知/);
    assert.match(text, /20,135,200.*>.*12,000,000/);
    assert.match(text, /实体\/开盘 11\.02%.*≥3%.*实体\/振幅 100\.00%.*≥60%/);
    assert.match(text, /防守.*5\.8879.*目标.*9\.3168/);
    assert.doesNotMatch(text, /跳空|第一类|undefined/);
    assert.ok(numberedTradeReasons(marker).some((line) => /严格突破/.test(line)));
    assert.doesNotMatch(numberedTradeReasons(marker).join("\n"), /undefined|—/);
});

test("missing child duration stays absent rather than becoming a zero-day comparison", () => {
    const text = waveEntryEvidence([{ ...evidence, combined_a_child_pullback_sessions: null }]).join("\n");
    assert.match(text, /组合内部回调 70/);
    assert.match(text, /子级回调时长未提供/);
    assert.doesNotMatch(text, /子级回调 0|子级回调 null|undefined/);
    assert.deepEqual(waveEntryEvidence([{ ...evidence, channel: "other", buy_point_type: "other" }]), []);
});

test("engine ISO timestamp and gap branch name the dated prior high without inventing a rebound pivot", () => {
    const text = waveEntryEvidence([
        {
            ...evidence,
            time: undefined,
            timestamp: "2025-04-03T15:00:00+08:00",
            combined_a_breakout_type: "gap",
            combined_a_breakout_date: "2025-04-02",
            combined_a_breakout_known_date: "2025-04-02",
        },
    ]).join("\n");
    assert.match(text, /2025-04-03 收盘.*严格突破前日最高价 2025-04-02.*2025-04-02 起可知/);
    assert.match(text, /缺口未回补/);
    assert.doesNotMatch(text, /回调参考高|undefined/);
});

test("combined A signal annotation, trade detail and clipboard share its dated proof", () => {
    const view = {
        symbol: "sh.601086",
        variant: "lecture_v3",
        asof: "2025-04-03",
        bars: [{ time: "2025-04-03", close: 7.1604 }],
        markers: [{ ...marker, kind: "signal", side: "LONG" }],
        backtest: { start: "2024-01-01" },
    };
    const [annotation] = buildAnnotations(view, {
        asof: view.asof,
        events: [
            {
                event: "n_completed",
                direction: "up",
                time: view.asof,
                available_at: view.asof,
                one_p: 7.5,
                two_t: 8.5,
            },
        ],
    });
    assert.match(annotation.description, /组合A回调放量突破.*回调及整理 58.*严格突破/);
    assert.deepEqual(
        annotation.levels.map((level) => level.price),
        [marker.price, marker.stop, marker.target],
    );
    assert.match(formatFilledTradeCopy(view, marker, "V3", "20%"), /2\/3.*严格突破.*20,135,200/s);
    const dom = new JSDOM("<section></section>");
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.querySelector("section");
        appendTradeEvidence(panel, marker);
        assert.match(panel.textContent, /回调及整理 58.*严格突破/s);
        assert.doesNotMatch(panel.textContent, /第一类买点|undefined 级|翻多 undefined|NaN/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});
