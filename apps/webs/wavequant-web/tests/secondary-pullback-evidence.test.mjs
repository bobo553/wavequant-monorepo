import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { reasonText } from "../public/annotations.js";
import { label } from "../public/labels.js";
import { secondaryReclaimEvidence } from "../public/secondary-reclaim-evidence.js";
import { appendTradeEvidence } from "../public/trade-review.js";
import { waveEntryEvidence } from "../public/wave-entry-evidence.js";

const proof = {
    buy_point_type: "secondary_deep_pullback_reclaim",
    secondary_high_date: "2022-06-24",
    secondary_high: 6.67,
    secondary_high_known_date: "2022-07-04",
    secondary_pullback_low_date: "2022-07-18",
    secondary_pullback_low: 4.81,
    secondary_resistance_date: "2022-07-13",
    secondary_resistance_high: 5.05,
    reclaim_ceiling_date: "2022-07-18",
    reclaim_ceiling: 5.08,
    confirmation_close: 5.45,
    reclaim_type: "body",
    reclaim_body_fraction: 0.37 / 5.08,
    breakout_volume: 33044829,
    previous_volume: 10050400,
    stop: 4.81,
    target: 6.67,
};

test("published deep pullback explains its own body reclaim and actual defense", () => {
    const lines = secondaryReclaimEvidence([proof]);
    assert.equal(lines.length, 3);
    assert.deepEqual(waveEntryEvidence([proof]), lines);
    assert.match(lines[0], /2022-06-24.*6\.6700.*2022-07-04.*2022-07-18.*4\.8100/);
    assert.match(lines[1], /放量阳线实体收复.*5\.4500.*5\.0800.*7\.28%.*33,044,829.*10,050,400/);
    assert.doesNotMatch(lines.join("\n"), /未回补跳空|新 N/);
    assert.match(lines[2], /4\.8100.*6\.6700.*正式趋势低点/);
    assert.match(secondaryReclaimEvidence([{ ...proof, stop: undefined }])[0], /缺少完整/);
});

test("trade review does not fabricate an N or formal alternation chain", () => {
    const dom = new JSDOM("<div id='panel'></div>");
    const previousDocument = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.getElementById("panel");
        appendTradeEvidence(panel, {
            kind: "signal",
            side: "BUY",
            reason: "system_secondary_deep_pullback_reclaim",
            decision_evidence: [proof],
        });
        assert.match(panel.textContent, /二级深回调放量收复/);
        assert.doesNotMatch(panel.textContent, /第一类买点|undefined|NaN|新 N/);
    } finally {
        globalThis.document = previousDocument;
        dom.window.close();
    }
    assert.equal(reasonText("system_secondary_deep_pullback_reclaim"), "二级深回调放量收复");
    assert.equal(label("secondary_deep_pullback_reclaim"), "二级深回调放量收复");
});
