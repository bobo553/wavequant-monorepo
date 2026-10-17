const DATABASE_NAME = "wavequant-market-data";
const DATABASE_VERSION = 2;
const STORE_NAME = "stock-catalogs";
const TIMEFRAME_STORE_NAME = "market-timeframes";
const TIMEFRAME_CACHE_LIMIT = 40;

function openCatalogDatabase(indexedDBFactory = globalThis.indexedDB) {
    if (!indexedDBFactory) return Promise.resolve(null);
    return new Promise((resolve, reject) => {
        const request = indexedDBFactory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                request.result.createObjectStore(STORE_NAME, { keyPath: "source" });
            }
            if (!request.result.objectStoreNames.contains(TIMEFRAME_STORE_NAME)) {
                const store = request.result.createObjectStore(TIMEFRAME_STORE_NAME, { keyPath: "key" });
                store.createIndex("by-source-symbol", ["source", "symbol"], { unique: false });
                store.createIndex("by-cached-at", "cachedAt", { unique: false });
            }
        };
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error || new Error("股票目录缓存初始化失败"));
        request.onblocked = () => reject(new Error("股票目录缓存升级被阻止"));
    });
}

function transactionResult(transaction, request) {
    return new Promise((resolve, reject) => {
        transaction.oncomplete = () => resolve(request.result ?? null);
        transaction.onerror = () => reject(transaction.error || request.error || new Error("股票目录缓存事务失败"));
        transaction.onabort = () => reject(transaction.error || new Error("股票目录缓存事务已取消"));
    });
}

export const stockCatalogStorage = {
    async get(source) {
        const database = await openCatalogDatabase();
        if (!database) return null;
        try {
            const transaction = database.transaction(STORE_NAME, "readonly");
            const request = transaction.objectStore(STORE_NAME).get(source);
            const entry = await transactionResult(transaction, request);
            return entry?.catalog && typeof entry.etag === "string" ? entry : null;
        } finally {
            database.close();
        }
    },

    async put(entry) {
        const database = await openCatalogDatabase();
        if (!database) return;
        try {
            const transaction = database.transaction(STORE_NAME, "readwrite");
            const request = transaction.objectStore(STORE_NAME).put(entry);
            await transactionResult(transaction, request);
        } finally {
            database.close();
        }
    },
};

export function marketTimeframeCacheKey(
    source,
    symbol,
    requestedAsOf,
    timeframe,
    nTargetTrendConfirmationEnabled = false,
    ordinaryTrendCompletionEnabled = false,
) {
    if (typeof ordinaryTrendCompletionEnabled !== "boolean") throw new TypeError("普通升降整波结束开关必须为布尔值");
    if (typeof nTargetTrendConfirmationEnabled !== "boolean") throw new TypeError("N 一饱趋势确认开关必须为布尔值");
    return `v2:${source}:${symbol}:${requestedAsOf}:${timeframe}:n-target-${nTargetTrendConfirmationEnabled}:ordinary-${ordinaryTrendCompletionEnabled}`;
}

export const marketTimeframeStorage = {
    async get(key) {
        const database = await openCatalogDatabase();
        if (!database) return null;
        try {
            const transaction = database.transaction(TIMEFRAME_STORE_NAME, "readonly");
            const request = transaction.objectStore(TIMEFRAME_STORE_NAME).get(key);
            const entry = await transactionResult(transaction, request);
            return entry?.bundle && typeof entry.etag === "string" ? entry : null;
        } finally {
            database.close();
        }
    },

    async put(entry) {
        const database = await openCatalogDatabase();
        if (!database) return;
        try {
            const write = database.transaction(TIMEFRAME_STORE_NAME, "readwrite");
            await transactionResult(write, write.objectStore(TIMEFRAME_STORE_NAME).put(entry));
            const read = database.transaction(TIMEFRAME_STORE_NAME, "readonly");
            const records = await transactionResult(read, read.objectStore(TIMEFRAME_STORE_NAME).getAll());
            const expired = records
                .sort((left, right) => String(right.cachedAt).localeCompare(String(left.cachedAt)))
                .slice(TIMEFRAME_CACHE_LIMIT);
            if (expired.length) {
                const prune = database.transaction(TIMEFRAME_STORE_NAME, "readwrite");
                const store = prune.objectStore(TIMEFRAME_STORE_NAME);
                expired.forEach((record) => store.delete(record.key));
                await new Promise((resolve, reject) => {
                    prune.oncomplete = resolve;
                    prune.onerror = () => reject(prune.error || new Error("周期缓存清理失败"));
                    prune.onabort = () => reject(prune.error || new Error("周期缓存清理已取消"));
                });
            }
        } finally {
            database.close();
        }
    },
};

