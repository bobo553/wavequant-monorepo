import assert from "node:assert/strict";
import test from "node:test";

import { loadStockCatalog } from "../public/catalog-cache.js";

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
