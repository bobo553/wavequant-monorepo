import { decodeIndexedDocument } from "./indexed-document.js";
const MIGRATION_KEY = "wavequant.watchlists.server-migration.v1";
const PREVIEW_KEY = "wavequant.watchlists.preview.v2";
const OLD_PREVIEW_KEY = "wavequant.watchlists.preview.v1";
let initialRead = true;
const MAX_PREVIEW_BYTES = 256 * 1024;

function browserPreferences() {
    try {
        return globalThis.localStorage;
    } catch {
        return null;
    }
}

async function requestWatchlists(body) {
    if (!body && initialRead && globalThis.window?.wavequantWatchlistBoot?.loadFresh) {
        initialRead = false;
        return globalThis.window.wavequantWatchlistBoot.loadFresh();
    }
    initialRead = false;
    const response = await fetch(body ? "/api/watchlists" : "/api/watchlists?cache=refresh", {
        method: body ? "POST" : "GET",
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(15_000),
        cache: "no-store",
    });
    const result = decodeIndexedDocument(await response.json());
    if (!response.ok) {
        const error = new Error(result.error || "服务器自选数据暂不可用");
        error.httpStatus = response.status;
        throw error;
    }
    return result;
}

/** 服务器是唯一写入依据；本地快照仅用于同步前的只读预览。 */
export function createServerWatchlistStorage({
    normalize,
    legacy,
    request = requestWatchlists,
    preferences = browserPreferences(),
    parse = (value) => globalThis.window.wavequantParseWatchlistDocument(value),
}) {
    let document = null;
    let pending = Promise.resolve();
    const accept = (value) => {
        const next = parse(value);
        if (document && next.revision < document.revision) return document;
        document = next;
        try {
            globalThis.window?.wavequantWatchlistBoot?.accept(document, "data");
            const preview = JSON.stringify({ revision: document.revision, snapshot: document.snapshot, settings: null });
            let previous = null;
            try { previous = JSON.parse(preferences?.getItem(PREVIEW_KEY) || "null"); } catch { /* 损坏快照可替换。 */ }
            if (!Number.isSafeInteger(previous?.revision) || previous.revision <= document.revision) {
                if (preview.length <= MAX_PREVIEW_BYTES / 2) preferences?.setItem(PREVIEW_KEY, preview);
                else preferences?.removeItem?.(PREVIEW_KEY);
            }
        } catch {
            // 浏览器容量或隐私限制不影响服务器数据及写入流程。
        }
        return document;
    };
    const mutate = (body) => {
        const operation = pending
            .catch(() => {})
            .then(async () => {
                try {
                    return accept(await request({ ...body, revision: document.revision }));
                } catch (error) {
                    if (error.httpStatus === 409) accept(await request());
                    throw error;
                }
            });
        pending = operation;
        return operation;
    };
    return {
        get document() {
            return document;
        },
        readPreview() {
            try {
                const live = globalThis.window?.wavequantWatchlistBoot?.preview;
                if (live) return normalize(parse(live).snapshot);
                const raw = preferences?.getItem(PREVIEW_KEY) || preferences?.getItem(OLD_PREVIEW_KEY);
                if (!raw || raw.length > MAX_PREVIEW_BYTES / 2) return null;
                return normalize(parse(JSON.parse(raw)).snapshot);
            } catch {
                return null;
            }
        },
        async load({ onSnapshot } = {}) {
            accept(await request());
            onSnapshot?.(normalize(document.snapshot));
            let migration = null;
            try {
                migration = JSON.parse(preferences?.getItem(MIGRATION_KEY) || "null");
            } catch {
                /* Preferences may be unavailable. */
            }
            if (!migration?.done) {
                let old;
                try {
                    old = normalize(await legacy.load());
                } catch {
                    return normalize(document.snapshot);
                }
                const token = migration?.token || crypto.randomUUID();
                try {
                    preferences?.setItem(MIGRATION_KEY, JSON.stringify({ token, done: false }));
                } catch {
                    /* Server token still deduplicates this request. */
                }
                if (old.memberships.length || old.groups.length > 1)
                    await mutate({ action: "import", token, snapshot: old });
                try {
                    preferences?.setItem(MIGRATION_KEY, JSON.stringify({ token, done: true }));
                } catch {
                    /* Merging again retains server members. */
                }
            }
            return normalize(document.snapshot);
        },
        async save(value) {
            await mutate({ action: "save", snapshot: normalize(value) });
            return normalize(document.snapshot);
        },
        async addStocks(groupId, stocks) {
            await mutate({ action: "add", groupId, stocks });
            return normalize(document.snapshot);
        },
        async removeStock(groupId, symbol) {
            await mutate({ action: "remove", groupId, stocks: [{ symbol }] });
            return normalize(document.snapshot);
        },
        async refresh() {
            await pending.catch(() => {});
            const previous = document?.revision;
            accept(await request());
            return previous !== document.revision;
        },
        async configure(context, enabled) {
            const calculationContext = { ...context, buy_interval_level: context.buy_interval_level ?? "2" };
            const params = Object.fromEntries(
                Object.entries(calculationContext)
                    .filter(([key]) => key !== "group" && key !== "cutoff")
                    .map(([key, value]) => [key, String(value)]),
            );
            const settings = { enabled, context: params };
            if (
                document.settings?.enabled === enabled &&
                Object.keys(params).every((key) => document.settings.context[key] === params[key])
            )
                return;
            await mutate({ action: "settings", settings });
        },
        async retry() {
            await mutate({ action: "retry" });
        },
    };
}
