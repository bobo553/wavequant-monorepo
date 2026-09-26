import { expect, test } from "@playwright/test";

test("opening the workspace selects and backtests the first saved watchlist stock", async ({ page }) => {
    const asof = "2026-09-24";
    const stocks = [
        { symbol: "sz.000001", name: "甲股票", has_data: true, last: asof },
        { symbol: "sz.000002", name: "乙股票", has_data: true, last: asof },
    ];
    const theory = {
        asof,
        points: [],
        events: [],
        shapes: [],
        polyline_segments: [],
        lecture_drawing: { strokes: [], teaching_paths: [], inside_connections: [], issues: [] },
        reversal_trends: { strokes: [], developing_strokes: [] },
        secondary_trends: { strokes: [], developing_strokes: [] },
        tertiary_trends: { strokes: [], developing_strokes: [] },
    };
    await page.route("**/api/catalog", (route) =>
        route.fulfill({
            json: { runs: [{ id: "test-run", start: "2018-01-01", end: asof, symbols: [] }], variants: {} },
        }),
    );
    await page.route("**/api/akshare-catalog", (route) =>
        route.fulfill({ json: { available: true, latest: asof, with_daily: 2, stocks } }),
    );
    await page.route("**/api/market-timeframe?*", (route) => {
        const symbol = new URL(route.request().url()).searchParams.get("symbol");
        const view = {
            symbol,
            asof,
            result_scope: "akshare",
            data_source: "akshare",
            price_basis: "raw_unadjusted",
            provider_version: "test",
            timeframe: "1d",
            timeframe_label: "日线",
            sessions: [asof],
            bars: [{ time: asof, open: 10, high: 11, low: 9, close: 10.5, raw_close: 10.5, volume: 100 }],
            markers: [],
            signals: [],
            orders: [],
            trades: [],
            curve: [],
            metrics: null,
            theory,
            evidence: "模拟行情",
        };
        return route.fulfill({
            json: {
                schema_version: 1,
                snapshot_id: `test-${symbol}`,
                data_version: "test-data",
                algorithm_version: "test-algorithm",
                resolved_asof: asof,
                view,
                theory,
            },
        });
    });
    await page.route("**/api/backtest-version?*", (route) => route.fulfill({ json: { version: "strategy-v1" } }));
    await page.route("**/api/backtest-jobs*", (route) =>
        route.fulfill({ json: { active: 0, max_active: 4, jobs: [] } }),
    );
    const backtestSymbols = [];
    await page.route("**/api/akshare-backtest?*", (route) => {
        backtestSymbols.push(new URL(route.request().url()).searchParams.get("symbol"));
        return route.fulfill({ status: 503, json: { error: "test backtest response" } });
    });

    await page.goto("/research?page=workspace");
    await expect(page.locator("#watchlist-status")).toHaveText("自选股已从浏览器本地数据加载");
    await page.evaluate(async () => {
        const request = globalThis.indexedDB.open("wavequant-user-data", 1);
        const database = await new Promise((resolve, reject) => {
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
        });
        await new Promise((resolve, reject) => {
            const transaction = database.transaction("watchlist-memberships", "readwrite");
            const memberships = transaction.objectStore("watchlist-memberships");
            memberships.put({ groupId: "default", symbol: "sz.000002", name: "乙股票", position: 0 });
            memberships.put({ groupId: "default", symbol: "sz.000001", name: "甲股票", position: 1 });
            transaction.oncomplete = resolve;
            transaction.onerror = () => reject(transaction.error);
        });
        database.close();
    });
    await page.reload();

    await expect(page.locator("#symbol-select")).toHaveValue("sz.000002");
    await expect(page.locator("#result-scope")).toHaveValue("akshare-backtest");
    await expect.poll(() => backtestSymbols).toEqual(["sz.000002"]);
    await expect(page.locator("#watchlist-auto-backtest-toggle")).toHaveAttribute("aria-pressed", "false");
});
