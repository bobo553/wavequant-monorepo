import assert from "node:assert/strict";
import test from "node:test";

import { JSDOM } from "jsdom";

import { reasonText } from "../public/annotations.js";
import { label } from "../public/labels.js";
import { secondaryReclaimEvidence } from "../public/secondary-reclaim-evidence.js";
import { tradeReasonItems } from "../public/trade-reasons.js";
import { appendTradeEvidence } from "../public/trade-review.js";
import { waveEntryEvidence } from "../public/wave-entry-evidence.js";

const proof = {
    event: "long_transition_evidence",
    buy_point_type: "secondary_resistance_reclaim",
    trend_level: 2,
    secondary_high_date: "2024-12-11",
    secondary_high: 8.17,
    secondary_high_known_date: "2025-01-21",
    secondary_attack_date: "2025-02-28",
    secondary_resistance_date: "2025-03-03",
    secondary_resistance_high: 8.77,
    secondary_resistance_known_date: "2025-03-03",
    secondary_reclaim_type: "body",
    secondary_reclaim_close: 8.89,
    secondary_reclaim_body_fraction: 0.19 / 8.7,
    breakout_volume: 126499296,
    previous_volume: 80806757,
    secondary_pullback_low_date: "2025-03-04",
    secondary_pullback_low: 7.43,
    secondary_origin_date: "2025-01-13",
    secondary_origin_low: 5.51,
    stop: 7.43,
    target: 9.77,
};

test("independent secondary recovery explains frozen pressure and strict volume/body", () => {
    const lines = secondaryReclaimEvidence([proof]);
    assert.equal(lines.length, 3);
    assert.match(lines[0], /2024-12-11.*8\.1700.*2025-01-21.*2025-02-28.*2025-03-03.*8\.7700/);
    assert.match(lines[1], /8\.8900.*8\.7700.*2\.18%.*> 2%.*126,499,296.*80,806,757/);
    assert.match(lines[2], /2025-03-04.*7\.4300.*2025-01-13.*5\.5100.*9\.7700/);
    assert.deepEqual(waveEntryEvidence([proof]), lines);
});

test("gap route describes its own strength threshold", () => {
    const lines = secondaryReclaimEvidence([{ ...proof, secondary_reclaim_type: "gap" }]);
    assert.match(lines[1], /未回补跳空中大阳收复.*≥ 3%.*≥ 60%/);
});

test("missing published fields explicitly report incomplete evidence", () => {
    assert.deepEqual(secondaryReclaimEvidence(), []);
    assert.deepEqual(secondaryReclaimEvidence([{ buy_point_type: "transition_squeeze" }]), []);
    assert.match(secondaryReclaimEvidence([{ ...proof, secondary_resistance_high: undefined }])[0], /缺少完整/);
    assert.match(secondaryReclaimEvidence([{ ...proof, secondary_high_known_date: null }])[0], /缺少完整/);
});

test("buy explanation and panel avoid a fabricated first-class N chain", () => {
    const item = {
        side: "BUY",
        kind: "signal",
        reason: "system_secondary_resistance_reclaim",
        decision_evidence: [proof],
    };
    const reasons = tradeReasonItems(item).join("\n");
    assert.match(reasons, /二级突破抵抗放量收复/);
    assert.match(reasons, /2025-03-03.*8\.7700/);
    const dom = new JSDOM("<div id='panel'></div>");
    const previousDocument = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const panel = document.getElementById("panel");
        appendTradeEvidence(panel, item);
        assert.match(panel.textContent, /二级突破抵抗放量收复/);
        assert.doesNotMatch(panel.textContent, /第一类买点|翻多高点|undefined|NaN/);
    } finally {
        globalThis.document = previousDocument;
        dom.window.close();
    }
});

test("signal reason and scan classification use the same independent name", () => {
    assert.equal(reasonText("system_secondary_resistance_reclaim"), "二级突破抵抗放量收复");
    assert.equal(label("secondary_resistance_reclaim"), "二级突破抵抗放量收复");
});
