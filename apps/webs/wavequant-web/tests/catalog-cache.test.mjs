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

test("an internal Core rule revision replaces a cached AkShare low with July 11 even when candles are unchanged", async () => {
    const point = (time, value) => ({ time, kind: "L", value, available_at: "2018-08-01" });
    const theory = (low) => ({ reversal_trends: { strokes: [{ id: "same-path", kind: "reversal", points: [low] }] } });
    const old = {
        ...bundle,
        symbol: "sz.000678",
        timeframe: "1d",
        view: { ...bundle.view, symbol: "sz.000678" },
        theory: theory(point("2018-07-16", 5)),
    };
    const replacement = {
        ...old,
        snapshot_id: "d".repeat(64),
        algorithm_version: "e".repeat(64),
        theory: theory(point("2018-07-11", 4.84)),
    };
    const storage = memoryStorage({
        key: marketTimeframeCacheKey("akshare", "sz.000678", "2026-09-07", "1d"),
        etag: '"old-rule"',
        bundle: old,
    });
    const result = await loadMarketTimeframeSnapshot("akshare", "sz.000678", "2026-09-07", "1d", {
        storage,
        fetcher: async (_path, options) => {
            assert.equal(options.headers["If-None-Match"], '"old-rule"');
            return new Response(JSON.stringify(replacement), {
                status: 200,
                headers: { "Content-Type": "application/json", ETag: '"new-rule"' },
            });
        },
    });
    assert.equal(result.browser_cache, "updated");
    assert.equal(result.data_version, old.data_version);
    assert.deepEqual(result.theory.reversal_trends.strokes[0].points, [point("2018-07-11", 4.84)]);
    assert.equal(storage.current().etag, '"new-rule"');
    assert.deepEqual(storage.current().bundle.theory, replacement.theory);
    assert.deepEqual(old.theory.reversal_trends.strokes[0].points, [point("2018-07-16", 5)]);
});

test("switching stocks aborts a snapshot fetch without returning offline cached geometry", async () => {
    const storage = memoryStorage({
        key: marketTimeframeCacheKey("akshare", bundle.symbol, bundle.requested_asof, bundle.timeframe),
        etag: '"snapshot"',
        bundle,
    });
    const controller = new AbortController();
    const request = loadMarketTimeframeSnapshot("akshare", bundle.symbol, bundle.requested_asof, bundle.timeframe, {
        signal: controller.signal,
        storage,
        fetcher: (_url, { signal }) =>
            new Promise((_, reject) => {
                signal.addEventListener("abort", () => reject(signal.reason), { once: true });
                controller.abort();
            }),
    });
    await assert.rejects(request, { name: "AbortError" });
});

test("full geometry with another data version cannot replace already displayed annual bars", async () => {
    await assert.rejects(
        loadMarketTimeframeSnapshot("akshare", bundle.symbol, bundle.requested_asof, bundle.timeframe, {
            storage: memoryStorage(),
            expectedDataVersion: "other-version",
            fetcher: async () => new Response(JSON.stringify(bundle)),
        }),
        { code: "MARKET_DATA_VERSION_CHANGED" },
    );
});

test("a stalled uncached full snapshot has a deadline", async () => {
    await assert.rejects(
        loadMarketTimeframeSnapshot("akshare", bundle.symbol, bundle.requested_asof, bundle.timeframe, {
            storage: memoryStorage(),
            timeoutMs: 10,
            fetcher: (_url, { signal }) =>
                new Promise((_, reject) => {
                    const keeper = setTimeout(() => {}, 100);
                    signal.addEventListener(
                        "abort",
                        () => {
                            clearTimeout(keeper);
                            reject(signal.reason);
                        },
                        { once: true },
                    );
                }),
        }),
        /完整趋势请求超时/,
    );
});

