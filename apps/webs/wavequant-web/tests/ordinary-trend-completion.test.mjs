import assert from "node:assert/strict";
import test from "node:test";

import {
    researchOrdinaryPreference,
    researchViewRequest,
    watchlistSettingsToSave,
    watchlistSettingsRestoration,
} from "../public/research-settings.js";
import { marketTimeframeCacheKey } from "../public/catalog-cache.js";
import { backtestArgumentsKey } from "../public/watchlist-backtest-queue.js";

test("ordinary completion defaults off and persists explicit selection", () => {
    assert.equal(researchOrdinaryPreference(null, {}), false);
    const saved = { enabled: true, context: { source: "tdx", n_target_trend_confirmation_enabled: "false" } };
    assert.equal(researchOrdinaryPreference(saved, { ordinaryTrendCompletionEnabled: true }), false);
    const selected = watchlistSettingsToSave(null, saved, true, false, true);
    assert.equal(selected.context.ordinary_trend_completion_enabled, "true");
    assert.equal(researchOrdinaryPreference(selected, {}), true);
    assert.deepEqual(watchlistSettingsRestoration(null, selected, true, false, false), {
        kind: "n-target", enabled: false, ordinaryEnabled: true,
    });
    assert.equal(researchViewRequest("stock", {}, false).params.ordinary_trend_completion_enabled, "false");
    assert.equal(researchViewRequest("stock", {}, false, true).params.ordinary_trend_completion_enabled, "true");
});

test("both chart and backtest caches distinguish ordinary completion mode", () => {
    const args = ["tdx", "sh.600825", "2021-12-09", "1d", false];
    assert.notEqual(marketTimeframeCacheKey(...args, false), marketTimeframeCacheKey(...args, true));
    const path = "/api/tdx-backtest";
    const params = { symbol: "sh.600825" };
    assert.equal(backtestArgumentsKey(path, params), backtestArgumentsKey(path, {
        ...params, ordinary_trend_completion_enabled: "false",
    }));
    assert.notEqual(backtestArgumentsKey(path, params), backtestArgumentsKey(path, {
        ...params, ordinary_trend_completion_enabled: "true",
    }));
});
