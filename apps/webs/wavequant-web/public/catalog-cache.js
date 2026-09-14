const DATABASE_NAME = "wavequant-market-data";
const DATABASE_VERSION = 1;
const STORE_NAME = "stock-catalogs";

function openCatalogDatabase(indexedDBFactory = globalThis.indexedDB) {
    if (!indexedDBFactory) return Promise.resolve(null);
    return new Promise((resolve, reject) => {
        const request = indexedDBFactory.open(DATABASE_NAME, DATABASE_VERSION);
        request.onupgradeneeded = () => {
            if (!request.result.objectStoreNames.contains(STORE_NAME)) {
                request.result.createObjectStore(STORE_NAME, { keyPath: "source" });
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
export async function loadStockCatalog(source, path, { fetcher = fetch, storage = stockCatalogStorage } = {}) {
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
        throw error;
    }
}