function cachedCatalog(cached, status, warning) {
    const warnings = Array.isArray(cached.catalog.warnings) ? cached.catalog.warnings : [];
    return {
        ...cached.catalog,
        catalog_cache: status,
        catalog_etag: cached.etag,
        ...(warning ? { warnings: [...warnings, warning] } : {}),
    };
}

/**
 * Read the persisted stock catalog first, then conditionally validate it.
 * A 304 response transfers no catalog body; a changed ETag atomically replaces
 * the IndexedDB record. Existing data remains usable during a transient outage.
 */
export async function loadStockCatalog(
    source,
    path,
    { fetcher = fetch, storage = stockCatalogStorage, timeoutMs = 30_000 } = {},
) {
    let cached = null;
    try {
        cached = await storage.get(source);
    } catch {
        // Private browsing or a damaged database must not prevent a network load.
    }

    try {
        const response = await fetcher(path, {
            cache: "no-store",
            headers: cached?.etag ? { "If-None-Match": cached.etag } : {},
            signal: AbortSignal.timeout(timeoutMs),
        });
        if (response.status === 304) {
            if (!cached) throw new Error("服务器返回 304，但本地股票目录不存在");
            return cachedCatalog(cached, "validated");
        }
        const body = await response.json();
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        const etag = response.headers.get("ETag");
        if (etag && (!cached || cached.etag !== etag)) {
            try {
                await storage.put({ source, etag, catalog: body, updatedAt: new Date().toISOString() });
            } catch {
                // The fresh server response remains authoritative if persistence fails.
            }
        }
        return {
            ...body,
            catalog_cache: etag && cached?.etag === etag ? "validated" : "updated",
            catalog_etag: etag,
        };
    } catch (error) {
        if (cached) return cachedCatalog(cached, "offline", "服务器目录校验失败，当前使用浏览器缓存");
        if (error?.name === "TimeoutError") throw new Error(`${source} 股票目录请求超时，请稍后重试`);
        throw error;
    }
}

function validatedTimeframeBundle(body) {
    if (
        !body ||
        body.schema_version !== 1 ||
        typeof body.snapshot_id !== "string" ||
        typeof body.data_version !== "string" ||
        typeof body.algorithm_version !== "string" ||
        !body.view ||
        !body.theory
    ) {
        throw new Error("服务器返回的周期快照格式无效");
    }
    return body;
}

/** 完整缓存只作为同身份行情候选；年度请求不等待 IndexedDB，也不写入此存储。 */
export async function readCachedMarketTimeframeSnapshot(
    source,
    symbol,
    requestedAsOf,
    timeframe,
    { storage = marketTimeframeStorage, nTargetTrendConfirmationEnabled = false, ordinaryTrendCompletionEnabled = false } = {},
) {
    const key = marketTimeframeCacheKey(source, symbol, requestedAsOf, timeframe, nTargetTrendConfirmationEnabled, ordinaryTrendCompletionEnabled);
    try {
        const entry = await storage.get(key);
        if (entry?.key !== key) return null;
        const bundle = validatedTimeframeBundle(entry.bundle);
        return bundle.source === source &&
            bundle.symbol === symbol &&
            bundle.requested_asof === requestedAsOf &&
            bundle.timeframe === timeframe &&
            bundle.view.symbol === symbol
            ? bundle
            : null;
    } catch {
        return null;
    }
}

/**
 * Load one coherent candle+drawing bundle. The lookup key identifies the user
 * request while ETag identifies its immutable data/algorithm result.
 */
