import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { runInNewContext } from "node:vm";

import { JSDOM } from "jsdom";

import { buildAnnotations, reasonText } from "../public/annotations.js";
import { BuyPoints } from "../public/buy-points.js";
import { combinedAEntryEvidence } from "../public/combined-a-entry-evidence.js";
import { formatFilledTradeCopy } from "../public/filled-trade-copy.js";
import { label, num, pct } from "../public/labels.js";
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

const scanMatch = {
    symbol: "sh.601086",
    run_id: "fixture",
    signal_date: "2025-04-03",
    buy_point_type: evidence.buy_point_type,
    status: "awaiting_next_open",
    regime: "轧空",
    rvol: 3.13,
    retracement: 0.608,
    gross_reward_risk: 1.7,
    raw_reference_price: 5.24,
    reason: marker.reason,
    evidence: marker.decision_evidence,
};

async function scanClickText(match, source, { markerEvidence = match.evidence, includeMarker = true } = {}) {
    const appSource = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");
    const begin = appSource.indexOf("onSelect: async (match, p) => {");
    assert.ok(begin >= 0);
    const end = appSource.indexOf("\n    },\n});\nconst structureSignals", begin);
    assert.ok(end > begin);
    const callback = appSource.slice(begin + "onSelect: ".length, end) + "\n}";
    const dom = new JSDOM(`
        <select id="result-scope"><option>akshare</option><option>tdx-backtest</option><option>stock</option></select>
        <select id="symbol-select"><option>sh.601086</option></select>
        <input id="show-markers" type="checkbox"><section id="selection-info"></section>
    `);
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const $ = (id) => document.getElementById(id);
        const view = {
            run_id: match.run_id,
            symbol: match.symbol,
            asof: "2025-04-03",
            bars: [{ time: "2025-04-03", close: marker.price }],
            markers: includeMarker
                ? [{ ...marker, reason: match.reason, kind: "signal", side: "LONG", decision_evidence: markerEvidence }]
                : [],
        };
        const detail = (title, text) => {
            $("selection-info").replaceChildren();
            const paragraph = document.createElement("p");
            paragraph.textContent = `${title}。${text}`;
            $("selection-info").append(paragraph);
        };
        const selectMatch = runInNewContext(`(${callback})`, {
            $,
            document,
            state: { view, error: false },
            combinedAEntryEvidence,
            waveEntryEvidence,
            reasonText,
            num,
            pct,
            detail,
            fillSymbols() {},
            setTimeframe() {},
            preserveCutoff() {},
            showPage() {},
            async loadView() {},
            annotationOptions: () => ({}),
            chart: {
                setAnnotationOptions() {},
                flashSelectedAnnotation() {},
                selectAnnotation(id) {
                    const annotation = buildAnnotations(view).find((item) => item.id === id);
                    detail(annotation.title, annotation.description);
                    appendTradeEvidence($("selection-info"), annotation);
                },
            },
        });
        await selectMatch(match, { source, asof: view.asof });
        return $("selection-info").textContent;
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
}

function assertCombinedScanText(text) {
    assert.match(text, /组合A回调放量突破/);
    assert.match(text, /回调及整理 58.*组合内部回调 70.*或.*子级回调 9.*满足其一/s);
    assert.match(text, /2\/3.*严格突破.*20,135,200.*实体\/开盘.*防守.*目标/s);
    assert.doesNotMatch(text, /undefined|第一类|NaN/);
}

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

