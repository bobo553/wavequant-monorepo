import assert from "node:assert/strict";
import test from "node:test";

import { MarketBarsPageSchema } from "@repo/contracts";

import {
    loadMarketBarsHistory as loadHistory,
    marketPreviewMatches,
    validateMarketBarsPage,
} from "../public/market-bars-loader.js";

const parsePage = (value) => MarketBarsPageSchema.parse(value);
const loadMarketBarsHistory = (selection, options = {}) => loadHistory(selection, { parsePage, ...options });
const version = "a".repeat(64);

const request = { source: "tdx", symbol: "sz.000678", asof: "2026-10-11", timeframe: "1d" };
const selection = {
    kind: "market",
    source: request.source,
    symbol: request.symbol,
    requestedAsOf: request.asof,
    resolvedAsOf: "2026-10-09",
    timeframe: request.timeframe,
    dataVersion: version,
    priceBasis: "raw_unadjusted",
};
const bar = (year) => ({
    time: `${year}-10-09`,
    open: 10,
    high: 11,
    low: 9,
    close: 10.5,
    volume: 1000,
    raw_close: 10.5,
    factor: 1,
});
function page(year, years = [2026, 2025, 2024]) {
    const next = years[years.indexOf(year) + 1] ?? null;
    return {
        schema_version: 1,
        ...request,
        requested_asof: request.asof,
        resolved_asof: "2026-10-09",
        data_version: version,
        year,
        next_year: next,
        has_more: next !== null,
        available_years: years,
        history_start: `${years.at(-1)}-10-09`,
        history_end: "2026-10-09",
        total_bars: years.length,
        view: {
            symbol: request.symbol,
            data_source: request.source,
            result_scope: request.source,
            timeframe: request.timeframe,
            asof: "2026-10-09",
            bars: [bar(year)],
            sessions: [bar(year).time],
            data_version: version,
            price_basis: "raw_unadjusted",
            markers: [],
            orders: [],
            signals: [],
            trades: [],
            curve: [],
        },
    };
}
const response = (value) => new Response(JSON.stringify(value));
function deferred() {
    let resolve;
    const promise = new Promise((done) => {
        resolve = done;
    });
    return { promise, resolve };
}

test("latest year becomes visible while older annual requests are still pending", async () => {
    const visible = deferred(),
        older = deferred();
    const requests = [];
    const pending = loadMarketBarsHistory(request, {
        fetcher: async (url) => {
            const query = new URL(url, "http://local").searchParams;
            requests.push(query);
            const year = Number(query.get("year") || 2026);
            return year === 2025 ? older.promise : response(page(year));
        },
        onPage: (progress) => {
            if (progress.loadedYears === 1) visible.resolve(progress);
        },
    });
    const first = await visible.promise;
    assert.deepEqual(
        first.view.bars.map((value) => value.time),
        ["2026-10-09"],
    );
    assert.equal(first.loadedYears, 1);
    older.resolve(response(page(2025)));
    const complete = await pending;
    assert.deepEqual(
        requests.map((query) => query.get("year")),
        [null, "2025", "2024"],
    );
    assert.deepEqual(
        requests.map((query) => query.get("data_version")),
        [null, version, version],
    );
    assert.deepEqual(
        complete.view.bars.map((value) => value.time),
        ["2024-10-09", "2025-10-09", "2026-10-09"],
    );
    assert.equal(first.view.bars.length, 1, "published preview snapshots are not mutated by prepends");
});

test("same-stock backtest accounts and foreign preview identities cannot be reused as market bars", async () => {
    const view = { ...page(2026).view, data_source: "通达信本地行情" };
    assert.equal(marketPreviewMatches(view, request, selection), true);
    for (const changed of [
        { ...view, price_basis: "causal_adjusted" },
        { ...view, data_version: "b".repeat(64) },
        { ...view, backtest: { status: "completed" }, result_scope: "stock" },
    ])
        assert.equal(marketPreviewMatches(changed, request, selection), false);
    assert.equal(marketPreviewMatches(view, request, { ...selection, source: "akshare" }), false);
    const result = await loadMarketBarsHistory(request, {
        initialPreview: { view: { ...view, bars: [bar(2024)] }, selection: { ...selection, source: "akshare" } },
        fetcher: async (url) =>
            response(page(Number(new URL(url, "http://local").searchParams.get("year") || 2026), [2026, 2025])),
    });
    assert.deepEqual(
        result.view.bars.map((value) => value.time),
        ["2025-10-09", "2026-10-09"],
    );
});

test("missing calendar years are skipped using the server available-year chain", async () => {
    const years = [];
    await loadMarketBarsHistory(request, {
        fetcher: async (url) => {
            const year = Number(new URL(url, "http://local").searchParams.get("year") || 2026);
            years.push(year);
            return response(page(year, [2026, 2024]));
        },
    });
    assert.deepEqual(years, [2026, 2024]);
});

test("a truncated or disordered complete cache cannot skip annual history", async () => {
    for (const cachedBars of [[bar(2026)], [bar(2025), bar(2024), bar(2026)], [bar(2024), bar(2024), bar(2026)]]) {
        const years = [];
        const cached = {
            schema_version: 1,
            ...request,
            requested_asof: request.asof,
            resolved_asof: "2026-10-09",
            data_version: version,
            view: { ...page(2026).view, bars: cachedBars },
        };
        const result = await loadMarketBarsHistory(request, {
            getCachedBundle: () => cached,
            fetcher: async (url) => {
                const year = Number(new URL(url, "http://local").searchParams.get("year") || 2026);
                years.push(year);
                return response(page(year));
            },
        });
        assert.deepEqual(years, [2026, 2025, 2024]);
        assert.equal(result.cachedBundle, null);
        assert.equal(result.view.bars.length, 3);
    }
});

