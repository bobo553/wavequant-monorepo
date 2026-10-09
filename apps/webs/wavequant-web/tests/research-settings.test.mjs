import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { WatchlistDocumentSchema } from "@repo/contracts";

import {
    researchNTargetPreference,
    researchViewRequest,
    watchlistSettingsRestoration,
    watchlistSettingsToSave,
} from "../public/research-settings.js";
import { createServerWatchlistStorage } from "../public/server-watchlists.js";

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

function fixture() {
    const calls = [];
    let server = {
        revision: 0,
        settings: { enabled: false, context: { ...context } },
        snapshot: {
            schemaVersion: 1,
            groups: [{ id: "default", name: "默认", position: 0, protected: true }],
            memberships: [],
        },
    };
    const storage = createServerWatchlistStorage({
        normalize: (value) => value,
        legacy: { load: async () => server.snapshot },
        parse: (value) => WatchlistDocumentSchema.parse(value),
        preferences: { getItem: () => '{"done":true}', setItem: () => {} },
        request: async (body) => {
            if (body) {
                calls.push(body);
                assert.equal(body.action, "settings");
                assert.equal(body.revision, server.revision);
                server = { ...server, revision: server.revision + 1, settings: body.settings };
            }
            return structuredClone(server);
        },
    });
    return {
        storage,
        calls,
        remote: (enabled) => (server.settings.context.n_target_trend_confirmation_enabled = enabled),
    };
}

for (const scope of ["stock", "portfolio"]) {
    test(`${scope} option toggles persist with the saved context and polling preserves the sealed selection`, async () => {
        const { storage, calls } = fixture();
        await storage.load();
        const ui = { scope, variant: "lecture_v2", checked: false };
        for (const checked of [true, false]) {
            ui.checked = checked;
            const settings = watchlistSettingsToSave(null, storage.document.settings, true, ui.checked);
            assert.deepEqual(settings, {
                enabled: false,
                context: { ...context, n_target_trend_confirmation_enabled: String(checked) },
            });
            await storage.configure(settings.context, settings.enabled);
            for (let poll = 0; poll < 2; poll++) {
                await storage.refresh();
                const plan = watchlistSettingsRestoration(null, storage.document.settings, true, ui.checked);
                assert.equal(plan, null);
                assert.equal(ui.scope, scope);
                assert.equal(ui.variant, "lecture_v2");
                assert.equal(ui.checked, checked);
            }
        }
        assert.deepEqual(
            calls.map((body) => body.settings.context.n_target_trend_confirmation_enabled),
            ["true", "false"],
        );
    });
}

test("server updates restore only the global option when no market queue context exists", async () => {
    const { storage, remote } = fixture();
    await storage.load();
    remote("true");
    await storage.refresh();
    assert.deepEqual(watchlistSettingsRestoration(null, storage.document.settings, false, false), {
        kind: "n-target",
        enabled: true,
    });
});

test("market contexts retain their full settings synchronization", () => {
    const snapshot = { context: { ...context, group: "focus", cutoff: "2026-10-09" } };
    const saved = { enabled: false, context: { ...context, variant: "lecture_v2" } };
    assert.deepEqual(watchlistSettingsRestoration(snapshot, saved, false, false), { kind: "context", settings: saved });
    assert.deepEqual(watchlistSettingsToSave(snapshot, saved, true, true), {
        enabled: true,
        context: { ...context, n_target_trend_confirmation_enabled: "true" },
    });
});

test("first use without a server context stores no invented background settings and restores the browser fallback", () => {
    assert.equal(watchlistSettingsToSave(null, null, true, true), null);
    assert.equal(watchlistSettingsRestoration(null, null, true, true), null);
    assert.equal(researchNTargetPreference(null, { nTargetTrendConfirmationEnabled: true }), true);
    for (const invalid of [undefined, null, "true", "false", 1, 0]) {
        assert.equal(researchNTargetPreference(null, { nTargetTrendConfirmationEnabled: invalid }), false);
    }
    assert.equal(researchNTargetPreference(null, {}), false);
    assert.equal(
        researchNTargetPreference({ context: { ...context } }, { nTargetTrendConfirmationEnabled: true }),
        false,
    );
    assert.equal(
        researchNTargetPreference({ context: { ...context, n_target_trend_confirmation_enabled: "true" } }, {}),
        true,
    );
});

test("only the real stock-view consumer receives the selected option", () => {
    const params = { run: "example", variant: "lecture_v2", scenario: "base", symbol: "sz.000678", asof: "2018-09-25" };
    for (const selected of [undefined, false, true]) {
        assert.deepEqual(researchViewRequest("stock", params, selected), {
            path: "/api/stock-view",
            params: { ...params, n_target_trend_confirmation_enabled: String(selected === true) },
        });
        assert.deepEqual(researchViewRequest("portfolio", params, selected), { path: "/api/view", params });
    }
    assert.equal(Object.hasOwn(params, "n_target_trend_confirmation_enabled"), false);
});

test("consumer and saving boundaries reject non-boolean options", () => {
    for (const invalid of [null, 0, 1, "true", "false"]) {
        assert.throws(() => researchViewRequest("stock", {}, invalid), TypeError);
        assert.throws(() => watchlistSettingsToSave(null, null, true, invalid), TypeError);
        assert.throws(() => watchlistSettingsRestoration(null, null, true, invalid), TypeError);
    }
});

test("workbench request and polling use the tested flow and retain browser preference fallback", () => {
    const source = readFileSync(new URL("../public/app.js", import.meta.url), "utf8");
    assert.match(source, /api\(snapshotRequest\.path, snapshotRequest\.params, requestSignal\)/);
    assert.match(source, /const settings = watchlistSettingsToSave\(/);
    assert.match(source, /const restoration = watchlistSettingsRestoration\(/);
    assert.match(source, /restoration\?\.kind === "n-target"[\s\S]*?checked = restoration\.enabled/);
    assert.match(source, /researchNTargetPreference\(savedSettings, chartPreferences\)/);
    assert.match(source, /nTargetTrendConfirmationEnabled: \$\("n-target-trend-confirmation"\)\.checked/);
});