test("full snapshot validates the displayed resolved cutoff, view version and price basis before installation", async () => {
    const coherent = {
        ...bundle,
        view: {
            ...bundle.view,
            asof: bundle.resolved_asof,
            data_version: bundle.data_version,
            timeframe: bundle.timeframe,
            price_basis: "raw_unadjusted",
        },
    };
    const incompatible = [
        { ...coherent, resolved_asof: "2026-09-03", view: { ...coherent.view, asof: "2026-09-03" } },
        { ...coherent, view: { ...coherent.view, data_version: "different-view-version" } },
        { ...coherent, view: { ...coherent.view, price_basis: "causal_adjusted" } },
    ];
    for (const changed of incompatible) {
        await assert.rejects(
            loadMarketTimeframeSnapshot("akshare", bundle.symbol, bundle.requested_asof, bundle.timeframe, {
                storage: memoryStorage(),
                expectedDataVersion: bundle.data_version,
                expectedResolvedAsOf: bundle.resolved_asof,
                expectedPriceBasis: "raw_unadjusted",
                fetcher: async () => new Response(JSON.stringify(changed)),
            }),
            { code: "MARKET_DATA_VERSION_CHANGED" },
        );
    }
});

test("timeframe snapshot rejects unvalidated cached geometry during a server outage", async () => {
    const storage = memoryStorage({
        key: marketTimeframeCacheKey("akshare", "sh.600519", "2026-09-07", "1w"),
        etag: '"snapshot"',
        bundle,
    });
    await assert.rejects(
        loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1w", {
            storage,
            fetcher: async () => {
                throw new TypeError("network failed");
            },
        }),
        /network failed/,
    );
    assert.deepEqual(storage.current().bundle, bundle);
});

test("matching candles cannot authorize an old algorithm on server failure", async () => {
    const cached = {
        ...bundle,
        view: {
            ...bundle.view,
            asof: bundle.resolved_asof,
            data_version: bundle.data_version,
            timeframe: bundle.timeframe,
            price_basis: "raw_unadjusted",
        },
        theory: { rule_version: "v118", lecture_drawing: { teaching_paths: [] } },
    };
    for (const status of [500, 503]) {
        const storage = memoryStorage({
            key: marketTimeframeCacheKey(cached.source, cached.symbol, cached.requested_asof, cached.timeframe),
            etag: '"old-v118"',
            bundle: cached,
        });
        await assert.rejects(
            loadMarketTimeframeSnapshot(cached.source, cached.symbol, cached.requested_asof, cached.timeframe, {
                storage,
                expectedDataVersion: cached.data_version,
                expectedResolvedAsOf: cached.resolved_asof,
                expectedPriceBasis: cached.view.price_basis,
                fetcher: async () =>
                    new Response(JSON.stringify({ error: "current algorithm unavailable" }), { status }),
            }),
            /current algorithm unavailable/,
        );
        assert.deepEqual(storage.current().bundle, cached);
    }
});

test("a cached complete geometry does not bypass the server validation deadline", async () => {
    const storage = memoryStorage({
        key: marketTimeframeCacheKey(bundle.source, bundle.symbol, bundle.requested_asof, bundle.timeframe),
        etag: '"old-v118"',
        bundle,
    });
    await assert.rejects(
        loadMarketTimeframeSnapshot(bundle.source, bundle.symbol, bundle.requested_asof, bundle.timeframe, {
            storage,
            timeoutMs: 10,
            fetcher: (_url, { signal }) =>
                new Promise((_, reject) => {
                    const keeper = setTimeout(() => {}, 100);
                    signal.addEventListener(
                        "abort",
                        () => {
                            clearTimeout(keeper);
                            reject(signal.reason);
                        },
                        { once: true },
                    );
                }),
        }),
        /完整趋势请求超时/,
    );
    assert.deepEqual(storage.current().bundle, bundle);
});

test("N target trend option defaults off and isolates its timeframe cache without truthy coercion", () => {
    const args = ["akshare", "sz.000678", "2018-09-25", "1d"];
    assert.equal(marketTimeframeCacheKey(...args), marketTimeframeCacheKey(...args, false));
    assert.notEqual(marketTimeframeCacheKey(...args, false), marketTimeframeCacheKey(...args, true));
    assert.notEqual(marketTimeframeCacheKey(...args), `v1:${args.join(":")}`);
    for (const invalid of ["false", "true", 0, 1, null])
        assert.throws(() => marketTimeframeCacheKey(...args, invalid), /布尔值/);
});

