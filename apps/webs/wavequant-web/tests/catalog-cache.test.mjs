import assert from "node:assert/strict";
import test from "node:test";

import { loadMarketTimeframeSnapshot, loadStockCatalog, marketTimeframeCacheKey } from "../public/catalog-cache.js";

function memoryStorage(initial = null) {
    let value = initial;
    return {
        async get() {
            return value;
        },
        async put(next) {
            value = next;
        },
        current() {
            return value;
        },
    };
}

test("stock catalog reuses IndexedDB data after a matching 304", async () => {
    const storage = memoryStorage({
        source: "akshare",
        etag: '"same"',
        catalog: { available: true, stocks: [{ symbol: "sh.600000" }], with_daily: 1 },
    });
    let requestOptions;
    const result = await loadStockCatalog("akshare", "/api/akshare-catalog", {
        storage,
        fetcher: async (_path, options) => {
            requestOptions = options;
            return new Response(null, { status: 304, headers: { ETag: '"same"' } });
        },
    });

    assert.equal(requestOptions.headers["If-None-Match"], '"same"');
    assert.equal(result.catalog_cache, "validated");
    assert.equal(result.stocks[0].symbol, "sh.600000");
});

test("stock catalog replaces IndexedDB data when the server ETag changes", async () => {
    const storage = memoryStorage({
        source: "tdx",
        etag: '"old"',
        catalog: { available: true, stocks: [{ symbol: "sh.600000" }], with_daily: 1 },
    });
    const fresh = { available: true, stocks: [{ symbol: "sz.000001" }], with_daily: 1 };
    const result = await loadStockCatalog("tdx", "/api/tdx-catalog", {
        storage,
        fetcher: async () =>
            new Response(JSON.stringify(fresh), {
                status: 200,
                headers: { "Content-Type": "application/json", ETag: '"new"' },
            }),
    });

    assert.equal(result.catalog_cache, "updated");
    assert.equal(storage.current().etag, '"new"');
    assert.equal(storage.current().catalog.stocks[0].symbol, "sz.000001");
});

test("stock catalog remains available from IndexedDB during a server outage", async () => {
    const storage = memoryStorage({
        source: "akshare",
        etag: '"cached"',
        catalog: { available: true, stocks: [{ symbol: "bj.920000" }], with_daily: 1, warnings: [] },
    });
    const result = await loadStockCatalog("akshare", "/api/akshare-catalog", {
        storage,
        fetcher: async () => {
            throw new TypeError("network failed");
        },
    });

    assert.equal(result.catalog_cache, "offline");
    assert.equal(result.stocks[0].symbol, "bj.920000");
    assert.match(result.warnings.at(-1), /浏览器缓存/);
});

test("a stalled catalog request ends and uses the existing browser catalog", async () => {
    const storage = memoryStorage({
        source: "akshare",
        etag: '"cached"',
        catalog: { available: true, stocks: [{ symbol: "sz.000001" }], with_daily: 1, warnings: [] },
    });
    const result = await loadStockCatalog("akshare", "/api/akshare-catalog", {
        storage,
        timeoutMs: 10,
        fetcher: (_path, { signal }) =>
            new Promise((_, reject) => {
                signal.addEventListener("abort", () => reject(signal.reason), { once: true });
            }),
    });
    assert.equal(result.catalog_cache, "offline");
    assert.equal(result.stocks[0].symbol, "sz.000001");
});

const bundle = {
    schema_version: 1,
    snapshot_id: "a".repeat(64),
    source: "akshare",
    symbol: "sh.600519",
    timeframe: "1w",
    requested_asof: "2026-09-07",
    resolved_asof: "2026-09-04",
    algorithm_version: "b".repeat(64),
    data_version: "c".repeat(64),
    view: { symbol: "sh.600519", bars: [{ time: "2026-09-04" }] },
    theory: { lecture_drawing: { teaching_paths: [] } },
};

test("timeframe snapshot revalidates a coherent candle and drawing bundle", async () => {
    const key = marketTimeframeCacheKey("akshare", "sh.600519", "2026-09-07", "1w");
    const storage = memoryStorage({ key, etag: '"snapshot"', bundle });
    const result = await loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1w", {
        storage,
        fetcher: async (_path, options) => {
            assert.equal(options.headers["If-None-Match"], '"snapshot"');
            return new Response(null, { status: 304, headers: { ETag: '"snapshot"' } });
        },
    });

    assert.equal(result.browser_cache, "validated");
    assert.equal(result.view.bars[0].time, result.resolved_asof);
    assert.ok(result.theory.lecture_drawing);
});

test("timeframe snapshot replaces changed server versions", async () => {
    const storage = memoryStorage(null);
    const result = await loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1mo", {
        storage,
        fetcher: async (path) => {
            assert.match(path, /source=akshare/);
            assert.match(path, /timeframe=1mo/);
            return new Response(JSON.stringify({ ...bundle, timeframe: "1mo" }), {
                status: 200,
                headers: { "Content-Type": "application/json", ETag: '"new-snapshot"' },
            });
        },
    });

    assert.equal(result.browser_cache, "updated");
    assert.equal(storage.current().dataVersion, bundle.data_version);
    assert.equal(storage.current().algorithmVersion, bundle.algorithm_version);
});

test("timeframe snapshot falls back to IndexedDB during a server outage", async () => {
    const storage = memoryStorage({ etag: '"snapshot"', bundle });
    const result = await loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1w", {
        storage,
        fetcher: async () => {
            throw new TypeError("network failed");
        },
    });

    assert.equal(result.browser_cache, "offline");
    assert.match(result.cache_warning, /浏览器缓存/);
});
