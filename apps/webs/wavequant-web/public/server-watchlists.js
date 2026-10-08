const MIGRATION_KEY = "wavequant.watchlists.server-migration.v1";

function browserPreferences() {
    try {
        return globalThis.localStorage;
    } catch {
        return null;
    }
}

async function requestWatchlists(body) {
    const response = await fetch("/api/watchlists", {
        method: body ? "POST" : "GET",
        ...(body ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {}),
        signal: AbortSignal.timeout(15_000),
        cache: "no-store",
    });
    const result = await response.json();
    if (!response.ok) {
        const error = new Error(result.error || "服务器自选数据暂不可用");
        error.httpStatus = response.status;
        throw error;
    }
    return result;
}

/** The server is authoritative; IndexedDB is only a retained migration source. */
export function createServerWatchlistStorage({
    normalize,
    legacy,
    request = requestWatchlists,
    preferences = browserPreferences(),
    parse = (value) => globalThis.window.wavequantParseWatchlistDocument(value),
}) {
    let document = null;
    let pending = Promise.resolve();
    const accept = (value) => (document = parse(value));
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
        async load() {
            accept(await request());
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
        async refresh() {
            await pending.catch(() => {});
            const previous = document?.revision;
            accept(await request());
            return previous !== document.revision;
        },
        async configure(context, enabled) {
            const params = Object.fromEntries(
                Object.entries(context)
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
