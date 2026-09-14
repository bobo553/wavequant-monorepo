import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { funnelLines, profileName, scanContextKey, sortedMatches } from "../public/buy-points.js";

test("buy-point UI reads published snapshots and cannot start interactive scans", async () => {
    const source = await readFile(new URL("../public/buy-points.js", import.meta.url), "utf8");
    assert.match(source, /\/api\/buy-signals/);
    assert.doesNotMatch(source, /\/api\/buy-scan/);
    assert.doesNotMatch(source, /method.*POST/);
});

test("version labels and gate units distinguish stock totals from repeated evaluations", () => {
    assert.equal(profileName("lecture_v1"), "讲义因果版 V1");
    assert.match(profileName("strict_full"), /旧/);
    assert.deepEqual(funnelLines({ stocks: { no_long_signal: 2 }, rejections: { bullish_transition_not_ready: 9 } }), [
        "窗口内无入场信号：2 只",
        "翻多、交替或多头确认未齐备：9 次评估",
    ]);
    assert.deepEqual(funnelLines(null), []);
});

test("scan context ignores selected symbol for market scans but tracks it for AkShare", () => {
    const p = {
        run: "r",
        variant: "strict_full",
        scenario: "base",
        source: "tdx",
        asof: "2026-01-01",
        start: "2018-01-01",
        lookback: 1,
    };
    for (const key of Object.keys(p)) assert.notEqual(scanContextKey(p), scanContextKey({ ...p, [key]: "changed" }));
    assert.equal(scanContextKey(p), scanContextKey({ ...p, symbol: "sh.600519" }));
    const akshare = { ...p, source: "akshare", symbol: "sh.600519" };
    assert.notEqual(scanContextKey(akshare), scanContextKey({ ...akshare, symbol: "sz.000651" }));
});
test("recent dates sort first; stable symbol ordering and input is immutable", () => {
    const rows = [
        { symbol: "B", signal_date: "2026-01-02" },
        { symbol: "C", signal_date: "2026-01-03" },
        { symbol: "A", signal_date: "2026-01-02" },
    ];
    const before = JSON.stringify(rows);
    assert.deepEqual(
        sortedMatches(rows).map((r) => r.symbol),
        ["C", "A", "B"],
    );
    assert.equal(JSON.stringify(rows), before);
});

test("V2 second-buy priority within the same date and explicit mature rejection labels", () => {
    assert.equal(profileName("lecture_v2"), "分级双买点 V2");
    const rows = [
        { symbol: "A", signal_date: "2026-01-02", priority: 1 },
        { symbol: "B", signal_date: "2026-01-02", priority: 2 },
    ];
    assert.deepEqual(
        sortedMatches(rows).map((r) => r.symbol),
        ["B", "A"],
    );
    assert.deepEqual(funnelLines({ rejections: { mature_pullback_not_shallow: 3 } }), [
        "成熟多头回撤不小于 1/3：3 次评估",
    ]);
});
