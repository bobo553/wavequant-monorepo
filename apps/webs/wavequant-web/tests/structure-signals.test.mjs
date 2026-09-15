import assert from "node:assert/strict";
import test from "node:test";

import {
    normalizeStructureMarkets,
    normalizeStructureSignalTypes,
    sortedStructureMatches,
    structureCoverageStatus,
    structureScanContextKey,
    structureUniverseCoverage,
} from "../public/structure-signals.js";

test("structure signal selections use a stable order and support subsets", () => {
    assert.deepEqual(normalizeStructureSignalTypes(["bullish_turn", "bear_to_bull"]), ["bear_to_bull", "bullish_turn"]);
    assert.deepEqual(normalizeStructureSignalTypes([]), []);
});

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

test("AkShare rebuilding keeps the last complete snapshot visible with target progress", () => {
    const base = {
        status: "rebuilding",
        params: { asof: "2026-09-14" },
        snapshot: { asof: "2026-09-07", is_fallback: true },
        coverage: { published_stocks: 5_562, building_stocks: 128, expected_stocks: 5_565 },
    };
    assert.equal(
        structureCoverageStatus(base, 5_565, 5_100),
        "AkShare 后台更新中：当前展示 2026-09-07 完整快照；目标 2026-09-14 已发布 128 / 5565 只；当前筛选市场 5100 只",
    );
    assert.equal(
        structureCoverageStatus({ ...base, snapshot: { asof: "2026-09-14", is_fallback: false } }, 5_565, 5_100),
        "AkShare 首次重建中：目标 2026-09-14 已发布 128 / 5565 只；当前展示已完成部分",
    );
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
