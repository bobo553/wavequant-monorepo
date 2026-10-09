import assert from "node:assert/strict";
import test from "node:test";

import { WatchlistDocumentSchema } from "@repo/contracts";

import { createServerWatchlistStorage } from "../public/server-watchlists.js";
import { IdleWatchlistBacktests } from "../public/watchlist-backtest-queue.js";
import { addWatchlistMembers, normalizeWatchlistSnapshot } from "../public/watchlists.js";

const context = {
    run: "example",
    variant: "lecture_v3",
    scenario: "base",
    source: "akshare",
    start: "2018-01-01",
    volume_filter: "false",
    net_reward_risk_filter: "false",
    shallow_base_breakout_enabled: "true",
    n_target_trend_confirmation_enabled: "false",
    initial_capital: "100000",
    max_position_weight: "1",
};
const snapshot = (symbols = []) =>
    addWatchlistMembers(
        normalizeWatchlistSnapshot(null),
        "default",
        symbols.map((symbol) => ({ symbol, name: symbol })),
    ).state;
const document = (symbols = []) => ({ revision: 0, snapshot: snapshot(symbols), settings: null });
const cleanDocument = (value) => WatchlistDocumentSchema.parse(value);

function fixture({ old = snapshot(["sz.000678"]), doc = document(), request } = {}) {
    const preferences = new Map();
    const calls = [];
    const server = { doc };
    const subject = createServerWatchlistStorage({
        normalize: normalizeWatchlistSnapshot,
        legacy: { load: async () => old },
        parse: cleanDocument,
        preferences: { getItem: (key) => preferences.get(key), setItem: (key, value) => preferences.set(key, value) },
        request:
            request ||
            (async (body) => {
                calls.push(body);
                if (body?.action === "import") {
                    server.doc = {
                        ...server.doc,
                        revision: server.doc.revision + 1,
                        snapshot: addWatchlistMembers(server.doc.snapshot, "default", body.snapshot.memberships).state,
                    };
                } else if (body?.action === "save") {
                    server.doc = { ...server.doc, revision: server.doc.revision + 1, snapshot: body.snapshot };
                } else if (body?.action === "settings") {
                    server.doc = { ...server.doc, revision: server.doc.revision + 1, settings: body.settings };
                }
                return server.doc;
            }),
    });
    return { subject, server, preferences, calls };
}

test("first load migrates old stock data and keeps the server list; subsequent loads do not reimport", async () => {
    const { subject, calls } = fixture({ doc: document(["sh.600001"]) });
    assert.deepEqual((await subject.load()).memberships.map((member) => member.symbol).sort(), [
        "sh.600001",
        "sz.000678",
    ]);
    await subject.load();
    assert.equal(calls.filter((body) => body?.action === "import").length, 1);
});

test("a lost migration reply reuses the token and keeps the local migration backup", async () => {
    const imports = [];
    let attempts = 0;
    const { subject, preferences } = fixture({
        request: async (body) => {
            if (body?.action === "import") {
                imports.push(body.token);
                if (++attempts === 1) throw new Error("reply lost");
            }
            return document(["sz.000678"]);
        },
    });
    await assert.rejects(subject.load(), /reply lost/);
    assert.equal(
        [...preferences.values()].some((value) => JSON.parse(value).done),
        false,
    );
    await subject.load();
    assert.equal(imports[0], imports[1]);
});

test("a failed server read never falls back to local data or overwrites server data", async () => {
    let legacyReads = 0;
    const subject = createServerWatchlistStorage({
        normalize: normalizeWatchlistSnapshot,
        legacy: { load: async () => (++legacyReads, snapshot()) },
        parse: cleanDocument,
        request: async () => {
            throw new Error("offline");
        },
        preferences: null,
    });
    await assert.rejects(subject.load(), /offline/);
    assert.equal(legacyReads, 0);
});

test("server response is checked against the shared contract before use", async () => {
    const { subject } = fixture({ request: async () => ({ ...document(), revision: -1 }) });
    await assert.rejects(subject.load());
});

test("old watchlist settings default N target trend off and reject noncanonical persisted flags", () => {
    const old = { ...context };
    delete old.n_target_trend_confirmation_enabled;
    const parsed = cleanDocument({ ...document(), settings: { enabled: true, context: old } });
    assert.equal(parsed.settings.context.n_target_trend_confirmation_enabled, "false");
    for (const invalid of [true, false, 0, 1, null, "1", "TRUE"])
        assert.throws(() =>
            cleanDocument({
                ...document(),
                settings: { enabled: true, context: { ...context, n_target_trend_confirmation_enabled: invalid } },
            }),
        );
});

