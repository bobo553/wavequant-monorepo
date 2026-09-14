import assert from "node:assert/strict";
import test from "node:test";

import {
    normalizeStructureMarkets,
    sortedStructureMatches,
    structureScanContextKey,
    structureUniverseCoverage,
} from "../public/structure-signals.js";

test("structure context ignores the selected stock for every market source", () => {
    const params = {
        run: "run",
        variant: "lecture_v1",
        source: "tdx",
        asof: "2026-09-07",
        lookback: 5,
        signal_type: "bear_to_bull",
        trend_level: 0,
        markets: "shanghai,shenzhen,chinext",
    };
    for (const key of Object.keys(params)) {
        assert.notEqual(structureScanContextKey(params), structureScanContextKey({ ...params, [key]: "changed" }));
    }
    assert.equal(structureScanContextKey(params), structureScanContextKey({ ...params, symbol: "sh.600519" }));
    const akshare = { ...params, source: "akshare", symbol: "sh.600519" };
    assert.equal(structureScanContextKey(akshare), structureScanContextKey({ ...akshare, symbol: "sz.000651" }));
});

test("structure markets use a stable server cache order", () => {
    assert.equal(
        normalizeStructureMarkets(["beijing", "chinext", "shanghai", "star"]),
        "shanghai,chinext,star,beijing",
    );
    assert.equal(normalizeStructureMarkets([]), "");
});

test("AkShare coverage distinguishes the full catalog from the selected eligible universe", () => {
    const stocks = [
        { symbol: "sh.600000", name: "浦发银行" },
        { symbol: "sh.688001", name: "科创样本" },
        { symbol: "sz.000001", name: "平安银行" },
        { symbol: "sz.300001", name: "创业样本" },
        { symbol: "bj.430001", name: "北交样本" },
        { symbol: "sh.600001", name: "*ST 沪股" },
        { symbol: "sh.600002", name: "退市样本", catalog_source: "tdx" },
        { symbol: "invalid", name: "无效代码" },
    ];

    assert.deepEqual(structureUniverseCoverage(stocks, "shanghai,shenzhen,chinext"), {
        catalogStocks: 6,
        selectedStocks: 3,
    });
    assert.deepEqual(structureUniverseCoverage(stocks, "star,beijing"), {
        catalogStocks: 6,
        selectedStocks: 2,
    });
});

test("structure results sort by confirmation date, level, event date and symbol without mutation", () => {
    const rows = [
        { id: "a", symbol: "sz.000001", available_at: "2026-09-01", event_date: "2026-08-20", trend_level: 1 },
        { id: "b", symbol: "sh.600000", available_at: "2026-09-02", event_date: "2026-08-18", trend_level: 1 },
        { id: "c", symbol: "sh.600001", available_at: "2026-09-02", event_date: "2026-08-19", trend_level: 2 },
    ];
    const before = JSON.stringify(rows);
    assert.deepEqual(
        sortedStructureMatches(rows).map((row) => row.id),
        ["c", "b", "a"],
    );
    assert.equal(JSON.stringify(rows), before);
});