test("changing N target option requests separate authoritative snapshots and revalidates only the matching cache", async () => {
    const values = new Map(),
        requests = [],
        storage = { get: async (key) => values.get(key), put: async (value) => values.set(value.key, value) };
    const fetcher = async (path, options) => {
        const enabled = new URL(path, "https://wavequant.test").searchParams.get("n_target_trend_confirmation_enabled");
        requests.push({ enabled, etag: options.headers["If-None-Match"] });
        const etag = `"n-target-${enabled}"`;
        if (options.headers["If-None-Match"] === etag)
            return new Response(null, { status: 304, headers: { ETag: etag } });
        return new Response(
            JSON.stringify({
                ...bundle,
                theory: { reversal_trends: { n_target_trend_confirmation_enabled: enabled === "true" } },
            }),
            { status: 200, headers: { ETag: etag } },
        );
    };
    for (const enabled of [false, true, false, true]) {
        const result = await loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1w", {
            storage,
            fetcher,
            nTargetTrendConfirmationEnabled: enabled,
        });
        assert.equal(result.theory.reversal_trends.n_target_trend_confirmation_enabled, enabled);
    }
    assert.deepEqual(requests, [
        { enabled: "false", etag: undefined },
        { enabled: "true", etag: undefined },
        { enabled: "false", etag: '"n-target-false"' },
        { enabled: "true", etag: '"n-target-true"' },
    ]);
    assert.equal(values.size, 2);
});

test("old or opposite-option cache cannot serve a failed N-option request", async () => {
    for (const oldKey of [
        "v1:akshare:sh.600519:2026-09-07:1w",
        marketTimeframeCacheKey("akshare", "sh.600519", "2026-09-07", "1w", true),
    ]) {
        await assert.rejects(
            loadMarketTimeframeSnapshot("akshare", "sh.600519", "2026-09-07", "1w", {
                storage: memoryStorage({ key: oldKey, etag: '"old"', bundle }),
                fetcher: async (_path, options) => {
                    assert.equal(options.headers["If-None-Match"], undefined);
                    throw new Error("offline");
                },
            }),
            /offline/,
        );
    }
});


test("cached catalog is delivered while validation is still pending", async () => {
    const storage = memoryStorage({ source: "akshare", etag: '"old"',
        catalog: { available: true, stocks: [{ symbol: "sz.000978", name: "旧名称" }], with_daily: 1 } });
    let finishRequest;
    let receiveRefresh;
    const network = new Promise((resolve) => { finishRequest = resolve; });
    const refreshed = new Promise((resolve) => { receiveRefresh = resolve; });
    const initial = await loadStockCatalog("akshare", "/catalog", {
        storage, staleWhileRevalidate: true, fetcher: () => network, onRefresh: receiveRefresh,
    });
    assert.equal(initial.catalog_cache, "stale-while-revalidate");
    assert.equal(initial.stocks[0].name, "旧名称");
    finishRequest(new Response(JSON.stringify({ available: true,
        stocks: [{ symbol: "sz.000978", name: "新名称" }], with_daily: 1 }),
        { headers: { ETag: '"new"' } }));
    const updated = await refreshed;
    assert.equal(updated.stocks[0].name, "新名称");
    assert.equal(storage.current().etag, '"new"');
});

test("background validation failure keeps the delivered catalog available", async () => {
    const storage = memoryStorage({ source: "akshare", etag: '"old"',
        catalog: { stocks: [{ symbol: "sz.000978" }], with_daily: 1 } });
    let receiveRefresh;
    const refreshed = new Promise((resolve) => { receiveRefresh = resolve; });
    const initial = await loadStockCatalog("akshare", "/catalog", {
        storage, staleWhileRevalidate: true,
        fetcher: async () => { throw new TypeError("offline"); }, onRefresh: receiveRefresh,
    });
    const fallback = await refreshed;
    assert.equal(initial.stocks[0].symbol, fallback.stocks[0].symbol);
    assert.equal(fallback.catalog_cache, "offline");
    assert.equal(storage.current().etag, '"old"');
});

test("catalog cache never crosses data sources", async () => {
    const storage = memoryStorage({ source: "tdx", etag: '"wrong"',
        catalog: { stocks: [{ symbol: "sh.600000" }], with_daily: 1 } });
    let headers;
    const result = await loadStockCatalog("akshare", "/catalog", {
        storage, staleWhileRevalidate: true,
        fetcher: async (_path, options) => {
            headers = options.headers;
            return new Response(JSON.stringify({ stocks: [{ symbol: "sz.000978" }], with_daily: 1 }));
        },
    });
    assert.equal(headers["If-None-Match"], undefined);
    assert.equal(result.stocks[0].symbol, "sz.000978");
    assert.equal(result.catalog_cache, "updated");
});