test("watchlist option survives a server reload and both flag transitions are persisted", async () => {
    const { subject, calls } = fixture({ old: snapshot() });
    await subject.load();
    await subject.configure({ ...context, n_target_trend_confirmation_enabled: true }, true);
    assert.equal(subject.document.settings.context.n_target_trend_confirmation_enabled, "true");
    await subject.refresh();
    assert.equal(subject.document.settings.context.n_target_trend_confirmation_enabled, "true");
    await subject.configure({ ...context, n_target_trend_confirmation_enabled: false }, true);
    assert.equal(subject.document.settings.context.n_target_trend_confirmation_enabled, "false");
    assert.deepEqual(
        calls
            .filter((body) => body?.action === "settings")
            .map((body) => body.settings.context.n_target_trend_confirmation_enabled),
        ["true", "false"],
    );
});

test("saving uses the current revision and stale writes remain failures", async () => {
    const { subject, server, calls } = fixture({ old: snapshot() });
    await subject.load();
    await subject.save(snapshot(["sz.000678"]));
    await subject.save(snapshot(["sz.000678", "sh.600001"]));
    assert.deepEqual(
        calls.filter((body) => body?.action === "save").map((body) => body.revision),
        [0, 1],
    );
    server.doc = { ...server.doc, revision: 3, snapshot: snapshot(["bj.920001"]) };
    assert.equal(await subject.refresh(), true);
    assert.equal(subject.document.snapshot.memberships[0].symbol, "bj.920001");
});

test("backtest configuration removes view-only fields, normalizes sizing and avoids redundant saves", async () => {
    const { subject, calls } = fixture({ old: snapshot() });
    await subject.load();
    await subject.configure(
        { ...context, group: "focus", cutoff: "2026-09-30", initial_capital: 100000, max_position_weight: 1 },
        true,
    );
    await subject.configure(context, true);
    await subject.configure(context, false);
    assert.equal(calls.filter((body) => body?.action === "settings").length, 2);
    assert.equal(subject.document.settings.enabled, false);
    assert.equal(subject.document.settings.context.initial_capital, "100000");
    assert.equal(Object.hasOwn(subject.document.settings.context, "group"), false);
});

test("a conflicting save reloads the authoritative document without claiming success", async () => {
    let rejectSave = true;
    const { subject } = fixture({
        old: snapshot(),
        request: async (body) => {
            if (body?.action === "save" && rejectSave) {
                const error = new Error("other page updated");
                error.httpStatus = 409;
                throw error;
            }
            return { ...document(["sh.600001"]), revision: 4 };
        },
    });
    await subject.load();
    await assert.rejects(subject.save(snapshot(["sz.000678"])), /other page updated/);
    assert.equal(subject.document.revision, 4);
    assert.equal(subject.document.snapshot.memberships[0].symbol, "sh.600001");
    rejectSave = false;
});

test("a server-managed queue checks new versions but never starts a duplicate browser backtest", async () => {
    let runs = 0,
        version = "v1",
        now = 0;
    const subject = new IdleWatchlistBacktests({
        serverManaged: true,
        snapshot: () => ({ context, members: [{ symbol: "sz.000678", asof: "2026-09-30" }] }),
        version: async () => ({ version }),
        run: async () => {
            runs++;
        },
        isIdle: () => true,
        onChange: () => {},
        now: () => now,
    });
    await subject.tick();
    assert.equal(subject.strategyVersion, "v1");
    version = "v2";
    now = 30001;
    await subject.tick();
    assert.equal(subject.strategyVersion, "v2");
    assert.equal(runs, 0);
});

test("durable current results keep metrics after restart; a different strategy version remains stale", async () => {
    const subject = new IdleWatchlistBacktests({
        snapshot: () => ({ context, members: [{ symbol: "sz.000678", asof: "2026-09-30" }] }),
        version: async () => ({ version: "v2" }),
        run: async () => {},
        isIdle: () => false,
        onChange: () => {},
    });
    await subject.ensureVersion();
    const completion = {
        path: "/api/akshare-backtest",
        params: { ...context, symbol: "sz.000678", asof: "2026-09-30" },
        symbol: "sz.000678",
        version: "v2",
        status: "completed",
        result_valid: true,
        persisted_current: true,
        result_available: false,
        total_return: 0.1,
        total_pnl: 10,
        fill_count: 2,
    };
    delete completion.params.source;
    assert.equal(subject.adoptServerStatus({ ...completion, version: "v1" }), false);
    assert.equal(subject.adoptServerStatus(completion), true);
    assert.equal(subject.state().statuses[completion.symbol], "completed");
    assert.equal(subject.state().returns[completion.symbol].amount, 10);
    assert.equal(subject.matchingJobId(completion.path, completion.params), null);
});