test("opening N scan click uses opening proof and price basis across data sources", async () => {
    const openingMatch = {
        ...scanMatch,
        buy_point_type: "n_opening_gap_squeeze",
        reason: "system_n_opening_gap_squeeze",
        evidence: [
            {
                event: "long_transition_evidence",
                buy_point_type: "n_opening_gap_squeeze",
                squeeze_confirmation: "known_n_opening_gap",
                n_opening_attack_date: "2025-03-31",
                n_opening_known_date: "2025-04-01",
                n_opening_defense: 4.9,
                opening_price: 5.62,
                prior_bar_date: "2025-04-02",
                prior_close: 5.46,
                decision_timestamp: "2025-04-03T09:30:00+08:00",
            },
        ],
    };
    for (const source of ["akshare", "tdx", "snapshot"]) {
        const text = await scanClickText(openingMatch, source);
        assert.match(text, /正 N.*高开.*开盘.*买入/);
        assert.match(text, /2025-03-31.*2025-04-01.*5\.6200.*2025-04-02.*5\.4600.*09:30/s);
        assert.doesNotMatch(text, /undefined|NaN|第一类|翻多 —|收盘参考盈亏比/);
        if (source === "akshare") assert.match(text, /未模拟成交/);
        else assert.match(text, /开盘参考盈亏比/);
    }
    const noMarker = await scanClickText(openingMatch, "snapshot", { includeMarker: false });
    assert.match(noMarker, /正 N 高开买点复核.*09:30/s);
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

test("scanner list names combined A without a trend level and preserves the two classified buy points", () => {
    const dom = new JSDOM(`
        <button id="scan-start"></button><select id="scan-lookback"><option>1</option></select>
        <p id="scan-status"></p><div id="buy-points-list"></div>
    `);
    const previous = globalThis.document;
    globalThis.document = dom.window.document;
    try {
        const points = new BuyPoints({ api() {}, getContext() {}, onSelect() {} });
        points.job = {
            params: { source: "akshare", asof: "2025-04-03", variant: "lecture_v3" },
            status: "ready",
            processed: 3,
            total: 3,
            skipped: 0,
            stale: 0,
            failed: 0,
            errors: [],
            results: [
                scanMatch,
                {
                    ...scanMatch,
                    symbol: "sh.601087",
                    buy_point_type: "transitioned_squeeze",
                    trend_level: 2,
                    priority: 1,
                },
                {
                    ...scanMatch,
                    symbol: "sh.601088",
                    buy_point_type: "mature_shallow_squeeze",
                    trend_level: 3,
                    priority: 2,
                },
            ],
        };
        points.render();
        const list = document.getElementById("buy-points-list");
        const combined = list.querySelector('[data-symbol="sh.601086"]').textContent;
        assert.match(combined, /组合A回调放量突破.*当日新信号/);
        assert.doesNotMatch(combined, /undefined|第一类|级/);
        assert.match(list.querySelector('[data-symbol="sh.601087"]').textContent, /第一类 \/ 2 级/);
        assert.match(list.querySelector('[data-symbol="sh.601088"]').textContent, /第二类 · 重点 \/ 3 级/);
    } finally {
        globalThis.document = previous;
        dom.window.close();
    }
});

test("AkShare scan click explains its complete combined proof while remaining signal only", async () => {
    const text = await scanClickText(scanMatch, "akshare");
    assertCombinedScanText(text);
    assert.match(text, /未模拟成交/);
});

test("TDX scan click keeps the complete marker proof and skips the old flip chain", async () => {
    const text = await scanClickText(scanMatch, "tdx");
    assertCombinedScanText(text);
    assert.equal(text.match(/放量中大阳线确认/g).length, 2);
    assert.doesNotMatch(text, /翻多 —/);
});

test("snapshot scan click uses the published complete proof when the selected marker has only a channel", async () => {
    assertCombinedScanText(
        await scanClickText(scanMatch, "snapshot", { markerEvidence: [marker.decision_evidence[0]] }),
    );
});

test("combined scan proof remains reviewable if the chart has no matching marker", async () => {
    assertCombinedScanText(await scanClickText(scanMatch, "snapshot", { includeMarker: false }));
});

test("other scan types retain AkShare's overview and the original classified flip chain", async () => {
    const legacy = {
        ...scanMatch,
        buy_point_type: "mature_shallow_squeeze",
        reason: "system_entry",
        evidence: [
            {
                event: "long_transition_evidence",
                buy_point_type: "mature_shallow_squeeze",
                trend_level: 2,
                priority: 2,
            },
        ],
    };
    const akshare = await scanClickText(legacy, "akshare");
    assert.match(akshare, /参考 5\.24 元.*未模拟成交/);
    assert.doesNotMatch(akshare, /组合 A|组合A|回调及整理/);
    const tdx = await scanClickText(legacy, "tdx");
    assert.match(tdx, /2 级 · 第二类（重点）：翻多 — → 交替 — → 再破翻多高 — → 浅回撤 — → 攻击 —/);
});