test("switch cancellation stops pending older history without publishing it", async () => {
    const controller = new AbortController();
    const entered = deferred();
    let published = 0;
    const pending = loadMarketBarsHistory(request, {
        signal: controller.signal,
        onPage: () => published++,
        fetcher: async (url, { signal }) => {
            if (!new URL(url, "http://local").searchParams.has("year")) return response(page(2026));
            entered.resolve();
            return new Promise((_, reject) =>
                signal.addEventListener("abort", () => reject(signal.reason), { once: true }),
            );
        },
    });
    await entered.promise;
    controller.abort();
    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(published, 1);
});

test("an older-year failure retains the already published recent candles and identifies the retry year", async () => {
    await assert.rejects(
        loadMarketBarsHistory(request, {
            fetcher: async (url) =>
                new URL(url, "http://local").searchParams.has("year")
                    ? new Response(JSON.stringify({ error: "older-year unavailable" }), { status: 503 })
                    : response(page(2026)),
        }),
        (error) => {
            assert.equal(error.year, 2025);
            assert.equal(error.partialView.bars[0].time, "2026-10-09");
            assert.match(error.message, /unavailable/);
            return true;
        },
    );
});

test("changed versions cannot merge an older page into the current preview", async () => {
    let published = 0;
    await assert.rejects(
        loadMarketBarsHistory(request, {
            onPage: () => published++,
            fetcher: async (url) => {
                const year = Number(new URL(url, "http://local").searchParams.get("year") || 2026);
                return response({
                    ...page(year),
                    data_version: year === 2026 ? version : "b".repeat(64),
                    view: { ...page(year).view, data_version: year === 2026 ? version : "b".repeat(64) },
                });
            },
        }),
        /版本已变化/,
    );
    assert.equal(published, 1);
});

test("a same-identity cached complete view is reused only after its data version is validated by the first year", async () => {
    let calls = 0;
    const cached = {
        schema_version: 1,
        ...request,
        requested_asof: request.asof,
        data_version: version,
        resolved_asof: "2026-10-09",
        view: { ...page(2026).view, bars: [bar(2024), bar(2025), bar(2026)] },
    };
    const result = await loadMarketBarsHistory(request, {
        getCachedBundle: () => cached,
        fetcher: async () => {
            calls++;
            return response(page(2026));
        },
    });
    assert.equal(calls, 1);
    assert.equal(result.view, cached.view);
    assert.equal(result.cachedBundle, cached);
});

test("annual shape validation rejects wrong-year, unsorted, future and inconsistent-next pages", () => {
    for (const mutation of [
        { view: { ...page(2026).view, bars: [bar(2025)] } },
        { available_years: [2026, 2024, 2025] },
        { next_year: 2024 },
        { resolved_asof: "2027-10-09" },
    ])
        assert.throws(() => validateMarketBarsPage({ ...page(2026), ...mutation }, parsePage));
});

test("retry retains same-version earlier preview bars while restarting its annual chain", async () => {
    const old = { ...page(2026).view, bars: [bar(2025), bar(2026)] };
    const previews = [];
    const result = await loadMarketBarsHistory(request, {
        initialPreview: { view: old, selection },
        onPage: ({ view }) => previews.push(view),
        fetcher: async (url) => response(page(Number(new URL(url, "http://local").searchParams.get("year") || 2026))),
    });
    assert.equal(previews[0].bars.length, 2);
    assert.equal(result.view.bars.length, 3);
    assert.equal(old.bars.length, 2);
});

test("historical cutoff retains later source trading sessions without inventing future bars", async () => {
    const resolved = "2026-10-09";
    const result = await loadMarketBarsHistory(request, {
        fetcher: async (url) => {
            const year = Number(new URL(url, "http://local").searchParams.get("year") || 2026);
            const value = page(year);
            value.view.sessions = [bar(year).time, `${year}-11-10`];
            return response(value);
        },
    });
    assert.equal(result.view.bars.at(-1).time, resolved);
    assert.equal(result.view.sessions.at(-1), "2026-11-10");
    assert.equal(result.view.sessions.length, 6);
});

test("a suspended current year starts with the latest available older year and preserves the requested cutoff", async () => {
    const selected = { ...request, asof: "2026-10-11" };
    const years = [];
    const result = await loadMarketBarsHistory(selected, {
        fetcher: async (url) => {
            const year = Number(new URL(url, "http://local").searchParams.get("year") || 2025);
            years.push(year);
            const value = page(year, [2025, 2024]);
            value.resolved_asof = value.history_end = value.view.asof = "2025-10-09";
            return response(value);
        },
    });
    assert.deepEqual(years, [2025, 2024]);
    assert.equal(result.view.asof, "2025-10-09");
});

test("different price bases and foreign cached identities are not reused", async () => {
    let calls = 0;
    const foreign = {
        schema_version: 1,
        ...request,
        requested_asof: request.asof,
        resolved_asof: "2026-10-09",
        data_version: version,
        view: { ...page(2026).view, price_basis: "adjusted", bars: [bar(2024), bar(2025), bar(2026)] },
    };
    await loadMarketBarsHistory(request, {
        getCachedBundle: () => foreign,
        fetcher: async (url) => {
            calls++;
            return response(page(Number(new URL(url, "http://local").searchParams.get("year") || 2026)));
        },
    });
    assert.equal(calls, 3);
    await assert.rejects(
        loadMarketBarsHistory(request, {
            fetcher: async (url) => {
                const year = Number(new URL(url, "http://local").searchParams.get("year") || 2026);
                const value = page(year);
                if (year === 2025) value.view.price_basis = "adjusted";
                return response(value);
            },
        }),
        /价格口径已变化/,
    );
});