export async function loadMarketTimeframeSnapshot(
    source,
    symbol,
    requestedAsOf,
    timeframe,
    {
        fetcher = fetch,
        storage = marketTimeframeStorage,
        nTargetTrendConfirmationEnabled = false,
        ordinaryTrendCompletionEnabled = false,
        signal,
        timeoutMs = 45_000,
        expectedDataVersion,
        expectedPriceBasis,
        expectedResolvedAsOf,
    } = {},
) {
    const key = marketTimeframeCacheKey(source, symbol, requestedAsOf, timeframe, nTargetTrendConfirmationEnabled, ordinaryTrendCompletionEnabled);
    let cached = null;
    try {
        const entry = await storage.get(key);
        cached = entry?.key === key ? entry : null;
    } catch {
        // IndexedDB can be unavailable in private browsing; network remains authoritative.
    }
    const query = new URLSearchParams({
        source,
        symbol,
        asof: requestedAsOf,
        timeframe,
        n_target_trend_confirmation_enabled: String(nTargetTrendConfirmationEnabled),
        ordinary_trend_completion_enabled: String(ordinaryTrendCompletionEnabled),
    });
    const validateIdentity = (bundle) => {
        if (
            bundle.source !== source ||
            bundle.symbol !== symbol ||
            bundle.requested_asof !== requestedAsOf ||
            bundle.timeframe !== timeframe ||
            bundle.view.symbol !== symbol
        )
            throw new Error("周期快照与当前股票请求不一致");
        if (
            expectedDataVersion &&
            (bundle.data_version !== expectedDataVersion || bundle.view.data_version !== expectedDataVersion)
        ) {
            const error = new Error("行情版本已变化，请重试后加载完整趋势");
            error.code = "MARKET_DATA_VERSION_CHANGED";
            throw error;
        }
        if (
            (expectedPriceBasis && bundle.view.price_basis !== expectedPriceBasis) ||
            (expectedResolvedAsOf && bundle.resolved_asof !== expectedResolvedAsOf) ||
            (expectedDataVersion &&
                (bundle.view.asof !== bundle.resolved_asof || (bundle.view.timeframe || "1d") !== timeframe))
        ) {
            const error = new Error("完整趋势与已加载行情的价格口径或截面不一致，请重试");
            error.code = "MARKET_DATA_VERSION_CHANGED";
            throw error;
        }
        return bundle;
    };
    try {
        signal?.throwIfAborted();
        const deadline = AbortSignal.timeout(timeoutMs);
        const response = await fetcher(`/api/market-timeframe?${query}`, {
            cache: "no-store",
            headers: cached?.etag ? { "If-None-Match": cached.etag } : {},
            signal: signal ? AbortSignal.any([signal, deadline]) : deadline,
        });
        if (response.status === 304) {
            if (!cached) throw new Error("服务器返回 304，但本地周期快照不存在");
            signal?.throwIfAborted();
            return { ...validateIdentity(validatedTimeframeBundle(cached.bundle)), browser_cache: "validated" };
        }
        const body = await response.json();
        signal?.throwIfAborted();
        if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
        const bundle = validateIdentity(validatedTimeframeBundle(body));
        const etag = response.headers.get("ETag");
        if (etag) {
            try {
                await storage.put({
                    schemaVersion: 2,
                    key,
                    source,
                    symbol,
                    timeframe,
                    requestedAsOf,
                    resolvedAsOf: bundle.resolved_asof,
                    dataVersion: bundle.data_version,
                    algorithmVersion: bundle.algorithm_version,
                    etag,
                    bundle,
                    cachedAt: new Date().toISOString(),
                });
            } catch {
                // A valid server response is still usable when persistence fails.
            }
        }
        return { ...bundle, browser_cache: etag && cached?.etag === etag ? "validated" : "updated" };
    } catch (error) {
        if (signal?.aborted) throw signal.reason;
        if (error.code === "MARKET_DATA_VERSION_CHANGED") throw error;
        // OHLC 版本不能证明缓存中的趋势规则仍是当前版本；失败时由年度预览保留行情。
        if (error.name === "TimeoutError") throw new Error("完整趋势请求超时；已加载的 K 线仍可查看");
        throw error;
    }
}
